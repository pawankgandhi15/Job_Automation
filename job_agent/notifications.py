"""Persistent in-app alerts; optional desktop/webhook delivery is explicit opt-in."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

from .common import WorkflowError, digest, domain, now, parse_time
from .types import Record

if TYPE_CHECKING:
    from .services.context import AgentContext


def scan_notifications(agent: AgentContext) -> None:
    for row in agent.store.all():
        notices = []
        if (
            row["payload"].get("tier") == "GREAT"
            and (row["evaluation"] or {}).get("status") == "READY_FOR_REVIEW"
        ):
            notices.append(
                (
                    "STRONG_MATCH",
                    row["package_hash"],
                    "A verified GREAT-tier role is ready for review: " + row["opportunity_id"],
                )
            )
        if (
            row["follow_up_due_at"]
            and not row["follow_up_sent_at"]
            and parse_time(row["follow_up_due_at"]) <= now()
            and row["state"]
            not in {"REJECTED", "WITHDRAWN", "REPLIED", "INTERVIEW", "CLOSED", "DISMISSED"}
        ):
            notices.append(
                (
                    "FOLLOW_UP_DUE",
                    row["follow_up_due_at"],
                    "Follow-up status needs review for " + row["opportunity_id"],
                )
            )
        for kind, version, message in notices:
            key = "notice:" + digest([row["id"], kind, version])
            if not agent.store.setting(key):
                with agent.store.transaction():
                    agent.store.event(row["id"], kind, message)
                    agent.store.set_setting(key, True)
    for attempt in agent.store.db.execute(
        "SELECT token,expires_at FROM attempt_expiry JOIN attempts USING(token) WHERE attempts.status='EXECUTING'"
    ).fetchall():
        remaining = parse_time(attempt["expires_at"]) - now()
        key = "notice:expiry:" + attempt["token"]
        if timedelta(0) < remaining <= timedelta(hours=12) and not agent.store.setting(key):
            with agent.store.transaction():
                agent.store.event(
                    None,
                    "HANDOFF_EXPIRING",
                    "An outstanding manual handoff expires within twelve hours.",
                )
                agent.store.db.execute(
                    "INSERT INTO notifications(kind,message,created_at) VALUES('HANDOFF_EXPIRING',?,?)",
                    (
                        "An outstanding manual handoff expires within twelve hours.",
                        now().isoformat(),
                    ),
                )
                agent.store.set_setting(key, True)


def deliver_notification(message: str, configuration: Record) -> None:
    channel = configuration.get("channel")
    if not configuration.get("enabled"):
        raise WorkflowError(
            "Notification delivery is disabled; enable a selected destination explicitly."
        )
    if channel == "webhook":
        endpoint = configuration.get("url", "")
        domain(endpoint)
        request = Request(
            endpoint,
            data=json.dumps({"content": message}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise WorkflowError("Notification endpoint did not accept delivery.")
    elif channel == "desktop" and sys.platform == "win32":
        script = """$text=[Console]::In.ReadToEnd(); [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null; $xml=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText01); $xml.GetElementsByTagName('text').Item(0).AppendChild($xml.CreateTextNode($text)) > $null; [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JobAgent').Show([Windows.UI.Notifications.ToastNotification]::new($xml))"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            input=message,
            text=True,
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise WorkflowError(
                "Windows did not accept the desktop notification; keep the in-app alert and check notification settings."
            )
    else:
        raise WorkflowError("Select a supported notification destination.")
