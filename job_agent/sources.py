"""Read-only adapters for documented, public employer ATS endpoints."""

from __future__ import annotations

import html
import json
import re
import socket
import ssl
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

from .common import WorkflowError, canonical_url, digest, domain, same_domain, timestamp
from .evidence import remote_restricted_outside_india, unrelated_role
from .types import HTTPClient, Record


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.parts: list[str] = []
        self.embeds: list[str] = []
        self.structured_data: list[str] = []
        self._script: list[str] | None = None
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style"}:
            self._hidden += 1
            if tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json":
                self._script = []
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)
        if tag == "iframe" and attributes.get("src"):
            self.embeds.append(str(attributes["src"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)
            if self._script is not None and tag == "script":
                self.structured_data.append("".join(self._script))
                self._script = None

    def handle_data(self, value: str) -> None:
        if self._script is not None:
            self._script.append(value)
        if not self._hidden:
            self.parts.append(value)


def plain(value: str | None) -> str:
    parser = Page()
    parser.feed(html.unescape(value or ""))
    return " ".join(" ".join(parser.parts).split())


class HTTP:
    def get(self, url: str, *, as_json: bool = False) -> Any:
        domain(url)
        request = Request(
            url,
            headers={
                "User-Agent": "JobAgent/0.3 (read-only public job discovery)",
                "Accept": "application/json" if as_json else "text/html",
            },
        )
        for attempt in range(2):
            try:
                with urlopen(request, timeout=20) as response:
                    # A redirect to a login page is not a verified vacancy.
                    final_url = response.url
                    content_type = response.headers.get("Content-Type", "")
                    body = response.read(8_000_001)
                    if len(body) > 8_000_000:
                        raise WorkflowError(
                            "Source response exceeded 8 MB; verification incomplete."
                        )
                    text = body.decode("utf-8", errors="replace")
                    if as_json:
                        return json.loads(text)
                    return {"text": text, "url": final_url, "content_type": content_type}
            except HTTPError as exc:
                # No retry for rate limiting, authentication, access restrictions or missing pages.
                raise WorkflowError(
                    f"Source HTTP {exc.code}; verification incomplete, not proof of closure."
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt:
                    reason = exc.reason if isinstance(exc, URLError) else exc
                    if isinstance(reason, ssl.SSLCertVerificationError):
                        detail = "TLS certificate verification failed; configure a trusted CA bundle. Certificate verification was not disabled."
                    elif isinstance(reason, socket.gaierror):
                        detail = "DNS resolution failed; check network/proxy configuration."
                    elif (
                        isinstance(reason, PermissionError)
                        or getattr(reason, "winerror", None) == 10013
                    ):
                        detail = "Network access denied by the environment/firewall."
                    elif isinstance(reason, (TimeoutError, socket.timeout)):
                        detail = "Connection timed out."
                    else:
                        detail = type(reason).__name__ + (
                            f" (error {reason.errno})"
                            if isinstance(reason, OSError) and reason.errno
                            else ""
                        )
                    raise WorkflowError(
                        "Source unavailable; verification incomplete: " + detail
                    ) from exc
            except json.JSONDecodeError as exc:
                raise WorkflowError("Source did not return the expected JSON.") from exc


def board_link(source: Record) -> str:
    token = source["board"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise WorkflowError(
            "Board token must contain only letters, numbers, underscores or hyphens."
        )
    hosts = {
        "greenhouse": "job-boards.greenhouse.io",
        "lever": "jobs.lever.co",
        "ashby": "jobs.ashbyhq.com",
        "smartrecruiters": "jobs.smartrecruiters.com",
        "workable": "apply.workable.com",
    }
    if source["provider"] not in hosts:
        raise WorkflowError("Unsupported public ATS provider.")
    return "https://" + hosts[str(source["provider"])] + "/" + str(token)


def ats_target(url: str) -> dict[str, str] | None:
    """Identify a supported board from an exact employer-published ATS URL."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower()
    segments = [unquote(p) for p in parts.path.split("/") if p]
    query = parse_qs(parts.query)
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        if segments[:1] == ["embed"]:
            token = query.get("for", [None])[0]
        else:
            token = segments[0] if segments else None
        provider = "greenhouse"
    elif host == "jobs.lever.co":
        provider, token = "lever", segments[0] if segments else None
    elif host == "jobs.ashbyhq.com":
        provider, token = "ashby", segments[0] if segments else None
    elif host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        provider, token = "smartrecruiters", segments[0] if segments else None
    elif host == "apply.workable.com":
        provider, token = "workable", segments[0] if segments else None
    else:
        return None
    if token and re.fullmatch(r"[A-Za-z0-9_-]+", token):
        return {"provider": provider, "board": token}
    return None


def published_ats_targets(page: Record, base_url: str) -> tuple[Page, list[dict[str, str]]]:
    parser = Page()
    parser.feed(page["text"])
    # Modern careers pages publish exact application URLs in serialized page
    # data. Read only URL literals; do not execute inline scripts or guess tokens.
    decoded = html.unescape(page["text"]).replace("\\/", "/")
    literals = re.findall(
        r"https://(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com)/[^\s\x22\x27<>\\]+",
        decoded,
    )
    targets = {}
    for href in parser.links + parser.embeds + literals:
        target = ats_target(urljoin(base_url, href))
        if target:
            targets[(target["provider"], target["board"])] = target
    return parser, list(targets.values())


def discover_employer_sources(
    company: str,
    website: str,
    careers_url: str,
    tier: str = "GOOD",
    http: HTTPClient | None = None,
    _follow: bool = True,
) -> list[Record]:
    http = http or HTTP()
    if not same_domain(careers_url, domain(website)):
        raise WorkflowError("Careers URL must be on the employer's own domain.")
    page = http.get(careers_url)
    if not same_domain(page["url"], domain(website)):
        raise WorkflowError("Careers page redirected away from the employer.")
    parser, targets = published_ats_targets(page, careers_url)
    sources: list[Record] = [
        {
            "company": company,
            "official_website": website,
            "official_careers_url": careers_url,
            "tier": tier,
            **target,
        }
        for target in targets
    ]
    if not sources and parser.structured_data:
        from .structured import job_postings

        if list(job_postings(parser.structured_data)):
            sources.append(
                {
                    "company": company,
                    "official_website": website,
                    "official_careers_url": careers_url,
                    "tier": tier,
                    "provider": "jobposting",
                    "posting_urls": [careers_url],
                }
            )
    if not sources and _follow:
        checked = {canonical_url(careers_url), canonical_url(page["url"])}
        count = 0
        for href in parser.links:
            candidate = urljoin(page["url"], href)
            try:
                normalized = canonical_url(candidate)
                if (
                    normalized in checked
                    or not same_domain(candidate, domain(website))
                    or not re.search(r"career|jobs|open.positions", urlsplit(candidate).path, re.I)
                ):
                    continue
                checked.add(normalized)
                count += 1
                sources = discover_employer_sources(
                    company, website, candidate, tier, http, _follow=False
                )
                if sources:
                    break
            except (WorkflowError, ValueError):
                pass
            if count >= 3:
                break
    if not sources:
        raise WorkflowError(
            "No supported employer-linked ATS or JobPosting metadata found; no board guessed."
        )
    return sources


def verify_employer_link(source: Record, http: HTTPClient) -> None:
    careers = source["official_careers_url"]
    if not same_domain(careers, domain(source["official_website"])):
        raise WorkflowError("Employer identity must originate from the official employer domain.")
    board_link(source)  # Validate provider and token before fetching anything.
    page = http.get(careers)
    if not same_domain(page["url"], domain(source["official_website"])):
        raise WorkflowError(
            "Careers page redirected away from the employer; supply an official page that links to the ATS."
        )
    _, candidates = published_ats_targets(page, careers)
    for candidate in candidates:
        if candidate and candidate == {"provider": source["provider"], "board": source["board"]}:
            return
    raise WorkflowError(
        "Official employer page did not link to this ATS board. Employer identity remains unverified."
    )


def fetch_board(source: Record, http: HTTPClient | None = None) -> list[Record]:
    http = http or HTTP()
    if source["provider"] == "jobposting":
        from .structured import fetch_jobposting

        return fetch_jobposting(source, http)
    verify_employer_link(source, http)
    from .adapters import ADAPTERS

    adapter = ADAPTERS.get(source["provider"])
    if not adapter:
        raise WorkflowError(
            "Unsupported public provider; review its employer-linked endpoint first."
        )
    items = adapter.fetch(source, http)
    return [normalize(source, item) for item in items if item.get("isListed", True)]


def normalize(source: Record, raw: Record) -> Record:
    provider = source["provider"]
    if provider == "greenhouse":
        url = raw["absolute_url"]
        identity, title, location, description = (
            str(raw["id"]),
            raw["title"],
            raw.get("location", {}).get("name", "Not disclosed"),
            plain(raw.get("content")),
        )
        published, employment = None, "unknown"  # updated_at is not the posting date.
    elif provider == "lever":
        url = raw["hostedUrl"]
        identity, title, location = (
            str(raw["id"]),
            raw["text"],
            raw.get("categories", {}).get("location", "Not disclosed"),
        )
        description = " ".join(
            [
                plain(raw.get("descriptionPlain") or raw.get("description")),
                *[
                    plain(x.get("text", "") + " " + x.get("content", ""))
                    for x in raw.get("lists", [])
                ],
                plain(raw.get("additionalPlain") or raw.get("additional")),
            ]
        )
        published = (
            None  # Do not substitute creation or crawl timestamps for a disclosed posting date.
        )
        employment = raw.get("categories", {}).get("commitment", "unknown")
    elif provider == "smartrecruiters":
        url = raw["postingUrl"]
        identity, title = str(raw["id"]), raw["name"]
        location = ", ".join(
            str(raw.get("location", {}).get(key, "")) for key in ("city", "country")
        )
        description = " ".join(
            plain(item.get("text", ""))
            for item in raw.get("jobAd", {}).get("sections", {}).values()
        )
        published, employment = (
            (raw.get("releasedDate") or "")[:10] or None,
            raw.get("typeOfEmployment", {}).get("label", "unknown"),
        )
    elif provider == "workable":
        url = raw.get("url") or raw["shortlink"]
        identity, title = str(raw.get("shortcode") or raw.get("id") or url), raw["title"]
        location = raw.get("location", "Not disclosed")
        if isinstance(location, dict):
            location = location.get("location_str") or ", ".join(
                str(location.get(key, "")) for key in ("city", "country")
            )
        description = plain(raw.get("description", ""))
        published, employment = (
            (raw.get("published_on") or "")[:10] or None,
            raw.get("employment_type", "unknown"),
        )
    else:
        url = raw["jobUrl"]
        identity, title, location = (
            str(raw.get("id") or urlsplit(url).path.rstrip("/").split("/")[-1]),
            raw["title"],
            raw.get("location", "Not disclosed"),
        )
        description = plain(raw.get("descriptionPlain") or raw.get("descriptionHtml"))
        published, employment = (
            (raw.get("publishedAt") or "")[:10] or None,
            raw.get("employmentType", "unknown"),
        )
    from .structured import employment_type

    kind = employment_type(employment, title)
    checked = timestamp()
    gates = {
        "employer_identity": {
            "status": "PASS",
            "reason": "Official employer page links to this exact ATS board.",
            "source": source["official_careers_url"],
            "checked_at": checked,
        },
        "live_vacancy": {
            "status": "PASS",
            "reason": "Listed by the employer-linked public ATS API at verification time.",
            "source": url,
            "checked_at": checked,
        },
        "application_route": {
            "status": "UNKNOWN",
            "reason": "Review the individual application form and required declarations before marking the route usable.",
            "source": url,
            "checked_at": checked,
        },
    }
    return {
        "company": source["company"],
        "official_website": source["official_website"],
        "tier": source.get("tier", "GOOD"),
        "role": title,
        "job_id": identity,
        "canonical_url": canonical_url(url),
        "application_url": raw.get("applyUrl") or raw.get("application_url") or url,
        "location": location,
        "employment_type": kind,
        "posting_date": published,
        "deadline": None,
        "workplace_type": "remote"
        if raw.get("isRemote") is True or str(raw.get("workplaceType", "")).lower() == "remote"
        else "unknown",
        "description": description,
        "last_verified_at": checked,
        "provider": provider,
        "provider_id": identity,
        "source_config": source,
        "source_content_hash": digest(
            {k: v for k, v in raw.items() if k not in {"updated_at", "updatedAt", "createdAt"}}
        ),
        "primary_source_urls": [url, source["official_careers_url"]],
        "eligibility_gates": gates,
    }


def relevant_lead(job: Record, broaden: bool = False) -> bool:
    title = job["role"]
    if (
        unrelated_role(title)
        or re.search(r"\bcustomer support\b", title, re.I)
        or remote_restricted_outside_india(job.get("location", ""))
    ):
        return False
    pattern = r"software|cloud|devops|backend|back.end|systems|\bsde\b"
    if broaden:
        pattern += r"|platform|infrastructure|automation|reliability|graduate|engineering intern"
    return bool(re.search(pattern, title, re.I))
