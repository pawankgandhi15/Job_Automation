from copy import deepcopy

from job_agent.common import digest, read_json, write_json
from job_agent.company_web import export_company_web
from job_agent.role_briefs import role_details
from job_agent.structured import employment_type


def test_internal_systems_does_not_imply_an_internship():
    assert employment_type("FULL_TIME", "Software Engineer, Internal Systems") == "full_time"
    assert employment_type("", "Software Engineer, Internal Systems") == "unknown"
    assert employment_type("", "Software Engineer Intern") == "internship"
    assert employment_type("INTERNSHIP", "Software Engineering") == "internship"


def test_reviewed_highlights_match_exact_employer_description_and_expire_on_change(job):
    original = deepcopy(job)
    brief = {
        "description_sha256": digest(job["description"]),
        "summary": "Build backend APIs.",
        "requirements": ["Python or Go; one language is sufficient."],
        "skills": ["Python / Go"],
        "preferred": ["Docker is optional."],
        "stack": ["PostgreSQL"],
        "screening_answers": "private",
    }
    reviewed = {"roles": {job["canonical_url"]: brief}}
    result = role_details(job, reviewed)
    assert result["method"] == "reviewed"
    assert result["skills"] == ["Python / Go"]
    assert "screening_answers" not in result
    job["description"] += " Changed employer requirements."
    assert role_details(job, reviewed)["method"] == "unavailable"
    assert original["description"] != job["description"]


def test_new_public_jds_get_excerpts_without_promoting_optional_skills(job):
    job.update(
        provider="ashby",
        description=(
            "ABOUT THE ROLE Build backend APIs and operate production services. "
            "WHAT WE ARE LOOKING FOR • Python or Go experience; one language is sufficient. "
            "• At least 2 years operating distributed systems. "
            "NICE TO HAVE • Kubernetes and Rust experience would be a bonus. "
            "WHAT WE OFFER Fully remote work and health insurance."
        ),
    )
    result = role_details(job)
    assert result["method"] == "jd_excerpts"
    assert result["summary"] == "Build backend APIs and operate production services."
    assert result["requirements"][0] == "Python or Go experience; one language is sufficient."
    assert "Python" in result["skills"]
    assert "Kubernetes" not in result["skills"]
    assert "Rust" not in result["skills"]
    assert "Kubernetes" in result["preferred"][0]
    assert "health insurance" not in str(result)


def test_analyst_notes_and_missing_requirements_are_not_invented(job):
    job["description"] = (
        "ABOUT THE ROLE Candidate-specific private note. REQUIREMENTS Private screening answer."
    )
    assert role_details(job)["summary"] is None
    job.update(
        provider="lever", description="ABOUT THE ROLE Build reliable APIs for our customers."
    )
    result = role_details(job)
    assert result["summary"]
    assert result["requirements"] == []
    assert result["skills"] == []


def test_export_keeps_jd_highlights_on_future_snapshot_refreshes(agent, job):
    agent.store.upsert(job)
    config = {
        "roles": {
            job["canonical_url"]: {
                "description_sha256": digest(job["description"]),
                "summary": "Build backend APIs.",
                "requirements": ["Python programming."],
                "skills": ["Python"],
            }
        }
    }
    write_json(agent.root / "config/role_briefs.json", config)
    for _ in range(2):
        export_company_web(agent.root, agent.prepare())
        snapshot = read_json(agent.root / "web/company-overview/public/companies.json")
        details = snapshot["companies"][0]["jobs"][0]["details"]
        assert details["summary"] == "Build backend APIs."
        assert details["source"] == job["canonical_url"]
