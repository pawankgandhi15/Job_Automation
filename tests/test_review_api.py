import http.client
import json
import threading

import pytest

from job_agent.server import ReviewServer


@pytest.fixture
def workstation(prepared):
    agent, job_id = prepared
    server = ReviewServer(agent.root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, body=None, *, csrf=True, cookie=True, origin=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {}
        if cookie:
            headers["Cookie"] = "agent_session=" + server.token
        if body is not None:
            headers["Content-Type"] = "application/json"
            if csrf:
                headers["X-Job-Agent-CSRF"] = server.token
        if origin:
            headers["Origin"] = origin
        if host:
            headers["Host"] = host
        connection.request(
            "POST" if body is not None else "GET",
            path,
            body=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data)

    yield agent, job_id, server, call
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_session_and_read_routes_are_authenticated_and_never_authorize_actions(workstation):
    agent, job_id, server, call = workstation
    status, data = call("/api/v1/session", cookie=False)
    assert status == 200 and data["csrf"] == server.token
    assert call("/api/v1/queue", cookie=False)[0] == 403
    status, queue = call("/api/v1/queue?q=Python")
    assert status == 200 and queue["items"][0]["opportunity_id"] == job_id
    status, detail = call("/api/v1/opportunities/" + job_id)
    assert status == 200 and detail["scope_hash"]
    assert detail["payload"]["description"]
    assert call("/api/v1/notifications")[0] == 200
    assert call("/api/v1/health")[0] == 200
    assert call("/api/v1/no-such-route")[0] == 404
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


@pytest.mark.parametrize(
    "options",
    [
        {"csrf": False},
        {"cookie": False},
        {"origin": "https://attacker.example"},
        {"host": "attacker.example"},
    ],
)
def test_mutations_reject_missing_tokens_and_cross_origin_requests(workstation, options):
    agent, job_id, _, call = workstation
    assert (
        call(
            f"/api/v1/opportunities/{job_id}/approve",
            {"channels": ["portal"], "expected_scope_hash": "not-current"},
            **options,
        )[0]
        == 403
    )
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_stale_review_cannot_approve_but_exact_scope_can_handoff_and_reconcile(workstation):
    agent, job_id, _, call = workstation
    route = f"/api/v1/opportunities/{job_id}"
    assert (
        call(route + "/approve", {"channels": ["portal"], "expected_scope_hash": "stale"})[0] == 409
    )
    _, detail = call(route)
    status, result = call(
        route + "/approve", {"channels": ["portal"], "expected_scope_hash": detail["scope_hash"]}
    )
    assert status == 200 and result["external_action_performed"] is False
    assert (
        call(route + "/skip", {"expected_scope_hash": detail["scope_hash"], "reason": "old view"})[
            0
        ]
        == 409
    )
    _, detail = call(route)
    status, handoff = call(
        route + "/handoff", {"channel": "portal", "expected_scope_hash": detail["scope_hash"]}
    )
    assert status == 200 and handoff["external_action_performed_by_agent"] is False
    assert agent.store.db.execute("SELECT COUNT(*) FROM attempt_expiry").fetchone()[0] == 1
    status, result = call(
        f"/api/v1/attempts/{handoff['token']}/reconcile",
        {"outcome": "uncertain", "evidence": "Synthetic timeout"},
    )
    assert status == 200 and result["state"] == "SUBMISSION_UNCERTAIN"
    _, detail = call(route)
    assert (
        call(
            route + "/skip",
            {"expected_scope_hash": detail["scope_hash"], "reason": "hide uncertainty"},
        )[0]
        == 400
    )
    assert (
        call(
            route + "/approve",
            {"expected_scope_hash": detail["scope_hash"], "channels": ["portal"]},
        )[0]
        == 400
    )
    assert agent.store.verify_audit()["valid"]


def test_report_review_requires_current_artifact_hash_and_explicit_user_action(workstation):
    agent, _, _, call = workstation
    status, report = call("/api/v1/reports", {})
    assert status == 200 and report["markdown"] and not report["delivered"]
    route = f"/api/v1/reports/{report['report_id']}/review"
    assert call(route, {"expected_hash": "changed"})[0] == 409
    assert call(route, {"expected_hash": report["review_hash"]})[0] == 200
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_skip_requires_reason_and_records_audited_dismissal(workstation):
    agent, job_id, _, call = workstation
    _, detail = call("/api/v1/opportunities/" + job_id)
    body = {"expected_scope_hash": detail["scope_hash"]}
    route = f"/api/v1/opportunities/{job_id}/skip"
    assert call(route, body)[0] == 400
    assert call(route, {**body, "reason": "Synthetic location mismatch"})[0] == 200
    assert agent.store.get(job_id)["state"] == "DISMISSED"
    assert agent.store.verify_audit()["valid"]


def test_queue_filters_and_pagination_apply_before_page_limit(workstation):
    agent, job_id, _, call = workstation
    job = agent.store.get(job_id)["payload"]
    status, page = call("/api/v1/queue?limit=1&tier=" + job["tier"])
    assert status == 200 and len(page["items"]) == 1
    assert page["limit"] == 1 and page["has_more"] is False
    assert call("/api/v1/queue?tier=INVALID")[0] == 400
    assert call("/api/v1/queue?family=INVALID")[0] == 400
    assert call("/api/v1/queue?limit=0")[0] == 400
    assert call("/api/v1/queue?offset=1")[1]["items"] == []


def test_evidence_requires_primary_source_exact_excerpt_and_current_scope(workstation):
    agent, job_id, _, call = workstation
    route = f"/api/v1/opportunities/{job_id}"
    _, detail = call(route)
    body = {
        "expected_scope_hash": detail["scope_hash"],
        "gate": "degree_level",
        "status": "PASS",
        "reason": "Synthetic reviewer checked the requirement",
        "source": "https://unrelated.example/guess",
    }
    assert call(route + "/evidence", body)[0] == 400
    body["source"] = detail["payload"]["canonical_url"]
    assert call(route + "/evidence", {**body, "quote": "Invented excerpt absent from JD"})[0] == 400
    status, result = call(
        route + "/evidence", {**body, "quote": detail["payload"]["description"][:25]}
    )
    assert status == 200 and result["gate"] == "degree_level"
    assert (
        call(
            route + "/approve",
            {"expected_scope_hash": detail["scope_hash"], "channels": ["portal"]},
        )[0]
        == 409
    )
    assert agent.store.verify_audit()["valid"]


def test_question_edits_require_exact_question_and_explicit_candidate_evidence(workstation):
    agent, job_id, _, call = workstation
    job = agent.store.get(job_id)["payload"]
    job["required_questions"] = ["What is your preferred start month?"]
    job["required_consents"] = ["Have you read the privacy notice?"]
    agent.store.upsert(job)
    agent.prepare()
    route = f"/api/v1/opportunities/{job_id}"
    _, detail = call(route)
    body = {
        "expected_scope_hash": detail["scope_hash"],
        "question": job["required_questions"][0],
        "answer": "January 2027",
    }
    assert call(route + "/answer", body)[0] == 400
    assert (
        call(route + "/answer", {**body, "evidence": "Synthetic candidate confirmation"})[0] == 200
    )
    _, detail = call(route)
    consent = {
        "expected_scope_hash": detail["scope_hash"],
        "question": job["required_consents"][0],
        "answer": "maybe",
    }
    assert call(route + "/consent", consent)[0] == 400
    assert call(route + "/consent", {**consent, "answer": "yes"})[0] == 200


def test_invalid_outcome_cannot_clear_uncertain_submission(workstation):
    agent, job_id, _, call = workstation
    agent.approve(job_id, ["portal"])
    handoff = agent.handoff(job_id, "portal")
    agent.record_result(handoff["token"], "uncertain", "Synthetic timeout")
    assert (
        call(
            f"/api/v1/attempts/{handoff['token']}/reconcile",
            {"outcome": "invented", "evidence": "Guess"},
        )[0]
        == 400
    )
    assert agent.store.get(job_id)["state"] == "SUBMISSION_UNCERTAIN"
