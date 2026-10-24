"""Checksum-verified, atomic SQLite migrations with consistent pre-upgrade backups."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import WorkflowError


class MigrationError(WorkflowError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def migration_files(directory: Path) -> list[Migration]:
    result = []
    versions: set[int] = set()
    for path in sorted(directory.glob("*.sql")):
        match = re.fullmatch(r"(\d{3,})_[a-z0-9_]+\.sql", path.name)
        if not match or int(match[1]) <= 0 or int(match[1]) in versions:
            raise MigrationError("Invalid or duplicate migration version: " + path.name)
        version = int(match[1])
        versions.add(version)
        sql = path.read_text(encoding="utf-8")  # Normalize CRLF to LF on every OS.
        result.append(
            Migration(version, path.name, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
        )
    if not result:
        raise MigrationError("No packaged database migrations found.")
    return sorted(result, key=lambda item: item.version)


def _applied(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in connection.execute("SELECT version,name,checksum FROM schema_migrations")
    }


def _pending(connection: sqlite3.Connection, files: list[Migration]) -> list[Migration]:
    applied = _applied(connection)
    available = {item.version: item for item in files}
    for version, (name, checksum) in applied.items():
        item = available.get(version)
        if not item or item.name != name or item.checksum != checksum:
            raise MigrationError(
                f"Migration v{version} is missing or has a checksum/name mismatch. Restore the original migration; the ledger was not upgraded."
            )
    if applied and any(
        item.version < max(applied) and item.version not in applied for item in files
    ):
        raise MigrationError("An unapplied migration precedes the recorded database version.")
    return [item for item in files if item.version not in applied]


def _statements(sql: str) -> list[str]:
    result, buffer = [], ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            plain = re.sub(r"/\*.*?\*/|--[^\n]*", "", buffer, flags=re.S).lstrip()
            first = plain.split(None, 1)[0].upper().rstrip(";") if plain else ""
            if first in {
                "BEGIN",
                "COMMIT",
                "ROLLBACK",
                "END",
                "SAVEPOINT",
                "RELEASE",
                "VACUUM",
                "ATTACH",
                "DETACH",
                "PRAGMA",
            }:
                raise MigrationError(
                    "Migration SQL cannot control transactions, connections or PRAGMAs."
                )
            result.append(buffer)
            buffer = ""
    if re.sub(r"/\*.*?\*/|--[^\n]*", "", buffer, flags=re.S).strip():
        raise MigrationError("Migration has incomplete SQL; statements must end with a semicolon.")
    return result


def _backup(connection: sqlite3.Connection, database: Path) -> Path:
    directory = database.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = (
        directory / f"{database.stem}-pre-migration-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    )
    target = sqlite3.connect(destination)
    try:
        connection.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise MigrationError("Pre-migration backup failed its integrity check.")
    finally:
        target.close()
    return destination


def run_migrations(
    connection: sqlite3.Connection, database: Path, directory: Path | None = None
) -> list[int]:
    files = migration_files(directory or Path(__file__).with_name("sql"))
    if connection.in_transaction:
        raise MigrationError("Migrations require an idle database connection.")
    try:
        pending = _pending(connection, files)
        if not pending:
            return []
        statements = {item.version: _statements(item.sql) for item in pending}
        # The online backup includes committed WAL pages. It is taken outside the
        # write transaction so SQLite's backup API cannot wait on our own write lock.
        existing = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
        ).fetchone()
        backup = _backup(connection, database) if existing else None
        connection.execute("BEGIN IMMEDIATE")
        try:
            pending = _pending(connection, files)  # Recheck after acquiring the writer lock.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL,backup_path TEXT)"
            )
            for item in pending:
                for statement in statements[item.version]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?,?,?)",
                    (
                        item.version,
                        item.name,
                        item.checksum,
                        datetime.now(timezone.utc).isoformat(),
                        str(backup) if backup else None,
                    ),
                )
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise MigrationError(
                    "Foreign-key validation failed; the schema upgrade was rolled back."
                )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        return [item.version for item in pending]
    except sqlite3.Error as exc:
        raise MigrationError("Database migration failed and was rolled back: " + str(exc)) from exc
