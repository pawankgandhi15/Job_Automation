"""Conservative extraction of explicit exclusion evidence, never eligibility passes."""

from __future__ import annotations

import re

from .sections import segment
from .types import Record

_HEADINGS = re.compile(
    r"(?P<preferred>preferred qualifications|nice[- ]to[- ]haves?|bonus points|what would be a bonus)"
    r"|(?P<required>you might be (?:a )?(?:good )?fit if(?: you)?(?: have)?|"
    r"what you['’]ll bring|what you bring|minimum qualifications|basic qualifications|"
    r"required qualifications|qualifications|requirements|what we['’]re looking for)"
    r"|(?P<end>what we offer|about the team|benefits|compensation|about (?:the company|us))",
    re.I,
)
_YEARS = re.compile(
    r"(?P<minimum>at least\s+|minimum(?: of)?\s+|no less than\s+)?"
    r"(?P<years>\d+(?:\.\d+)?)\s*(?P<range>\+|[-–—]\s*\d+(?:\.\d+)?|to\s+\d+(?:\.\d+)?)?"
    r"\s*(?:years?|yrs?)\b",
    re.I,
)
_PREFERENCE = re.compile(
    r"\b(?:preferred|preferably|ideally|desirable|bonus)\b|nice[- ]to[- ]have", re.I
)


def experience_requirements(description: str, source: str) -> list[Record]:
    """Return numeric requirements with exact excerpts and their strength.

    Ambiguous alternatives, education durations, maxima, company history and
    preferences cannot establish a strict professional-experience minimum.
    """
    headings = list(_HEADINGS.finditer(description))
    sections = segment(description)
    results = []
    # Public ATS descriptions may be flattened HTML; retain bullet boundaries.
    for clause_match in re.finditer(r"[^\n•▪]+", description):
        for part in re.finditer(
            r"(?:^|\s+-\s+(?!\d)|;)(.*?)(?=\s+-\s+(?!\d)|;|$)", clause_match.group()
        ):
            clause = part.group(1).strip()
            if not clause:
                continue
            start = clause_match.start() + part.start(1)
            for match in _YEARS.finditer(clause):
                position = start + match.start()
                section = next(
                    (h.lastgroup for h in reversed(headings) if h.start() < position), None
                )
                section_kind = next(
                    (item["kind"] for item in sections if item["start"] <= position < item["end"]),
                    "unstructured",
                )
                if section_kind in {"intro", "benefits"}:
                    continue
                before, after = clause[: match.start()], clause[match.end() :]
                if re.search(r"\b(?:up to|no more than|less than|under)\s*$", before, re.I):
                    continue
                if re.search(
                    r"\b(?:degree|education|academic|university|school|course|study|programme|program)\b",
                    before[-55:] + after[:60],
                    re.I,
                ):
                    continue
                if not re.search(
                    r"experience|engineering|development|production|infrastructure|\bsre\b|software|systems|backend|cloud|working|building|product management",
                    after[:160],
                    re.I,
                ):
                    continue
                explicit = bool(
                    match.group("minimum")
                    or re.search(r"\b(?:required|must|require)\b", before[-50:] + after[:50], re.I)
                )
                if section != "required" and section != "preferred" and not explicit:
                    continue
                if re.search(r"\bwe (?:have|bring|offer)\b", before, re.I) or re.search(
                    r"\b(?:our company|our team|founded|established)\b", before, re.I
                ):
                    continue
                strength = "mandatory"
                if (
                    section == "preferred"
                    or _PREFERENCE.search(before[-70:])
                    or re.match(
                        r"\s*(?:of\s+)?experience\s*(?:is\s+)?(?:preferred|desirable|a bonus)\b",
                        after,
                        re.I,
                    )
                ):
                    strength = "preferred"
                elif re.search(r"\b(?:not required|no minimum)\b", clause, re.I):
                    continue
                elif re.search(
                    r"\bor\b.{0,70}\b(?:equivalent|ex-founder|founder|projects?|qualifications?|internships?|graduates?|students?|degrees?|bootcamp)\b",
                    after,
                    re.I,
                ) or re.search(
                    r"\b(?:graduates?|students?|internships?|degrees?)\b.{0,40}\bor\b", before, re.I
                ):
                    strength = "needs_review"
                # In a requirements section, N+ and N-M are minima. Bare N years
                # needs an explicit at-least/required statement to be decisive.
                if not match.group("range") and not explicit:
                    strength = "needs_review" if strength == "mandatory" else strength
                results.append(
                    {
                        "minimum_years": float(match.group("years")),
                        "strength": strength,
                        "quote": clause,
                        "source": source,
                        "method": "explicit_numeric_experience_v1",
                    }
                )
    return results


def unrelated_role(title: str) -> bool:
    return bool(
        re.search(
            r"\b(?:product (?:lead|manager|management)|sales|account executive|recruiter|marketing|designer|people operations|strategy\s*(?:&|and)\s*operations|seller systems)\b",
            title,
            re.I,
        )
    )


def remote_restricted_outside_india(location: str) -> bool:
    if re.search(
        r"\bindia\b|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|noida|delhi|gurugram|gurgaon|\bglobal\b|worldwide|\bapac\b|anywhere",
        location,
        re.I,
    ):
        return False
    countries = r"(?:us|usa|united states|canada|uk|united kingdom|europe|emea|australia|latam)"
    return bool(
        re.search(
            r"\b"
            + countries
            + r"\s*[-,:]?\s*remote\b|\bremote\s+(?:in\s+)?(?:the\s+)?"
            + countries
            + r"\b",
            location,
            re.I,
        )
    )
