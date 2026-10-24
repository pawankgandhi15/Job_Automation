"""Public GET transport with ETags, bounded retries and per-host isolation."""

from __future__ import annotations

import json
import random
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .common import WorkflowError, digest, domain, read_json, write_json
from .types import Record


def retry_after(value: str | None, clock: float | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(
                0.0,
                parsedate_to_datetime(value).timestamp()
                - (time.time() if clock is None else clock),
            )
        except (ValueError, TypeError, OverflowError):
            return None


class ResilientHTTP:
    def __init__(self, cache: Path, *, spacing: float = 1.0, max_wait: float = 30.0) -> None:
        self.cache, self.spacing, self.max_wait = cache, spacing, max_wait
        self.hosts: dict[str, Record] = {}
        self.lock = threading.Lock()

    def get(self, url: str, *, as_json: bool = False) -> Any:
        host = domain(url)
        with self.lock:
            state = self.hosts.setdefault(
                host, {"lock": threading.Lock(), "last": 0.0, "failures": [], "open_until": 0.0}
            )
        with state["lock"]:
            if time.monotonic() < state["open_until"]:
                raise WorkflowError(
                    "Source circuit is open; verification incomplete, not proof of closure."
                )
            path = self.cache / (digest(url) + ".json")
            cached = read_json(path) if path.is_file() else None
            headers = {
                "User-Agent": "JobAgent/1.0 public read-only discovery",
                "Accept": "application/json" if as_json else "text/html",
            }
            if cached and cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            for attempt in range(4):
                time.sleep(max(0.0, self.spacing - (time.monotonic() - state["last"])))
                state["last"] = time.monotonic()
                try:
                    with urlopen(Request(url, headers=headers), timeout=20) as response:
                        body = response.read(8_000_001)
                        if len(body) > 8_000_000:
                            raise WorkflowError(
                                "Source response exceeded 8 MB; verification incomplete."
                            )
                        text = body.decode("utf-8", errors="replace")
                        value = (
                            json.loads(text)
                            if as_json
                            else {
                                "text": text,
                                "url": response.url,
                                "content_type": response.headers.get("Content-Type", ""),
                            }
                        )
                        write_json(path, {"etag": response.headers.get("ETag"), "value": value})
                        state["failures"] = []
                        return value
                except HTTPError as exc:
                    if exc.code == 304 and cached:
                        state["failures"] = []
                        return cached["value"]
                    if exc.code not in {429, 503}:
                        raise WorkflowError(
                            f"Source HTTP {exc.code}; verification incomplete, not proof of closure."
                        ) from exc
                    delay = retry_after(exc.headers.get("Retry-After"))
                    if delay is None:
                        delay = min(self.max_wait, 2**attempt + random.uniform(0, 1))
                    failure: Exception = exc
                except (URLError, TimeoutError, OSError) as exc:
                    failure = exc
                    delay = min(self.max_wait, 2**attempt + random.uniform(0, 1))
                except json.JSONDecodeError as exc:
                    raise WorkflowError(
                        "Source did not return expected JSON; verification incomplete."
                    ) from exc
                state["failures"] = [
                    item for item in state["failures"] if time.monotonic() - item < 60
                ] + [time.monotonic()]
                if len(state["failures"]) >= 5:
                    state["open_until"] = time.monotonic() + 300
                if delay > self.max_wait:
                    state["open_until"] = max(state["open_until"], time.monotonic() + delay)
                    raise WorkflowError(
                        "Source requested a longer Retry-After; deferred without shortening its delay."
                    ) from failure
                if attempt == 3 or time.monotonic() < state["open_until"]:
                    raise WorkflowError(
                        "Source unavailable after bounded attempts; verification incomplete, not proof of closure."
                    ) from failure
                time.sleep(delay)
        raise WorkflowError("Source verification incomplete.")
