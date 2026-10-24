from __future__ import annotations

import uuid
from datetime import timedelta

from ..common import (
    WorkflowError,
    now,
    parse_time,
    timestamp,
    write_json,
)
from ..types import Record
from .context import AgentContext

TERMINAL = {"REJECTED", "WITHDRAWN", "INTERVIEW", "REPLIED", "CLOSED", "DISMISSED"}
CONFIRMED = {"SUBMITTED_CONFIRMED", "OUTREACH_SENT_CONFIRMED", "SENT"}


class ExecutionService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def refresh_for_execution(self, row: Record) -> Record:
        job = row["payload"]
        if job.get("source_config"):
            jobs = self.agent.fetch_board(job["source_config"], self.agent.http)
            match = next((j for j in jobs if str(j.get("job_id")) == str(job.get("job_id"))), None)
            if not match:
                self.agent.store.invalidate(row["id"], "NO_LONGER_LISTED_BY_OFFICIAL_ATS")
                raise WorkflowError(
                    "Vacancy no longer appears on the official board. Approval invalidated; review its status."
                )
            merged = next(self.agent.merge_discovery([match]))
            self.agent.store.upsert(merged)
            self.agent.prepare()
            return self.agent.store.get(row["id"])
        # Imported/manual sources need a recent human recheck, with explicit status, destination and duplicate checks.
        recheck = self.agent.store.setting(f"recheck:{row['id']}")
        if (
            not recheck
            or recheck.get("material_hash") != row["material_hash"]
            or not timedelta(0) <= now() - parse_time(recheck["checked_at"]) <= timedelta(minutes=5)
        ):
            raise WorkflowError(
                "Manual/IBM source: recheck the official vacancy, eligibility, deadline, destination and duplicate history, then record recheck within five minutes of handoff."
            )
        return row

    def handoff(self, job_id: str, channel: str) -> Record:
        self.agent.active()
        self.agent.prepare()
        row = self.agent.refresh_for_execution(self.agent.store.get(job_id))
        if row["state"] in TERMINAL:
            raise WorkflowError("Opportunity has a stopping outcome.")
        if channel == "follow_up":
            self.agent.follow_up_eligible(row)
            if row["evaluation"]["gates"]["live_vacancy"]["status"] != "PASS":
                raise WorkflowError(
                    "Follow-up is held because the vacancy is no longer verified live."
                )
        elif row["evaluation"]["status"] != "READY_FOR_REVIEW" or row["package"]["blockers"]:
            raise WorkflowError("Eligibility or package blockers prevent execution.")
        if channel in {"outreach", "follow_up"}:
            checked = self.agent.store.setting(f"recheck:{row['id']}")
            if (
                not checked
                or checked.get("material_hash") != row["material_hash"]
                or not timedelta(0)
                <= now() - parse_time(checked["checked_at"])
                <= timedelta(minutes=5)
            ):
                raise WorkflowError(
                    "Record a current manual destination/recipient-identity/recruiting-relevance recheck before email handoff."
                )
        if self.agent.resume_for(row["payload"]) != row["package"]["resume"]:
            raise WorkflowError("Resume changed; prepare and approve the new attachment version.")
        approval = self.agent.store.db.execute(
            "SELECT * FROM approvals WHERE opportunity_id=? AND channel=? AND consumed_at IS NULL AND invalidated_at IS NULL ORDER BY id DESC LIMIT 1",
            (row["id"], channel),
        ).fetchone()
        if not approval or approval["scope_hash"] != self.agent.scope_hash(row, channel):
            raise WorkflowError("No valid approval exists for this exact action package.")
        if not parse_time(approval["valid_from"]) <= now() <= parse_time(approval["expires_at"]):
            raise WorkflowError("Approval expired or follow-up check is not yet due.")
        outstanding = self.agent.store.db.execute(
            "SELECT token FROM attempts WHERE opportunity_id=? AND channel=? AND status IN ('EXECUTING','SUBMISSION_UNCERTAIN')",
            (row["id"], channel),
        ).fetchone()
        if outstanding:
            raise WorkflowError(
                "An execution is unresolved. Reconcile its token; do not repeat the action."
            )
        status = (
            row["portal_status"]
            if channel == "portal"
            else row["outreach_status"]
            if channel == "outreach"
            else row["follow_up_status"]
        )
        if status in CONFIRMED or (channel == "follow_up" and row["follow_up_sent_at"]):
            raise WorkflowError("This action has already been confirmed.")
        token = uuid.uuid4().hex
        from ..domain import validate_transition

        validate_transition(row["state"], "EXECUTING")
        expires_at = min(parse_time(approval["expires_at"]), now() + timedelta(hours=24))
        if channel == "follow_up":
            original = next(a for a in row["package"]["actions"] if a["channel"] == "outreach")
            action = {
                "channel": "follow_up",
                "destination": original["destination"],
                "body": row["package"]["follow_up_draft"].replace(
                    "{confirmed_application_date}", row["submitted_at"][:10]
                ),
                "thread_instruction": "Use the existing approved initial outreach thread; verify it manually.",
            }
        else:
            action = next(a for a in row["package"]["actions"] if a["channel"] == channel)
        path = self.agent.root / "handoffs" / (token + ".json")
        # Persist intent before returning any material for external action.
        with self.agent.store.transaction():
            self.agent.store.db.execute(
                "INSERT INTO attempts VALUES(?,?,?,?,?,?,NULL,NULL)",
                (token, row["id"], channel, approval["id"], timestamp(), "EXECUTING"),
            )
            self.agent.store.db.execute(
                "UPDATE approvals SET consumed_at=? WHERE id=?", (timestamp(), approval["id"])
            )
            self.agent.store.db.execute(
                "INSERT INTO attempt_expiry VALUES(?,?)", (token, timestamp(expires_at))
            )
            field = {
                "portal": "portal_status",
                "outreach": "outreach_status",
                "follow_up": "follow_up_status",
            }[channel]
            self.agent.store.db.execute(
                f"UPDATE opportunities SET {field}='EXECUTING',state='EXECUTING' WHERE id=?",
                (row["id"],),
            )
            self.agent.store.event(
                row["id"], "HANDOFF_PREPARED", "EXECUTING", {"token": token, "channel": channel}
            )
        payload = {
            "token": token,
            "opportunity_id": job_id,
            "created_at": timestamp(),
            "expires_at": timestamp(expires_at),
            "action": action,
            "instructions": "Perform only this approved action. This file is not proof of submission. Record provider confirmation/reference and actual action time using record-result; if uncertain, do not retry.",
            "external_action_performed_by_agent": False,
        }
        write_json(path, payload)
        return {"path": str(path), **payload}

    def record_result(
        self, token: str, outcome: str, evidence: str | None = None, occurred_at: str | None = None
    ) -> Record:
        if outcome not in {"confirmed", "failed", "uncertain", "not_submitted"}:
            raise WorkflowError("Unsupported outcome; uncertainty must be explicitly reconciled.")
        if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
            raise WorkflowError("Evidence must be nonempty text.")
        if occurred_at is not None and not isinstance(occurred_at, str):
            raise WorkflowError("Actual action time must be a timestamp string.")
        attempt = self.agent.store.db.execute(
            "SELECT * FROM attempts WHERE token=?", (token,)
        ).fetchone()
        if not attempt:
            raise WorkflowError("Unknown execution token.")
        if attempt["status"] not in {"EXECUTING", "SUBMISSION_UNCERTAIN"}:
            raise WorkflowError(
                "This attempt is already finalized; no duplicate result will be recorded."
            )
        allowed = {"confirmed", "failed", "uncertain", "not_submitted"}
        if outcome not in allowed:
            raise WorkflowError("Outcome must be confirmed, failed, uncertain or not_submitted.")
        if outcome in {"confirmed", "not_submitted"} and not evidence:
            raise WorkflowError(
                "Provider confirmation/reference or checked portal/sent-mail evidence is required."
            )
        if attempt["status"] == "SUBMISSION_UNCERTAIN" and outcome == "failed":
            raise WorkflowError(
                "Resolve uncertainty with confirmed or not_submitted evidence before retrying."
            )
        at = parse_time(occurred_at) if occurred_at else now()
        if at < parse_time(attempt["started_at"]) or at > now() + timedelta(minutes=5):
            raise WorkflowError(
                "Actual action time must be after handoff and cannot be in the future."
            )
        channel = attempt["channel"]
        final = (
            (
                "SUBMITTED_CONFIRMED"
                if channel == "portal"
                else "OUTREACH_SENT_CONFIRMED"
                if channel == "outreach"
                else "SENT"
            )
            if outcome == "confirmed"
            else "SUBMISSION_UNCERTAIN"
            if outcome == "uncertain"
            else "ACTION_FAILED"
        )
        field = {
            "portal": "portal_status",
            "outreach": "outreach_status",
            "follow_up": "follow_up_status",
        }[channel]
        with self.agent.store.transaction():
            self.agent.store.db.execute(
                "UPDATE attempts SET status=?,evidence=?,completed_at=? WHERE token=?",
                (final, evidence, timestamp(at), token),
            )
            self.agent.store.db.execute(
                f"UPDATE opportunities SET {field}=?,last_error=? WHERE id=?",
                (
                    final,
                    None if outcome == "confirmed" else evidence or outcome,
                    attempt["opportunity_id"],
                ),
            )
            row = self.agent.store.get(attempt["opportunity_id"])
            if outcome == "confirmed":
                if channel == "follow_up":
                    self.agent.store.db.execute(
                        "UPDATE opportunities SET follow_up_sent_at=? WHERE id=?",
                        (timestamp(at), row["id"]),
                    )
                else:
                    earliest = (
                        min(parse_time(row["submitted_at"]), at) if row["submitted_at"] else at
                    )
                    due = earliest + timedelta(days=self.agent.settings["follow_up_days"])
                    self.agent.store.db.execute(
                        "UPDATE opportunities SET submitted_at=?,follow_up_due_at=?,follow_up_status=CASE WHEN follow_up_sent_at IS NULL THEN 'CHECK_DUE' ELSE follow_up_status END WHERE id=?",
                        (timestamp(earliest), timestamp(due), row["id"]),
                    )
            self.agent.reconcile_state(row["id"])
            self.agent.store.event(
                row["id"],
                "MANUAL_RESULT_RECORDED",
                final,
                {
                    "token": token,
                    "evidence": evidence,
                    "occurred_at": timestamp(at),
                    "provenance": "user-provided provider confirmation; not independently checked by software",
                },
            )
        return self.agent.store.get(attempt["opportunity_id"])

    def reconcile_state(self, number: int) -> None:
        row = self.agent.store.get(number)
        statuses = {row["portal_status"], row["outreach_status"]}
        outstanding = self.agent.store.db.execute(
            "SELECT 1 FROM attempts WHERE opportunity_id=? AND status='EXECUTING'", (number,)
        ).fetchone()
        if row["state"] in TERMINAL:
            return
        if outstanding:
            state = "EXECUTING"
        elif (
            "SUBMISSION_UNCERTAIN" in statuses or row["follow_up_status"] == "SUBMISSION_UNCERTAIN"
        ):
            state = "SUBMISSION_UNCERTAIN"
        elif statuses & CONFIRMED:
            if "ACTION_FAILED" in statuses:
                state = "PARTIAL_SUCCESS"
            elif row["portal_status"] == "SUBMITTED_CONFIRMED":
                state = "SUBMITTED_CONFIRMED"
            else:
                state = "OUTREACH_SENT_CONFIRMED"
        else:
            state = "ACTION_FAILED"
        self.agent.store.db.execute("UPDATE opportunities SET state=? WHERE id=?", (state, number))

    def follow_up_eligible(self, row: Record) -> None:
        if (
            row["state"] in TERMINAL
            or row["payload"].get("no_follow_ups")
            or row["follow_up_sent_at"]
        ):
            raise WorkflowError(
                "Follow-up is ineligible: stopping outcome, employer restriction or already sent."
            )
        if not row["follow_up_due_at"] or now() < parse_time(row["follow_up_due_at"]):
            raise WorkflowError("Seven-day follow-up is not yet due.")
        if row["outreach_status"] != "OUTREACH_SENT_CONFIRMED":
            raise WorkflowError("Portal-only submission does not authorize new cold outreach.")
        check = row["status_check"]
        if not check or not timedelta(0) <= now() - parse_time(check["checked_at"]) <= timedelta(
            hours=24
        ):
            raise WorkflowError(
                "Reply status unknown. Check the authorized application/email status or explicitly reconfirm sending with this uncertainty."
            )
        if check["status"] not in {"no_reply", "unknown_reconfirmed"}:
            raise WorkflowError("Reply or application status prevents automatic follow-up.")
