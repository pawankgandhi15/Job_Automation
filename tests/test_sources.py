from copy import deepcopy

import pytest

from job_agent.common import WorkflowError
from job_agent.sources import fetch_board, normalize, verify_employer_link


def source(provider="greenhouse"):
    return {
        "company": "Synthetic Employer",
        "official_website": "https://example.com",
        "official_careers_url": "https://example.com/careers",
        "provider": provider,
        "board": "synthetic",
        "tier": "GOOD",
    }


class FakeHTTP:
    def __init__(self, href="https://job-boards.greenhouse.io/synthetic/jobs/123", jobs=None):
        self.href, self.jobs, self.calls = href, jobs or [], []

    def get(self, url, *, as_json=False):
        self.calls.append(url)
        if not as_json:
            return {"url": url, "text": f'<a href="{self.href}">Apply</a>'}
        return {"jobs": self.jobs}


@pytest.mark.parametrize(
    "href",
    [
        "https://job-boards.greenhouse.io/other",
        "https://job-boards.greenhouse.io.evil.example/synthetic",
        "https://jobs.lever.co/synthetic",
        "https://job-boards.greenhouse.io/synthetic-imposter",
    ],
)
def test_ats_hostname_alone_does_not_establish_employer_identity(href):
    with pytest.raises(WorkflowError, match="did not link"):
        verify_employer_link(source(), FakeHTTP(href))


def test_official_employer_domain_must_own_identity_page():
    config = source()
    config["official_careers_url"] = "https://unrelated.example/careers"
    with pytest.raises(WorkflowError, match="official employer domain"):
        verify_employer_link(config, FakeHTTP())


def test_greenhouse_updated_date_is_never_claimed_as_posting_date():
    raw = {
        "id": 123,
        "title": "Backend Intern",
        "location": {"name": "India"},
        "content": "<p>Python backend</p>",
        "absolute_url": "https://job-boards.greenhouse.io/synthetic/jobs/123",
        "updated_at": "2026-10-21T10:00:00Z",
    }
    http = FakeHTTP(jobs=[raw])
    job = fetch_board(source(), http)[0]
    assert job["posting_date"] is None
    assert job["job_id"] == "123"
    assert job["eligibility_gates"]["live_vacancy"]["status"] == "PASS"
    assert job["eligibility_gates"]["application_route"]["status"] == "UNKNOWN"
    assert "degree_level" not in job["eligibility_gates"]
    assert len(http.calls) == 2


def test_ashby_can_derive_id_from_job_url_and_keeps_published_date():
    raw = {
        "title": "Backend Intern",
        "location": "India",
        "descriptionPlain": "Python",
        "jobUrl": "https://jobs.ashbyhq.com/synthetic/12345",
        "applyUrl": "https://jobs.ashbyhq.com/synthetic/12345/apply",
        "publishedAt": "2026-10-20T10:00:00Z",
        "employmentType": "Intern",
    }
    job = normalize(source("ashby"), raw)
    assert job["job_id"] == "12345"
    assert job["posting_date"] == "2026-10-20"
    assert job["application_url"].endswith("/apply")


def test_board_refresh_invalidates_changed_jd_and_discards_old_eligibility(agent, job):
    job["description"] = "Previous synthetic description"
    agent.store.upsert(job)
    changed = deepcopy(job)
    changed["description"] = "Changed synthetic mandatory requirements"
    changed["eligibility_gates"] = {"live_vacancy": job["eligibility_gates"]["live_vacancy"]}
    merged = next(agent.merge_discovery([changed]))
    assert "academic_schedule" not in merged["eligibility_gates"]


def test_source_failure_is_reported_without_claiming_job_closed(agent, job):
    class BlockedHTTP:
        def get(self, *args, **kwargs):
            raise WorkflowError("Source HTTP 403; verification incomplete, not proof of closure.")

    agent.settings["sources"] = [source()]
    agent.http = BlockedHTTP()
    job["source_config"] = source()
    job_id, _ = agent.store.upsert(job)
    agent.discover()
    assert agent.store.get(job_id)["state"] != "CLOSED"
    assert agent.store.get(job_id)["evaluation"]["gates"]["live_vacancy"]["status"] == "UNKNOWN"
    assert "403" in agent.store.setting("last_discovery")["errors"][0]
