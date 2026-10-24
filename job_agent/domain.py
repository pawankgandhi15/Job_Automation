"""Explicit lifecycle guards retaining uncertain and partially successful states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .common import WorkflowError


class OpportunityState(StrEnum):
    DISCOVERED = "DISCOVERED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    SUBMITTED_CONFIRMED = "SUBMITTED_CONFIRMED"
    OUTREACH_SENT_CONFIRMED = "OUTREACH_SENT_CONFIRMED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    ACTION_FAILED = "ACTION_FAILED"
    BELOW_THRESHOLD = "BELOW_THRESHOLD"
    REJECTED = "REJECTED"
    DISMISSED = "DISMISSED"
    REPLIED = "REPLIED"
    INTERVIEW = "INTERVIEW"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"
    VERIFYING = "VERIFYING"
    EXPIRED = "EXPIRED"


class StateTransitionError(WorkflowError):
    pass


def validate_transition(before: str, after: str) -> None:
    OpportunityState(before)
    OpportunityState(after)
    if after == "APPROVED" and before not in {
        "APPROVED",
        "AWAITING_APPROVAL",
        "READY_FOR_REVIEW",
        "ACTION_FAILED",
        "EXPIRED",
    }:
        raise StateTransitionError(f"Invalid approval transition: {before} -> {after}")
    if after == "EXECUTING" and before not in {
        "APPROVED",
        "EXECUTING",
        "SUBMITTED_CONFIRMED",
        "OUTREACH_SENT_CONFIRMED",
        "PARTIAL_SUCCESS",
        "ACTION_FAILED",
    }:
        raise StateTransitionError(f"Invalid execution transition: {before} -> {after}")
    if before == "SUBMISSION_UNCERTAIN" and after in {
        "APPROVED",
        "EXECUTING",
        "AWAITING_APPROVAL",
        "READY_FOR_REVIEW",
    }:
        raise StateTransitionError(
            "An uncertain submission must be reconciled before another attempt."
        )


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    opportunity_id: str
    channel: str
    scope_hash: str


@dataclass(frozen=True, slots=True)
class AttemptToken:
    token: str
    opportunity_id: str
    expires_at: str
