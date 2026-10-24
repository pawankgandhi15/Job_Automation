from copy import deepcopy
from datetime import timedelta
from urllib.error import HTTPError

import pytest
from test_contracts import Response

from job_agent.common import WorkflowError, now, parse_time
from job_agent.deduplication import potential_cross_post, simhash
from job_agent.domain import StateTransitionError, validate_transition
from job_agent.engine import Agent
from job_agent.sections import segment
from job_agent.transport import ResilientHTTP, retry_after


def test_event_bus_delivers_immutable_events_and_rollbacks_emit_nothing(agent, job):
    received = []
    agent.store.bus.subscribe(received.append)
    with pytest.raises(RuntimeError), agent.store.transaction():
        agent.store.event(None, "SYNTHETIC", "ROLLBACK")
        raise RuntimeError()
    assert received == []
    agent.store.upsert(job)
    assert received[0].action == "DISCOVERED"
    assert agent.store.verify_audit()["valid"]
    agent.store.db.execute("UPDATE events SET result='TAMPERED' WHERE id=1")
    assert not agent.store.verify_audit()["valid"]
    with pytest.raises(WorkflowError, match="Audit"):
        Agent(agent.root)


@pytest.mark.parametrize(
    "before,after",
    [("DISCOVERED", "EXECUTING"), ("SUBMISSION_UNCERTAIN", "APPROVED"), ("REJECTED", "APPROVED")],
)
def test_state_machine_protects_approval_and_uncertainty(before, after):
    with pytest.raises(StateTransitionError):
        validate_transition(before, after)
    validate_transition("APPROVED", "EXECUTING")


def test_sections_and_simhash_keep_exact_source_text_and_do_not_conflate_companies(job):
    text = "About us We have 20 years of history. Requirements Python and 2+ years experience. Benefits Flexible work."
    sections = segment(text)
    assert all(item["text"] == text[item["start"] : item["end"]] for item in sections)
    assert any(item["kind"] == "minimum" for item in sections)
    first, second = deepcopy(job), deepcopy(job)
    first["description"] = second["description"] = (
        "Build robust Python services using SQL and Docker."
    )
    assert potential_cross_post(first, second)
    second["official_website"] = "https://different.example"
    assert not potential_cross_post(first, second)
    assert simhash("PYTHON services") == simhash("python services")


def test_identical_cross_provider_route_is_aliased_without_another_ledger_entry(agent, job):
    job["provider"] = "greenhouse"
    first, _ = agent.store.upsert(job)
    cross = deepcopy(job)
    cross.update(provider="ashby", job_id="another-provider-id")
    second, changed = agent.store.upsert(cross)
    assert first == second and not changed
    assert len(agent.store.all()) == 1
    assert agent.store.upsert(cross)[0] == first


def test_resume_variant_is_blocked_until_separately_reconciled(agent, job):
    path = agent.root / "backend.pdf"
    path.write_bytes(b"synthetic backend resume")
    agent.settings["resume_variants"] = {"backend": str(path)}
    resume = agent.resume_for(job)
    assert resume["available"] and not resume["profile_checked"]
    from job_agent.availability import resume_profile_digest

    agent.store.set_setting(
        "resume_attestation:variant:backend",
        {
            "resume_sha256": resume["sha256"],
            "resume_profile_sha256": resume_profile_digest(agent.profile),
        },
    )
    assert agent.resume_for(job)["profile_checked"]
    path.write_bytes(b"modified synthetic resume")
    assert not agent.resume_for(job)["profile_checked"]


def test_handoff_expires_within_24_hours_without_restoring_consumed_approval(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    handoff = agent.handoff(job_id, "portal")
    assert parse_time(handoff["expires_at"]) <= now() + timedelta(hours=24)
    assert agent.store.db.execute("SELECT consumed_at FROM approvals").fetchone()[0]


def test_transport_uses_etag_only_after_success_and_304_reuses_checked_cache(tmp_path, monkeypatch):
    calls = []
    response = Response(b'{"jobs":[]}')
    response.headers = {"ETag": '"v1"'}

    def network(request, **kwargs):
        calls.append(request)
        if len(calls) > 1:
            raise HTTPError(request.full_url, 304, "Not modified", {}, None)
        return response

    monkeypatch.setattr("job_agent.transport.urlopen", network)
    client = ResilientHTTP(tmp_path, spacing=0)
    assert client.get("https://example.com/jobs", as_json=True) == {"jobs": []}
    assert client.get("https://example.com/jobs", as_json=True) == {"jobs": []}
    assert calls[1].get_header("If-none-match") == '"v1"'


def test_retry_after_is_honored_and_long_waits_defer_instead_of_hammering(tmp_path, monkeypatch):
    calls = []

    def rate_limited(request, **kwargs):
        calls.append(request)
        raise HTTPError(request.full_url, 429, "Slow down", {"Retry-After": "120"}, None)

    monkeypatch.setattr("job_agent.transport.urlopen", rate_limited)
    client = ResilientHTTP(tmp_path, spacing=0)
    with pytest.raises(WorkflowError, match="deferred"):
        client.get("https://example.com/jobs")
    with pytest.raises(WorkflowError, match="circuit"):
        client.get("https://example.com/jobs")
    assert len(calls) == 1
    assert retry_after("12") == 12
    assert retry_after("invalid") is None
    assert retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 0) > 0
