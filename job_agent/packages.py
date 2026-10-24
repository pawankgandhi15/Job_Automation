from __future__ import annotations

from pathlib import Path

from .availability import availability_text, resume_profile_digest
from .candidate import education_level, education_record, education_summary, experience_summary
from .common import digest, file_digest, material_job
from .policy import is_primary, role_family
from .types import Record


def resume_version(
    root: Path, settings: Record, profile: Record, attestation: Record | None
) -> Record:
    path = (root / settings["resume_path"]).resolve()
    result = {
        "path": str(path),
        "available": path.is_file(),
        "sha256": None,
        "profile_checked": False,
    }
    if result["available"]:
        result["sha256"] = file_digest(path)
        result["profile_checked"] = bool(
            attestation
            and attestation.get("resume_sha256") == result["sha256"]
            and (
                attestation.get("resume_profile_sha256") == resume_profile_digest(profile)
                if attestation.get("resume_profile_sha256")
                else attestation.get("profile_sha256") == digest(profile)
            )
        )
    return result


def build_package(job: Record, evaluation: Record, profile: Record, resume: Record) -> Record:
    family = role_family(job["role"])
    cloud = family == "cloud"
    supported = evaluation["matches"][:3]
    skills = ", ".join(item["group"] for item in supported) or ", ".join(
        list(profile["skill_evidence"])[:3]
    )
    strength = (
        f"documented skills in {skills}" if skills else "the experience recorded in my resume"
    )
    summary = [education_summary(profile), experience_summary(profile)]
    ordered = (
        sorted(profile["experience"], key=lambda item: "cloud" not in item["title"].casefold())
        if cloud
        else profile["experience"]
    )
    bullet_suggestions = [
        {"company": x["company"], "title": x["title"], "bullets": x["bullets"]} for x in ordered
    ]
    subject = f"Application enquiry: {job['role']}" + (
        f" ({job['job_id']})" if job.get("job_id") else ""
    )
    from .packaging import render_template

    context = {
        "role": job["role"],
        "company": job["company"],
        "education": education_summary(profile),
        "strength": strength,
        "availability": availability_text(profile, job["employment_type"]),
        "name": profile["name"],
    }
    body = render_template(family, context)
    blockers = []
    if not resume["available"]:
        blockers.append(
            "Designated resume is unavailable; attachment-dependent actions are blocked."
        )
    elif not resume["profile_checked"]:
        blockers.append(
            "Candidate must reconcile the supplied profile with this resume version before external use."
        )
    if len(body.split()) > 150:
        blockers.append("Outreach draft exceeds 150 words; shorten before approval.")
    ongoing = [
        item
        for item in profile["education"]
        if item["status"].casefold() in {"ongoing", "pursuing", "current"}
    ]
    rank = {"other": 0, "undergraduate": 1, "postgraduate": 2, "doctorate": 3}
    highest = max(ongoing, key=lambda item: rank[education_level(item)]) if ongoing else None
    answers = {
        "name": profile["name"],
        "email": profile["email"],
        "phone": profile["phone"],
        "location": profile["location"],
        "highest_ongoing_degree": highest,
        "undergraduate_degree": education_record(profile, "undergraduate"),
        "current_ctc": None,
        "notice_period": availability_text(profile, job["employment_type"]),
        "internship_hours_per_day": profile.get("internship_hours_per_day"),
        "work_authorization": profile.get("work_authorization"),
        "expected_graduation_month": profile.get("expected_graduation_month"),
        "experience": experience_summary(profile),
    }
    for question, confirmed in job.get("confirmed_answers", {}).items():
        if (
            question in job.get("required_questions", [])
            and confirmed.get("evidence")
            and confirmed.get("confirmed_at")
        ):
            answers[question] = confirmed["answer"]
    unanswered = [
        q for q in job.get("required_questions", []) if q not in answers or answers[q] is None
    ]
    blockers.extend("Unresolved required screening question: " + q for q in unanswered)
    contact = job.get("contact") or {}
    contact_usable = bool(
        contact.get("name")
        and contact.get("destination")
        and contact.get("recruiting_relevance")
        and contact.get("status") in {"SOURCE_CONFIRMED", "PUBLICLY_ATTRIBUTED"}
        and contact.get("source")
        and contact.get("checked_at")
    )
    # Email syntax is not evidence of ownership or deliverability.
    if contact.get("status") == "SOURCE_CONFIRMED" and not is_primary(job, contact.get("source")):
        contact_usable = False
    consents = job.get("consent_answers", {})
    unresolved_consents = [
        q for q in job.get("required_consents", []) if consents.get(q) is not True
    ]
    actions = [
        {
            "channel": "portal",
            "destination": job.get("application_url", job["canonical_url"]),
            "attachment": resume,
            "answers": answers,
            "unresolved_questions": unanswered,
            "required_consents": job.get("required_consents", []),
            "consent_answers": consents,
        }
    ]
    if contact_usable:
        actions.append(
            {
                "channel": "outreach",
                "destination": contact["destination"],
                "recipient_name": contact["name"],
                "subject": subject,
                "body": body,
                "signature": f"{profile['name']}\n{profile['email']}\n{profile['linkedin']}",
                "attachment": resume,
                "contact_evidence": contact,
            }
        )
    if unresolved_consents:
        blockers.append(
            "Mandatory declarations/consents need explicit candidate answers before execution."
        )
    return {
        "draft_version": "draft-"
        + digest({"job": material_job(job), "profile": profile, "resume": resume})[:12],
        "profile_version": profile["version"],
        "profile_sha256": digest(profile),
        "resume": resume,
        "provisional": not resume["profile_checked"],
        "summary": summary,
        "matches": [{"skill": r["group"], "evidence": r["candidate_evidence"]} for r in supported],
        "gaps": evaluation["gaps"][:3],
        "resume_bullet_order": bullet_suggestions,
        "screening_answers": answers,
        "blockers": blockers,
        "outreach_draft": {"subject": subject, "body": body},
        "actions": actions,
        "follow_up_draft": render_template(family, context, follow_up=True),
        "follow_up_included_in_initial_approval": False,
    }


def export_resume_text(profile: Record) -> str:
    lines = [
        profile["name"],
        f"{profile['email']} | {profile['phone']} | {profile['location']}",
        profile["linkedin"],
        profile["github"],
        "",
        "EDUCATION",
    ]
    for item in profile["education"]:
        grade = (
            f"CGPA {item['cgpa']:.2f}/{item['scale']}"
            if item.get("cgpa") is not None
            else "Grade not disclosed"
        )
        lines.append(
            f"{item['degree']} {item['branch']} | {item['institution']} | {item['start_year']}–{item['end_year']} | {grade}"
        )
    lines += ["", "SKILLS", ", ".join(profile["skill_evidence"]), "", "INTERNSHIPS"]
    for item in profile["experience"]:
        lines += [
            f"{item['title']} | {item['company']} | {item['start']}–{item['end']}",
            *["• " + b for b in item["bullets"]],
        ]
    lines += ["", "PROJECTS"]
    for item in profile.get("projects", []):
        lines += [
            f"{item['name']} | {item['year']} | {', '.join(item['technologies'])}",
            *["• " + b for b in item["bullets"]],
        ]
    lines += [
        "",
        "COURSE COMPLETIONS",
        *profile.get("credentials", []),
        "",
        "ACHIEVEMENTS",
        *profile.get("achievements", []),
    ]
    return "\n".join(lines) + "\n"
