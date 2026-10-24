import pytest

from job_agent.evidence import experience_requirements
from job_agent.policy import evaluate
from job_agent.sources import relevant_lead


@pytest.mark.parametrize(
    "description,minimum",
    [
        (
            "YOU MIGHT BE A GOOD FIT IF YOU - (Required) Have 4+ years of professional experience shipping Go.",
            4,
        ),
        ("WHAT YOU'LL BRING - At least 5 years of backend or systems engineering experience.", 5),
        ("YOU MIGHT BE A GOOD FIT IF YOU - Have 5+ years of experience in cloud governance.", 5),
        (
            "YOU MIGHT BE A FIT IF YOU HAVE: - 5+ years experience in SRE/DevOps/Cloud Infrastructure; - 4+ years experience in networking.",
            5,
        ),
        ("Minimum 2 years of professional software experience required.", 2),
        ("Requirements: 3–5 years of software development experience.", 3),
        ("Requirements:\n• 2+ years of production engineering experience.", 2),
    ],
)
def test_decisive_numeric_experience_becomes_rejection_evidence(agent, job, description, minimum):
    job["description"] = description
    assessment = evaluate(job, agent.profile, agent.settings)
    assert assessment["status"] == "REJECTED"
    gate = assessment["gates"]["experience"]
    assert gate["status"] == "FAIL"
    assert gate["evidence"]["minimum_years"] == minimum
    assert gate["evidence"]["quote"] in description
    assert gate["source"] == job["canonical_url"]


@pytest.mark.parametrize(
    "description",
    [
        "Requirements: Proficient in Go, ideally with 5+ years building production services.",
        "Preferred qualifications: 5+ years of professional software experience.",
        "Requirements: 3+ years of experience preferred.",
        "Requirements: Up to 5 years of software experience.",
        "Our company has 10 years of software development experience.",
        "Requirements: We have 10+ years of experience building software.",
        "Requirements: A 4-year university degree in software engineering.",
        "Requirements: 2+ years experience or equivalent personal projects.",
        "Requirements: 2+ years of experience or a graduate with strong projects.",
        "Requirements: 2+ years of software experience is not required.",
        "Requirements: Familiarity with software development. No minimum years of experience.",
        "We provide a 3-year software engineering learning programme.",
        "Requirements: 0–2 years of professional software experience.",
    ],
)
def test_preferences_maxima_alternatives_and_education_are_not_hard_failures(
    agent, job, description
):
    job["description"] = description
    assert evaluate(job, agent.profile, agent.settings)["gates"]["experience"]["status"] == "PASS"


def test_preference_in_subordinate_cloud_skill_does_not_erase_required_years(agent, job):
    job["description"] = (
        "YOU MIGHT BE A GOOD FIT IF YOU - Have 5+ years experience with cloud infrastructure (AWS required, additional experience with GCP and Azure is a plus)."
    )
    assert evaluate(job, agent.profile, agent.settings)["status"] == "REJECTED"


def test_single_required_year_is_not_promoted_to_two(agent, job):
    job["description"] = "Requirements: 1+ years of software engineering experience."
    assert evaluate(job, agent.profile, agent.settings)["gates"]["experience"]["status"] == "PASS"


def test_unattributed_text_cannot_establish_a_failed_primary_gate(agent, job):
    job["canonical_url"] = "https://unverified.example/job"
    job["description"] = "Requirements: 5+ years of professional software experience."
    assert evaluate(job, agent.profile, agent.settings)["gates"]["experience"]["status"] == "PASS"


def test_product_lead_is_not_an_engineering_lead(agent, job):
    job["role"] = "Product Lead - Infrastructure"
    assert relevant_lead(job, broaden=True) is False
    assert evaluate(job, agent.profile, agent.settings)["status"] == "REJECTED"
    job["role"] = "Lead Infrastructure Engineer"
    assert relevant_lead(job, broaden=True) is True


def test_experience_without_a_supported_section_stays_undecided():
    assert (
        experience_requirements(
            "Our users bring 5+ years of software experience.", "https://example.com"
        )
        == []
    )
