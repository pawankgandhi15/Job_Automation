from datetime import timedelta

import pytest

from job_agent.common import WorkflowError, canonical_url, now, timestamp
from job_agent.policy import evaluate


def assess(agent, job, **kwargs):
    return evaluate(job, agent.profile, agent.settings, **kwargs)


def test_complete_supported_evidence_and_undisclosed_pay(agent, job):
    result = assess(agent, job)
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["match_score"] == 9
    assert result["score_components"]["compensation"] == 0


@pytest.mark.parametrize(
    "gate", ["academic_schedule", "work_authorization", "undergraduate_branch", "start_date"]
)
def test_unknown_mandatory_gate_blocks_shortlist(agent, job, gate):
    del job["eligibility_gates"][gate]
    assert assess(agent, job)["status"] == "NEEDS_CLARIFICATION"


def test_secondary_source_cannot_pass_eligibility(agent, job):
    job["eligibility_gates"]["degree_level"]["source"] = "https://unrelated.example/jobs"
    assert assess(agent, job)["gates"]["degree_level"]["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "constraints,gate",
    [
        ({"minimum_professional_years": 2}, "experience"),
        ({"accepted_undergraduate_branches": ["Computer Science"]}, "undergraduate_branch"),
        ({"immediate_full_time": True}, "start_date"),
    ],
)
def test_explicit_incompatible_constraints_override_asserted_pass(agent, job, constraints, gate):
    job["constraints"] = constraints
    if gate == "start_date":
        job["employment_type"] = "full_time"
    result = assess(agent, job)
    assert result["status"] == "REJECTED"
    assert result["gates"][gate]["status"] == "FAIL"


def test_preferred_skill_gap_is_not_mandatory_failure(agent, job):
    job["technical_groups"].append(
        {"group": "Kubernetes", "priority": "preferred", "source": job["canonical_url"]}
    )
    result = assess(agent, job)
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["gaps"] == ["kubernetes"]
    assert result["score_components"]["technical"] == pytest.approx(4 * 5 / 6)


def test_required_unsupported_skill_rejects(agent, job):
    job["technical_groups"].append(
        {"group": "Terraform", "priority": "required", "source": job["canonical_url"]}
    )
    assert assess(agent, job)["status"] == "REJECTED"


def test_skill_synonyms_only_count_once_at_highest_priority(agent, job):
    job["technical_groups"].append(
        {"group": "Containerization", "priority": "required", "source": job["canonical_url"]}
    )
    groups = assess(agent, job)["technical_groups"]
    assert len(groups) == 3
    assert next(g for g in groups if g["group"] == "docker")["weight"] == 2


def test_no_technical_denominator_is_not_invented(agent, job):
    job["technical_groups"] = []
    result = assess(agent, job)
    assert result["match_score"] is None
    assert result["status"] == "NEEDS_CLARIFICATION"


def test_salary_exception_requires_explicit_acceptance_and_great_tier(agent, job):
    job["compensation"] = {
        "minimum": 10000,
        "currency": "INR",
        "period": "month",
        "source": job["canonical_url"],
    }
    assert assess(agent, job)["status"] == "NEEDS_CLARIFICATION"
    assert assess(agent, job, salary_exception=True)["status"] == "NEEDS_CLARIFICATION"
    job["tier"] = "GREAT"
    result = assess(agent, job, salary_exception=True)
    assert result["status"] == "READY_FOR_REVIEW"
    assert result["score_components"]["compensation"] == 0.5


def test_monthly_floor_and_annual_floor_are_not_interchangeable(agent, job):
    job["compensation"] = {
        "minimum": 600000,
        "currency": "INR",
        "period": "year",
        "source": job["canonical_url"],
    }
    assert assess(agent, job)["compensation_status"] == "PAY_NEEDS_CLARIFICATION"


def test_stale_profile_and_expired_deadline(agent, job):
    future = now().replace(year=2028)
    assert assess(agent, job, clock=future)["gates"]["student_status"]["status"] == "UNKNOWN"
    job["deadline"] = timestamp(now() - timedelta(seconds=1))
    assert assess(agent, job)["status"] == "REJECTED"


def test_naive_deadline_is_rejected(agent, job):
    job["deadline"] = "2027-01-01T12:00:00"
    with pytest.raises(WorkflowError, match="timezone"):
        assess(agent, job)


def test_only_known_tracking_parameters_are_removed():
    assert (
        canonical_url("https://example.com/jobs?jobId=123&utm_source=board&ref=abc")
        == "https://example.com/jobs?jobId=123&ref=abc"
    )


def test_old_non_programme_is_watchlisted(agent, job):
    job["posting_date"] = (now() - timedelta(days=40)).date().isoformat()
    assert assess(agent, job)["status"] == "NEEDS_CLARIFICATION"


def test_fee_is_a_hard_rejection(agent, job):
    job["recruitment_fee_required"] = True
    assert assess(agent, job)["status"] == "REJECTED"
