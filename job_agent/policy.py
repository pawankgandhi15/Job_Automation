from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

from .availability import assess_availability
from .candidate import branch_tokens, education_record, experience_months, experience_summary
from .common import WorkflowError, canonical_url, domain, now, parse_time, same_domain
from .evidence import experience_requirements, remote_restricted_outside_india, unrelated_role
from .types import Record

# A reviewer must resolve every mandatory dimension, including explicit N/A reasons.
GATES = (
    "employer_identity",
    "live_vacancy",
    "application_route",
    "location",
    "work_authorization",
    "employment_type",
    "degree_level",
    "undergraduate_branch",
    "grades",
    "experience",
    "graduation_batch",
    "student_status",
    "start_date",
    "academic_schedule",
    "duration",
    "essential_skills",
)
ALIASES = {
    "containerization": "docker",
    "containers": "docker",
    "amazon web services": "aws",
    "ec2": "aws",
    "s3": "aws",
    "iam": "aws",
    "continuous integration": "ci/cd",
    "continuous delivery": "ci/cd",
    "continuous deployment": "ci/cd",
    "data structures and algorithms": "dsa",
    "object oriented programming": "oop",
    "object-oriented programming": "oop",
    "rest": "rest apis",
    "rest api": "rest apis",
    "os": "operating systems",
    "postgresql": "postgresql",
    "k8s": "kubernetes",
}


def skill_group(value: str) -> str:
    cleaned = value.strip().casefold()
    return ALIASES.get(cleaned, cleaned)


def validate_job(job: Record) -> Record:
    if not isinstance(job, dict):
        raise WorkflowError("Each opportunity must be a JSON object.")
    for name in (
        "company",
        "official_website",
        "role",
        "canonical_url",
        "location",
        "employment_type",
    ):
        if not isinstance(job.get(name), str) or not job[name].strip():
            raise WorkflowError(f"Opportunity requires nonempty field: {name}")
    domain(job["official_website"])
    canonical_url(job["canonical_url"])
    if job.get("tier", "GOOD") not in {"GOOD", "BETTER", "GREAT"}:
        raise WorkflowError("Tier must be GOOD, BETTER or GREAT.")
    if job["employment_type"] not in {"internship", "full_time", "graduate_programme", "unknown"}:
        raise WorkflowError(
            "Employment type must be internship, full_time, graduate_programme or unknown."
        )
    for field in ("posting_date",):
        if job.get(field):
            date.fromisoformat(job[field][:10])
    if job.get("deadline"):
        parse_time(job["deadline"])
    for source in job.get("primary_source_urls", []):
        domain(source)
    for name, gate in job.get("eligibility_gates", {}).items():
        if name not in GATES or gate.get("status") not in {"PASS", "FAIL", "UNKNOWN"}:
            raise WorkflowError(f"Invalid eligibility gate: {name}")
        if not gate.get("reason"):
            raise WorkflowError(f"Gate {name} needs a specific reason.")
        if gate.get("checked_at"):
            parse_time(gate["checked_at"])
    for requirement in job.get("technical_groups", []):
        if not requirement.get("group") or requirement.get("priority") not in {
            "required",
            "preferred",
        }:
            raise WorkflowError(
                "Each technical group needs a group and required/preferred priority."
            )
    minimum = (job.get("compensation") or {}).get("minimum")
    if minimum is not None and (
        isinstance(minimum, bool)
        or not isinstance(minimum, (int, float))
        or not math.isfinite(minimum)
        or minimum < 0
    ):
        raise WorkflowError("Disclosed minimum compensation must be a finite, nonnegative number.")
    return job


def is_primary(job: Record, source: str | None) -> bool:
    if not source:
        return False
    try:
        normalized = canonical_url(source)
        known = {canonical_url(x) for x in job.get("primary_source_urls", [])}
        return normalized in known or same_domain(source, domain(job["official_website"]))
    except WorkflowError:
        return False


def evaluate(
    job: Record,
    profile: Record,
    settings: Record,
    *,
    salary_exception: bool = False,
    clock: datetime | None = None,
) -> Record:
    clock = clock or now()
    validate_job(job)

    def validate_gate(name: str) -> Record:
        original = job.get("eligibility_gates", {}).get(name, {})
        gate: Record = (
            dict(original)
            if original
            else {"status": "UNKNOWN", "reason": "Not yet verified against the official JD."}
        )
        if gate["status"] in {"PASS", "FAIL"}:
            if not is_primary(job, gate.get("source")) or not gate.get("checked_at"):
                gate = {
                    "status": "UNKNOWN",
                    "reason": "A primary source URL and verification timestamp are required.",
                }
            elif parse_time(gate["checked_at"]) > clock + timedelta(minutes=5):
                gate = {"status": "UNKNOWN", "reason": "Verification timestamp is in the future."}
        return gate

    from .rules import ordered_gates

    dependencies = settings.get(
        "gate_dependencies",
        {
            "application_route": ["employer_identity", "live_vacancy"],
            "start_date": ["student_status", "graduation_batch"],
        },
    )
    gates = ordered_gates(GATES, validate_gate, dependencies)

    def override(name: str, status: str, reason: str) -> None:
        gates[name] = {"status": status, "reason": reason, "source": job["canonical_url"]}

    extracted = (
        experience_requirements(job.get("description", ""), job["canonical_url"])
        if is_primary(job, job["canonical_url"])
        else []
    )
    total_months = experience_months(profile)
    exclusion_minimum = settings.get("automatic_experience_exclusion_min_years", 2)
    strict_years = [
        item
        for item in extracted
        if item["strength"] == "mandatory"
        and item["minimum_years"] >= exclusion_minimum
        and item["minimum_years"] * 12 > total_months
    ]
    if strict_years:
        evidence = max(strict_years, key=lambda item: item["minimum_years"])
        override(
            "experience",
            "FAIL",
            f"Official JD requires {evidence['minimum_years']:g}+ years, exceeding the recorded experience duration. {experience_summary(profile)} Excerpt: {evidence['quote']}",
        )
        gates["experience"]["evidence"] = evidence
        gates["experience"]["checked_at"] = job.get("last_verified_at")
    if unrelated_role(job["role"]):
        override(
            "role_scope",
            "FAIL",
            "Role title is outside the requested software, cloud, DevOps, backend, systems and SDE role families.",
        )
    if is_primary(job, job["canonical_url"]) and remote_restricted_outside_india(job["location"]):
        override(
            "location",
            "FAIL",
            "Employer-listed remote locations are restricted outside India; remote does not establish permission to work from India.",
        )

    constraints = job.get("constraints", {})
    minimum_professional = constraints.get("minimum_professional_years", 0)
    if minimum_professional >= exclusion_minimum and minimum_professional * 12 > experience_months(
        profile, professional_only=True
    ):
        override(
            "experience",
            "FAIL",
            f"Strict minimum of {minimum_professional:g} professional years exceeds the recorded full-time professional experience. Internships are counted separately. {experience_summary(profile)}",
        )
    branches = constraints.get("accepted_undergraduate_branches")
    if branches:
        undergraduate = education_record(profile, "undergraduate")
        accepted = {re.sub(r"[^a-z0-9]", "", item.casefold()) for item in branches}
        if not undergraduate:
            override(
                "undergraduate_branch",
                "UNKNOWN",
                "Undergraduate education is not recorded; the stated branch requirement needs confirmation.",
            )
        elif not accepted.intersection(branch_tokens(undergraduate)) and not constraints.get(
            "explicit_postgraduate_alternative"
        ):
            override(
                "undergraduate_branch",
                "FAIL",
                f"Recorded undergraduate branch is {undergraduate['branch']}; postgraduate study does not override the stated undergraduate branch restriction.",
            )
    if (
        constraints.get("immediate_full_time")
        and job["employment_type"] != "internship"
        and not profile.get("availability")
    ):
        override(
            "start_date",
            "UNKNOWN",
            "Immediate joining requires confirmed candidate availability; no structured start window is recorded.",
        )
    availability_changes, availability_facts = assess_availability(job, profile, clock)
    if not is_primary(job, job.get("requirements_source", job["canonical_url"])):
        availability_changes = {}
    for name, change in availability_changes.items():
        previous = gates[name]
        if (
            previous["status"] == "FAIL"
            and previous.get("profile_version", profile["version"]) == profile["version"]
            and change["status"] != "FAIL"
        ):
            continue  # Compatible daily hours alone cannot erase an independent disqualifier.
        gates[name] = change
    if job.get("listing_type") in {"talent_pool", "expression_of_interest"}:
        override(
            "live_vacancy",
            "UNKNOWN",
            "Talent pool/expression of interest is not a confirmed live vacancy.",
        )
    if clock.date() > date.fromisoformat(profile["reconfirm_after"]):
        override(
            "student_status",
            "UNKNOWN",
            "Reconfirm graduation and availability; supplied student profile is stale.",
        )
        override(
            "start_date", "UNKNOWN", "Reconfirm availability after the recorded graduation period."
        )
    if job.get("deadline") and parse_time(job["deadline"]) <= clock:
        override("live_vacancy", "FAIL", "The stated application deadline has passed.")
    elif job.get("deadline_display") and not job.get("deadline"):
        override(
            "live_vacancy",
            "UNKNOWN",
            "Employer states deadline "
            + str(job["deadline_display"])
            + "; exact cutoff/timezone is unconfirmed. Verify it before execution.",
        )
    if job.get("recruitment_fee_required"):
        override(
            "employer_identity",
            "FAIL",
            "Application fee, deposit or paid mandatory hiring training is required.",
        )

    assessed: dict[str, Record] = {}
    for item in job.get("technical_groups", []):
        key = skill_group(item["group"])
        weight = 2 if item["priority"] == "required" else 1
        prior_group = assessed.get(key)
        candidate_evidence = profile["skill_evidence"].get(key)
        record = {
            "group": key,
            "weight": weight,
            "priority": item["priority"],
            "matched": bool(candidate_evidence),
            "candidate_evidence": candidate_evidence,
            "source": item.get("source"),
            "mandatory": item.get("mandatory", item["priority"] == "required"),
        }
        if not prior_group or weight > prior_group["weight"]:
            assessed[key] = record
        elif record["mandatory"]:
            prior_group["mandatory"] = True
    groups = list(assessed.values())
    missing = [r["group"] for r in groups if r["mandatory"] and not r["matched"]]
    if missing:
        override("essential_skills", "FAIL", "Unsupported mandatory skills: " + ", ".join(missing))
    sources_supported = bool(groups) and all(is_primary(job, r.get("source")) for r in groups)
    technical = (
        4 * sum(r["weight"] for r in groups if r["matched"]) / sum(r["weight"] for r in groups)
        if sources_supported
        else None
    )

    compensation = job.get("compensation") or {}
    pay_status, pay_points = "PAY_NOT_DISCLOSED", 0.0
    pay_block = False
    if compensation.get("minimum") is not None:
        expected = "month" if job["employment_type"] == "internship" else "year"
        if (
            compensation.get("currency") != "INR"
            or compensation.get("period") != expected
            or not is_primary(job, compensation.get("source"))
        ):
            pay_status, pay_block = "PAY_NEEDS_CLARIFICATION", True
        else:
            floor = (
                settings["internship_monthly_floor_inr"]
                if expected == "month"
                else settings["full_time_annual_floor_inr"]
            )
            if compensation["minimum"] >= floor:
                pay_status, pay_points = "MEETS_FLOOR", 1
            elif salary_exception and job.get("tier") == "GREAT":
                pay_status, pay_points = "ACCEPTED_BELOW_FLOOR_EXCEPTION", 0.5
            else:
                pay_status, pay_block = "BELOW_FLOOR_EXCEPTION_REQUIRED", True

    alignment = job.get("responsibility_alignment")
    responsibility = (
        alignment
        if alignment in (0, 1, 2) and is_primary(job, job.get("responsibility_source"))
        else None
    )
    level = job.get("level_fit")
    level = level if level in (1, 2) and is_primary(job, job.get("level_source")) else None
    company_points = (
        0.5
        if job.get("business_description") and is_primary(job, job.get("business_source"))
        else 0
    )
    company_points += (
        0.5
        if job.get("engineering_context") and is_primary(job, job.get("engineering_source"))
        else 0
    )
    components = {
        "technical": technical,
        "responsibility": responsibility,
        "level_timeline": level,
        "compensation": pay_points,
        "company_role_evidence": company_points,
    }
    score = (
        round(sum(v for v in components.values() if v is not None), 1)
        if all(v is not None for v in components.values())
        else None
    )
    unknowns = [
        name + ": " + gate["reason"] for name, gate in gates.items() if gate["status"] == "UNKNOWN"
    ]
    flags = []
    if not sources_supported:
        unknowns.append("Technical skill groups lack a defensible primary-source denominator.")
    if responsibility is None or level is None:
        unknowns.append(
            "Responsibility alignment or timeline score needs primary-source assessment."
        )
    if pay_block:
        unknowns.append(pay_status)
    if not job.get("posting_date"):
        flags.append("Posting date: Not disclosed")
    else:
        age = (clock.date() - date.fromisoformat(job["posting_date"][:10])).days
        if age < 0:
            unknowns.append("Posting date is in the future; verify it.")
        elif age > settings["posting_age_days"]:
            if job["employment_type"] == "graduate_programme" and job.get(
                "older_programme_exception"
            ):
                flags.append("Older active graduate programme exception")
            else:
                unknowns.append(
                    "Older than the listing preference; not an allowed graduate-programme exception."
                )
    checked = job.get("last_verified_at")
    if not checked or not timedelta(0) <= clock - parse_time(checked) <= timedelta(hours=24):
        unknowns.append("Live vacancy verification is missing or older than 24 hours.")
    failed = [name for name, gate in gates.items() if gate["status"] == "FAIL"]
    if failed:
        status = "REJECTED"
    elif unknowns or score is None:
        status = "NEEDS_CLARIFICATION"
    elif score < settings["shortlist_threshold"]:
        status = "BELOW_THRESHOLD"
    else:
        status = "READY_FOR_REVIEW"
    matches = [r for r in groups if r["matched"]]
    gaps = [r["group"] for r in groups if not r["matched"]]
    return {
        "status": status,
        "gates": gates,
        "score_components": components,
        "match_score": score,
        "technical_groups": groups,
        "matches": matches,
        "gaps": gaps,
        "compensation_status": pay_status,
        "unknowns": unknowns,
        "flags": flags,
        "extracted_experience_evidence": extracted,
        "availability_assessment": availability_facts,
        "confidence": "High"
        if status == "READY_FOR_REVIEW" and pay_status == "MEETS_FLOOR"
        else "Medium"
        if status == "READY_FOR_REVIEW"
        else "Low",
    }


def role_family(title: str) -> str:
    if re.search(r"cloud|devops|systems|infrastructure|platform|reliability", title, re.I):
        return "cloud"
    if re.search(r"backend|back.end|python|django|fastapi|\bapi\b|\bai\b", title, re.I):
        return "backend"
    return "sde"
