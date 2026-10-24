"""Different employers per day, with persistent selection history and no invented quota."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .store import Store
from .types import Record

if TYPE_CHECKING:
    from .services.context import AgentContext

from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .common import WorkflowError, domain, keys, now, parse_time, read_json, timestamp, write_json
from .review import lead_priority
from .sources import discover_employer_sources


def refresh_source_pool(agent: AgentContext) -> Record:
    path = agent.root / "config" / "employers.json"
    employers = read_json(path).get("employers", []) if path.is_file() else []
    known = {
        (
            domain(source["official_website"]),
            source["provider"],
            source.get("board"),
            tuple(source.get("posting_urls", [])),
        )
        for source in agent.settings["sources"]
    }
    checks, added = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = [
            (
                employer,
                pool.submit(
                    discover_employer_sources,
                    employer["company"],
                    employer["official_website"],
                    employer["official_careers_url"],
                    employer.get("tier", "GOOD"),
                    agent.http,
                ),
            )
            for employer in employers
        ]
        for employer, future in tasks:
            try:
                found = future.result()
                for source in found:
                    identity = (
                        domain(source["official_website"]),
                        source["provider"],
                        source.get("board"),
                        tuple(source.get("posting_urls", [])),
                    )
                    if identity not in known:
                        agent.settings["sources"].append(source)
                        known.add(identity)
                        added.append(source)
                checks.append(
                    {
                        "company": employer["company"],
                        "status": "SUPPORTED_LINK_FOUND",
                        "source": employer["official_careers_url"],
                    }
                )
            except (WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
                checks.append(
                    {
                        "company": employer["company"],
                        "status": "REVIEW_NEEDED",
                        "source": employer["official_careers_url"],
                        "reason": str(exc),
                    }
                )
    if added:
        write_json(agent.root / "config" / "settings.json", agent.settings)
        agent.store.event(
            None, "SOURCE_POOL_EXPANDED", "EMPLOYER_LINKS_VERIFIED", {"sources": added}
        )
    result = {
        "at": timestamp(),
        "added": added,
        "checks": checks,
        "configured_source_count": len(agent.settings["sources"]),
    }
    agent.store.set_setting("source_pool_check", result)
    write_json(agent.root / "data" / "source-pool-check.json", result)
    return result


def diverse_leads(
    leads: list[Record],
    preferred: str,
    known_companies: set[str],
    budget: int,
    cap: int,
    existing_counts: dict[str, int] | None = None,
    aliases: dict[str, str] | None = None,
) -> list[Record]:
    """Round-robin employers so a large board cannot consume the daily budget."""
    grouped: dict[str, list[Record]] = {}
    for lead in sorted(leads, key=lambda item: lead_priority(item, preferred)):
        company = keys(lead, aliases)[0]
        grouped.setdefault(company, []).append(lead)
    companies = sorted(
        grouped,
        key=lambda key: (
            grouped[key][0]["company"].casefold() != preferred.casefold(),
            key in known_companies,
            key,
        ),
    )
    queues = [
        deque(grouped[company][: max(0, cap - (existing_counts or {}).get(company, 0))])
        for company in companies
    ]
    queues = [queue for queue in queues if queue]
    selected: list[Record] = []
    while queues and len(selected) < budget:
        remaining = []
        for queue in queues:
            if len(selected) >= budget:
                break
            selected.append(queue.popleft())
            if queue:
                remaining.append(queue)
        queues = remaining
    return selected


def daily_selection(
    store: Store, rows: list[Record], limit: int = 5, day: str | None = None, preferred: str = "IBM"
) -> Record:
    day = day or now().date().isoformat()
    # Start with companies actually present in historical delivered reports.
    historical = {row[0] for row in store.db.execute("SELECT company_key FROM reported_companies")}
    previous = {
        row[0]
        for row in store.db.execute("SELECT company_key FROM daily_companies WHERE day<>?", (day,))
    }
    current = [
        dict(row)
        for row in store.db.execute(
            "SELECT * FROM daily_companies WHERE day=? ORDER BY position", (day,)
        )
    ]
    selected = {row["company_key"] for row in current}
    candidates: dict[str, Record] = {}
    for row in rows:
        evaluation = row.get("evaluation") or {}
        if row["state"] not in {
            "NEEDS_CLARIFICATION",
            "AWAITING_APPROVAL",
            "READY_FOR_REVIEW",
            "APPROVED",
        } or evaluation.get("status") in {"REJECTED", "BELOW_THRESHOLD"}:
            continue
        if (
            evaluation.get("gates", {}).get("employer_identity", {}).get("status") != "PASS"
            or evaluation.get("gates", {}).get("live_vacancy", {}).get("status") != "PASS"
        ):
            continue
        checked = row["payload"].get("last_verified_at")
        if not checked or not 0 <= (now() - parse_time(checked)).total_seconds() <= 86400:
            continue
        if row["company_key"] in historical | previous | selected:
            continue
        existing = candidates.get(row["company_key"])
        if not existing or lead_priority(row["payload"], preferred) < lead_priority(
            existing["payload"], preferred
        ):
            candidates[row["company_key"]] = row
    ordered = sorted(
        candidates.values(),
        key=lambda row: (lead_priority(row["payload"], preferred), row["company_key"]),
    )
    with store.transaction():
        for row in ordered[: max(0, limit - len(current))]:
            store.db.execute(
                "INSERT INTO daily_companies(day,company_key,company_domain,position,selected_at) VALUES(?,?,?,?,?)",
                (
                    day,
                    row["company_key"],
                    domain(row["payload"]["official_website"]),
                    len(current),
                    timestamp(),
                ),
            )
            current.append(
                {
                    "company_key": row["company_key"],
                    "company_domain": domain(row["payload"]["official_website"]),
                }
            )
        store.event(
            None,
            "DAILY_COMPANY_SELECTION",
            day,
            {"domains": [item["company_domain"] for item in current], "target": limit},
        )
    return {
        "date": day,
        "domains": [item["company_domain"] for item in current],
        "target": limit,
        "note": "New companies to review, not approved applications. Previously selected or reported companies are not recycled to fill the target.",
    }


def current_daily_selection(store: Store) -> Record:
    rows = [
        dict(row)
        for row in store.db.execute(
            "SELECT * FROM daily_companies WHERE day=? ORDER BY position",
            (now().date().isoformat(),),
        )
    ]
    return {
        "date": now().date().isoformat(),
        "domains": [row["company_domain"] for row in rows],
        "target": store.setting("daily_company_target", 5),
    }
