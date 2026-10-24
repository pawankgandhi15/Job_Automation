import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest
from test_sources import FakeHTTP, source

from job_agent.availability import assess_availability, availability_text, resume_profile_digest
from job_agent.common import WorkflowError, now, read_json
from job_agent.policy import evaluate
from job_agent.sources import (
    ats_target,
    discover_employer_sources,
    fetch_board,
    relevant_lead,
    verify_employer_link,
)
from job_agent.structured import job_postings, normalize_jobposting


@pytest.mark.parametrize(
    "constraints,status",
    [
        ({"start_date": "2026-12-31"}, "FAIL"),
        ({"start_date": "2027-01-05"}, "UNKNOWN"),
        ({"start_date": "2027-02-01"}, "PASS"),
        ({"start_month": "2027-01"}, "PASS"),
        ({"start_month": "2026-12"}, "FAIL"),
        ({"immediate_joining": True}, "FAIL"),
    ],
)
def test_start_month_precision_is_preserved(agent, job, constraints, status):
    job["constraints"] = constraints
    gates, facts = assess_availability(
        job, agent.profile, datetime.fromisoformat("2026-10-22T09:00:00+05:30")
    )
    assert gates["start_date"]["status"] == status
    assert facts["start_precision"] == "month"
    assert facts["exact_joining_day_known"] is False


@pytest.mark.parametrize(
    "constraints,kind,remote,status",
    [
        ({"required_hours_per_day": 8}, "internship", True, "FAIL"),
        ({"required_hours_per_day": 8, "required_hours_per_week": 40}, "internship", True, "FAIL"),
        ({"required_hours_per_day": 6}, "internship", True, "PASS"),
        ({"required_hours_per_day": 6}, "internship", False, "UNKNOWN"),
        ({"required_hours_per_week": 30}, "internship", True, "UNKNOWN"),
        ({}, "full_time", True, "UNKNOWN"),
    ],
)
def test_six_hour_cap_is_independent_of_january_joining(
    agent, job, constraints, kind, remote, status
):
    job.update(
        constraints=constraints,
        employment_type=kind,
        workplace_type="remote" if remote else "onsite",
    )
    gates, _ = assess_availability(job, agent.profile, now())
    assert gates["academic_schedule"]["status"] == status


def test_compatible_hours_do_not_erase_independent_academic_failure(agent, job):
    job.update(constraints={"required_hours_per_day": 6}, workplace_type="remote")
    job["eligibility_gates"]["academic_schedule"].update(
        status="FAIL", reason="Mandatory attendance conflicts with confirmed exams."
    )
    result = evaluate(job, agent.profile, agent.settings)
    assert result["gates"]["academic_schedule"]["status"] == "FAIL"


def test_availability_change_invalidates_action_but_preserves_resume_review(prepared):
    agent, job_id = prepared
    resume = agent.resume()
    agent.store.set_setting(
        "resume_attestation",
        {
            "resume_sha256": resume["sha256"],
            "resume_profile_sha256": resume_profile_digest(agent.profile),
        },
    )
    agent.approve(job_id, ["portal"])
    agent.profile["availability"]["internship"]["earliest_start_month"] = "2027-02"
    agent.profile["version"] = "test-availability-update"
    agent.prepare()
    assert agent.resume()["profile_checked"] is True
    approval = agent.store.db.execute("SELECT * FROM approvals").fetchone()
    assert approval["invalidated_at"]
    assert "February 2027" in availability_text(agent.profile, "internship")
    agent.profile["education"][0]["degree"] = "Changed credential"
    assert agent.resume()["profile_checked"] is False


def test_greenhouse_embed_establishes_exact_board_only():
    target = "https://job-boards.greenhouse.io/embed/job_app?for=synthetic&token=123"
    assert ats_target(target) == {"provider": "greenhouse", "board": "synthetic"}
    verify_employer_link(source(), FakeHTTP(target))
    with pytest.raises(WorkflowError):
        verify_employer_link(source(), FakeHTTP(target.replace("for=synthetic", "for=other")))
    assert ats_target(target.replace("greenhouse.io/", "greenhouse.io.bad.example/")) is None


def test_official_source_discovery_does_not_guess_board():
    sources = discover_employer_sources(
        "Synthetic", "https://example.com", "https://example.com/careers", http=FakeHTTP()
    )
    assert sources[0]["board"] == "synthetic"
    with pytest.raises(WorkflowError, match="no board guessed"):
        discover_employer_sources(
            "Synthetic",
            "https://example.com",
            "https://example.com/careers",
            http=FakeHTTP("https://unsupported.example/jobs"),
        )


def structured_record():
    return {
        "@type": "JobPosting",
        "title": "Python Backend Intern",
        "description": "<p>Python APIs</p>",
        "hiringOrganization": {"url": "https://example.com"},
        "employmentType": "INTERN",
        "datePosted": "2026-10-20",
        "validThrough": "2027-01-18",
        "jobStartDate": "2027-01-05",
        "workHours": "6 hours per day",
        "directApply": True,
        "jobLocationType": "TELECOMMUTE",
        "baseSalary": {"currency": "INR", "value": {"minValue": 18000, "unitText": "MONTH"}},
    }


def test_jsonld_graph_malformed_blocks_and_form_uncertainty():
    raw = structured_record()
    found = list(job_postings(["bad-json", json.dumps({"@graph": [raw]})]))
    assert found == [raw]
    job = normalize_jobposting(source(), found[0], "https://example.com/jobs/1")
    assert job["posting_date"] == "2026-10-20"
    assert job["deadline"] is None  # Date alone does not establish a timezone/cutoff.
    assert job["deadline_display"] == "2027-01-18"
    assert job["compensation"]["minimum"] == 18000
    assert job["compensation"]["period"] == "month"
    assert job["constraints"] == {"start_date": "2027-01-05", "required_hours_per_day": 6.0}
    assert job["eligibility_gates"]["application_route"]["status"] == "UNKNOWN"


def test_jsonld_does_not_use_estimated_pay_or_range_as_exact_hours():
    raw = structured_record()
    raw["estimatedSalary"] = raw.pop("baseSalary")
    raw["workHours"] = "5 - 8 hours per day"
    raw["experienceRequirements"] = {"monthsOfExperience": 24}
    job = normalize_jobposting(source(), raw, "https://example.com/jobs/1")
    assert job["compensation"] is None
    assert "required_hours_per_day" not in job["constraints"]
    assert "minimum_professional_years" not in job["constraints"]
    assert job["structured_requirements"]["experienceRequirements"]["monthsOfExperience"] == 24


def test_jsonld_rejects_conflicting_employer_domain():
    raw = structured_record()
    raw["hiringOrganization"]["url"] = "https://unrelated.example"
    with pytest.raises(WorkflowError, match="differs"):
        normalize_jobposting(source(), raw, "https://example.com/jobs/1")


def test_jobposting_source_reads_metadata_without_running_scripts():
    class MetadataHTTP:
        def get(self, url):
            return {
                "url": url,
                "text": '<script type="application/ld+json">'
                + json.dumps(structured_record())
                + '</script><script>throw Error("never run");</script>',
            }

    configs = discover_employer_sources(
        "Synthetic", "https://example.com", "https://example.com/jobs/1", http=MetadataHTTP()
    )
    assert configs[0]["provider"] == "jobposting"
    assert fetch_board(configs[0], MetadataHTTP())[0]["role"] == "Python Backend Intern"


def test_run_history_distinguishes_new_and_duplicate_leads(agent, job, monkeypatch):
    agent.settings["sources"] = [source()]
    monkeypatch.setattr("job_agent.engine.fetch_board", lambda *args: [deepcopy(job)])
    first = agent.discover()
    second = agent.discover()
    assert first["counts"]["new_leads"] == 1
    assert second["counts"]["new_leads"] == 0
    assert second["counts"]["duplicates_unchanged"] == 1
    runs = agent.store.runs()
    assert len(runs) == 2
    assert runs[0]["status"] == "COMPLETED"
    assert runs[0]["sources"][0]["postings_count"] == 1
    assert runs[0]["report_id"] == second["report_id"]
    assert agent.store.export()["source_checks"]


def test_partial_source_failure_does_not_discard_other_source(agent, job, monkeypatch):
    good, bad = source(), source("ashby")
    bad["company"] = "Blocked source"
    agent.settings["sources"] = [good, bad]

    def fetch(config, http):
        if config == bad:
            raise WorkflowError("Source HTTP 429; verification incomplete")
        return [deepcopy(job)]

    monkeypatch.setattr("job_agent.engine.fetch_board", fetch)
    result = agent.discover()
    assert result["counts"]["sources_failed"] == 1
    assert result["counts"]["new_leads"] == 1
    assert agent.store.runs()[0]["status"] == "PARTIAL"
    assert {s["status"] for s in agent.store.source_status()} == {"OK", "ERROR"}


def test_unexpected_failure_marks_run_failed(agent, monkeypatch):
    monkeypatch.setattr(agent, "report", lambda: (_ for _ in ()).throw(RuntimeError("test crash")))
    agent.settings["sources"] = []
    with pytest.raises(RuntimeError):
        agent.discover()
    assert agent.store.runs()[0]["status"] == "FAILED"


def test_budget_is_reported_and_early_career_india_lead_is_prioritized(agent, job, monkeypatch):
    jobs = [deepcopy(job), deepcopy(job)]
    jobs[0].update(job_id="senior", role="Senior Backend Engineer")
    jobs[1].update(job_id="intern", role="Backend Intern")
    agent.settings.update(sources=[source()], max_leads_per_run=1)
    monkeypatch.setattr("job_agent.engine.fetch_board", lambda *args: jobs)
    result = agent.discover()
    assert result["counts"]["deferred_by_run_budget"] == 1
    assert agent.store.all()[0]["payload"]["job_id"] == "intern"


def test_unchanged_watchlist_suppressed_only_after_delivery(agent, job):
    job["eligibility_gates"]["academic_schedule"]["status"] = "UNKNOWN"
    agent.store.upsert(job)
    first, before_delivery = agent.report(), agent.report()
    assert before_delivery["counts"]["new_or_changed_watchlist_items"] == 1
    agent.deliver(first["report_id"])
    after = agent.report()
    assert after["counts"]["unchanged_watchlist_items"] == 1
    assert read_json(Path(after["review_queue_path"]))["total"] == 1
    assert len(read_json(Path(after["review_details_path"]))["pending_clarification"]) == 1


def test_queue_does_not_promote_rejected_role_based_on_keywords(agent, job):
    job["constraints"] = {"required_hours_per_day": 8}
    job["description"] = "Python Django AWS intern"
    agent.store.upsert(job)
    assert agent.review_queue()["total"] == 0


def test_revised_candidate_facts_can_recover_auto_rejection(agent, job):
    job["constraints"] = {"start_month": "2026-12"}
    job_id, _ = agent.store.upsert(job)
    agent.prepare()
    assert agent.store.get(job_id)["state"] == "REJECTED"
    job["constraints"] = {"start_month": "2027-02"}
    agent.store.upsert(job)
    agent.prepare()
    assert agent.store.get(job_id)["state"] == "AWAITING_APPROVAL"


def test_real_employer_rejection_remains_terminal(agent, job):
    job_id, _ = agent.store.upsert(job)
    agent.prepare()
    agent.store.db.execute(
        "UPDATE opportunities SET state='REJECTED',status_check=? WHERE id=1",
        (json.dumps({"status": "rejected", "evidence": "Test employer decision"}),),
    )
    job["description"] = "Changed Python role"
    agent.store.upsert(job)
    agent.prepare()
    assert agent.store.get(job_id)["state"] == "REJECTED"


@pytest.mark.parametrize(
    "title",
    [
        "Global People Operations - Systems Administrator",
        "Seller Systems Team Lead",
        "Designer, Web Presence & Platform",
        "Strategy & Operations, Infrastructure",
    ],
)
def test_broad_search_excludes_non_engineering_roles(agent, job, title):
    job["role"] = title
    assert relevant_lead(job, broaden=True) is False
    assert evaluate(job, agent.profile, agent.settings)["status"] == "REJECTED"


@pytest.mark.parametrize(
    "location",
    ["US-Remote, Chicago, Seattle", "Remote in US, Remote in Canada", "Canada-Remote", "Remote US"],
)
def test_remote_does_not_override_employer_country_restriction(agent, job, location):
    job["location"] = location
    assert relevant_lead(job) is False
    assert evaluate(job, agent.profile, agent.settings)["gates"]["location"]["status"] == "FAIL"


@pytest.mark.parametrize(
    "location",
    [
        "Remote, Global",
        "Bengaluru, India; Remote in US",
        "Remote APAC",
        "Remote; location not disclosed",
    ],
)
def test_potential_india_remote_locations_remain_reviewable(job, location):
    job["location"] = location
    assert relevant_lead(job) is True


def test_source_disappearance_requires_reverification_without_claiming_closure(
    agent, job, monkeypatch
):
    config = source()
    agent.settings["sources"] = [config]
    job["source_config"] = config
    job_id, _ = agent.store.upsert(job)
    monkeypatch.setattr("job_agent.engine.fetch_board", lambda *args: [])
    result = agent.discover()
    row = agent.store.get(job_id)
    assert row["evaluation"]["gates"]["live_vacancy"]["status"] == "UNKNOWN"
    assert row["state"] != "CLOSED"
    assert result["counts"]["sources_failed"] == 0


def test_changed_structured_hours_discard_stale_assessments(agent, job):
    job["source_content_hash"] = "original-source"
    agent.store.upsert(job)
    changed = deepcopy(job)
    changed["source_content_hash"] = "changed-source-hours"
    changed["constraints"] = {"required_hours_per_day": 8}
    changed["eligibility_gates"] = {"live_vacancy": job["eligibility_gates"]["live_vacancy"]}
    merged = next(agent.merge_discovery([changed]))
    assert merged["constraints"]["required_hours_per_day"] == 8
    assert "academic_schedule" not in merged["eligibility_gates"]


def test_cli_returns_nonzero_when_every_source_fails(agent, monkeypatch, capsys):
    from job_agent.cli import main

    def failed(*args):
        raise WorkflowError("Synthetic DNS failure")

    monkeypatch.setattr("job_agent.engine.fetch_board", failed)
    assert main(["--root", str(agent.root), "discover"]) == 3
    assert agent.store.runs()[0]["status"] == "PARTIAL"
