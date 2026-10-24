from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..common import (
    WorkflowError,
    digest,
    keys,
    now,
    timestamp,
    write_json,
)
from ..review import lead_priority
from ..sources import relevant_lead
from ..types import Record
from .context import AgentContext

TERMINAL = {"REJECTED", "WITHDRAWN", "INTERVIEW", "REPLIED", "CLOSED", "DISMISSED"}
CONFIRMED = {"SUBMITTED_CONFIRMED", "OUTREACH_SENT_CONFIRMED", "SENT"}


class DiscoveryService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def discover(self) -> Record:
        self.agent.active()
        run_id = "RUN-" + now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        # A process killed during the preceding run leaves an explicit incomplete run.
        self.agent.store.db.execute(
            "UPDATE discovery_runs SET status='INTERRUPTED',finished_at=? WHERE status='RUNNING'",
            (timestamp(),),
        )
        self.agent.store.db.execute(
            "INSERT INTO discovery_runs VALUES(?,?,NULL,'RUNNING','{}',NULL)", (run_id, timestamp())
        )
        try:
            result = self.agent._discover(run_id)
            self.agent.store.db.execute(
                "UPDATE discovery_runs SET finished_at=?,status=?,summary=?,report_id=? WHERE id=?",
                (
                    timestamp(),
                    "PARTIAL"
                    if self.agent.store.setting("last_discovery")["errors"]
                    else "COMPLETED",
                    json.dumps(result["counts"]),
                    result["report_id"],
                    run_id,
                ),
            )
            result["run_id"] = run_id
            return result
        except BaseException as exc:
            self.agent.store.db.execute(
                "UPDATE discovery_runs SET finished_at=?,status='FAILED',summary=? WHERE id=?",
                (timestamp(), json.dumps({"error_type": type(exc).__name__}), run_id),
            )
            raise

    def _discover(self, run_id: str) -> Record:
        errors, leads, seen = [], [], 0
        before = {r["opportunity_id"] for r in self.agent.store.all()}
        sources = sorted(
            self.agent.settings["sources"],
            key=lambda x: (
                x["company"].casefold() != self.agent.settings["preferred_employer"].casefold()
            ),
        )
        workers = max(1, min(4, int(self.agent.settings.get("discovery_workers", 3))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                (source, pool.submit(self.agent.fetch_board, source, self.agent.http))
                for source in sources
            ]
            for source, future in futures:
                try:
                    board = future.result()
                    if not board:
                        average = self.agent.store.db.execute(
                            "SELECT AVG(postings_count) FROM source_checks WHERE source_key=? AND status='OK' AND checked_at>=?",
                            (digest(source), timestamp(now() - timedelta(days=7))),
                        ).fetchone()[0]
                        if average is not None and average >= 10:
                            self.agent.store.event(
                                None,
                                "SOURCE_ANOMALY",
                                source["company"]
                                + ": zero postings after a seven-day mean of "
                                + str(round(average, 1)),
                                {"source_key": digest(source), "run_id": run_id},
                            )
                    seen += len(board)
                    leads.extend(board)
                    current_keys = {keys(job, self.agent.store.aliases)[1] for job in board}
                    for row in self.agent.store.all():
                        if (
                            row["payload"].get("source_config") == source
                            and row["opportunity_key"] not in current_keys
                        ):
                            job = row["payload"]
                            job.setdefault("eligibility_gates", {})["live_vacancy"] = {
                                "status": "UNKNOWN",
                                "reason": "Previously seen role was absent from the current source response. Reverify the individual vacancy; absence alone is not proof of closure.",
                                "source": job["canonical_url"],
                                "checked_at": timestamp(),
                            }
                            self.agent.store.upsert(job)
                    self.agent.store.db.execute(
                        "INSERT INTO source_checks(run_id,source_key,company,provider,checked_at,status,postings_count) VALUES(?,?,?,?,?,'OK',?)",
                        (
                            run_id,
                            digest(source),
                            source["company"],
                            source["provider"],
                            timestamp(),
                            len(board),
                        ),
                    )
                except (WorkflowError, KeyError, TypeError, ValueError, OSError) as exc:
                    errors.append(f"{source.get('company', 'Source')}: {exc}")
                    self.agent.store.db.execute(
                        "INSERT INTO source_checks(run_id,source_key,company,provider,checked_at,status,postings_count,error) VALUES(?,?,?,?,?,'ERROR',0,?)",
                        (
                            run_id,
                            digest(source),
                            source.get("company", "Source"),
                            source.get("provider", "unknown"),
                            timestamp(),
                            str(exc),
                        ),
                    )
                    self.agent._source_failed(source)
        locations = self.agent.settings.get("discovery_location_terms", [])
        scoped = [
            j
            for j in leads
            if not locations
            or j.get("location") in {None, "", "Not disclosed"}
            or any(
                re.search(r"\b" + re.escape(term) + r"\b", j.get("location", ""), re.I)
                for term in locations
            )
        ]
        main = [j for j in scoped if relevant_lead(j)]
        budget = max(1, int(self.agent.settings.get("max_leads_per_run", 150)))
        from ..daily import diverse_leads

        known_companies = {
            r[0]
            for r in self.agent.store.db.execute(
                "SELECT company_key FROM reported_companies UNION SELECT company_key FROM daily_companies"
            )
        }
        chosen = diverse_leads(
            main,
            self.agent.settings["preferred_employer"],
            known_companies,
            budget,
            self.agent.settings.get("max_leads_per_company", 10),
            aliases=self.agent.store.aliases,
        )
        deferred = len(main) - len(chosen)
        imported = [self.agent.store.upsert(j) for j in self.agent.merge_discovery(chosen)]
        rows = self.agent.prepare()
        ready_count = len(
            {r["company_key"] for r in rows if r["evaluation"]["status"] == "READY_FOR_REVIEW"}
        )
        broadened = 0
        if ready_count < 5:
            extras = sorted(
                [j for j in scoped if not relevant_lead(j) and relevant_lead(j, broaden=True)],
                key=lambda j: lead_priority(j, self.agent.settings["preferred_employer"]),
            )
            remaining = max(0, budget - len(imported))
            main_company_counts: dict[str, int] = {}
            for job in chosen:
                key = keys(job, self.agent.store.aliases)[0]
                main_company_counts[key] = main_company_counts.get(key, 0) + 1
            selected_extras = diverse_leads(
                extras,
                self.agent.settings["preferred_employer"],
                known_companies,
                remaining,
                self.agent.settings.get("max_leads_per_company", 10),
                main_company_counts,
                self.agent.store.aliases,
            )
            deferred += len(extras) - len(selected_extras)
            broadened = len(selected_extras)
            imported += [
                self.agent.store.upsert(j) for j in self.agent.merge_discovery(selected_extras)
            ]
        stats = {
            "board_roles_seen": seen,
            "relevant_leads": len(imported),
            "new_leads": sum(job_id not in before for job_id, _ in imported),
            "changed_leads": sum(job_id in before and changed for job_id, changed in imported),
            "duplicates_unchanged": sum(not changed for _, changed in imported),
            "deferred_by_run_budget": deferred,
            "outside_location_search_scope": len(leads) - len(scoped),
            "additional_pass_leads": broadened,
            "sources_checked": len(sources),
            "sources_failed": len(errors),
        }
        self.agent.store.set_setting(
            "last_discovery",
            {"run_id": run_id, "at": timestamp(), "counts": stats, "errors": errors},
        )
        from ..notifications import scan_notifications

        scan_notifications(self.agent)
        return self.agent.report()

    def _source_failed(self, source: Record) -> None:
        for row in self.agent.store.all():
            if row["payload"].get("source_config") == source:
                job = row["payload"]
                job.setdefault("eligibility_gates", {})["live_vacancy"] = {
                    "status": "UNKNOWN",
                    "reason": "Current source access failed; verification incomplete, not proof of closure.",
                    "source": job["canonical_url"],
                    "checked_at": timestamp(),
                }
                self.agent.store.upsert(job)

    def daily(self) -> Record:
        self.agent.active()
        from ..daily import daily_selection, refresh_source_pool

        try:
            last_pool = self.agent.store.setting("source_pool_check", {})
            if (last_pool.get("at") or "")[:10] != now().date().isoformat():
                refresh_source_pool(self.agent)
            discovery_report = self.agent.discover()
            target = self.agent.settings.get("daily_company_target", 5)
            self.agent.store.set_setting("daily_company_target", target)
            selection = daily_selection(
                self.agent.store,
                self.agent.prepare(),
                target,
                preferred=self.agent.settings["preferred_employer"],
            )
            result = self.agent.report()
            result.update(
                daily_companies=selection, discovery_report_id=discovery_report["report_id"]
            )
            write_json(self.agent.root / "data" / "daily" / "latest-result.json", result)
            write_json(
                self.agent.root / "data" / "daily" / (now().date().isoformat() + ".json"), result
            )
            return result
        except BaseException as exc:
            write_json(
                self.agent.root / "data" / "daily" / "latest-result.json",
                {"at": timestamp(), "status": "FAILED", "error": str(exc)},
            )
            raise

    def merge_discovery(self, leads: Iterable[Record]) -> Iterator[Record]:
        from ..deduplication import potential_cross_post

        existing = {r["opportunity_key"]: r for r in self.agent.store.all()}
        for lead in leads:
            row = existing.get(keys(lead, self.agent.store.aliases)[1])
            if row:
                old = row["payload"]
                fields = (
                    "description",
                    "role",
                    "location",
                    "employment_type",
                    "canonical_url",
                    "application_url",
                )
                source_unchanged = not old.get("source_content_hash") or old.get(
                    "source_content_hash"
                ) == lead.get("source_content_hash")
                if source_unchanged and all(old.get(k) == lead.get(k) for k in fields):
                    merged = dict(old)
                    merged["last_verified_at"] = lead["last_verified_at"]
                    if lead.get("source_content_hash"):
                        merged["source_content_hash"] = lead["source_content_hash"]
                    merged.setdefault("eligibility_gates", {})
                    for key in ("employer_identity", "live_vacancy"):
                        merged["eligibility_gates"][key] = lead["eligibility_gates"][key]
                    lead = merged
                # Any JD/role/location change discards old assessments rather than silently reusing them.
            elif len(lead.get("description", "")) > 200:
                possible = [
                    item["opportunity_id"]
                    for item in existing.values()
                    if item["company_key"] == keys(lead, self.agent.store.aliases)[0]
                    and potential_cross_post(item["payload"], lead)
                ]
                if possible:
                    lead = {**lead, "possible_cross_posts": possible}
            yield lead
