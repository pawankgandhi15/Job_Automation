from copy import deepcopy

import pytest

from job_agent.candidate import education_record, education_summary, experience_months
from job_agent.common import read_json, write_json
from job_agent.configuration import ConfigurationError, validate_profile, validate_settings
from job_agent.engine import Agent
from job_agent.packages import build_package, export_resume_text
from job_agent.policy import evaluate


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(name=""),
        lambda value: value.update(education=[]),
        lambda value: value["education"][0].update(cgpa=11),
        lambda value: value["education"][0].update(start_year=2028),
        lambda value: value["education"][0].update(branch_aliases="CS"),
        lambda value: value["education"][0].update(level="invented"),
        lambda value: value["skill_evidence"].update(python=""),
        lambda value: value["experience"][0].update(start="bad"),
        lambda value: value["experience"][0].update(end="2010-01"),
        lambda value: value["availability"]["internship"].update(hours_per_day=True),
        lambda value: value["availability"]["internship"].update(hours_per_day=float("nan")),
        lambda value: value["availability"]["internship"].update(days_per_week=8),
        lambda value: value["availability"]["internship"].update(earliest_start_date="2027-02-01"),
        lambda value: value["availability"]["internship"].update(source=""),
        lambda value: value["availability"]["internship"].update(
            confirmed_at="2026-10-23T09:00:00"
        ),
        lambda value: value["work_authorization"].update(eligible="yes"),
    ],
)
def test_invalid_candidate_configuration_is_rejected_before_ledger_open(agent, tmp_path, change):
    destination = tmp_path / "invalid"
    profile = deepcopy(agent.profile)
    change(profile)
    write_json(destination / "config/profile.json", profile)
    write_json(destination / "config/settings.json", agent.settings)
    with pytest.raises(ConfigurationError):
        Agent(destination)
    assert not (destination / "data/ledger.sqlite3").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(shortlist_threshold=11),
        lambda value: value.update(approval_hours=0),
        lambda value: value.update(discovery_workers=100),
        lambda value: value.update(history_complete="true"),
        lambda value: value.update(sources={}),
        lambda value: value["sources"][0].update(provider="guessed"),
        lambda value: value["sources"][0].update(board="../other"),
        lambda value: value["sources"][0].update(
            official_careers_url="https://imposter.example/careers"
        ),
        lambda value: value.update(daily_task_name="bad/task"),
    ],
)
def test_invalid_runtime_settings_are_rejected(agent, change):
    settings = deepcopy(agent.settings)
    change(settings)
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_validation_never_rewrites_or_fills_unknown_candidate_facts(agent):
    original = deepcopy(agent.profile)
    assert validate_profile(agent.profile) == original
    assert agent.profile == read_json(agent.root / "config/profile.json")
    assert agent.profile["availability"]["internship"]["days_per_week"] is None
    assert agent.profile["availability"]["internship"]["earliest_start_date"] is None


def test_another_candidate_and_reordered_education_have_no_embedded_profile_claims(agent, job):
    profile = deepcopy(agent.profile)
    profile.update(
        name="Synthetic Candidate",
        email="candidate@example.org",
        phone="0000000000",
        experience=[],
        projects=[],
        credentials=[],
        achievements=[],
        skill_evidence={"rust": "Synthetic project evidence"},
    )
    profile["education"] = [
        {
            "degree": "B.Tech",
            "branch": "Computer Science",
            "institution": "Synthetic College",
            "start_year": 2020,
            "end_year": 2024,
            "status": "Completed",
            "cgpa": None,
        },
        {
            "degree": "M.Sc",
            "branch": "Computing",
            "institution": "Example University",
            "start_year": 2025,
            "end_year": 2027,
            "status": "Ongoing",
            "cgpa": None,
        },
    ]
    validate_profile(profile)
    result = evaluate(job, profile, agent.settings)
    package = build_package(job, result, profile, {"available": True, "profile_checked": True})
    assert package["screening_answers"]["undergraduate_degree"]["degree"] == "B.Tech"
    assert package["screening_answers"]["highest_ongoing_degree"]["degree"] == "M.Sc"
    assert "Example University" in package["outreach_draft"]["body"]
    assert "NIT Jalandhar" not in package["outreach_draft"]["body"]
    assert "Mechanical" not in " ".join(package["summary"])
    assert package["screening_answers"]["current_ctc"] is None
    assert "Grade not disclosed" in export_resume_text(profile)


def test_degree_aliases_do_not_imply_postgraduate_equivalence_or_create_passes(agent, job):
    profile = deepcopy(agent.profile)
    undergraduate = education_record(profile, "undergraduate")
    undergraduate.update(branch="Computer Science and Engineering", branch_aliases=["CSE"])
    job["constraints"] = {"accepted_undergraduate_branches": ["CSE"]}
    assert (
        evaluate(job, profile, agent.settings)["gates"]["undergraduate_branch"]["status"] == "PASS"
    )
    job["eligibility_gates"].pop("undergraduate_branch")
    assert (
        evaluate(job, profile, agent.settings)["gates"]["undergraduate_branch"]["status"]
        == "UNKNOWN"
    )
    job["constraints"] = {
        "accepted_undergraduate_branches": ["Mechanical Engineering"],
        "explicit_postgraduate_alternative": True,
    }
    assert (
        evaluate(job, profile, agent.settings)["gates"]["undergraduate_branch"]["status"]
        == "UNKNOWN"
    )


def test_missing_undergraduate_record_stays_unknown_and_dynamic_experience_is_not_capped(
    agent, job
):
    profile = deepcopy(agent.profile)
    profile["education"] = profile["education"][:1]
    job["constraints"] = {"accepted_undergraduate_branches": ["Computer Science"]}
    assert (
        evaluate(job, profile, agent.settings)["gates"]["undergraduate_branch"]["status"]
        == "UNKNOWN"
    )
    profile["experience"] = [
        {
            "company": "Example",
            "title": "Engineer",
            "type": "Full-time",
            "start": "2021-01",
            "end": "2023-12",
            "bullets": [],
        }
    ]
    profile["experience"].append(deepcopy(profile["experience"][0]))
    assert experience_months(profile, professional_only=True) == 36
    job["constraints"] = {"minimum_professional_years": 2}
    assert evaluate(job, profile, agent.settings)["gates"]["experience"]["status"] == "PASS"
    job["eligibility_gates"].pop("experience")
    assert evaluate(job, profile, agent.settings)["gates"]["experience"]["status"] == "UNKNOWN"
    assert "2025" in education_summary(profile)
