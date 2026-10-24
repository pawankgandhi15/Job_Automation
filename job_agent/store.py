from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .common import WorkflowError, digest, keys, material_job, timestamp
from .migrations import run_migrations
from .observability import ActivityLog, DomainEvent, EventBus, correlation_id
from .types import Record


class Store:
    @classmethod
    def readonly(cls, path: Path) -> "Store":
        store = cls.__new__(cls)
        store.aliases = {}
        store.db = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None
        )
        store.db.row_factory = sqlite3.Row
        return store

    def __init__(self, path: Path, aliases: dict[str, str] | None = None) -> None:
        self.aliases = aliases or {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA cache_size=-32000")
            self.applied_migrations = run_migrations(self.db, path)
            self.bus = EventBus()
            self.bus.subscribe(ActivityLog(path.parent / "activity.jsonl"))
            self._pending_events: list[DomainEvent] = []
            self._initialize_audit()
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        pending_start = len(self._pending_events)
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            del self._pending_events[pending_start:]
            raise
        committed = self._pending_events[pending_start:]
        del self._pending_events[pending_start:]
        for event in committed:
            self.bus.publish(event)

    def event(self, job_id: int | None, action: str, result: str, evidence: Any = None) -> None:
        if not self.db.in_transaction:
            with self.transaction():
                self.event(job_id, action, result, evidence)
            return
        at = timestamp()
        cursor = self.db.execute(
            "INSERT INTO events(at,opportunity_id,action,result,evidence) VALUES(?,?,?,?,?)",
            (at, job_id, action, result, json.dumps(evidence or {}, ensure_ascii=False)),
        )
        number = int(cursor.lastrowid or 0)
        self._chain_event(number)
        self._pending_events.append(
            DomainEvent(number, job_id, action, result, at, correlation_id())
        )
        if action in {"SOURCE_ANOMALY", "HANDOFF_PREPARED", "STRONG_MATCH", "FOLLOW_UP_DUE"}:
            self.db.execute(
                "INSERT INTO notifications(event_id,kind,message,created_at) VALUES(?,?,?,?)",
                (number, action, result, at),
            )

    def _chain_event(self, number: int) -> None:
        previous = self.db.execute(
            "SELECT event_hash FROM event_integrity ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        event = dict(self.db.execute("SELECT * FROM events WHERE id=?", (number,)).fetchone())
        self.db.execute(
            "INSERT INTO event_integrity VALUES(?,?,?)",
            (number, previous_hash, digest({"previous_hash": previous_hash, "event": event})),
        )

    def _initialize_audit(self) -> None:
        if not self.db.execute("SELECT 1 FROM event_integrity LIMIT 1").fetchone():
            with self.transaction():
                for row in self.db.execute("SELECT id FROM events ORDER BY id").fetchall():
                    self._chain_event(row[0])
        if not self.verify_audit()["valid"]:
            raise WorkflowError(
                "Audit-chain verification failed. Preserve the database and investigate; no action is authorized."
            )

    def verify_audit(self) -> Record:
        previous = "0" * 64
        count = 0
        for row in self.db.execute("SELECT * FROM events ORDER BY id"):
            stored = self.db.execute(
                "SELECT * FROM event_integrity WHERE event_id=?", (row["id"],)
            ).fetchone()
            expected = digest({"previous_hash": previous, "event": dict(row)})
            if (
                not stored
                or stored["previous_hash"] != previous
                or stored["event_hash"] != expected
            ):
                return {"valid": False, "event_id": row["id"], "checked_events": count}
            previous = expected
            count += 1
        if self.db.execute("SELECT COUNT(*) FROM event_integrity").fetchone()[0] != count:
            return {
                "valid": False,
                "checked_events": count,
                "reason": "Event/chain length mismatch",
            }
        return {"valid": True, "checked_events": count, "head": previous}

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def upsert(self, payload: Record) -> tuple[str, bool]:
        company_key, opportunity_key = keys(payload, self.aliases)
        alias = self.db.execute(
            "SELECT opportunity_id FROM opportunity_aliases WHERE alias_key=?", (opportunity_key,)
        ).fetchone()
        if alias:
            return f"JOB-{alias[0]:03d}", False
        if payload.get("provider"):
            from .deduplication import same_canonical_posting

            for candidate in self.db.execute(
                "SELECT id,payload FROM opportunities WHERE company_key=?", (company_key,)
            ):
                prior = json.loads(candidate["payload"])
                if (
                    prior.get("provider")
                    and prior["provider"] != payload["provider"]
                    and prior["role"].casefold() == payload["role"].casefold()
                    and prior["location"].casefold() == payload["location"].casefold()
                    and same_canonical_posting(prior, payload)
                ):
                    self.db.execute(
                        "INSERT OR IGNORE INTO opportunity_aliases VALUES(?,?,?,?)",
                        (opportunity_key, candidate["id"], payload["canonical_url"], timestamp()),
                    )
                    return f"JOB-{candidate['id']:03d}", False
        hashed = digest(material_job(payload))
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM opportunities WHERE opportunity_key=?", (opportunity_key,)
            ).fetchone()
            if row:
                changed = row["material_hash"] != hashed
                self.db.execute(
                    "UPDATE opportunities SET payload=?,material_hash=?,last_verified_at=? WHERE id=?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        hashed,
                        payload.get("last_verified_at"),
                        row["id"],
                    ),
                )
                if changed:
                    self.db.execute(
                        "UPDATE opportunities SET package=NULL,package_hash=NULL,evaluation=NULL WHERE id=?",
                        (row["id"],),
                    )
                    old_payload = json.loads(row["payload"])
                    old_material, new_material = material_job(old_payload), material_job(payload)
                    changed_fields = {
                        k
                        for k in old_material.keys() | new_material.keys()
                        if old_material.get(k) != new_material.get(k)
                    }
                    if changed_fields <= {"contact"}:
                        self.db.execute(
                            "UPDATE approvals SET invalidated_at=? WHERE opportunity_id=? AND channel IN ('outreach','follow_up') AND consumed_at IS NULL",
                            (timestamp(), row["id"]),
                        )
                    else:
                        self.db.execute(
                            "UPDATE approvals SET invalidated_at=? WHERE opportunity_id=? AND consumed_at IS NULL",
                            (timestamp(), row["id"]),
                        )
                    auto_rejection = (
                        row["state"] == "REJECTED"
                        and row["evaluation"]
                        and json.loads(row["evaluation"])["status"] == "REJECTED"
                        and (json.loads(row["status_check"]) if row["status_check"] else {}).get(
                            "status"
                        )
                        not in {"rejected", "withdrawn", "closed"}
                        and not self.db.execute(
                            "SELECT 1 FROM attempts WHERE opportunity_id=?", (row["id"],)
                        ).fetchone()
                    )
                    if auto_rejection or row["state"] not in {
                        "EXECUTING",
                        "SUBMISSION_UNCERTAIN",
                        "SUBMITTED_CONFIRMED",
                        "OUTREACH_SENT_CONFIRMED",
                        "PARTIAL_SUCCESS",
                        "REPLIED",
                        "REJECTED",
                        "INTERVIEW",
                        "WITHDRAWN",
                        "CLOSED",
                        "DISMISSED",
                    }:
                        self.db.execute(
                            "UPDATE opportunities SET state='DISCOVERED' WHERE id=?", (row["id"],)
                        )
                    self.event(row["id"], "OPPORTUNITY_UPDATED", "APPROVALS_INVALIDATED")
                return f"JOB-{row['id']:03d}", changed
            cursor = self.db.execute(
                "INSERT INTO opportunities(company_key,opportunity_key,payload,material_hash,first_seen_at,last_verified_at) VALUES(?,?,?,?,?,?)",
                (
                    company_key,
                    opportunity_key,
                    json.dumps(payload, ensure_ascii=False),
                    hashed,
                    timestamp(),
                    payload.get("last_verified_at"),
                ),
            )
            self.event(cursor.lastrowid, "DISCOVERED", "RECORDED")
            return f"JOB-{cursor.lastrowid:03d}", True

    def get(self, job_id: str | int) -> Record:
        try:
            number = int(str(job_id).removeprefix("JOB-"))
        except ValueError as exc:
            raise WorkflowError("Use a stable ID such as JOB-001.") from exc
        row = self.db.execute("SELECT * FROM opportunities WHERE id=?", (number,)).fetchone()
        if not row:
            raise WorkflowError(f"Unknown opportunity: {job_id}")
        result = dict(row)
        result["opportunity_id"] = f"JOB-{number:03d}"
        for key in ("payload", "evaluation", "package", "status_check"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def all(self) -> list[Record]:
        return [
            self.get(row[0]) for row in self.db.execute("SELECT id FROM opportunities ORDER BY id")
        ]

    def invalidate(self, number: int, reason: str) -> None:
        self.db.execute(
            "UPDATE approvals SET invalidated_at=? WHERE opportunity_id=? AND consumed_at IS NULL",
            (timestamp(), number),
        )
        self.event(number, "APPROVAL_INVALIDATED", reason)

    def export(self) -> Record:
        return {
            "exported_at": timestamp(),
            "settings": {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT * FROM settings")},
            "opportunities": self.all(),
            **{
                name: [dict(r) for r in self.db.execute(f"SELECT * FROM {name}")]
                for name in (
                    "approvals",
                    "attempts",
                    "reports",
                    "reported_companies",
                    "events",
                    "discovery_runs",
                    "source_checks",
                    "daily_companies",
                )
            },
        }

    def runs(self, limit: int = 10) -> list[Record]:
        records = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM discovery_runs ORDER BY rowid DESC LIMIT ?", (limit,)
            )
        ]
        for record in records:
            record["summary"] = json.loads(record["summary"])
            record["sources"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM source_checks WHERE run_id=? ORDER BY id", (record["id"],)
                )
            ]
        return records

    def source_status(self) -> list[Record]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM source_checks WHERE id IN (SELECT MAX(id) FROM source_checks GROUP BY source_key) ORDER BY company"
            )
        ]

    def schema_status(self) -> Record:
        return {
            "versions": [
                row[0]
                for row in self.db.execute("SELECT version FROM schema_migrations ORDER BY version")
            ],
            "journal_mode": self.db.execute("PRAGMA journal_mode").fetchone()[0],
            "foreign_keys": bool(self.db.execute("PRAGMA foreign_keys").fetchone()[0]),
            "search_index_rows": self.db.execute(
                "SELECT COUNT(*) FROM opportunities_fts"
            ).fetchone()[0],
        }

    def search(
        self,
        query: str = "",
        *,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
        tier: str | None = None,
        family: str | None = None,
    ) -> list[Record]:
        if not isinstance(query, str) or len(query) > 500:
            raise WorkflowError("Search query must be text of at most 500 characters.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise WorkflowError("Search requires a limit of 1–1000 and a nonnegative offset.")
        terms = re.findall(r"\w+", query, re.UNICODE)
        if query.strip() and not terms:
            return []
        clauses, parameters = [], []
        source, ordering = "opportunities o", "o.id"
        if terms:
            # Treat user input as literal terms, never as an FTS query program.
            source = "opportunities_fts CROSS JOIN opportunities o ON opportunities_fts.rowid=o.id"
            clauses.append("opportunities_fts MATCH ?")
            parameters.append(" AND ".join('"' + term + '"' for term in terms))
            ordering = "opportunities_fts.rowid"
        if state:
            clauses.append("o.state=?")
            parameters.append(state)
        if tier:
            if tier not in {"GOOD", "BETTER", "GREAT"}:
                raise WorkflowError("Unknown company tier.")
            clauses.append("coalesce(json_extract(o.payload,'$.tier'),'GOOD')=?")
            parameters.append(tier)
        if family:
            from .policy import role_family

            if family not in {"cloud", "backend", "sde"}:
                raise WorkflowError("Unknown role family.")
            self.db.create_function("job_role_family", 1, role_family, deterministic=True)
            clauses.append("job_role_family(json_extract(o.payload,'$.role'))=?")
            parameters.append(family)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = (
            "SELECT o.id,o.state,o.company_key,json_extract(o.payload,'$.company') AS company,json_extract(o.payload,'$.role') AS role,json_extract(o.payload,'$.location') AS location,json_extract(o.evaluation,'$.match_score') AS match_score"
            + " FROM "
            + source
            + where
            + " ORDER BY "
            + ordering
            + " LIMIT ? OFFSET ?"
        )
        return [
            {**dict(row), "opportunity_id": f"JOB-{row['id']:03d}"}
            for row in self.db.execute(sql, [*parameters, limit, offset])
        ]
