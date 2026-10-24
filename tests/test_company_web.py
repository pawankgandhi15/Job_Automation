import json
from copy import deepcopy
from pathlib import Path

from job_agent.common import read_json
from job_agent.company_web import company_snapshot, export_company_web


def test_company_export_omits_candidate_resume_and_application_details(agent, job):
    job["application_url"] = "https://example.com/jobs/backend/apply?job=123"
    job["confirmed_answers"] = {"Question": {"answer": "private screening answer"}}
    job["contact"] = {"destination": "private-recruiter@example.com"}
    agent.store.upsert(job)
    rows = agent.prepare()
    snapshot = company_snapshot(rows, {})
    encoded = json.dumps(snapshot)
    for private in (
        agent.profile["email"],
        agent.profile["phone"],
        "private screening answer",
        "private-recruiter@example.com",
        "resume_sha256",
        "outreach_draft",
        "screening_answers",
    ):
        assert private not in encoded
    assert snapshot["company_count"] == 1
    assert snapshot["review_count"] == 1
    assert snapshot["companies"][0]["ceo"] is None
    assert snapshot["companies"][0]["jobs"][0]["application_url"] == job["application_url"]


def test_unknown_and_stale_research_remains_explicit(agent, job):
    agent.store.upsert(job)
    rows = agent.prepare()
    briefs = {
        "companies": {
            "example.com": {
                "checked_on": "2020-01-01",
                "ceo": {"name": "Old name", "source": "https://example.com/old"},
            }
        }
    }
    snapshot = company_snapshot(rows, briefs)
    assert snapshot["companies"][0]["research_stale"] is True
    assert snapshot["companies"][0]["recent_update"] is None
    assert rows[0]["payload"]["company"] == job["company"]


def test_generated_script_cannot_break_out_via_job_content(agent, job):
    job["company"] = '</script><script>alert("injection")</script>'
    agent.store.upsert(job)
    result = export_company_web(agent.root, agent.prepare())
    script = (Path(result["path"]).parent / "companies.js").read_text(encoding="utf-8")
    assert "</script>" not in script
    assert "\\u003c/script>" in script
    assert read_json(Path(result["snapshot_path"]))["companies"][0]["name"] == job["company"]


def test_report_refreshes_company_snapshot_without_approving_or_sending(agent, job):
    first_id, _ = agent.store.upsert(job)
    rejected = deepcopy(job)
    rejected.update(job_id="rejected", constraints={"required_hours_per_day": 8})
    agent.store.upsert(rejected)
    report = agent.report()
    snapshot = read_json(Path(report["company_web"]["snapshot_path"]))
    assert snapshot["report_id"] == report["report_id"]
    assert snapshot["company_count"] == 1
    assert snapshot["review_count"] == 1
    assert snapshot["companies"][0]["recorded_count"] == 2
    assert snapshot["companies"][0]["jobs"][0]["id"] == first_id
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert agent.store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_react_public_and_built_snapshots_stay_in_sync_without_touching_bundle(agent, job):
    agent.store.upsert(job)
    bundle = agent.root / "web" / "company-overview" / "dist" / "assets" / "app.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("Existing compiled React bundle", encoding="utf-8")
    first = export_company_web(agent.root, agent.prepare())
    public = agent.root / "web" / "company-overview" / "public" / "companies.json"
    built = agent.root / "web" / "company-overview" / "dist" / "companies.json"
    assert read_json(public) == read_json(built)
    job["role"] = "Revised Python Backend Intern"
    agent.store.upsert(job)
    export_company_web(agent.root, agent.prepare())
    assert read_json(public) == read_json(built)
    assert read_json(public)["companies"][0]["jobs"][0]["title"] == job["role"]
    assert bundle.read_text(encoding="utf-8") == "Existing compiled React bundle"
    assert first["requires_http_server"] is True
