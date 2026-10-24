"""Fingerprint potential cross-posts without erasing distinct vacancies."""

from __future__ import annotations

import hashlib
import re

from .common import canonical_url, domain
from .types import Record


def simhash(text: str) -> int:
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    grams = {normalized[index : index + 3] for index in range(max(1, len(normalized) - 2))}
    weights = [0] * 64
    for gram in grams:
        value = int.from_bytes(hashlib.sha256(gram.encode()).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    return sum(1 << bit for bit, weight in enumerate(weights) if weight > 0)


def fingerprint(job: Record) -> int:
    return simhash(" ".join(str(job.get(key, "")) for key in ("role", "location", "description")))


def same_canonical_posting(first: Record, second: Record) -> bool:
    return domain(first["official_website"]) == domain(
        second["official_website"]
    ) and canonical_url(first["canonical_url"]) == canonical_url(second["canonical_url"])


def potential_cross_post(first: Record, second: Record) -> bool:
    return (
        domain(first["official_website"]) == domain(second["official_website"])
        and first["location"].casefold() == second["location"].casefold()
        and (fingerprint(first) ^ fingerprint(second)).bit_count() <= 3
    )
