from __future__ import annotations

import json
import uuid
from datetime import timedelta

from ..common import (
    WorkflowError,
    digest,
    now,
    parse_time,
    timestamp,
    write_json,
)
from ..reports import render
from ..review import review_queue
from ..types import Record
from .context import AgentContext

TERMINAL = {"REJECTED", "WITHDRAWN", "INTERVIEW", "REPLIED", "CLOSED", "DISMISSED"}
CONFIRMED = {"SUBMITTED_CONFIRMED", "OUTREACH_SENT_CONFIRMED", "SENT"}


class ReportingService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def report(self) -> Record:
        self.agent.active()
        rows = self.agent.prepare()
        known = {
            r[0] for r in self.agent.store.db.execute("SELECT company_key FROM reported_companies")
        }
        tiers = {"GREAT": 0, "BETTER": 1, "GOOD": 2}
        eligible = sorted(
            [
                r
                for r in rows
                if r["evaluation"]["status"] == "READY_FOR_REVIEW" and r["state"] not in TERMINAL
            ],
            key=lambda r: (
                tiers[r["payload"].get("tier", "GOOD")],
                -r["evaluation"]["match_score"],
                r["payload"].get("deadline") or "9999",
            ),
        )
        selected: list[Record] = []
        updates: list[Record] = []
        chosen: set[str] = set()
        for row in eligible:
            if row["company_key"] in known:
                prior = self.agent.store.setting(f"reported_package:{row['id']}")
                if prior != row["package_hash"]:
                    updates.append(row)
            elif (
                row["company_key"] not in chosen
                and len(selected) < self.agent.settings["max_new_companies"]
            ):
                selected.append(row)
                chosen.add(row["company_key"])
        watch_all = [r for r in rows if r["evaluation"]["status"] != "READY_FOR_REVIEW"]
        watch = [
            r
            for r in watch_all
            if self.agent.store.setting(f"reported_review:{r['id']}") != self.agent.review_hash(r)
        ]
        displayed = selected + updates + watch
        report_id = "REPORT-" + now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        path = self.agent.root / "reports" / (report_id + ".md")
        preflight = self.agent.preflight()
        last = self.agent.store.setting("last_discovery", {})
        counts = {
            **last.get("counts", {}),
            "ledger_opportunities": len(rows),
            "verified_live": sum(
                r["evaluation"]["gates"]["live_vacancy"]["status"] == "PASS" for r in rows
            ),
            "rejected": sum(r["evaluation"]["status"] == "REJECTED" for r in rows),
            "ready_for_review": len(eligible),
            "new_shortlist_companies": len(selected),
            "pending_clarification": sum(
                r["evaluation"]["status"] == "NEEDS_CLARIFICATION" for r in rows
            ),
            "new_or_changed_watchlist_items": len(watch),
            "unchanged_watchlist_items": len(watch_all) - len(watch),
        }
        cumulative = self.agent.status()
        text = render(
            report_id,
            rows,
            selected,
            updates,
            watch,
            counts,
            preflight["limitations"] + last.get("errors", []),
            cumulative,
            self.agent.settings,
            candidate_name=self.agent.profile["name"],
        )
        queue = review_queue(rows, self.agent.profile, self.agent.settings, len(rows))
        text += "\n## Availability and next reviews\n\n"
        from ..availability import availability_text

        text += (
            "Internships: "
            + availability_text(self.agent.profile, "internship")
            + ".\n\nJobs: "
            + availability_text(self.agent.profile, "full_time")
            + ".\n\n"
        )
        text += "Discovery counts describe the last discovery run; ledger counts cover all stored history. Unchanged watchlist rows remain in the companion review JSON and review queue.\n\n"
        text += (
            "\n".join(
                f"- {item['opportunity_id']} | {item['company']} | {item['priority']} review priority | {len(item['tasks'])} unresolved review tasks | {item['source']}"
                for item in queue["items"][:10]
            )
            + "\n"
        )
        health = self.agent.store.source_status()
        text += (
            "\n## Last recorded source checks\n\n"
            + "\n".join(
                f"- {s['company']} ({s['provider']}): {s['status']}, {s['postings_count']} postings; checked {s['checked_at']}. "
                + (s["error"] or "")
                for s in health
            )
            + "\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        snapshots = {
            r["opportunity_id"]: {
                "package_hash": r["package_hash"],
                "review_hash": self.agent.review_hash(r),
                "shortlisted": r in selected + updates,
            }
            for r in displayed
        }
        with self.agent.store.transaction():
            self.agent.store.db.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?)",
                (report_id, timestamp(), None, str(path), json.dumps(snapshots)),
            )
            self.agent.store.event(None, "REPORT_GENERATED", report_id, {"path": str(path)})
        rejected_details = [
            {
                "opportunity_id": r["opportunity_id"],
                "role": r["payload"]["role"],
                "reasons": [
                    g["reason"] for g in r["evaluation"]["gates"].values() if g["status"] == "FAIL"
                ],
            }
            for r in rows
            if r["evaluation"]["status"] == "REJECTED"
        ]
        pending_details = [
            {
                "opportunity_id": r["opportunity_id"],
                "role": r["payload"]["role"],
                "unresolved": r["evaluation"]["unknowns"],
            }
            for r in rows
            if r["evaluation"]["status"] == "NEEDS_CLARIFICATION"
        ]
        review_path = path.with_suffix(".review.json")
        write_json(
            review_path,
            {
                "report_id": report_id,
                "rejected": rejected_details,
                "pending_clarification": pending_details,
            },
        )
        queue_path = path.with_suffix(".queue.json")
        write_json(queue_path, {"report_id": report_id, **queue})
        result_path = path.with_suffix(".run.json")
        write_json(
            result_path,
            {
                "report_id": report_id,
                "discovery": last,
                "source_health": health,
                "ledger_counts": counts,
                "availability": self.agent.profile.get("availability", {}),
            },
        )
        from ..company_web import export_company_web
        from ..daily import current_daily_selection

        company_web = export_company_web(
            self.agent.root,
            rows,
            last,
            report_id,
            path.with_suffix(".companies.json"),
            daily=current_daily_selection(self.agent.store),
        )
        return {
            "report_id": report_id,
            "path": str(path),
            "counts": counts,
            "delivered": False,
            "delivery": {
                "local_report_created": True,
                "marked_reviewed": False,
                "email_sent": False,
            },
            "review_details_path": str(review_path),
            "review_queue_path": str(queue_path),
            "run_details_path": str(result_path),
            "company_web": company_web,
            "next_step": "Review the Markdown report and per-opportunity reasons in the companion review JSON. Only unresolved conditions need clarification; rejected roles are not awaiting approval.",
            "limitations": preflight["limitations"],
        }

    @staticmethod
    def review_hash(row: Record) -> str:
        # Source recheck timestamps should not masquerade as a material update.
        gates = {
            name: {k: v for k, v in gate.items() if k != "checked_at"}
            for name, gate in row["evaluation"]["gates"].items()
        }
        return digest(
            {
                "package": row["package_hash"],
                "gates": gates,
                "unknowns": row["evaluation"]["unknowns"],
                "status": row["state"],
            }
        )

    def deliver(self, report_id: str) -> Record:
        record = self.agent.store.db.execute(
            "SELECT * FROM reports WHERE id=?", (report_id,)
        ).fetchone()
        if not record:
            raise WorkflowError("Unknown report ID.")
        if record["delivered_at"]:
            return {
                "report_id": report_id,
                "delivered_at": record["delivered_at"],
                "already_recorded": True,
            }
        with self.agent.store.transaction():
            at = timestamp()
            for job_id, snapshot in json.loads(record["opportunity_ids"]).items():
                row = self.agent.store.get(job_id)
                self.agent.store.db.execute(
                    "INSERT OR IGNORE INTO reported_companies VALUES(?,?,?)",
                    (row["company_key"], report_id, at),
                )
                self.agent.store.db.execute(
                    "UPDATE opportunities SET report_id=? WHERE id=?", (report_id, row["id"])
                )
                self.agent.store.set_setting(
                    f"reported_package:{row['id']}", snapshot["package_hash"]
                )
                if snapshot.get("review_hash"):
                    self.agent.store.set_setting(
                        f"reported_review:{row['id']}", snapshot["review_hash"]
                    )
            self.agent.store.db.execute(
                "UPDATE reports SET delivered_at=? WHERE id=?", (at, report_id)
            )
            self.agent.store.event(None, "REPORT_DELIVERED_LOCALLY", report_id)
        return {"report_id": report_id, "delivered_at": at, "email_sent": False}

    def status(self) -> Record:
        rows = self.agent.store.all()

        def confirmed(key: str, state: str) -> int:
            return sum(r[key] == state for r in rows)

        return {
            "paused": self.agent.store.setting("paused", False),
            "unique_companies_reported": self.agent.store.db.execute(
                "SELECT COUNT(*) FROM reported_companies"
            ).fetchone()[0],
            "opportunities": len(rows),
            "applications_confirmed": confirmed("portal_status", "SUBMITTED_CONFIRMED"),
            "outreach_confirmed": confirmed("outreach_status", "OUTREACH_SENT_CONFIRMED"),
            "awaiting_approval": sum(r["state"] == "AWAITING_APPROVAL" for r in rows),
            "attempted_actions": self.agent.store.db.execute(
                "SELECT COUNT(*) FROM attempts"
            ).fetchone()[0],
            "follow_up_checks_due": sum(
                bool(
                    r["follow_up_due_at"]
                    and not r["follow_up_sent_at"]
                    and parse_time(r["follow_up_due_at"]) <= now()
                    and r["state"] not in TERMINAL
                )
                for r in rows
            ),
            "replies_recorded": sum(r["state"] == "REPLIED" for r in rows),
            "weekly_summary": self.agent.weekly_summary(rows),
            "scheduler": "Not managed by this process; inspect Windows Task Scheduler separately.",
        }

    def weekly_summary(self, rows: list[Record]) -> Record:
        start = now() - timedelta(days=7)
        if not rows or min(parse_time(r["first_seen_at"]) for r in rows) > start:
            return {
                "available": False,
                "reason": "Fewer than seven days of local opportunity history; no performance rates inferred.",
            }
        from ..policy import role_family

        families: dict[str, Record] = {}
        sources: dict[str, Record] = {}
        gaps: dict[str, int] = {}
        cohort = [r for r in rows if parse_time(r["first_seen_at"]) >= start]
        for row in cohort:
            for collection, key in (
                (families, role_family(row["payload"]["role"])),
                (sources, row["payload"].get("provider", "reviewed_import")),
            ):
                item = collection.setdefault(key, {"opportunities_found": 0, "currently_ready": 0})
                item["opportunities_found"] += 1
                item["currently_ready"] += int(
                    bool(row["evaluation"] and row["evaluation"]["status"] == "READY_FOR_REVIEW")
                )
            for gap in (row["evaluation"] or {}).get("gaps", []):
                gaps[gap] = gaps.get(gap, 0) + 1
        attempts = [
            dict(r)
            for r in self.agent.store.db.execute(
                "SELECT * FROM attempts WHERE completed_at IS NOT NULL"
            )
            if parse_time(r["completed_at"]) >= start
        ]
        events = [
            dict(r)
            for r in self.agent.store.db.execute("SELECT * FROM events WHERE action='STATUS_CHECK'")
            if parse_time(r["at"]) >= start
        ]
        confirmed_opportunities = {
            r["id"]
            for r in rows
            if r["portal_status"] == "SUBMITTED_CONFIRMED"
            or r["outreach_status"] == "OUTREACH_SENT_CONFIRMED"
        }
        return {
            "available": True,
            "window_start_ist": timestamp(start),
            "window_end_ist": timestamp(),
            "scope": "Observed local history only. Eligibility is the current assessment of roles first seen in this window. Replies/interviews may concern older submissions; no conversion rate inferred.",
            "new_opportunities_denominator": len(cohort),
            "by_role_family": families,
            "by_source": sources,
            "applications_confirmed_in_window": sum(
                a["status"] == "SUBMITTED_CONFIRMED" for a in attempts
            ),
            "outreach_confirmed_in_window": sum(
                a["status"] == "OUTREACH_SENT_CONFIRMED" for a in attempts
            ),
            "replies_recorded_in_window": len(
                {e["opportunity_id"] for e in events if e["result"] == "replied"}
            ),
            "interviews_recorded_in_window": len(
                {e["opportunity_id"] for e in events if e["result"] == "interview"}
            ),
            "all_time_confirmed_opportunities_denominator": len(confirmed_opportunities),
            "recurring_skill_gaps": gaps,
            "preparation_priority": "Review recurring gaps; no candidate skills or search preferences were changed automatically.",
        }

    def preflight(self) -> Record:
        resume = self.agent.resume()
        limitations = []
        if not self.agent.settings.get("history_complete"):
            limitations.append(
                "History incomplete: only this local ledger is available; earlier applications/company reports are unknown."
            )
        if not resume["available"]:
            limitations.append(
                f"Designated resume ({self.agent.settings['resume_path']}) is absent. Drafts are provisional and attachment-dependent execution is blocked."
            )
        elif not resume["profile_checked"]:
            limitations.append(
                "Resume/profile reconciliation has not been confirmed for the current file and profile hashes."
            )
        if not self.agent.settings["sources"]:
            limitations.append(
                "No employer-linked ATS boards configured. Add verified sources or import reviewed primary-source opportunity evidence."
            )
        schedule = self.agent.store.setting("installed_schedule", {})
        scheduling_note = "No scheduler has been installed by the application. Reports are files; no report emails are sent."
        if schedule.get("installed"):
            scheduling_note = "Windows daily discovery was registered for 09:00 IST; use schedule-status to check its current state. The computer must be available and the selected user signed in. Reports are local files; no report emails are sent."
        limitations += [
            "Portal submission and email sending use manual handoffs; this implementation does not control a browser or mail provider.",
            scheduling_note,
            "Global web search and recruiter-contact research require an analyst; query links are available with the queries command.",
        ]
        return {
            "generated_at_ist": timestamp(),
            "resume": resume,
            "profile_version": self.agent.profile["version"],
            "limitations": limitations,
            "configuration_validated": True,
            "database": self.agent.store.schema_status(),
            "paused": self.agent.store.setting("paused", False),
            "source_count": len(self.agent.settings["sources"]),
            "tools": [
                "public ATS HTTP GET",
                "SQLite ledger",
                "Markdown/JSON exports",
                "manual execution handoff",
            ],
        }
