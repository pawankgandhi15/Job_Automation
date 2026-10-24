"""Concise employer-JD highlights, separate from candidate fit assessments."""

from __future__ import annotations

import re

from .common import digest
from .types import Record

HEADINGS = re.compile(
    r"\b(?P<about>about (?:the |this )?role|role overview)\b"
    r"|\b(?P<duties>what you(?:['’]ll| will) (?:be doing|be responsible for|do|own)(?: \((?:Core )?Responsibilities\))?|(?-i:Responsibilities|RESPONSIBILITIES))"
    r"|\b(?-i:(?=[A-Z]))(?P<required>minimum (?:requirements|qualifications)|basic qualifications|required qualifications|"
    r"what we(?:['’]re| are) looking for|what we look for|you might be a good fit if(?: you)?|(?-i:Requirements|REQUIREMENTS))\b"
    r"|\b(?-i:(?=[A-Z]))(?P<preferred>preferred qualifications|nice[ -]to[ -]haves?|good to have|bonus points)\b"
    r"|\b(?P<end>what we offer|how we work|what to expect|why join|why sarvam|pay and benefits|in-office expectations|about the team|the challenges)\b",
    re.I,
)
SKILLS = [
    ("Python", r"\bpython\b"),
    ("Go", r"\b(?:golang|Go)\b"),
    ("TypeScript", r"\btypescript\b"),
    ("JavaScript", r"\bjavascript\b"),
    ("Java", r"\bjava\b"),
    ("Ruby", r"\bruby\b"),
    ("Rust", r"\brust\b"),
    ("SQL", r"\bsql\b"),
    ("PostgreSQL", r"\bpostgres(?:ql)?\b"),
    ("Redis", r"\bredis\b"),
    ("MySQL", r"\bmysql\b"),
    ("MongoDB", r"\bmongo(?:db)?\b"),
    ("Linux", r"\blinux\b"),
    ("Git", r"\bgit\b"),
    ("Docker", r"\bdocker\b"),
    ("Kubernetes", r"\bkubernetes\b"),
    ("AWS", r"\baws\b"),
    ("Terraform", r"\bterraform\b"),
    ("CI/CD", r"\bci/cd\b"),
    ("Distributed systems", r"\bdistributed[ -]systems\b"),
    ("Networking", r"\bnetworking\b"),
    ("Concurrency", r"\bconcurrency\b"),
    ("Observability", r"\bobservability\b"),
]
PREFERRED = re.compile(
    r"\b(?:preferred|preferably|ideally|bonus|not required)\b|nice[ -]to[ -]have", re.I
)


def chunks(text: str) -> list[str]:
    return [
        part.strip(" :-\n\t•▪")
        for part in re.split(
            r"\s*[•▪]\s*|\s+-\s+|[\r\n]+|(?<=[.!?])\s+(?=[A-Z0-9])|"
            r"\s+(?=(?:You have|Experience with|Experience proactively|Empathetic,|The ability to|Excellent oral|Build |Design |Debug |Improve |Partner |Collaborate |Mentor |Ensure |Map |Own )\b)",
            text,
        )
        if len(part.strip(" :-\n\t•▪")) >= 20
    ]


def role_details(job: Record, reviewed: Record | None = None) -> Record:
    """Use a reviewed summary only while the exact description still matches.

    New public-feed JDs fall back to excerpts from recognized headings. Other
    analyst notes are not exported as employer requirements or inferred skills.
    """
    source = job["canonical_url"]
    description = job.get("description") or ""
    result = {
        "summary": None,
        "requirements": [],
        "skills": [],
        "preferred": [],
        "stack": [],
        "source": source,
        "method": "unavailable",
    }
    brief = (reviewed or {}).get("roles", {}).get(source)
    if brief and description and brief.get("description_sha256") == digest(description):
        result.update(summary=brief.get("summary"), method="reviewed")
        for field in ("requirements", "skills", "preferred", "stack"):
            result[field] = list(brief.get(field, []))[: 8 if field in {"skills", "stack"} else 3]
        return result
    if job.get("provider") not in {"greenhouse", "lever", "ashby", "jobposting"} or not description:
        return result
    matches = list(HEADINGS.finditer(description))
    sections: dict[str | None, list[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(description)
        sections.setdefault(match.lastgroup, []).append(description[match.end() : end].strip(" :"))
    summaries = chunks(" ".join(sections.get("about") or sections.get("duties") or []))
    if summaries:
        summary = summaries[0]
        result["summary"] = (
            summary if len(summary) <= 300 else summary[:297].rsplit(" ", 1)[0] + "…"
        )
    required = chunks(" ".join(sections.get("required", [])))
    result["requirements"] = [
        part for part in required if len(part) <= 900 and not PREFERRED.search(part)
    ][:3]
    result["preferred"] = [
        part for part in chunks(" ".join(sections.get("preferred", []))) if len(part) <= 900
    ][:2]
    # Keywords are a navigation aid. Alternatives and qualifiers remain in the
    # requirements text; preferred-only clauses never populate the skill tags.
    skill_text = " ".join(part for part in required if not PREFERRED.search(part))
    result["skills"] = [
        name
        for name, pattern in SKILLS
        if re.search(pattern, skill_text, re.I if name != "Go" else 0)
    ][:8]
    if result["summary"] or result["requirements"] or result["preferred"]:
        result["method"] = "jd_excerpts"
    return result
