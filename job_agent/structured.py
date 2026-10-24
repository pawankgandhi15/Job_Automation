"""Read employer-published Schema.org JobPosting metadata without executing scripts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from .common import WorkflowError, digest, domain, parse_time, same_domain, timestamp
from .types import HTTPClient, Record


def job_postings(blocks: list[str]) -> Iterator[Record]:
    def walk(value: Any) -> Iterator[Record]:
        if isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if any(t.rsplit("/", 1)[-1] == "JobPosting" for t in types):
                yield value
            yield from walk(value.get("@graph", []))
            yield from walk(value.get("mainEntity", []))

    for text in blocks:
        try:
            yield from walk(json.loads(text))
        except (json.JSONDecodeError, TypeError):
            continue


def employment_type(value: Any, title: str = "") -> str:
    values = value if isinstance(value, list) else [value or ""]
    joined = " ".join(str(v) for v in values) + " " + title
    if re.search(r"\bintern(?:ship)?s?\b", joined, re.I):
        return "internship"
    if any(
        str(v).upper().replace("_", "").replace("-", "").replace(" ", "") == "FULLTIME"
        for v in values
    ):
        return "full_time"
    return "unknown"


def _locations(raw: Any) -> str:
    values = raw if isinstance(raw, list) else [raw or {}]
    labels = []
    for item in values:
        if isinstance(item, str):
            labels.append(item)
            continue
        address = item.get("address", {})
        if isinstance(address, str):
            labels.append(address)
            continue
        country = address.get("addressCountry", "")
        if isinstance(country, dict):
            country = country.get("name", "")
        label = ", ".join(
            str(x)
            for x in [address.get("addressLocality"), address.get("addressRegion"), country]
            if x
        )
        if label:
            labels.append(label)
    return " / ".join(labels) or "Not disclosed"


def disclosed_salary(raw: Any, url: str) -> Record | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    if not isinstance(value, dict):
        return None
    minimum = value.get("minValue", value.get("value"))
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
        return None
    unit = str(value.get("unitText", "")).upper()
    return {
        "minimum": minimum,
        "maximum": value.get("maxValue"),
        "currency": raw.get("currency"),
        "period": {"MONTH": "month", "YEAR": "year"}.get(unit, unit.lower()),
        "source": url,
        "basis": "Employer-disclosed base salary; no CTC or take-home conversion",
    }


def normalize_jobposting(source: Record, raw: Record, page_url: str) -> Record:
    from .sources import plain

    title = plain(raw.get("title", ""))
    if not title:
        raise WorkflowError("JobPosting has no title.")
    organization = raw.get("hiringOrganization") or {}
    if (
        isinstance(organization, dict)
        and organization.get("url")
        and not same_domain(organization["url"], domain(source["official_website"]))
    ):
        raise WorkflowError(
            "Structured hiring organization differs from the configured employer; identity needs review."
        )
    identity = raw.get("identifier")
    if isinstance(identity, dict):
        identity = identity.get("value")
    if not isinstance(identity, (str, int)):
        identity = None
    checked = timestamp()
    remote = raw.get("jobLocationType") == "TELECOMMUTE"
    location = _locations(raw.get("jobLocation"))
    if remote:
        location = "Remote; " + location
    constraints: Record = {}
    start = raw.get("jobStartDate")
    if isinstance(start, str):
        try:
            constraints["start_date"] = date.fromisoformat(start[:10]).isoformat()
        except ValueError:
            if re.fullmatch(r"immediate(?:ly)?", start.strip(), re.I):
                constraints["immediate_joining"] = True
    hours = raw.get("workHours", "")
    if isinstance(hours, str) and not re.search(r"\d\s*[-–]\s*\d", hours):
        for pattern, field in [
            (
                r"(?<![\d.\-–])\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|a|/)\s*day\b",
                "required_hours_per_day",
            ),
            (
                r"(?<![\d.\-–])\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|a|/)\s*week\b",
                "required_hours_per_week",
            ),
        ]:
            match = re.search(pattern, hours, re.I)
            if match:
                constraints[field] = float(match.group(1))
    # Structured months do not establish professional-only experience or exclude
    # education/internship alternatives. Preserve the original for human review.
    url = (
        str(raw["url"])
        if raw.get("url") and same_domain(raw["url"], domain(source["official_website"]))
        else page_url
    )
    domain(url)
    gates = {
        "employer_identity": {
            "status": "PASS",
            "reason": "JobPosting is published on the configured employer's own domain; no conflicting hiring-organization domain was found.",
            "source": page_url,
            "checked_at": checked,
        },
        "live_vacancy": {
            "status": "PASS",
            "reason": "Individual employer page publishes a JobPosting record at verification time; deadline is checked separately.",
            "source": page_url,
            "checked_at": checked,
        },
        "application_route": {
            "status": "UNKNOWN",
            "reason": "Structured directApply metadata is not proof that the application form is usable; inspect the route.",
            "source": page_url,
            "checked_at": checked,
        },
    }
    posted = raw.get("datePosted")
    try:
        posted = date.fromisoformat(str(posted)[:10]).isoformat()
    except ValueError:
        posted = None
    deadline, deadline_display = None, raw.get("validThrough")
    if deadline_display:
        try:
            parse_time(str(deadline_display))
            deadline = str(deadline_display)
        except (ValueError, WorkflowError):
            pass  # Date-only/naive deadlines remain honestly labelled, without an invented timezone.
    return {
        "company": source["company"],
        "official_website": source["official_website"],
        "tier": source.get("tier", "GOOD"),
        "role": title,
        "job_id": str(identity) if identity is not None else None,
        "canonical_url": url,
        "application_url": url,
        "location": location,
        "employment_type": employment_type(raw.get("employmentType"), title),
        "workplace_type": "remote" if remote else "unknown",
        "posting_date": posted,
        "deadline": deadline,
        "deadline_display": deadline_display,
        "description": plain(raw.get("description", "")),
        "constraints": constraints,
        "requirements_source": page_url,
        "last_verified_at": checked,
        "primary_source_urls": [url, page_url],
        "provider": "jobposting",
        "source_config": source,
        "eligibility_gates": gates,
        "source_content_hash": digest({k: v for k, v in raw.items() if k != "dateModified"}),
        "compensation": disclosed_salary(raw.get("baseSalary"), page_url),
        "structured_requirements": {
            key: raw[key]
            for key in (
                "educationRequirements",
                "experienceRequirements",
                "skills",
                "workHours",
                "applicantLocationRequirements",
                "eligibilityToWorkRequirement",
            )
            if key in raw
        },
    }


def fetch_jobposting(source: Record, http: HTTPClient) -> list[Record]:
    from .sources import Page

    urls = source.get("posting_urls") or [source["official_careers_url"]]
    result: list[Record] = []
    for url in urls:
        if not same_domain(url, domain(source["official_website"])):
            raise WorkflowError(
                "JobPosting pages must originate on the configured official employer domain."
            )
        page = http.get(url)
        if not same_domain(page["url"], domain(source["official_website"])):
            raise WorkflowError(
                "JobPosting page redirected away from the employer; verification incomplete."
            )
        parser = Page()
        parser.feed(page["text"])
        found = list(job_postings(parser.structured_data))
        if not found:
            raise WorkflowError(
                "No readable JobPosting metadata on the employer page; review/import it manually. No vacancy inferred."
            )
        result.extend(normalize_jobposting(source, item, page["url"]) for item in found)
    return result
