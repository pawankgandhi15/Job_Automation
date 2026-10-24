"""Actionable evidence queue; triage priority never substitutes for eligibility."""

from __future__ import annotations

import re

from .availability import availability_text
from .common import now, parse_time
from .types import Record

CANDIDATE = {"work_authorization", "grades", "student_status", "duration"}
EMPLOYER = {
    "undergraduate_branch",
    "graduation_batch",
    "start_date",
    "academic_schedule",
    "degree_level",
    "experience",
}


def lead_priority(
    job: Record, preferred_employer: str = "IBM"
) -> tuple[bool, bool, bool, bool, str]:
    text = job.get("role", "")
    location = job.get("location", "")
    early = bool(re.search(r"intern|graduate|\bjunior\b|\bentry\b", text, re.I))
    india = bool(
        re.search(
            r"india|bengaluru|bangalore|hyderabad|pune|noida|gurugram|gurgaon|mumbai|chennai|delhi|remote",
            location,
            re.I,
        )
    )
    senior = bool(re.search(r"\bsenior\b|\bstaff\b|\bprincipal\b|\blead\b|\bmanager\b", text, re.I))
    return (
        job.get("company", "").casefold() != preferred_employer.casefold(),
        not india,
        not early,
        senior,
        text.casefold(),
    )


def review_queue(rows: list[Record], profile: Record, settings: Record, limit: int = 20) -> Record:
    items = []
    for row in rows:
        evaluation, job = row["evaluation"], row["payload"]
        if row["state"] in {
            "REJECTED",
            "WITHDRAWN",
            "CLOSED",
            "DISMISSED",
            "REPLIED",
            "INTERVIEW",
            "EXECUTING",
            "SUBMISSION_UNCERTAIN",
            "SUBMITTED_CONFIRMED",
            "OUTREACH_SENT_CONFIRMED",
            "PARTIAL_SUCCESS",
        } or evaluation["status"] in {"REJECTED", "BELOW_THRESHOLD"}:
            continue
        tasks = []
        for name, gate in evaluation["gates"].items():
            if gate["status"] == "UNKNOWN":
                tasks.append(
                    {
                        "gate": name,
                        "owner": "candidate"
                        if name in CANDIDATE
                        else "employer_or_official_policy"
                        if name in EMPLOYER
                        else "source_reviewer",
                        "action": gate["reason"],
                        "source": gate.get("source") or job["canonical_url"],
                    }
                )
        tasks += [
            {
                "gate": "package",
                "owner": "candidate_or_reviewer",
                "action": reason,
                "source": job["canonical_url"],
            }
            for reason in row["package"]["blockers"]
        ]
        # Numeric skill credit requires assessed technical groups; mentions only aid review.
        mentions = sorted(
            skill
            for skill in profile["skill_evidence"]
            if re.search(
                r"(?<!\w)" + re.escape(skill) + r"(?!\w)", job.get("description", ""), re.I
            )
        )
        early = bool(re.search(r"intern|graduate|junior|entry", job["role"], re.I))
        urgent = bool(
            job.get("deadline")
            and 0 <= (parse_time(job["deadline"]) - now()).total_seconds() <= 3 * 86400
        )
        priority = (
            "HIGH"
            if urgent or evaluation["status"] == "READY_FOR_REVIEW" or (early and mentions)
            else "MEDIUM"
            if early or mentions
            else "LOW"
        )
        reasons = (
            (["Deadline within three days"] if urgent else [])
            + (["Early-career role title"] if early else [])
            + (
                [
                    "Resume-supported skills mentioned in the JD; requirement assessment remains necessary"
                ]
                if mentions
                else []
            )
        )
        if (
            evaluation["gates"]["employer_identity"]["status"] != "PASS"
            or evaluation["gates"]["live_vacancy"]["status"] != "PASS"
        ):
            priority = "LOW"
            reasons.append(
                "Employer identity or current vacancy must be verified before fit review"
            )
        items.append(
            {
                "opportunity_id": row["opportunity_id"],
                "company": job["company"],
                "role": job["role"],
                "status": evaluation["status"],
                "priority": priority,
                "priority_reasons": reasons,
                "supported_skill_mentions": mentions,
                "match_score": evaluation["match_score"],
                "availability": availability_text(profile, job["employment_type"]),
                "source": job["canonical_url"],
                "tasks": tasks,
                "other_unresolved": evaluation["unknowns"],
                "requires_execution_approval": True,
            }
        )
    ranks = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    items.sort(
        key=lambda item: (
            ranks[item["priority"]],
            item["company"].casefold() != settings["preferred_employer"].casefold(),
            len(item["tasks"]),
            item["opportunity_id"],
        )
    )
    return {
        "total": len(items),
        "shown": min(limit, len(items)),
        "items": items[:limit],
        "note": "Review priority is a triage aid, not an eligibility decision, match score or application authorization.",
    }
