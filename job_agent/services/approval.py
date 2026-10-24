from __future__ import annotations

import json
from datetime import timedelta

from ..common import (
    WorkflowError,
    digest,
    material_job,
    now,
    parse_time,
    timestamp,
)
from ..types import Record
from .context import AgentContext

TERMINAL = {"REJECTED", "WITHDRAWN", "INTERVIEW", "REPLIED", "CLOSED", "DISMISSED"}
CONFIRMED = {"SUBMITTED_CONFIRMED", "OUTREACH_SENT_CONFIRMED", "SENT"}


class ApprovalService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def scope_hash(self, row: Record, channel: str) -> str:
        package = row["package"] or {}
        if channel == "follow_up":
            contact_action = next(
                (a for a in package.get("actions", []) if a["channel"] == "outreach"), None
            )
            return digest(
                {
                    "channel": channel,
                    "draft": package.get("follow_up_draft"),
                    "due": row["follow_up_due_at"],
                    "submitted_at": row["submitted_at"],
                    "destination": contact_action and contact_action["destination"],
                    "initial_outreach": row["outreach_status"],
                    "profile": digest(self.agent.profile),
                }
            )
        action = next((a for a in package.get("actions", []) if a["channel"] == channel), None)
        relevant_job = {
            k: v
            for k, v in material_job(row["payload"]).items()
            if k not in {"contact", "reviews", "headquarters", "size"}
        }
        return digest(
            {
                "channel": channel,
                "job": relevant_job,
                "action": action,
                "profile": digest(self.agent.profile),
            }
        )

    def approve(self, job_id: str, channels: list[str]) -> Record:
        self.agent.active()
        self.agent.prepare()
        row = self.agent.store.get(job_id)
        if channels == ["follow_up"]:
            valid_from = parse_time(row["follow_up_due_at"]) if row["follow_up_due_at"] else None
            if (
                not valid_from
                or row["follow_up_sent_at"]
                or row["state"] in TERMINAL
                or row["outreach_status"] != "OUTREACH_SENT_CONFIRMED"
            ):
                raise WorkflowError(
                    "Follow-up requires a confirmed initial outreach, a due date and no stopping outcome; portal-only applications do not authorize cold outreach."
                )
        else:
            if row["evaluation"]["status"] != "READY_FOR_REVIEW" or row["state"] in TERMINAL:
                raise WorkflowError("Only verified, eligible shortlist items can be approved.")
            if row["package"]["blockers"]:
                raise WorkflowError(
                    "Resolve package blockers: " + "; ".join(row["package"]["blockers"])
                )
            report = self.agent.store.db.execute(
                "SELECT * FROM reports WHERE delivered_at IS NOT NULL ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            snapshot = (
                json.loads(report["opportunity_ids"]).get(row["opportunity_id"]) if report else None
            )
            if (
                not snapshot
                or not snapshot["shortlisted"]
                or snapshot["package_hash"] != row["package_hash"]
            ):
                raise WorkflowError(
                    "Generate and review/deliver a current report showing this exact action package before approval."
                )
            available = {a["channel"] for a in row["package"]["actions"]}
            if not channels or not set(channels) <= available:
                raise WorkflowError(
                    "Approval channels must match the displayed portal/outreach action plan."
                )
            for channel in channels:
                status = row["portal_status"] if channel == "portal" else row["outreach_status"]
                if status in CONFIRMED or status in {"EXECUTING", "SUBMISSION_UNCERTAIN"}:
                    raise WorkflowError(
                        f"Cannot approve duplicate or unresolved {channel} execution."
                    )
            valid_from = now()
        if channels != ["follow_up"]:
            from ..domain import validate_transition

            validate_transition(row["state"], "APPROVED")
        at = now()
        expires = max(valid_from, at) + timedelta(hours=self.agent.settings["approval_hours"])
        # A preapproved follow-up is valid at its due check plus 72 hours, not 72 hours from today.
        if channels == ["follow_up"]:
            expires = valid_from + timedelta(hours=self.agent.settings["approval_hours"])
            if at > expires:
                raise WorkflowError("The follow-up authorization window has expired.")
        with self.agent.store.transaction():
            for channel in channels:
                self.agent.store.db.execute(
                    "UPDATE approvals SET invalidated_at=? WHERE opportunity_id=? AND channel=? AND consumed_at IS NULL",
                    (timestamp(at), row["id"], channel),
                )
                self.agent.store.db.execute(
                    "INSERT INTO approvals(opportunity_id,channel,scope_hash,approved_at,valid_from,expires_at) VALUES(?,?,?,?,?,?)",
                    (
                        row["id"],
                        channel,
                        self.agent.scope_hash(row, channel),
                        timestamp(at),
                        timestamp(valid_from),
                        timestamp(expires),
                    ),
                )
            if channels == ["follow_up"]:
                self.agent.store.db.execute(
                    "UPDATE opportunities SET follow_up_status='AWAITING_APPROVAL' WHERE id=?",
                    (row["id"],),
                )
            else:
                self.agent.store.db.execute(
                    "UPDATE opportunities SET state='APPROVED' WHERE id=?", (row["id"],)
                )
            self.agent.store.event(
                row["id"],
                "APPROVED",
                "RECORDED",
                {"channels": channels, "expires_at": timestamp(expires)},
            )
        return {
            "opportunity_id": job_id,
            "channels": channels,
            "expires_at": timestamp(expires),
            "external_action_performed": False,
        }

    def accept_salary_exception(self, job_id: str) -> None:
        row = self.agent.store.get(job_id)
        if row["payload"].get("tier") != "GREAT":
            raise WorkflowError(
                "The prompt permits below-floor exceptions for GREAT-tier employers only."
            )
        self.agent.store.set_setting(
            f"salary_exception:{row['id']}",
            {
                "compensation_hash": digest(row["payload"].get("compensation")),
                "accepted_at": timestamp(),
            },
        )
        self.agent.store.event(
            row["id"],
            "BELOW_FLOOR_EXCEPTION",
            "EXPLICITLY_ACCEPTED",
            row["payload"].get("compensation"),
        )
