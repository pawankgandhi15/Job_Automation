from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from .common import now, timestamp
from .types import Record


def cell(value: Any) -> str:
    return (
        str(value if value is not None else "Not disclosed").replace("|", "\\|").replace("\n", " ")
    )


def render(
    report_id: str,
    rows: list[Record],
    selected: list[Record],
    updates: list[Record],
    watchlist: list[Record],
    counts: Record,
    limitations: list[str],
    cumulative: Record,
    settings: Record,
    *,
    candidate_name: str = "Candidate",
) -> str:
    clock = now()
    lines = [
        f"# Job shortlist | {candidate_name} | {clock.date()} | {len(selected)} new companies",
        "",
        f"Report: {report_id} · Generated: {timestamp(clock)} (IST)",
        f"Search preference: {(clock - timedelta(days=settings['posting_age_days'])).date()} through {clock.date()}.",
        "",
        "Run counts: " + "; ".join(f"{k}: {v}" for k, v in counts.items()) + ".",
        "",
        "Limitations:",
        *[f"- {x}" for x in limitations],
        "",
        "## Part A — Decision table",
        "",
        "Opportunity | Company / Tier | Role / Job ID | Location / Type | Official job | Match /10 | Eligibility | Pay | Deadline | State | Proposed action",
        "--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---",
    ]
    for row in selected:
        job, evaluation = row["payload"], row["evaluation"]
        values = [
            row["opportunity_id"],
            job["company"] + " / " + job.get("tier", "GOOD"),
            job["role"] + " / " + str(job.get("job_id") or "fallback key"),
            job["location"] + " / " + job["employment_type"],
            job["canonical_url"],
            evaluation["match_score"],
            evaluation["status"],
            evaluation["compensation_status"],
            job.get("deadline"),
            row["state"],
            ", ".join(a["channel"] for a in row["package"]["actions"]) + "; approval required",
        ]
        lines.append(" | ".join(cell(v) for v in values))
    if not selected:
        lines += ["", "No new companies meet all verification and eligibility requirements."]
    lines += ["", "## Part B — Opportunity briefs", ""]
    for row in selected + updates:
        job, evaluation, package = row["payload"], row["evaluation"], row["package"]
        lines += [
            f"### {row['opportunity_id']} — {job['company']} — {job['role']}",
            "",
            f"VERIFIED_FACTS: {job.get('business_description', 'Business description not available')}",
            f"Source: {job.get('business_source', 'Not available')}",
            f"Headquarters / size: {job.get('headquarters', 'Not available')} / {job.get('size', 'Not available')}",
            f"Official vacancy: {job['canonical_url']}",
            f"Posting date: {job.get('posting_date') or 'Not disclosed'}; first seen: {row['first_seen_at']}; last verified: {job.get('last_verified_at', 'Not verified')}",
            "",
            "Supported matches:",
            *[f"- {r['skill']}: {r['evidence']}" for r in package["matches"]],
            "",
            "Mandatory eligibility:",
            *[
                f"- {name}: {g['status']} — {g['reason']} Source: {g.get('source', 'Not available')}"
                for name, g in evaluation["gates"].items()
            ],
            "",
            "INFERENCES: Match score "
            + str(evaluation["match_score"])
            + "/10; "
            + json.dumps(evaluation["score_components"])
            + ".",
            "Technical groups: " + json.dumps(evaluation["technical_groups"], ensure_ascii=False),
            "Evidence confidence: " + evaluation["confidence"] + " (not an interview probability).",
            "Compensation: "
            + json.dumps(job.get("compensation") or "Not disclosed", ensure_ascii=False),
            "Reviews: " + json.dumps(job.get("reviews") or "Not available", ensure_ascii=False),
            "Contact: "
            + json.dumps(
                job.get("contact") or "No source-confirmed recruiting contact found",
                ensure_ascii=False,
            ),
            "UNKNOWNS: "
            + "; ".join(evaluation["unknowns"] + package["blockers"] or ["None identified"]),
            "Gaps: " + ", ".join(package["gaps"] or ["None identified in assessed skill groups"]),
            "Flags: " + "; ".join(evaluation["flags"] or ["None"]),
            "",
            "Resume summary" + (" — PROVISIONAL" if package["provisional"] else "") + ":",
            *package["summary"],
            "",
            "Resume bullet ordering / wording:",
            "```json",
            json.dumps(package["resume_bullet_order"], indent=2, ensure_ascii=False),
            "```",
            "",
            "Outreach subject: " + package["outreach_draft"]["subject"],
            "",
            package["outreach_draft"]["body"],
            "",
            "Screening answers and exact action plan:",
            "```json",
            json.dumps(package["actions"], indent=2, ensure_ascii=False),
            "```",
            "",
            "Follow-up draft (separate approval required):",
            package["follow_up_draft"],
            "",
        ]
    lines += ["## Part C — Watchlist and existing-company updates", ""]
    for row in watchlist:
        e = row["evaluation"]
        failures = [
            name + ": " + gate["reason"]
            for name, gate in e["gates"].items()
            if gate["status"] == "FAIL"
        ]
        reasons = failures if e["status"] == "REJECTED" else e["unknowns"]
        if e["status"] == "BELOW_THRESHOLD":
            reasons.append("Score below shortlist threshold.")
        lines.append(
            f"- {row['opportunity_id']} | {row['payload']['company']} | {row['payload']['role']} | {e['status']} | "
            + "; ".join(reasons)
            + f" | {row['payload']['canonical_url']}"
        )
    if not watchlist:
        lines.append("No watchlist items.")
    lines += ["", "Existing-company updates (excluded from the new-company quota):"]
    lines += [
        f"- {r['opportunity_id']}: {r['payload']['company']} — {r['payload']['role']} (new role or materially changed package)."
        for r in updates
    ] or ["None."]
    lines += [
        "",
        "## Part D — Pipeline and approval request",
        "",
        json.dumps(cumulative, ensure_ascii=False),
        "",
        "Urgent strong matches: "
        + ", ".join(
            r["opportunity_id"]
            for r in selected + updates
            if r["payload"].get("deadline")
            and r["payload"]["deadline"][:10] <= (clock + timedelta(days=3)).date().isoformat()
        ),
        "",
        "Proposed actions (no action or follow-up is authorized by generating this report):",
    ]
    for row in selected + updates:
        lines.append(
            f"- {row['opportunity_id']}: "
            + "; ".join(a["channel"] + " → " + a["destination"] for a in row["package"]["actions"])
            + f"; attachment SHA-256: {row['package']['resume'].get('sha256') or 'UNAVAILABLE'}."
        )
        lines.extend("  Blocker: " + text for text in row["package"]["blockers"])
    if not selected and not updates:
        lines.append("- None. Resolve watchlist evidence before requesting execution approval.")
    lines += [
        "",
        "Report delivery: local file only. Email was not sent. Mark this report delivered only after you have reviewed it.",
        "",
    ]
    return "\n".join(lines)
