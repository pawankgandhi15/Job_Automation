import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from job_agent.cli import dispatch
from job_agent.common import WorkflowError, read_json, write_json


@pytest.mark.parametrize(
    "error_id,category,missing",
    [
        ("CmdletizationQuery_NotFound_TaskName", "ObjectNotFound", True),
        ("HRESULT 0x80041003", "PermissionDenied", False),
        ("ProviderUnavailable", "ObjectNotFound", False),
    ],
)
def test_task_lookup_distinguishes_absence_from_query_failure(
    tmp_path, error_id, category, missing
):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")
    (tmp_path / "pythonw.exe").touch()
    command = r"""
function Get-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName)
  $record = [System.Management.Automation.ErrorRecord]::new(
    [System.Exception]::new('Synthetic scheduler query failure'),
    $global:schedulerTestRequest.error_id,
    [System.Management.Automation.ErrorCategory]$global:schedulerTestRequest.category,
    $TaskName)
  $PSCmdlet.WriteError($record)
}
$global:schedulerTestRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
$request = $global:schedulerTestRequest
& $request.script -Action Status -Root $request.root -PythonExecutable $request.python -TaskName SyntheticTask
"""
    request = {
        "error_id": error_id,
        "category": category,
        "root": str(tmp_path),
        "python": str(tmp_path / "python.exe"),
        "script": str(Path(__file__).resolve().parent.parent / "scripts/windows_daily_task.ps1"),
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if missing:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"installed": False, "task_name": "SyntheticTask"}
    else:
        assert result.returncode != 0
        assert "Synthetic scheduler query failure" in result.stderr
        assert '"installed"' not in result.stdout


def test_failed_status_check_preserves_last_verified_schedule(agent, monkeypatch):
    recorded = {"installed": True, "task_name": "SyntheticTask", "enabled": True}
    agent.store.set_setting("installed_schedule", recorded)
    path = agent.root / "data/daily/schedule.json"
    write_json(path, recorded)

    def denied(*args):
        raise WorkflowError("Windows task operation failed: Access denied")

    monkeypatch.setattr("job_agent.schedule.windows_schedule", denied)
    with pytest.raises(WorkflowError, match="Access denied"):
        dispatch(Namespace(command="schedule-status"), agent)
    assert agent.store.setting("installed_schedule") == recorded
    assert read_json(path) == recorded
