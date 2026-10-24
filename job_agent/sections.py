"""Two-pass job-description section boundaries with exact source offsets."""

from __future__ import annotations

import re

from .types import Record

HEADINGS = re.compile(
    r"\b(?P<minimum>minimum qualifications|basic qualifications|required qualifications|requirements|what you['’]ll bring)|\b(?P<preferred>preferred qualifications|nice[- ]to[- ]haves?|bonus points)|\b(?P<responsibilities>responsibilities|what you['’]ll do)|\b(?P<intro>about us|about the company|who we are)|\b(?P<benefits>benefits|equal opportunity|what we offer|compensation)",
    re.I,
)


def segment(description: str) -> list[Record]:
    headings = list(HEADINGS.finditer(description))
    result = []
    if not headings:
        return [{"kind": "unstructured", "start": 0, "end": len(description), "text": description}]
    if headings[0].start():
        result.append(
            {
                "kind": "intro",
                "start": 0,
                "end": headings[0].start(),
                "text": description[: headings[0].start()],
            }
        )
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(description)
        result.append(
            {
                "kind": heading.lastgroup,
                "start": heading.end(),
                "end": end,
                "text": description[heading.end() : end],
            }
        )
    return result
