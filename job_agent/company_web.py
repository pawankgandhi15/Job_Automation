"""Publish a minimal, sourced company snapshot without candidate/application data."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .common import WorkflowError, domain, now, read_json, replace_file, timestamp, write_json
from .role_briefs import role_details
from .types import Record

ACTIVE_STATES = {"NEEDS_CLARIFICATION", "READY_FOR_REVIEW", "AWAITING_APPROVAL", "APPROVED"}


def company_snapshot(
    rows: list[Record],
    briefs: Record,
    discovery: Record | None = None,
    report_id: str | None = None,
    daily: Record | None = None,
    role_briefs: Record | None = None,
) -> Record:
    groups: dict[str, Record] = {}
    for row in rows:
        job = row["payload"]
        key = domain(job["official_website"])
        application_url = job.get("application_url")
        if isinstance(application_url, str):
            try:
                domain(application_url)  # Only HTTPS links without embedded credentials.
            except (WorkflowError, ValueError):
                application_url = None
        else:
            application_url = None
        record = groups.setdefault(
            key,
            {"domain": key, "name": job["company"], "website": job["official_website"], "jobs": []},
        )
        record["jobs"].append(
            {
                "id": row["opportunity_id"],
                "title": job["role"],
                "location": job["location"],
                "employment_type": job["employment_type"],
                "status": (row.get("evaluation") or {}).get("status", "NEEDS_CLARIFICATION"),
                "review_needed": row["state"] in ACTIVE_STATES,
                "url": job["canonical_url"],
                "application_url": application_url,
                "last_verified_at": job.get("last_verified_at"),
                "details": role_details(job, role_briefs),
            }
        )
    for key, company in groups.items():
        brief = briefs.get("companies", {}).get(key, {})
        # Whitelist facts: do not serialize raw opportunity payloads, drafts or answers.
        for name in (
            "sector",
            "about",
            "about_source",
            "ceo",
            "recent_update",
            "tags",
            "checked_on",
            "research_note",
        ):
            company[name] = brief.get(name)
        checked = company["checked_on"]
        company["research_stale"] = (
            not checked or (now().date() - date.fromisoformat(checked)).days > 30
        )
        company["review_count"] = sum(job["review_needed"] for job in company["jobs"])
        company["recorded_count"] = len(company["jobs"])
        company["jobs"].sort(key=lambda job: (not job["review_needed"], job["id"]))
    companies = sorted(
        groups.values(),
        key=lambda company: (
            company["name"].casefold() not in {"stripe", "supabase"},
            -company["review_count"],
            company["name"].casefold(),
        ),
    )
    last = discovery or {}
    return {
        "schema_version": 1,
        "kind": "job_agent_company_snapshot",
        "generated_at": timestamp(),
        "report_id": report_id,
        "discovery_at": last.get("at"),
        "discovery_counts": {
            key: last.get("counts", {}).get(key, 0)
            for key in ("board_roles_seen", "sources_checked", "sources_failed", "new_leads")
        },
        "companies": companies,
        "company_count": len(companies),
        "review_count": sum(company["review_count"] for company in companies),
        "daily_selection": daily or {"date": now().date().isoformat(), "domains": [], "target": 5},
    }


def export_company_web(
    root: Path,
    rows: list[Record],
    discovery: Record | None = None,
    report_id: str | None = None,
    snapshot_path: Path | None = None,
    daily: Record | None = None,
) -> Record:
    brief_path = root / "config" / "company_briefs.json"
    briefs = read_json(brief_path) if brief_path.is_file() else {}
    roles_path = root / "config" / "role_briefs.json"
    roles = read_json(roles_path) if roles_path.is_file() else {}
    snapshot = company_snapshot(rows, briefs, discovery, report_id, daily, roles)
    project = root / "web" / "company-overview"
    directory = project / "dist"
    # Vite copies public assets into dist. Update both so a future build retains
    # the newest export and an already-built local preview refreshes immediately.
    encoded = (
        json.dumps(snapshot, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    for destination in (project / "public", directory):
        destination.mkdir(parents=True, exist_ok=True)
        write_json(destination / "companies.json", snapshot)
        # Kept as a data export for consumers of the original script format.
        script = destination / "companies.js"
        temp = script.with_suffix(".js.tmp")
        temp.write_text("window.COMPANY_DATA = " + encoded + ";\n", encoding="utf-8")
        replace_file(temp, script)
    if snapshot_path:
        write_json(snapshot_path, snapshot)
    return {
        "path": str(directory / "index.html"),
        "snapshot_path": str(snapshot_path or directory / "companies.json"),
        "company_count": len(snapshot["companies"]),
        "requires_http_server": True,
        "dev_command": "npm.cmd run dev (from web/company-overview in Windows PowerShell)",
        "hosted_page_updated": False,
    }
