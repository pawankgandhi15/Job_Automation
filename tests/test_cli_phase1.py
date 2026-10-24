from argparse import Namespace

import pytest

from job_agent.cli import dispatch, main
from job_agent.common import WorkflowError, read_json, write_json


def command(agent, name, **options):
    return dispatch(Namespace(command=name, **options), agent)


@pytest.mark.parametrize(
    "name,options",
    [
        ("preflight", {}),
        ("db-status", {}),
        ("search", {"query": "Python", "state": None, "limit": 10, "offset": 0}),
        ("queries", {}),
        ("availability", {}),
        ("source-status", {}),
        ("status", {"id": None}),
        ("review-queue", {"limit": 5}),
        ("runs", {"limit": 5}),
        ("company-web", {}),
    ],
)
def test_read_and_export_commands_never_approve_or_attempt_employer_actions(
    agent, job, name, options
):
    agent.store.upsert(job)
    result = command(agent, name, **options)
    assert isinstance(result, dict)
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert agent.store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_export_import_and_resume_text_commands_use_isolated_files(agent, job):
    source = agent.root / "opportunities.json"
    write_json(source, {"opportunities": [job]})
    command(agent, "import", file=source)
    result = command(agent, "export-opportunity", id="JOB-001", output=agent.root / "out.json")
    assert result
    assert read_json(agent.root / "out.json")["opportunities"][0]["company"] == job["company"]
    text = agent.root / "resume.txt"
    assert command(agent, "resume-text", output=text)["provisional"]
    assert text.is_file()


def test_cli_approval_handoff_and_reconciliation_preserve_exact_scope(prepared):
    agent, job_id = prepared
    approved = command(agent, "approve", id=job_id, channels=["portal"], all=False)
    assert approved
    command(
        agent, "recheck", id=job_id, evidence="Synthetic current primary-source and duplicate check"
    )
    handoff = command(agent, "handoff", id=job_id, channel="portal")
    result = command(
        agent,
        "record-result",
        token=handoff["token"],
        outcome="uncertain",
        evidence="Synthetic timeout",
        occurred_at=None,
    )
    assert result["state"] == "SUBMISSION_UNCERTAIN"
    with pytest.raises(WorkflowError):
        command(agent, "approve", id=job_id, channels=["portal"], all=False)
    command(agent, "check-status", id=job_id, status="replied", evidence="Synthetic employer reply")
    assert agent.store.get(job_id)["state"] == "REPLIED"


def test_pause_resume_and_skip_are_persistent_and_do_not_submit(agent, job):
    job_id, _ = agent.store.upsert(job)
    assert command(agent, "pause")["paused"]
    assert not command(agent, "resume")["paused"]
    assert command(agent, "skip", id=job_id)["dismissed"] == job_id
    assert agent.store.get(job_id)["state"] == "DISMISSED"


def test_cli_reports_configuration_errors_and_offline_search_as_json(agent, capsys):
    assert main(["--root", str(agent.root), "search", "Python"]) == 0
    assert '"results"' in capsys.readouterr().out
    settings = read_json(agent.root / "config/settings.json")
    settings["approval_hours"] = 0
    write_json(agent.root / "config/settings.json", settings)
    assert main(["--root", str(agent.root), "preflight"]) == 2
    assert "approval_hours" in capsys.readouterr().err
