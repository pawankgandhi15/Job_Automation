"""Register and inspect the one local, read-only daily discovery task."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .common import WorkflowError, read_json
from .types import Record

TASK_NAME = "JobAgent-DailyCompanyDiscovery"


def windows_schedule(root: Path, action: str) -> Record:
    if os.name != "nt":
        raise WorkflowError("This scheduler integration requires Windows Task Scheduler.")
    script = (root / "scripts" / "windows_daily_task.ps1").read_text(encoding="utf-8")
    # The request is data on stdin; no candidate paths are interpolated into code.
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $request = [Console]::In.ReadToEnd() | ConvertFrom-Json; & {\n"
        + script
        + "\n} -Action $request.action -Root $request.root -PythonExecutable $request.python -TaskName $request.task_name"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(
            {
                "action": action,
                "root": str(root),
                "python": sys.executable,
                "task_name": read_json(root / "config/settings.json").get(
                    "daily_task_name", TASK_NAME
                ),
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode:
        raise WorkflowError(
            "Windows task operation failed: " + (result.stderr.strip() or result.stdout.strip())
        )
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise WorkflowError("Windows task returned an unexpected result shape.")
        return value
    except json.JSONDecodeError as exc:
        raise WorkflowError("Windows task returned no usable verification result.") from exc
