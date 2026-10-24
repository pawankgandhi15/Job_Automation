from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from .availability import month_label
from .common import WorkflowError, now, read_json, timestamp
from .engine import Agent, run_lock
from .types import Record


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Evidence-based job search workflow. No automatic employer-facing actions."
    )
    result.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Project/config/data root",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Show a command plan without opening the ledger or executing a handler",
    )
    result.add_argument(
        "--json", action="store_true", help="Use JSON output (also the backward-compatible default)"
    )
    sub = result.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve", help="Run the local authenticated review workstation")
    server.add_argument("--port", type=int, default=8765)
    sub.add_parser("preflight", help="Inspect configuration, resume and actual capabilities")
    sub.add_parser("notifications", help="List pending local activity alerts")
    notify = sub.add_parser(
        "notify", help="Explicitly deliver one pending alert to a configured destination"
    )
    notify.add_argument("--id", type=int, required=True)
    notify.add_argument("--channel", choices=["desktop", "webhook"], required=True)
    sub.add_parser("db-status", help="Inspect applied database migrations and local search index")
    search = sub.add_parser(
        "search", help="Search stored companies, roles, locations and job descriptions offline"
    )
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--state")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--offset", type=int, default=0)
    sub.add_parser("discover", help="Fetch configured official ATS boards and write a report")
    sub.add_parser(
        "daily", help="Discover broadly and select different companies for today's review"
    )
    sub.add_parser(
        "source-pool-refresh",
        help="Verify supported ATS links on configured candidate employer pages",
    )
    sub.add_parser(
        "schedule-install",
        help="Register daily company discovery at 09:00 IST in Windows Task Scheduler",
    )
    sub.add_parser("schedule-status", help="Check the actual Windows daily-discovery task")
    sub.add_parser("report", help="Evaluate the ledger and generate a local review report")
    sub.add_parser("queries", help="Show IBM-first browser search queries; does not run searches")
    queue = sub.add_parser(
        "review-queue", help="Prioritized unresolved evidence and candidate/employer tasks"
    )
    queue.add_argument("--limit", type=int, default=20)
    runs = sub.add_parser("runs", help="Persistent discovery run history and source outcomes")
    runs.add_argument("--limit", type=int, default=10)
    sub.add_parser(
        "source-status", help="Last recorded check of every configured source; no network request"
    )
    sub.add_parser(
        "availability", help="Read confirmed availability and remaining precision limits"
    )
    sub.add_parser(
        "company-web", help="Refresh the local company overview and shareable company-only JSON"
    )
    source = sub.add_parser(
        "source-add",
        help="Find supported ATS or JobPosting sources linked by an official employer page",
    )
    source.add_argument("--company", required=True)
    source.add_argument("--website", required=True)
    source.add_argument("--careers-url", required=True)
    source.add_argument("--tier", choices=["GOOD", "BETTER", "GREAT"], default="GOOD")
    status = sub.add_parser("status", help="Read the ledger; never sends anything")
    status.add_argument("--id")
    imp = sub.add_parser("import", help="Import analyst-reviewed primary-source evidence")
    imp.add_argument("file", type=Path)
    exp = sub.add_parser("export", help="Export the entire ledger, event history and approvals")
    exp.add_argument("--output", type=Path, default=Path("data/ledger-export.json"))
    edit = sub.add_parser(
        "export-opportunity", help="Export an opportunity for evidence enrichment and reimport"
    )
    edit.add_argument("id")
    edit.add_argument("--output", type=Path, required=True)
    deliver = sub.add_parser(
        "mark-delivered", help="Record that you reviewed a local report; does not email it"
    )
    deliver.add_argument("report_id")
    approve = sub.add_parser(
        "approve", help="Approve only the exact displayed actions; does not execute"
    )
    approve.add_argument("id", nargs="?")
    approve.add_argument(
        "--channels", nargs="+", choices=["portal", "outreach", "follow_up"], required=True
    )
    approve.add_argument(
        "--all", action="store_true", help="Most recent delivered report's eligible items only"
    )
    salary = sub.add_parser(
        "accept-pay-exception",
        help="Explicitly accept this GREAT-tier role's disclosed below-floor pay",
    )
    salary.add_argument("id")
    repost = sub.add_parser(
        "resolve-repost", help="Attest that a suspected repost is a distinct vacancy, after review"
    )
    repost.add_argument("id")
    repost.add_argument("--distinct-vacancy", required=True, action="store_true")
    repost.add_argument("--evidence", required=True)
    consent = sub.add_parser(
        "record-consent", help="Record an explicit answer to one displayed mandatory consent"
    )
    consent.add_argument("id")
    consent.add_argument("--question", required=True)
    consent.add_argument("--answer", choices=["yes", "no"], required=True)
    answer = sub.add_parser(
        "record-answer",
        help="Record an explicit supported answer to a displayed required screening question",
    )
    answer.add_argument("id")
    answer.add_argument("--question", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--evidence", required=True)
    resume = sub.add_parser(
        "confirm-resume",
        help="Attest that you reconciled the profile with this exact resume version",
    )
    resume.add_argument("--file", type=Path, required=True)
    resume.add_argument("--variant", choices=["cloud", "backend", "sde"])
    resume.add_argument("--profile-reviewed", action="store_true", required=True)
    resume_text = sub.add_parser(
        "resume-text",
        help="Generate a selectable-text provisional resume preserving supplied facts",
    )
    resume_text.add_argument("--output", type=Path, default=Path("reports/resume_provisional.txt"))
    recheck = sub.add_parser(
        "recheck",
        help="Record a just-completed manual official vacancy/eligibility/destination/duplicate check",
    )
    recheck.add_argument("id")
    recheck.add_argument(
        "--evidence",
        required=True,
        help="Source URLs and a concise account of the checks actually performed",
    )
    handoff = sub.add_parser(
        "handoff", help="Consume a valid approval and create a single-use manual execution package"
    )
    handoff.add_argument("id")
    handoff.add_argument("--channel", choices=["portal", "outreach", "follow_up"], required=True)
    record = sub.add_parser(
        "record-result", help="Reconcile a handoff using actual provider evidence"
    )
    record.add_argument("token")
    record.add_argument(
        "--outcome", choices=["confirmed", "failed", "uncertain", "not_submitted"], required=True
    )
    record.add_argument("--evidence")
    record.add_argument(
        "--occurred-at", help="Actual action timestamp including offset; defaults to now"
    )
    check = sub.add_parser(
        "check-status", help="Record an authorized manual reply/application status check"
    )
    check.add_argument("id")
    check.add_argument(
        "--status",
        choices=[
            "no_reply",
            "unknown",
            "unknown_reconfirmed",
            "replied",
            "rejected",
            "withdrawn",
            "interview",
            "closed",
        ],
        required=True,
    )
    check.add_argument("--evidence", required=True)
    skip = sub.add_parser("skip", help="Dismiss an opportunity and invalidate unused approvals")
    skip.add_argument("id")
    sub.add_parser(
        "pause",
        help="Block discovery/new handoffs in this ledger; inspect external schedules separately",
    )
    sub.add_parser("resume", help="Resume the ledger workflow without reviving expired approvals")
    schedule = sub.add_parser(
        "schedule-template",
        help="Write Windows Task Scheduler XML for 09:00 IST; does not install a task",
    )
    schedule.add_argument("--output", type=Path, default=Path("data/daily-discovery.xml"))
    sub.add_parser(
        "demo", help="Generate an isolated synthetic report; does not alter the live ledger"
    )
    return result


def output_path(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def queries(agent: Agent) -> Record:
    year = agent.profile["education"][0]["end_year"]
    values = [
        f"IBM careers India systems cloud software engineer intern {year}",
        "Python backend intern Django FastAPI India",
        "cloud DevOps intern AWS Linux Docker India",
        f"graduate software engineer {year} India",
        "site:jobs.lever.co India software intern",
        "site:boards.greenhouse.io India backend intern",
        f"site:myworkdayjobs.com India graduate {year} software",
        "India platform infrastructure automation engineering intern",
    ]
    start = agent.profile.get("availability", {}).get("internship", {}).get("earliest_start_month")
    if start:
        hours = agent.profile["availability"]["internship"].get("hours_per_day")
        values += [
            f"India software internship {month_label(start)} part time {hours:g} hours"
            if hours
            else f"India software internship {month_label(start)} part time",
            f"India junior Python developer {start} flexible hours",
        ]
    return {
        "queries": [
            {"query": q, "url": "https://www.google.com/search?" + urlencode({"q": q})}
            for q in values
        ],
        "availability": agent.profile.get("availability", {}),
        "executed": False,
        "note": "Search results are leads. Verify employer identity and each official vacancy before importing decisive facts.",
    }


def schedule_template(root: Path, path: Path) -> Record:
    trigger = now().replace(hour=9, minute=0, second=0, microsecond=0)
    if trigger <= now():
        trigger += timedelta(days=1)
    python = Path(sys.executable).resolve()
    args = f'-m job_agent --root "{root}" discover'
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Read-only job discovery at 09:00 IST; report available after completion. No email or application execution.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{trigger.isoformat()}</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT1H</ExecutionTimeLimit></Settings>
  <Actions><Exec><Command>{escape(str(python))}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(str(root))}</WorkingDirectory></Exec></Actions>
</Task>
"""
    # Do not install or claim a schedule just because the template exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return {
        "path": str(path),
        "installed": False,
        "trigger_ist": trigger.isoformat(),
        "note": "Only install after explicitly choosing recurring operation. Use Windows Task Scheduler Import Task and select your user account. Check task status after installation. Daily reports finish after the trigger. The machine must be available; scheduling does not create an email delivery channel.",
    }


def demo(root: Path) -> Record:
    destination = root / "data" / "demo" / uuid.uuid4().hex[:8]
    (destination / "config").mkdir(parents=True)
    shutil.copy2(root / "config" / "settings.json", destination / "config" / "settings.json")
    shutil.copy2(root / "config" / "profile.json", destination / "config" / "profile.json")
    sample = read_json(root / "examples" / "opportunity.sample.json")
    agent = Agent(destination)
    try:
        for job in sample["opportunities"]:
            job["last_verified_at"] = timestamp()
            job["posting_date"] = now().date().isoformat()
            for gate in job["eligibility_gates"].values():
                gate["checked_at"] = timestamp()
            agent.store.upsert(job)
        report = agent.report()
        path = Path(report["path"])
        path.write_text(
            "# SYNTHETIC DEMO — NOT REAL JOB VACANCIES\n\n" + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        report["synthetic_demo"] = True
        report["note"] = (
            "Isolated ledger. No live search, applications, messages or schedules were executed."
        )
        return report
    finally:
        agent.close()


def dispatch(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .commands import dispatch as route

    return route(args, agent)


def _main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.dry_run:
        if sys.stdout is not None:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "command": args.command,
                        "handler_executed": False,
                        "ledger_opened": False,
                        "external_actions_performed": False,
                    }
                )
            )
        return 0
    if args.command == "serve":
        from .server import serve

        if not 1024 <= args.port <= 65535:
            parser().error("Local server port must be 1024–65535.")
        serve(args.root.resolve(), args.port)
        return 0
    result: Record | list[Record]
    try:
        if args.command == "demo":
            result = demo(args.root.resolve())
        else:
            with run_lock(args.root):
                agent = Agent(args.root)
                try:
                    result = dispatch(args, agent)
                finally:
                    agent.close()
        if sys.stdout is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        if (
            args.command in {"discover", "daily"}
            and isinstance(result, dict)
            and result["counts"].get("sources_checked", 0)
            and result["counts"].get("sources_failed") == result["counts"]["sources_checked"]
        ):
            return 3  # Scheduler/terminal can distinguish a report from successful live discovery.
        return 0
    except (WorkflowError, OSError, KeyError, TypeError, ValueError) as exc:
        if sys.stderr is not None:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    from .observability import trace_id

    token = trace_id.set(uuid.uuid4().hex)
    try:
        return _main(argv)
    finally:
        trace_id.reset(token)


if __name__ == "__main__":
    raise SystemExit(main())
