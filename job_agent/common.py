from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .types import Record

IST = timezone(timedelta(hours=5, minutes=30), "IST")


class WorkflowError(ValueError):
    pass


def now() -> datetime:
    return datetime.now(IST)


def timestamp(value: datetime | None = None) -> str:
    return (value or now()).astimezone(IST).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise WorkflowError("Timestamps must include a timezone offset.")
    return parsed


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    replace_file(temp, path)


def replace_file(source: Path, target: Path) -> None:
    """Publish a complete file, preserving Windows destination permissions."""
    if sys.platform == "win32" and target.exists():
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        replace = kernel.ReplaceFileW
        replace.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        replace.restype = ctypes.c_int
        if replace(str(target.resolve()), str(source.resolve()), None, 0, None, None):
            return
        error = ctypes.get_last_error()
        if error != 2:  # The destination may have disappeared between checks.
            raise ctypes.WinError(error)
    source.replace(target)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def domain(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise WorkflowError("Evidence and application URLs must be HTTPS URLs without credentials.")
    return parts.hostname.lower().removeprefix("www.")


def same_domain(url: str, official_domain: str) -> bool:
    host = domain(url)
    return host == official_domain or host.endswith("." + official_domain)


def canonical_url(url: str) -> str:
    domain(url)
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}
    ]
    # Job-identifying and unknown query parameters are deliberately preserved.
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(sorted(query, key=lambda pair: pair[0])),
            "",
        )
    )


def keys(job: Record, aliases: Mapping[str, str] | None = None) -> tuple[str, str]:
    company = domain(job["official_website"])
    company = (aliases or {}).get(company, company)
    if job.get("employing_entity"):
        company += ":" + job["employing_entity"].strip().casefold()
    identity = str(job.get("job_id") or "").strip()
    if not identity:
        identity = (
            "fallback:"
            + canonical_url(job["canonical_url"])
            + ":"
            + job["role"].casefold()
            + ":"
            + job["location"].casefold()
        )
    return company, company + "|" + identity


def material_job(job: Record) -> Record:
    """Verification timestamps and transport metadata do not change approved claims."""
    ignored = {
        "last_verified_at",
        "first_seen_at",
        "provider",
        "provider_id",
        "source_config",
        "live_checked_at",
    }
    result = {k: v for k, v in job.items() if k not in ignored}
    if "eligibility_gates" in result:
        result["eligibility_gates"] = {
            k: {a: b for a, b in v.items() if a != "checked_at"}
            for k, v in result["eligibility_gates"].items()
        }
    return result
