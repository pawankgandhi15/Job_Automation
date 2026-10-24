from argparse import Namespace
from xml.etree import ElementTree

import pytest

from job_agent.cli import dispatch, main, schedule_template
from job_agent.common import WorkflowError, read_json
from job_agent.engine import run_lock


def test_scheduled_template_is_only_an_artifact(agent):
    path = agent.root / "data" / "task.xml"
    result = schedule_template(agent.root, path)
    assert result["installed"] is False
    document = ElementTree.parse(path)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert document.find(".//t:StartBoundary", ns).text.endswith("+05:30")
    assert document.find(".//t:Arguments", ns).text.endswith("discover")
    assert agent.status()["applications_confirmed"] == 0


def test_run_lock_prevents_overlapping_workflow_processes(agent):
    with run_lock(agent.root):
        with pytest.raises(WorkflowError, match="lock"):
            with run_lock(agent.root):
                pytest.fail("Second runner entered the ledger")


def test_cli_exports_complete_evidence_and_approvals(prepared, capsys):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    path = agent.root / "data" / "export.json"
    assert main(["--root", str(agent.root), "export", "--output", str(path)]) == 0
    result = read_json(path)
    assert result["opportunities"][0]["opportunity_id"] == job_id
    assert result["approvals"][0]["channel"] == "portal"
    assert result["events"]
    assert result["reported_companies"]


def test_unresolved_required_answer_blocks_until_explicit_answer(agent, job):
    job["required_questions"] = ["Confirmed weekly internship hours"]
    job_id, _ = agent.store.upsert(job)
    agent.prepare()
    assert any("screening" in text for text in agent.store.get(job_id)["package"]["blockers"])
    dispatch(
        Namespace(
            command="record-answer",
            id=job_id,
            question="Confirmed weekly internship hours",
            answer="20 hours, subject to confirmed timetable",
            evidence="Synthetic candidate confirmation",
        ),
        agent,
    )
    agent.prepare()
    assert not agent.store.get(job_id)["package"]["blockers"]


def test_mandatory_consent_is_never_implied_by_apply(agent, job):
    job["required_consents"] = ["Synthetic mandatory privacy consent"]
    job_id, _ = agent.store.upsert(job)
    report = agent.report()
    agent.deliver(report["report_id"])
    with pytest.raises(WorkflowError, match="consent"):
        agent.approve(job_id, ["portal"])
    dispatch(
        Namespace(
            command="record-consent", id=job_id, question=job["required_consents"][0], answer="yes"
        ),
        agent,
    )
    agent.prepare()
    assert not agent.store.get(job_id)["package"]["blockers"]
