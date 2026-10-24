import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from job_agent.common import WorkflowError, timestamp
from job_agent.migrations import MigrationError, migration_files, run_migrations
from job_agent.store import Store

SQL = Path(__file__).resolve().parent.parent / "job_agent/sql"


def legacy_database(path, job):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.executescript((SQL / "001_initial_schema.sql").read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO opportunities(company_key,opportunity_key,payload,material_hash,first_seen_at,state,portal_status) VALUES(?,?,?,?,?,?,?)",
        (
            "example.com",
            "example.com|legacy",
            json.dumps(job),
            "original-material",
            timestamp(),
            "SUBMISSION_UNCERTAIN",
            "SUBMISSION_UNCERTAIN",
        ),
    )
    connection.execute(
        "INSERT INTO reports VALUES('legacy-report',?,NULL,'legacy.md','{}')", (timestamp(),)
    )
    connection.execute(
        "INSERT INTO reported_companies VALUES('old.example','legacy-report',?)", (timestamp(),)
    )
    connection.execute(
        "INSERT INTO daily_companies VALUES('2026-10-22','old.example','old.example',0,?)",
        (timestamp(),),
    )
    connection.execute(
        "INSERT INTO approvals(opportunity_id,channel,scope_hash,approved_at,valid_from,expires_at,consumed_at) VALUES(1,'portal','original-scope',?,?,?,?)",
        (timestamp(), timestamp(), timestamp(), timestamp()),
    )
    connection.execute(
        "INSERT INTO attempts VALUES('legacy-token',1,'portal',1,?,'SUBMISSION_UNCERTAIN',NULL,NULL)",
        (timestamp(),),
    )
    connection.execute(
        "INSERT INTO events(at,opportunity_id,action,result,evidence) VALUES(?,1,'OLD_EVENT','UNCHANGED','{}')",
        (timestamp(),),
    )
    connection.execute("INSERT INTO settings VALUES('resume_attestation','{\"original\":true}')")
    return connection


def snapshot(connection):
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: sorted(
            [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"')], key=repr
        )
        for name in names
    }


def test_legacy_migration_preserves_every_row_and_approval_and_takes_wal_backup(tmp_path, job):
    path = tmp_path / "ledger.sqlite3"
    connection = legacy_database(path, job)
    connection.execute("PRAGMA journal_mode=WAL")
    original = snapshot(connection)
    connection.close()
    store = Store(path)
    try:
        upgraded = snapshot(store.db)
        for table, rows in original.items():
            assert [tuple(row) for row in upgraded[table]] == rows
        assert store.schema_status()["versions"] == [1, 2, 3]
        assert store.search("Backend")[0]["opportunity_id"] == "JOB-001"
        backup_path = store.db.execute(
            "SELECT backup_path FROM schema_migrations LIMIT 1"
        ).fetchone()[0]
        with sqlite3.connect(backup_path) as backup:
            assert snapshot(backup) == original
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()
    second = Store(path)
    assert second.applied_migrations == []
    second.close()
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_failed_upgrade_rolls_back_ddl_and_migration_records(tmp_path, job):
    path = tmp_path / "ledger.sqlite3"
    connection = legacy_database(path, job)
    original = snapshot(connection)
    migrations = tmp_path / "sql"
    shutil.copytree(SQL, migrations)
    (migrations / "004_broken.sql").write_text(
        "CREATE TABLE should_rollback(id INTEGER); INSERT INTO missing_table VALUES(1);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationError, match="rolled back"):
        run_migrations(connection, path, migrations)
    assert snapshot(connection) == original
    assert not connection.in_transaction
    connection.close()


def test_modified_applied_checksum_fails_without_touching_existing_data(tmp_path, job):
    store = Store(tmp_path / "ledger.sqlite3")
    store.upsert(job)
    original = snapshot(store.db)
    migrations = tmp_path / "sql"
    shutil.copytree(SQL, migrations)
    with (migrations / "001_initial_schema.sql").open("a", encoding="utf-8") as file:
        file.write("\n-- Modified after application\n")
    with pytest.raises(MigrationError, match="checksum"):
        run_migrations(store.db, tmp_path / "ledger.sqlite3", migrations)
    assert {name: [tuple(row) for row in rows] for name, rows in snapshot(store.db).items()} == {
        name: [tuple(row) for row in rows] for name, rows in original.items()
    }
    store.close()


@pytest.mark.parametrize(
    "sql", ["BEGIN;", "PRAGMA foreign_keys=OFF;", "CREATE TABLE incomplete(id INTEGER)"]
)
def test_unsafe_or_incomplete_migration_never_changes_ledger(tmp_path, sql):
    directory = tmp_path / "sql"
    directory.mkdir()
    (directory / "001_bad.sql").write_text(sql, encoding="utf-8")
    connection = sqlite3.connect(tmp_path / "ledger.sqlite3", isolation_level=None)
    with pytest.raises(MigrationError):
        run_migrations(connection, tmp_path / "ledger.sqlite3", directory)
    assert snapshot(connection) == {}
    connection.close()


def test_migration_versions_are_unique_and_newline_independent(tmp_path):
    (tmp_path / "001_first.sql").write_bytes(b"CREATE TABLE test(id INTEGER);\r\n")
    checksum = migration_files(tmp_path)[0].checksum
    assert checksum == hashlib.sha256(b"CREATE TABLE test(id INTEGER);\n").hexdigest()
    (tmp_path / "001_duplicate.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate"):
        migration_files(tmp_path)


def test_foreign_key_failure_does_not_clean_or_drop_legacy_rows(tmp_path, job):
    path = tmp_path / "ledger.sqlite3"
    connection = legacy_database(path, job)
    connection.execute("UPDATE attempts SET opportunity_id=999")
    original = snapshot(connection)
    with pytest.raises(MigrationError, match="Foreign-key"):
        run_migrations(connection, path)
    assert snapshot(connection) == original
    connection.close()


def test_search_index_tracks_updates_deletes_and_transaction_rollback(agent, job):
    job["description"] = "UniqueZephyr Python backend"
    job_id, _ = agent.store.upsert(job)
    assert agent.store.search("UniqueZephyr")[0]["opportunity_id"] == job_id
    job["description"] = "UniqueAurora service"
    agent.store.upsert(job)
    assert agent.store.search("UniqueZephyr") == []
    assert len(agent.store.search("UniqueAurora")) == 1
    with pytest.raises(RuntimeError), agent.store.transaction():
        agent.store.db.execute("DELETE FROM opportunities")
        raise RuntimeError("rollback")
    assert len(agent.store.search("UniqueAurora")) == 1
    agent.store.db.execute("DELETE FROM opportunities")
    assert agent.store.search("UniqueAurora") == []


def test_concurrent_reader_sees_only_committed_ledger_and_index(agent, job):
    job_id, _ = agent.store.upsert(job)
    reader = Store(agent.root / "data/ledger.sqlite3")
    try:
        with agent.store.transaction():
            agent.store.db.execute("UPDATE opportunities SET state='DISMISSED' WHERE id=1")
            assert reader.search("", state="DISCOVERED")[0]["opportunity_id"] == job_id
            assert reader.search("", state="DISMISSED") == []
        assert reader.search("", state="DISMISSED")[0]["opportunity_id"] == job_id
    finally:
        reader.close()


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": True}, {"offset": -1}, {"query": "x" * 501}]
)
def test_search_rejects_invalid_bounds(agent, kwargs):
    with pytest.raises(WorkflowError):
        agent.store.search(**kwargs)


def test_literal_search_and_pagination_do_not_execute_query_syntax(agent, job):
    for index in range(3):
        job["job_id"] = str(index)
        agent.store.upsert(job)
    assert len(agent.store.search("Backend", limit=2, offset=1)) == 2
    assert agent.store.search('" OR *; DROP TABLE opportunities; --') == []
    assert agent.store.search("***") == []
    assert len(agent.store.all()) == 3
