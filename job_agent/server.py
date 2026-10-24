"""Loopback-only review API and React host. All employer actions remain handoffs."""

from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .common import WorkflowError, digest, timestamp
from .configuration import load_configuration
from .engine import Agent, run_lock
from .observability import trace_id
from .store import Store
from .types import Record


class Conflict(WorkflowError):
    pass


def review_scope(row: Record, profile: Record) -> str:
    return digest(
        {
            "package_hash": row["package_hash"],
            "material_hash": row["material_hash"],
            "state": row["state"],
            "profile": digest(profile),
        }
    )


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 8765) -> None:
        self.root, self.token = root.resolve(), secrets.token_urlsafe(32)
        self.actions = threading.Lock()
        self.stopping = threading.Event()
        with run_lock(self.root):
            agent = Agent(self.root)
            try:
                agent.prepare()
            finally:
                agent.close()
        super().__init__(("127.0.0.1", port), ReviewHandler)
        self.reminders = threading.Thread(target=self.remind, daemon=True)
        self.reminders.start()

    def remind(self) -> None:
        from .notifications import scan_notifications

        while not self.stopping.wait(60):
            try:
                with self.actions, run_lock(self.root):
                    agent = Agent(self.root)
                    try:
                        scan_notifications(agent)
                    finally:
                        agent.close()
            except (WorkflowError, OSError, sqlite3.Error):
                continue

    def server_close(self) -> None:
        self.stopping.set()
        if hasattr(self, "reminders"):
            self.reminders.join(timeout=2)
        super().server_close()


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, format: str, *args: object) -> None:
        pass  # Paths may contain private attempt tokens; activity is logged in the ledger.

    def json(self, status: int, data: object) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(encoded)))
        if self.path.startswith("/api/v1/session"):
            self.send_header(
                "Set-Cookie",
                f"agent_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/api",
            )
        self.end_headers()
        self.wfile.write(encoded)

    def boundary(self, *, mutation: bool = False, session: bool = True) -> None:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        # Vite forwards the API during local development; no wildcard CORS.
        allowed |= {"127.0.0.1:5173", "localhost:5173"}
        if host not in allowed:
            raise PermissionError("Unrecognized local host.")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + item for item in allowed}:
            raise PermissionError("Cross-origin review requests are not permitted.")
        if session:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            actual = cookies.get("agent_session")
            if not actual or not hmac.compare_digest(actual.value, self.server.token):
                raise PermissionError("Open the local review page to establish a session.")
        if mutation and not hmac.compare_digest(
            self.headers.get("X-Job-Agent-CSRF", ""), self.server.token
        ):
            raise PermissionError("Missing or invalid local review token.")

    def do_GET(self) -> None:
        try:
            path = urlsplit(self.path).path
            self.boundary(session=path.startswith("/api/") and path != "/api/v1/session")
            if path == "/api/v1/session":
                self.json(
                    200,
                    {
                        "csrf": self.server.token,
                        "mode": "local_review",
                        "external_actions": "manual_handoff",
                    },
                )
                return
            if path == "/api/v1/events/stream":
                self.stream()
                return
            if path.startswith("/api/"):
                self.read_api(path)
                return
            root = self.server.root / "web/company-overview/dist"
            relative = unquote(path).lstrip("/")
            target = (root / (relative or "index.html")).resolve()
            if path == "/review":
                target = root / "index.html"
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                self.json(404, {"error": "Not found"})
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            )
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Cache-Control", "no-store" if target.suffix in {".html", ".json"} else "no-cache"
            )
            self.end_headers()
            self.wfile.write(content)
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except (WorkflowError, ValueError, KeyError, sqlite3.Error) as exc:
            self.json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return

    def read_api(self, path: str) -> None:
        store = Store.readonly(self.server.root / "data/ledger.sqlite3")
        try:
            _, profile = load_configuration(self.server.root)
            query = parse_qs(urlsplit(self.path).query)
            if path == "/api/v1/queue":
                page_limit = min(100, int(query.get("limit", ["50"])[0]))
                if page_limit < 1:
                    raise WorkflowError("Page limit must be positive.")
                rows = store.search(
                    query.get("q", [""])[0],
                    state=query.get("state", [None])[0],
                    limit=page_limit + 1,
                    offset=int(query.get("offset", ["0"])[0]),
                    tier=query.get("tier", [None])[0],
                    family=query.get("family", [None])[0],
                )
                result = []
                for item in rows[:page_limit]:
                    row = store.get(item["id"])
                    evaluation = row["evaluation"] or {}
                    if query.get("tier") and row["payload"].get("tier") != query["tier"][0]:
                        continue
                    result.append(
                        {
                            **item,
                            "tier": row["payload"].get("tier", "GOOD"),
                            "scope_hash": review_scope(row, profile),
                            "blockers": (row["package"] or {}).get("blockers", []),
                            "gates": {
                                name: value["status"]
                                for name, value in evaluation.get("gates", {}).items()
                            },
                            "ready": evaluation.get("status") == "READY_FOR_REVIEW",
                        }
                    )
                self.json(
                    200, {"items": result, "limit": page_limit, "has_more": len(rows) > page_limit}
                )
            elif path.startswith("/api/v1/opportunities/"):
                row = store.get(path.rsplit("/", 1)[1])
                row["scope_hash"] = review_scope(row, profile)
                latest = store.db.execute(
                    "SELECT opportunity_ids FROM reports WHERE delivered_at IS NOT NULL ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                snapshot = json.loads(latest[0]).get(row["opportunity_id"], {}) if latest else {}
                row["reviewed_report"] = bool(
                    snapshot.get("shortlisted")
                    and snapshot.get("package_hash") == row["package_hash"]
                )
                row["audit"] = [
                    dict(item)
                    for item in store.db.execute(
                        "SELECT * FROM events WHERE opportunity_id=? ORDER BY id DESC LIMIT 50",
                        (row["id"],),
                    )
                ]
                row["attempts"] = [
                    dict(item)
                    for item in store.db.execute(
                        "SELECT token,channel,status,started_at FROM attempts WHERE opportunity_id=? ORDER BY started_at DESC",
                        (row["id"],),
                    )
                ]
                self.json(200, row)
            elif path == "/api/v1/notifications":
                self.json(
                    200,
                    {
                        "items": [
                            dict(item)
                            for item in store.db.execute(
                                "SELECT * FROM notifications WHERE acknowledged_at IS NULL ORDER BY id DESC LIMIT 50"
                            )
                        ]
                    },
                )
            elif path == "/api/v1/health":
                self.json(
                    200,
                    {
                        "mode": "local_review",
                        "at": timestamp(),
                        "paused": store.setting("paused", False),
                        "sources": store.source_status(),
                    },
                )
            else:
                self.json(404, {"error": "Unknown local API route"})
        finally:
            store.close()

    def do_POST(self) -> None:
        correlation = trace_id.set(secrets.token_hex(16))
        try:
            self.boundary(mutation=True)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise WorkflowError("Expected JSON request body.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise WorkflowError("Request body must be 1–65536 bytes.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise WorkflowError("Request body must be an object.")
            path = urlsplit(self.path).path
            with self.server.actions, run_lock(self.server.root):
                agent = Agent(self.server.root)
                try:
                    result = self.mutate(agent, path, body)
                finally:
                    agent.close()
            self.json(200, result)
        except PermissionError as exc:
            self.json(403, {"error": str(exc)})
        except Conflict as exc:
            self.json(409, {"error": str(exc), "refresh_required": True})
        except (WorkflowError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            self.json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            trace_id.reset(correlation)

    def mutate(self, agent: Agent, path: str, body: Record) -> Record:
        parts = path.strip("/").split("/")
        if path == "/api/v1/reports":
            report = agent.report()
            text = Path(report["path"]).read_text(encoding="utf-8")
            return {**report, "markdown": text, "review_hash": digest(text)}
        if len(parts) == 5 and parts[2] == "reports" and parts[4] == "review":
            report = agent.store.db.execute(
                "SELECT path FROM reports WHERE id=?", (parts[3],)
            ).fetchone()
            if not report or not hmac.compare_digest(
                str(body.get("expected_hash", "")),
                digest(Path(report[0]).read_text(encoding="utf-8")),
            ):
                raise Conflict("Report changed; reopen it before recording review.")
            return agent.deliver(parts[3])
        if len(parts) == 5 and parts[2] == "opportunities":
            agent.prepare()
            row = agent.store.get(parts[3])
            if not hmac.compare_digest(
                str(body.get("expected_scope_hash", "")), review_scope(row, agent.profile)
            ):
                raise Conflict(
                    "The job, candidate profile or action package changed. Review the current details."
                )
            action = parts[4]
            if action == "approve":
                channels = body.get("channels")
                if (
                    not isinstance(channels, list)
                    or not channels
                    or not all(item in {"portal", "outreach", "follow_up"} for item in channels)
                ):
                    raise WorkflowError("Select the exact approval channels.")
                return agent.approve(row["opportunity_id"], channels)
            if action == "handoff":
                return agent.handoff(row["opportunity_id"], body["channel"])
            if action == "skip":
                if row["state"] in {
                    "EXECUTING",
                    "SUBMISSION_UNCERTAIN",
                    "SUBMITTED_CONFIRMED",
                    "OUTREACH_SENT_CONFIRMED",
                    "PARTIAL_SUCCESS",
                }:
                    raise WorkflowError(
                        "An attempted action must be reconciled; it cannot be hidden by skipping."
                    )
                reason = str(body.get("reason", "")).strip()
                if not reason:
                    raise WorkflowError("A skip reason is required.")
                with agent.store.transaction():
                    agent.store.db.execute(
                        "UPDATE opportunities SET state='DISMISSED',follow_up_status='NOT_ELIGIBLE' WHERE id=?",
                        (row["id"],),
                    )
                    agent.store.invalidate(row["id"], "DISMISSED")
                    agent.store.event(row["id"], "DISMISSED", "USER_REVIEW", {"reason": reason})
                return {"dismissed": row["opportunity_id"]}
            if action == "recheck":
                if not isinstance(body.get("evidence"), str) or not body["evidence"].strip():
                    raise WorkflowError("Record the actual verification evidence.")
                from argparse import Namespace

                from .cli import dispatch

                result = dispatch(
                    Namespace(
                        command="recheck", id=row["opportunity_id"], evidence=body["evidence"]
                    ),
                    agent,
                )
                assert isinstance(result, dict)
                return result
            if action == "evidence":
                from .policy import GATES, is_primary

                gate_name = body.get("gate")
                status = body.get("status")
                if (
                    not isinstance(gate_name, str)
                    or gate_name not in GATES
                    or status not in {"PASS", "FAIL", "UNKNOWN"}
                ):
                    raise WorkflowError("Select a valid eligibility gate and status.")
                gate_reason, gate_source = body.get("reason"), body.get("source")
                if (
                    not isinstance(gate_reason, str)
                    or not gate_reason.strip()
                    or not isinstance(gate_source, str)
                ):
                    raise WorkflowError("A specific reason and primary source URL are required.")
                if status != "UNKNOWN" and not is_primary(row["payload"], gate_source):
                    raise WorkflowError(
                        "Decisive eligibility evidence must use a recorded primary source or the employer's domain."
                    )
                quote = body.get("quote", "")
                if not isinstance(quote, str):
                    raise WorkflowError("Evidence excerpt must be text.")
                if (
                    quote
                    and gate_source == row["payload"]["canonical_url"]
                    and quote not in row["payload"].get("description", "")
                ):
                    raise WorkflowError(
                        "The excerpt is not present in the recorded job description."
                    )
                job = row["payload"]
                record: Record = {
                    "status": status,
                    "reason": gate_reason,
                    "source": gate_source,
                    "checked_at": timestamp(),
                    "provenance": "User-reviewed evidence; not independently verified by software",
                }
                if quote:
                    record["evidence"] = {"quote": quote, "source": gate_source}
                job.setdefault("eligibility_gates", {})[gate_name] = record
                agent.store.upsert(job)
                agent.store.event(
                    row["id"],
                    "ELIGIBILITY_EVIDENCE",
                    "USER_REVIEWED",
                    {"gate": gate_name, "status": status, "source": gate_source},
                )
                agent.prepare()
                return {
                    "opportunity_id": row["opportunity_id"],
                    "gate": gate_name,
                    "status": agent.store.get(row["id"])["evaluation"]["gates"][gate_name][
                        "status"
                    ],
                }
            if action in {"answer", "consent"}:
                from argparse import Namespace

                from .cli import dispatch

                question, answer = body.get("question"), body.get("answer")
                if (
                    not isinstance(question, str)
                    or not isinstance(answer, str)
                    or not answer.strip()
                ):
                    raise WorkflowError(
                        "Select the exact question and provide your explicit answer."
                    )
                if action == "consent" and answer not in {"yes", "no"}:
                    raise WorkflowError("Choose an explicit Yes or No consent answer.")
                evidence = body.get("evidence", "")
                if action == "answer" and (not isinstance(evidence, str) or not evidence.strip()):
                    raise WorkflowError("Explain the candidate-confirmed basis for this answer.")
                result = dispatch(
                    Namespace(
                        command="record-answer" if action == "answer" else "record-consent",
                        id=row["opportunity_id"],
                        question=question,
                        answer=answer,
                        evidence=evidence,
                    ),
                    agent,
                )
                agent.prepare()
                assert isinstance(result, dict)
                return result
        if len(parts) == 5 and parts[2] == "attempts" and parts[4] == "reconcile":
            row = agent.record_result(
                parts[3], body["outcome"], body.get("evidence"), body.get("occurred_at")
            )
            return {"opportunity_id": row["opportunity_id"], "state": row["state"]}
        if len(parts) == 5 and parts[2] == "notifications" and parts[4] == "acknowledge":
            agent.store.db.execute(
                "UPDATE notifications SET acknowledged_at=? WHERE id=?",
                (timestamp(), int(parts[3])),
            )
            return {"acknowledged": int(parts[3])}
        raise WorkflowError("Unknown or unsupported mutation.")

    def stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        previous = self.headers.get("Last-Event-ID", "0")
        for _ in range(30):
            store = Store.readonly(self.server.root / "data/ledger.sqlite3")
            try:
                cursor = str(
                    store.db.execute("SELECT coalesce(MAX(id),0) FROM events").fetchone()[0]
                )
            finally:
                store.close()
            if cursor != previous:
                self.wfile.write(
                    f'id: {cursor}\nevent: ledger\ndata: {{"cursor":{cursor}}}\n\n'.encode()
                )
                previous = cursor
            else:
                self.wfile.write(b": heartbeat\n\n")
            self.wfile.flush()
            time.sleep(1)


def serve(root: Path, port: int = 8765) -> None:
    server = ReviewServer(root, port)
    print(f"Local review workstation: http://127.0.0.1:{server.server_port}/review", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
