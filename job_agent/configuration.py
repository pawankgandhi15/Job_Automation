"""Validate candidate and runtime configuration before opening the ledger.

Validation checks structure and consistency, not the truth of candidate claims.
Unknown facts remain absent; this module never fills in qualifications.
"""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from .common import WorkflowError, domain, parse_time, read_json, same_domain

Record = dict[str, Any]


class ConfigurationError(WorkflowError):
    pass


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise ConfigurationError(f"{path}: {message}")


def _object(value: Any, path: str) -> Record:
    _require(isinstance(value, dict), path, "must be an object")
    result: Record = value
    return result


def _text(value: Any, path: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), path, "must be nonempty text")
    result: str = value
    return result


def _number(
    value: Any, path: str, minimum: float, maximum: float, *, integer: bool = False
) -> None:
    _require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        path,
        "must be a finite number",
    )
    _require(
        minimum <= value <= maximum and math.isfinite(value),
        path,
        f"must be between {minimum:g} and {maximum:g}",
    )
    if integer:
        _require(isinstance(value, int), path, "must be an integer")


def _texts(value: Any, path: str) -> None:
    _require(isinstance(value, list), path, "must be an array")
    for index, item in enumerate(value):
        _text(item, f"{path}[{index}]")


def _month(value: Any, path: str) -> str:
    text = _text(value, path)
    _require(bool(re.fullmatch(r"\d{4}-\d{2}", text)), path, "must use YYYY-MM")
    date.fromisoformat(text + "-01")
    return text


def validate_profile(value: Any) -> Record:
    profile = _object(value, "profile")
    for key in (
        "version",
        "name",
        "email",
        "phone",
        "location",
        "linkedin",
        "github",
        "reconfirm_after",
    ):
        _text(profile.get(key), "profile." + key)
    date.fromisoformat(profile["reconfirm_after"])
    for key in ("linkedin", "github", "project_url"):
        if profile.get(key):
            domain(profile[key])
    education = profile.get("education", [])
    _require(
        isinstance(education, list) and bool(education),
        "profile.education",
        "needs at least one education record",
    )
    for index, item in enumerate(education):
        path = f"profile.education[{index}]"
        item = _object(item, path)
        for key in ("degree", "branch", "institution", "status"):
            _text(item.get(key), path + "." + key)
        for key in ("start_year", "end_year"):
            _number(item.get(key), path + "." + key, 1900, 2200, integer=True)
        _require(item["start_year"] <= item["end_year"], path, "start year exceeds end year")
        if item.get("cgpa") is not None:
            _number(item.get("scale"), path + ".scale", 1, 100)
            _number(item["cgpa"], path + ".cgpa", 0, item["scale"])
        if "level" in item:
            _require(
                item["level"] in {"undergraduate", "postgraduate", "doctorate", "other"},
                path + ".level",
                "unsupported education level",
            )
        if "branch_aliases" in item:
            _texts(item["branch_aliases"], path + ".branch_aliases")
    skills = _object(profile.get("skill_evidence"), "profile.skill_evidence")
    for skill, evidence in skills.items():
        _text(skill, "profile.skill_evidence key")
        _text(evidence, f"profile.skill_evidence.{skill}")
    _require(
        isinstance(profile.get("experience"), list),
        "profile.experience",
        "must be an array (empty is allowed)",
    )
    for index, item in enumerate(profile["experience"]):
        path = f"profile.experience[{index}]"
        item = _object(item, path)
        for key in ("company", "title", "type"):
            _text(item.get(key), path + "." + key)
        start, end = (
            _month(item.get("start"), path + ".start"),
            _month(item.get("end"), path + ".end"),
        )
        _require(start <= end, path, "start month exceeds end month")
        _texts(item.get("bullets"), path + ".bullets")
    for index, item in enumerate(profile.get("projects", [])):
        path = f"profile.projects[{index}]"
        item = _object(item, path)
        _text(item.get("name"), path + ".name")
        _number(item.get("year"), path + ".year", 1900, 2200, integer=True)
        _texts(item.get("technologies"), path + ".technologies")
        _texts(item.get("bullets"), path + ".bullets")
    for key in ("credentials", "achievements"):
        _texts(profile.get(key, []), "profile." + key)
    if profile.get("expected_graduation_month"):
        _month(profile["expected_graduation_month"], "profile.expected_graduation_month")
    authorization = _object(profile.get("work_authorization", {}), "profile.work_authorization")
    for key in ("eligible", "requires_sponsorship_now_or_future"):
        if key in authorization:
            _require(
                isinstance(authorization[key], bool),
                "profile.work_authorization." + key,
                "must be a boolean",
            )
    for kind, record in _object(profile.get("availability", {}), "profile.availability").items():
        path = "profile.availability." + kind
        _require(kind in {"internship", "full_time"}, path, "unsupported employment kind")
        record = _object(record, path)
        month, exact = record.get("earliest_start_month"), record.get("earliest_start_date")
        if month:
            _month(month, path + ".earliest_start_month")
        if exact:
            date.fromisoformat(exact)
            _require(not month or exact[:7] == month, path, "start day and month conflict")
        for key, maximum in (("hours_per_day", 24), ("days_per_week", 7), ("duration_months", 120)):
            if record.get(key) is not None:
                _number(record[key], path + "." + key, 0.1, maximum, integer=key == "days_per_week")
        if month or exact or record.get("hours_per_day") is not None:
            _text(record.get("source"), path + ".source")
            parse_time(_text(record.get("confirmed_at"), path + ".confirmed_at"))
        if "location_mode" in record:
            _require(
                record["location_mode"] in {"role_dependent", "remote", "onsite", "hybrid"},
                path + ".location_mode",
                "unsupported location mode",
            )
    return profile


def validate_settings(value: Any) -> Record:
    settings = _object(value, "settings")
    if "gate_dependencies" in settings:
        from .policy import GATES

        for gate, dependencies in _object(
            settings["gate_dependencies"], "settings.gate_dependencies"
        ).items():
            _require(gate in GATES, "settings.gate_dependencies", "unknown eligibility rule")
            _texts(dependencies, "settings.gate_dependencies." + gate)
            _require(
                all(item in GATES for item in dependencies),
                "settings.gate_dependencies",
                "unknown dependency",
            )
    if "resume_variants" in settings:
        for family, path in _object(
            settings["resume_variants"], "settings.resume_variants"
        ).items():
            _require(
                family in {"cloud", "backend", "sde"},
                "settings.resume_variants",
                "unsupported role family",
            )
            _text(path, "settings.resume_variants." + family)

    _require(
        settings.get("timezone") in {"Asia/Kolkata", "Asia/Calcutta"},
        "settings.timezone",
        "this workflow currently uses India Standard Time",
    )
    for key in ("preferred_employer", "resume_path"):
        _text(settings.get(key), "settings." + key)
    _texts(settings.get("roles"), "settings.roles")
    for key in ("internship_monthly_floor_inr", "full_time_annual_floor_inr"):
        _number(settings.get(key), "settings." + key, 0, 1_000_000_000)
    _number(settings.get("shortlist_threshold"), "settings.shortlist_threshold", 0, 10)
    if "automatic_experience_exclusion_min_years" in settings:
        _number(
            settings["automatic_experience_exclusion_min_years"],
            "settings.automatic_experience_exclusion_min_years",
            0.1,
            100,
        )
    for key, maximum in (
        ("max_new_companies", 1000),
        ("posting_age_days", 3650),
        ("approval_hours", 8760),
        ("follow_up_days", 365),
    ):
        _number(settings.get(key), "settings." + key, 1, maximum, integer=True)
    for key, maximum in (
        ("discovery_workers", 4),
        ("max_leads_per_run", 10000),
        ("max_leads_per_company", 1000),
        ("daily_company_target", 1000),
    ):
        if key in settings:
            _number(settings[key], "settings." + key, 1, maximum, integer=True)
    for key in ("history_complete", "report_email_authorized"):
        if key in settings:
            _require(isinstance(settings[key], bool), "settings." + key, "must be a boolean")
    aliases = _object(settings.get("company_domain_aliases", {}), "settings.company_domain_aliases")
    for alias, target in aliases.items():
        _require(
            domain("https://" + alias) == alias
            and domain("https://" + _text(target, "company alias")) == target,
            "settings.company_domain_aliases",
            "use bare canonical domains",
        )
    _texts(settings.get("discovery_location_terms", []), "settings.discovery_location_terms")
    _require(isinstance(settings.get("sources"), list), "settings.sources", "must be an array")
    for index, source in enumerate(settings["sources"]):
        path = f"settings.sources[{index}]"
        source = _object(source, path)
        _text(source.get("company"), path + ".company")
        website = domain(_text(source.get("official_website"), path + ".official_website"))
        _require(
            same_domain(
                _text(source.get("official_careers_url"), path + ".official_careers_url"), website
            ),
            path,
            "careers page must belong to the employer",
        )
        _require(
            source.get("provider")
            in {"greenhouse", "lever", "ashby", "jobposting", "smartrecruiters", "workable"},
            path + ".provider",
            "unsupported provider",
        )
        _require(
            source.get("tier", "GOOD") in {"GOOD", "BETTER", "GREAT"},
            path + ".tier",
            "unsupported tier",
        )
        if source["provider"] == "jobposting":
            _texts(source.get("posting_urls"), path + ".posting_urls")
            _require(bool(source["posting_urls"]), path + ".posting_urls", "needs at least one URL")
            for url in source["posting_urls"]:
                _require(same_domain(url, website), path, "posting URL must belong to the employer")
        else:
            _require(
                bool(re.fullmatch(r"[A-Za-z0-9_-]+", _text(source.get("board"), path + ".board"))),
                path + ".board",
                "invalid board token",
            )
    if "daily_task_name" in settings:
        _require(
            bool(
                re.fullmatch(
                    r"[A-Za-z0-9_-]{1,150}",
                    _text(settings["daily_task_name"], "settings.daily_task_name"),
                )
            ),
            "settings.daily_task_name",
            "use letters, numbers, hyphens and underscores",
        )
    return settings


def load_configuration(root: Path) -> tuple[Record, Record]:
    try:
        settings = validate_settings(read_json(root / "config/settings.json"))
        profile = validate_profile(read_json(root / "config/profile.json"))
    except (ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError("Invalid configuration: " + str(exc)) from exc
    return settings, profile
