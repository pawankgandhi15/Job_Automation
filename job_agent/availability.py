"""Candidate availability with explicit month/day precision and source provenance."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime

from .common import WorkflowError, digest
from .types import Record

RESUME_FIELDS = (
    "name",
    "location",
    "email",
    "phone",
    "linkedin",
    "github",
    "project_url",
    "education",
    "skill_evidence",
    "experience",
    "projects",
    "credentials",
    "achievements",
)


def resume_profile_digest(profile: Record) -> str:
    return digest({key: profile.get(key) for key in RESUME_FIELDS})


def month_label(value: str | None) -> str:
    if not value or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise WorkflowError("Availability month must use YYYY-MM.")
    year, month = map(int, value.split("-"))
    date(year, month, 1)
    return f"{calendar.month_name[month]} {year}"


def candidate_availability(profile: Record, kind: str) -> Record:
    key = "internship" if kind == "internship" else "full_time"
    record = dict(profile.get("availability", {}).get(key, {}))
    record.setdefault(
        "hours_per_day", profile.get("internship_hours_per_day") if key == "internship" else None
    )
    record.setdefault(
        "days_per_week", profile.get("internship_days_per_week") if key == "internship" else None
    )
    record.setdefault("location_mode", "role_dependent")
    return record


def availability_text(profile: Record, kind: str) -> str:
    record = candidate_availability(profile, kind)
    month, exact = record.get("earliest_start_month"), record.get("earliest_start_date")
    if exact:
        text = "Available from " + date.fromisoformat(exact).strftime("%d %B %Y")
    elif month:
        text = "Available from " + month_label(month) + "; exact joining day unconfirmed"
    else:
        text = profile.get(
            "internship_availability" if kind == "internship" else "full_time_availability",
            "Availability unconfirmed",
        )
    if record.get("hours_per_day"):
        text += f"; up to {record['hours_per_day']:g} hours per day"
    if record.get("location_mode") == "role_dependent":
        text += "; location subject to role-specific confirmation"
    return text


def assess_availability(job: Record, profile: Record, clock: datetime) -> tuple[Record, Record]:
    """Return derived gates without turning a month into a promised start day."""
    available = candidate_availability(profile, job["employment_type"])
    if not available.get("earliest_start_month") and not available.get("earliest_start_date"):
        return {}, {}
    constraints = job.get("constraints", {})
    source = job.get("requirements_source", job["canonical_url"])
    month = available.get("earliest_start_month")
    exact = available.get("earliest_start_date")
    if exact:
        date.fromisoformat(exact)
        month = exact[:7]
    month_label(month)
    assert isinstance(month, str)
    earliest = date.fromisoformat(month + "-01")
    changes: Record = {}
    facts: Record = {"candidate": available, "start_precision": "day" if exact else "month"}

    def gate(name: str, status: str, reason: str) -> None:
        if changes.get(name, {}).get("status") == "FAIL" and status != "FAIL":
            return
        changes[name] = {
            "status": status,
            "reason": reason,
            "source": source,
            "checked_at": job.get("last_verified_at"),
            "derived_from": "candidate_availability",
            "profile_version": profile["version"],
        }

    for name in ("academic_schedule", "duration"):
        previous = job.get("eligibility_gates", {}).get(name, {})
        if previous.get("profile_version") and previous["profile_version"] != profile["version"]:
            gate(
                name,
                "UNKNOWN",
                "Recheck this candidate-dependent condition against the updated availability; do not reuse an assessment from the previous profile.",
            )

    start = constraints.get("start_date") or job.get("start_date")
    start_month = constraints.get("start_month") or job.get("start_month")
    immediate = constraints.get("immediate_joining") or (
        constraints.get("immediate_full_time") and job["employment_type"] != "internship"
    )
    if immediate:
        if clock.date() < earliest:
            gate(
                "start_date",
                "FAIL",
                f"Immediate joining conflicts with confirmed availability from {month_label(month)}.",
            )
        elif not exact and clock.date().strftime("%Y-%m") == month:
            gate(
                "start_date",
                "UNKNOWN",
                "Joining day within the confirmed month is unconfirmed; do not enter zero-day notice.",
            )
        else:
            gate(
                "start_date",
                "UNKNOWN",
                "Immediate availability requires a current confirmation; the recorded start month alone does not establish it.",
            )
    elif start:
        target = date.fromisoformat(start[:10])
        facts["role_start_date"] = target.isoformat()
        if target < (date.fromisoformat(exact) if exact else earliest):
            gate(
                "start_date",
                "FAIL",
                "Stated job start precedes the candidate's confirmed availability.",
            )
        elif not exact and target.strftime("%Y-%m") == month:
            gate(
                "start_date",
                "UNKNOWN",
                f"Role starts on {target.isoformat()}; candidate confirmed {month_label(month)} but not an exact joining day.",
            )
        else:
            gate(
                "start_date",
                "PASS",
                "Stated job start is after the candidate's confirmed availability window; exact form answers remain subject to confirmation.",
            )
    elif start_month:
        month_label(start_month)
        facts["role_start_month"] = start_month
        gate(
            "start_date",
            "FAIL" if start_month < month else "PASS",
            "The role's disclosed start month "
            + ("precedes" if start_month < month else "is compatible with")
            + " the confirmed candidate start month; no exact day inferred.",
        )
    else:
        # Replace candidate-dependent assessments written under an older profile.
        previous = job.get("eligibility_gates", {}).get("start_date", {})
        if previous.get("profile_version") and previous["profile_version"] != profile["version"]:
            gate(
                "start_date",
                "UNKNOWN",
                "Recheck the role's start arrangement against the updated candidate availability.",
            )

    daily = constraints.get("required_hours_per_day")
    weekly = constraints.get("required_hours_per_week")
    cap, days = available.get("hours_per_day"), available.get("days_per_week")
    if daily is not None and cap is not None:
        facts["role_required_hours_per_day"] = daily
        if daily > cap:
            gate(
                "academic_schedule",
                "FAIL",
                f"Role requires {daily:g} hours per day; candidate can commit at most {cap:g}.",
            )
        elif weekly is not None and days is None:
            gate(
                "academic_schedule",
                "UNKNOWN",
                "Daily hours fit, but required weekly hours cannot be checked without confirmed working days.",
            )
        elif available.get("location_mode") == "role_dependent" and job.get(
            "workplace_type"
        ) not in {"remote", "REMOTE", "Remote"}:
            gate(
                "academic_schedule",
                "UNKNOWN",
                "Daily hours fit; on-site/hybrid location and academic compatibility still need confirmation.",
            )
        else:
            gate(
                "academic_schedule",
                "PASS",
                "Explicit daily requirement fits the candidate's confirmed cap and remote arrangement.",
            )
    if weekly is not None and cap is not None:
        if days is None:
            gate(
                "academic_schedule",
                "UNKNOWN",
                "Required weekly hours are disclosed; candidate has confirmed daily hours but not working days per week.",
            )
        elif weekly > cap * days:
            gate(
                "academic_schedule",
                "FAIL",
                "Required weekly hours exceed the candidate's explicitly confirmed weekly capacity.",
            )
    if (
        job.get("employment_type") in {"full_time", "graduate_programme"}
        and cap is not None
        and daily is None
        and weekly is None
    ):
        gate(
            "academic_schedule",
            "UNKNOWN",
            f"Candidate can join from {month_label(month)} for up to {cap:g} hours per day; employer acceptance of this schedule must be confirmed. Full-time does not imply a known daily requirement.",
        )
    facts["exact_joining_day_known"] = bool(exact)
    return changes, facts
