from __future__ import annotations

import json

from ..common import (
    WorkflowError,
    digest,
    timestamp,
)
from ..packages import build_package
from ..policy import evaluate, validate_job
from ..review import review_queue
from ..types import Record
from .context import AgentContext

TERMINAL = {"REJECTED", "WITHDRAWN", "INTERVIEW", "REPLIED", "CLOSED", "DISMISSED"}
CONFIRMED = {"SUBMITTED_CONFIRMED", "OUTREACH_SENT_CONFIRMED", "SENT"}


class EvaluationService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def import_jobs(self, document: Record | list[Record]) -> list[tuple[str, bool]]:
        self.agent.active()
        jobs = document["opportunities"] if isinstance(document, dict) else document
        if not isinstance(jobs, list):
            raise WorkflowError("Import must contain an opportunities array.")
        if isinstance(document, dict) and document.get("sample"):
            raise WorkflowError(
                "Sample data cannot be imported into a live ledger. Use demo in an isolated directory."
            )
        for job in jobs:
            validate_job(job)
        return [self.agent.store.upsert(job) for job in jobs]

    def prepare(self) -> list[Record]:
        rows = self.agent.store.all()
        confirmed: dict[tuple[str, str, str], list[Record]] = {}
        for item in rows:
            if (
                item["portal_status"] == "SUBMITTED_CONFIRMED"
                or item["outreach_status"] == "OUTREACH_SENT_CONFIRMED"
            ):
                key = (
                    item["company_key"],
                    item["payload"]["role"].casefold(),
                    item["payload"]["location"].casefold(),
                )
                confirmed.setdefault(key, []).append(item)
        for row in rows:
            accepted = self.agent.store.setting(f"salary_exception:{row['id']}")
            evaluation = evaluate(
                row["payload"],
                self.agent.profile,
                self.agent.settings,
                salary_exception=bool(
                    accepted
                    and accepted.get("compensation_hash")
                    == digest(row["payload"].get("compensation"))
                ),
            )
            key = (
                row["company_key"],
                row["payload"]["role"].casefold(),
                row["payload"]["location"].casefold(),
            )
            potential_reposts = [
                item["opportunity_id"] for item in confirmed.get(key, []) if item["id"] != row["id"]
            ]
            repost_review = self.agent.store.setting(f"repost_review:{row['id']}")
            if (
                potential_reposts
                and repost_review != row["material_hash"]
                and evaluation["status"] == "READY_FOR_REVIEW"
            ):
                evaluation["status"] = "NEEDS_CLARIFICATION"
                evaluation["unknowns"].append(
                    "Potential repost of an already actioned role: "
                    + ", ".join(potential_reposts)
                    + ". Review before submitting a distinct job ID."
                )
            if (
                row["payload"].get("possible_cross_posts")
                and evaluation["status"] == "READY_FOR_REVIEW"
            ):
                evaluation["status"] = "NEEDS_CLARIFICATION"
                evaluation["unknowns"].append(
                    "Possible cross-posts need identity review: "
                    + ", ".join(row["payload"]["possible_cross_posts"])
                )
            resume = self.agent.resume_for(row["payload"])
            package = build_package(row["payload"], evaluation, self.agent.profile, resume)
            hashed = digest(package)
            state = row["state"]
            if (
                state == "REJECTED"
                and row["evaluation"]
                and row["evaluation"]["status"] == "REJECTED"
                and evaluation["status"] != "REJECTED"
                and (row["status_check"] or {}).get("status")
                not in {"rejected", "withdrawn", "closed"}
            ):
                if not self.agent.store.db.execute(
                    "SELECT 1 FROM attempts WHERE opportunity_id=?", (row["id"],)
                ).fetchone():
                    state = "DISCOVERED"
                    self.agent.store.event(
                        row["id"], "ELIGIBILITY_REEVALUATED", evaluation["status"]
                    )
            if state in {
                "DISCOVERED",
                "VERIFYING",
                "READY_FOR_REVIEW",
                "AWAITING_APPROVAL",
                "APPROVED",
                "NEEDS_CLARIFICATION",
                "BELOW_THRESHOLD",
                "EXPIRED",
                "ACTION_FAILED",
            }:
                if evaluation["status"] != "READY_FOR_REVIEW":
                    state = evaluation["status"]
                elif state not in {"APPROVED", "ACTION_FAILED"}:
                    state = "AWAITING_APPROVAL"
            self.agent.store.db.execute(
                "UPDATE opportunities SET evaluation=?,package=?,package_hash=?,state=? WHERE id=?",
                (
                    json.dumps(evaluation),
                    json.dumps(package, ensure_ascii=False),
                    hashed,
                    state,
                    row["id"],
                ),
            )
            # Compare each channel independently; a different outreach recipient does not alter a portal answer.
            current = self.agent.store.get(row["id"])
            for approval in self.agent.store.db.execute(
                "SELECT * FROM approvals WHERE opportunity_id=? AND consumed_at IS NULL AND invalidated_at IS NULL",
                (row["id"],),
            ).fetchall():
                if approval["scope_hash"] != self.agent.scope_hash(current, approval["channel"]):
                    self.agent.store.db.execute(
                        "UPDATE approvals SET invalidated_at=? WHERE id=?",
                        (timestamp(), approval["id"]),
                    )
                    self.agent.store.event(
                        row["id"],
                        "APPROVAL_INVALIDATED",
                        "ACTION_OR_PROFILE_CHANGED",
                        {"channel": approval["channel"]},
                    )
        return self.agent.store.all()

    def review_queue(self, limit: int = 20) -> Record:
        return review_queue(self.agent.prepare(), self.agent.profile, self.agent.settings, limit)
