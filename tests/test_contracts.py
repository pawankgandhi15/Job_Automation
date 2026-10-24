from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from test_sources import source

from job_agent.common import WorkflowError, read_json, write_json
from job_agent.daily import refresh_source_pool
from job_agent.evidence import experience_requirements
from job_agent.sources import HTTP, board_link, fetch_board


@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby"])
def test_offline_provider_payloads_preserve_identity_and_application_routes(provider):
    config = source(provider)
    payload = read_json(Path(__file__).with_name("fixtures") / "ats" / (provider + ".json"))

    class FixtureHTTP:
        def get(self, url, *, as_json=False):
            if as_json:
                return payload
            return {"url": url, "text": f'<a href="{board_link(config)}">Jobs</a>'}

    jobs = fetch_board(config, FixtureHTTP())
    assert len(jobs) == 1
    job = jobs[0]
    assert job["company"] == config["company"]
    assert job["employment_type"] == "internship"
    assert job["application_url"].startswith(board_link(config))
    assert job["eligibility_gates"]["employer_identity"]["status"] == "PASS"
    assert job["eligibility_gates"]["application_route"]["status"] == "UNKNOWN"


def test_source_pool_adds_only_employer_proven_links_and_keeps_failure_evidence(agent):
    employers = [
        {
            "company": "New Synthetic",
            "official_website": "https://new.example",
            "official_careers_url": "https://new.example/careers",
        },
        {
            "company": "Blocked",
            "official_website": "https://blocked.example",
            "official_careers_url": "https://blocked.example/careers",
        },
    ]
    write_json(agent.root / "config/employers.json", {"employers": employers})

    class FixtureHTTP:
        def get(self, url, *, as_json=False):
            if "blocked" in url:
                raise WorkflowError("Source HTTP 403; no access")
            return {"url": url, "text": '<a href="https://jobs.ashbyhq.com/VerifiedToken">Jobs</a>'}

    agent.http = FixtureHTTP()
    result = refresh_source_pool(agent)
    assert len(result["added"]) == 1
    assert result["added"][0]["board"] == "VerifiedToken"
    assert result["checks"][1]["status"] == "REVIEW_NEEDED"
    assert "403" in result["checks"][1]["reason"]
    assert refresh_source_pool(agent)["added"] == []
    assert (
        len([item for item in agent.settings["sources"] if item.get("board") == "VerifiedToken"])
        == 1
    )


class Response:
    url = "https://example.com/careers"
    headers = {"Content-Type": "text/html"}

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.content[:count]


def test_public_http_reads_html_and_json_without_a_live_connection(monkeypatch):
    monkeypatch.setattr(
        "job_agent.sources.urlopen", lambda *args, **kwargs: Response(b'{"jobs":[]}')
    )
    assert HTTP().get("https://example.com/careers", as_json=True) == {"jobs": []}
    assert HTTP().get("https://example.com/careers")["url"] == Response.url


@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_http_errors_never_become_closed_vacancies_or_repeated_access_attempts(monkeypatch, status):
    calls = []

    def blocked(url, **kwargs):
        calls.append(url)
        raise HTTPError("https://example.com", status, "Synthetic failure", {}, None)

    monkeypatch.setattr("job_agent.sources.urlopen", blocked)
    with pytest.raises(WorkflowError, match=f"HTTP {status}.*not proof of closure"):
        HTTP().get("https://example.com/careers")
    assert len(calls) == 1


def test_network_failure_has_bounded_attempts_and_incomplete_verification(monkeypatch):
    calls = []

    def unreachable(*args, **kwargs):
        calls.append(1)
        raise URLError(TimeoutError())

    monkeypatch.setattr("job_agent.sources.urlopen", unreachable)
    with pytest.raises(WorkflowError, match="timed out"):
        HTTP().get("https://example.com/careers")
    assert len(calls) == 2


def test_source_payload_limits_and_json_errors_fail_explicitly(monkeypatch):
    monkeypatch.setattr(
        "job_agent.sources.urlopen", lambda *args, **kwargs: Response(b"x" * 8_000_001)
    )
    with pytest.raises(WorkflowError, match="8 MB"):
        HTTP().get("https://example.com")
    monkeypatch.setattr("job_agent.sources.urlopen", lambda *args, **kwargs: Response(b"not JSON"))
    with pytest.raises(WorkflowError, match="expected JSON"):
        HTTP().get("https://example.com", as_json=True)


@given(st.text(max_size=1000))
@settings(max_examples=60, deadline=None)
def test_experience_extraction_handles_arbitrary_text_without_inventing_source(text):
    results = experience_requirements(text, "https://example.com/jobs/1")
    for result in results:
        assert result["source"] == "https://example.com/jobs/1"
        assert result["minimum_years"] >= 0
