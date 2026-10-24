"""Derive presentation and conservative experience bounds from recorded facts."""

from __future__ import annotations

import re
from typing import Any

Record = dict[str, Any]


def education_level(record: Record) -> str:
    if record.get("level"):
        return str(record["level"])
    degree = re.sub(r"[^a-z0-9]", "", record.get("degree", "").casefold())
    if degree.startswith(("bachelor", "btech", "beng")) or degree in {"be", "bs", "bsc", "ba"}:
        return "undergraduate"
    if degree.startswith(("master", "mtech", "meng")) or degree in {
        "me",
        "ms",
        "msc",
        "ma",
        "mba",
        "mca",
    }:
        return "postgraduate"
    return "doctorate" if degree in {"phd", "doctorate"} else "other"


def education_record(profile: Record, level: str) -> Record | None:
    return next((item for item in profile["education"] if education_level(item) == level), None)


def education_summary(profile: Record) -> str:
    records = sorted(profile["education"], key=lambda item: item["end_year"], reverse=True)
    descriptions = []
    for item in records:
        status = (
            "ongoing"
            if item["status"].casefold() in {"ongoing", "pursuing", "current"}
            else item["status"]
        )
        descriptions.append(
            f"{item['degree']} in {item['branch']} at {item['institution']} ({item['start_year']}–{item['end_year']}; {status})"
        )
    return "; ".join(descriptions) + "."


def branch_tokens(record: Record) -> set[str]:
    # These are candidate-recorded labels, not automatic CS/IT equivalencies.
    return {
        re.sub(r"[^a-z0-9]", "", label.casefold())
        for label in [record["branch"], *record.get("branch_aliases", [])]
    }


def experience_months(
    profile: Record, *, professional_only: bool = False, internships_only: bool = False
) -> int:
    months: set[int] = set()
    for item in profile["experience"]:
        kind = re.sub(r"[^a-z]", "", item["type"].casefold())
        if professional_only and kind not in {"fulltime", "permanent"}:
            continue
        if internships_only and kind != "internship":
            continue
        start_year, start_month = map(int, item["start"].split("-"))
        end_year, end_month = map(int, item["end"].split("-"))
        months.update(range(start_year * 12 + start_month, end_year * 12 + end_month + 1))
    return len(months)


def experience_summary(profile: Record) -> str:
    internships = experience_months(profile, internships_only=True)
    professional = experience_months(profile, professional_only=True)
    return f"Recorded internships: {internships} months; recorded full-time professional experience: {professional} months. Month-level durations are approximate."
