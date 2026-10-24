from copy import deepcopy
from datetime import timedelta

import pytest
from test_sources import source

from job_agent.common import now, read_json, timestamp, write_json
from job_agent.daily import daily_selection, diverse_leads
from job_agent.sources import discover_employer_sources, published_ats_targets, verify_employer_link


def row(job, name):
    payload = deepcopy(job)
    payload.update(company=name, official_website=f"https://{name.lower()}.example")
    return {
        "company_key": f"{name.lower()}.example",
        "state": "NEEDS_CLARIFICATION",
        "payload": payload,
        "evaluation": {"status": "NEEDS_CLARIFICATION", "gates": payload["eligibility_gates"]},
    }


def test_daily_selection_is_stable_and_never_repeats_an_employer(agent, job):
    rows = [row(job, name) for name in ["Alpha", "Beta", "Gamma", "Delta"]]
    first = daily_selection(agent.store, rows, 2, day="2026-10-23")
    assert len(first["domains"]) == 2
    assert daily_selection(agent.store, list(reversed(rows)), 2, day="2026-10-23") == first
    second = daily_selection(agent.store, rows, 2, day="2026-10-24")
    assert len(second["domains"]) == 2
    assert not set(first["domains"]) & set(second["domains"])
    assert daily_selection(agent.store, rows, 2, day="2026-10-25")["domains"] == []


def test_delivered_companies_cannot_be_relabelled_new(prepared, job):
    agent, job_id = prepared
    prior = agent.store.get(job_id)
    assert daily_selection(agent.store, [prior], 5)["domains"] == []
    assert len(daily_selection(agent.store, [prior, row(job, "New")], 5)["domains"]) == 1


@pytest.mark.parametrize("change", ["stale", "identity", "vacancy", "rejected"])
def test_daily_selection_requires_current_verified_nonrejected_evidence(agent, job, change):
    candidate = row(job, "Alpha")
    if change == "stale":
        candidate["payload"]["last_verified_at"] = timestamp(now() - timedelta(days=2))
    elif change == "rejected":
        candidate["evaluation"]["status"] = "REJECTED"
    else:
        gate = "employer_identity" if change == "identity" else "live_vacancy"
        candidate["evaluation"]["gates"][gate]["status"] = "UNKNOWN"
    assert daily_selection(agent.store, [candidate])["domains"] == []


def test_round_robin_limits_large_boards_and_prioritizes_new_companies(job):
    leads = []
    for name in ["Old", "New", "Other"]:
        for index in range(12):
            item = row(job, name)["payload"]
            item["job_id"] = str(index)
            leads.append(item)
    selected = diverse_leads(leads, "IBM", {"old.example"}, 3, 10)
    assert [item["company"] for item in selected] == ["New", "Other", "Old"]
    capped = diverse_leads(leads, "IBM", set(), 100, 2, {"new.example": 1})
    assert len(capped) == 5
    assert sum(item["company"] == "New" for item in capped) == 1


def test_exact_serialized_ats_links_are_valid_provenance_but_lookalikes_are_not():
    class HTTP:
        def get(self, url):
            return {
                "url": url,
                "text": r'<script>{"apply":"https:\/\/job-boards.greenhouse.io\/synthetic\/jobs\/12"}</script>',
            }

    verify_employer_link(source(), HTTP())
    _, targets = published_ats_targets(
        {"text": '<script>"https://jobs.ashbyhq.com.evil.example/synthetic"</script>'},
        "https://example.com",
    )
    assert targets == []


def test_source_discovery_follows_only_owned_careers_links():
    class HTTP:
        calls = []

        def get(self, url):
            self.calls.append(url)
            text = (
                '<a href="https://evil.example/jobs">Jobs</a><a href="/careers/open-positions">Open roles</a>'
                if url.endswith("/careers")
                else '<a href="https://jobs.ashbyhq.com/ExactToken">Apply</a>'
            )
            return {"url": url, "text": text}

    http = HTTP()
    sources = discover_employer_sources(
        "Example", "https://example.com", "https://example.com/careers", http=http
    )
    assert sources[0]["board"] == "ExactToken"
    assert sources[0]["official_careers_url"] == "https://example.com/careers/open-positions"
    assert all(url.startswith("https://example.com/") for url in http.calls)


def test_daily_run_publishes_the_persistent_selection_without_approving(agent, job, monkeypatch):
    agent.store.upsert(job)
    agent.store.set_setting("source_pool_check", {"at": timestamp()})
    monkeypatch.setattr(agent, "discover", agent.report)
    first = agent.daily()
    assert first["daily_companies"]["domains"] == ["example.com"]
    second = agent.daily()
    assert second["daily_companies"] == first["daily_companies"]
    snapshot = read_json(agent.root / "web/company-overview/public/companies.json")
    assert snapshot["daily_selection"]["domains"] == ["example.com"]
    assert snapshot["daily_selection"]["date"] == now().date().isoformat()
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert (agent.root / "data/daily/latest-result.json").is_file()


def test_json_replacement_publishes_complete_latest_data(tmp_path):
    path = tmp_path / "snapshot.json"
    write_json(path, {"companies": ["first"]})
    write_json(path, {"companies": ["second"], "date": now().date().isoformat()})
    assert read_json(path)["companies"] == ["second"]
    assert not path.with_suffix(".json.tmp").exists()
