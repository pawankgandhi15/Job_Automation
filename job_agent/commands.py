from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .availability import availability_text, resume_profile_digest
from .common import WorkflowError, digest, read_json, timestamp, write_json
from .engine import Agent
from .packages import export_resume_text
from .sources import discover_employer_sources
from .types import Record


@dataclass(frozen=True)
class CommandSpec:
    handler: Callable[[argparse.Namespace, Agent], Record | list[Record]]
    mutates_state: bool


COMMANDS: dict[str, CommandSpec] = {}


def handle_preflight(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.preflight()


COMMANDS["preflight"] = CommandSpec(handle_preflight, False)


def handle_notifications(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return {
        "items": [
            dict(row)
            for row in agent.store.db.execute(
                "SELECT * FROM notifications WHERE acknowledged_at IS NULL ORDER BY id DESC LIMIT 100"
            )
        ]
    }


COMMANDS["notifications"] = CommandSpec(handle_notifications, False)


def handle_notify(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .notifications import deliver_notification

    row = agent.store.db.execute(
        "SELECT * FROM notifications WHERE id=? AND acknowledged_at IS NULL", (args.id,)
    ).fetchone()
    if not row:
        raise WorkflowError("No pending notification with that ID.")
    configuration = (
        {"channel": "desktop", "enabled": True}
        if args.channel == "desktop"
        else agent.settings.get("webhook_notifications", {})
    )
    if args.channel == "webhook" and configuration.get("channel") != "webhook":
        raise WorkflowError("Configure an explicitly enabled webhook destination first.")
    deliver_notification(row["message"], configuration)
    agent.store.db.execute(
        "UPDATE notifications SET acknowledged_at=? WHERE id=?", (timestamp(), args.id)
    )
    agent.store.event(None, "NOTIFICATION_DELIVERED", args.channel, {"notification_id": args.id})
    return {"delivered": args.id, "channel": args.channel}


COMMANDS["notify"] = CommandSpec(handle_notify, True)


def handle_db_status(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.store.schema_status()


COMMANDS["db-status"] = CommandSpec(handle_db_status, False)


def handle_search(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return {
        "results": agent.store.search(
            args.query, state=args.state, limit=args.limit, offset=args.offset
        ),
        "network_checked_now": False,
    }


COMMANDS["search"] = CommandSpec(handle_search, False)


def handle_queries(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .cli import queries

    return queries(agent)


COMMANDS["queries"] = CommandSpec(handle_queries, False)


def handle_availability(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return {
        "confirmed": agent.profile.get("availability", {}),
        "internship": availability_text(agent.profile, "internship"),
        "full_time": availability_text(agent.profile, "full_time"),
    }


COMMANDS["availability"] = CommandSpec(handle_availability, False)


def handle_company_web(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    root = agent.root
    from .company_web import export_company_web
    from .daily import current_daily_selection

    return export_company_web(
        root,
        agent.prepare(),
        agent.store.setting("last_discovery", {}),
        daily=current_daily_selection(agent.store),
    )


COMMANDS["company-web"] = CommandSpec(handle_company_web, True)


def handle_source_pool_refresh(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    agent.active()
    from .daily import refresh_source_pool

    return refresh_source_pool(agent)


COMMANDS["source-pool-refresh"] = CommandSpec(handle_source_pool_refresh, True)


def handle_daily(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.daily()


COMMANDS["daily"] = CommandSpec(handle_daily, True)


def handle_schedule_install(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    command, root = args.command, agent.root
    from .schedule import windows_schedule

    if command == "schedule-install":
        agent.active()
    result = windows_schedule(root, "Install" if command == "schedule-install" else "Status")
    agent.store.set_setting("installed_schedule", result)
    write_json(root / "data" / "daily" / "schedule.json", result)
    if command == "schedule-install":
        agent.store.event(None, "DAILY_SCHEDULE_REGISTERED", "VERIFIED", result)
    return result


COMMANDS["schedule-install"] = CommandSpec(handle_schedule_install, True)
COMMANDS["schedule-status"] = CommandSpec(handle_schedule_install, True)


def handle_review_queue(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    command = args.command
    if not 1 <= args.limit <= 1000:
        raise WorkflowError("Limit must be between 1 and 1000.")
    return (
        agent.review_queue(args.limit)
        if command == "review-queue"
        else {"runs": agent.store.runs(args.limit)}
    )


COMMANDS["review-queue"] = CommandSpec(handle_review_queue, False)
COMMANDS["runs"] = CommandSpec(handle_review_queue, False)


def handle_source_status(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return {"sources": agent.store.source_status(), "network_checked_now": False}


COMMANDS["source-status"] = CommandSpec(handle_source_status, False)


def handle_source_add(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    root = agent.root
    agent.active()
    sources = discover_employer_sources(
        args.company, args.website, args.careers_url, args.tier, agent.http
    )
    existing = {
        (s["official_website"], s["provider"], s.get("board"), tuple(s.get("posting_urls", [])))
        for s in agent.settings["sources"]
    }
    added = [
        s
        for s in sources
        if (
            s["official_website"],
            s["provider"],
            s.get("board"),
            tuple(s.get("posting_urls", [])),
        )
        not in existing
    ]
    agent.settings["sources"].extend(added)
    write_json(root / "config" / "settings.json", agent.settings)
    agent.store.event(None, "EMPLOYER_SOURCES_ADDED", "VERIFIED_LINKS", {"sources": added})
    return {"added": added, "vacancies_fetched": False}


COMMANDS["source-add"] = CommandSpec(handle_source_add, True)


def handle_status(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.store.get(args.id) if args.id else agent.status()


COMMANDS["status"] = CommandSpec(handle_status, False)


def handle_import(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return {"imported": agent.import_jobs(read_json(args.file))}


COMMANDS["import"] = CommandSpec(handle_import, True)


def handle_discover(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.discover()


COMMANDS["discover"] = CommandSpec(handle_discover, True)


def handle_report(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.report()


COMMANDS["report"] = CommandSpec(handle_report, True)


def handle_export(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .cli import output_path

    root = agent.root
    path = output_path(root, args.output)
    write_json(path, agent.store.export())
    return {"path": str(path)}


COMMANDS["export"] = CommandSpec(handle_export, True)


def handle_export_opportunity(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .cli import output_path

    root = agent.root
    path = output_path(root, args.output)
    write_json(path, {"opportunities": [agent.store.get(args.id)["payload"]]})
    return {"path": str(path)}


COMMANDS["export-opportunity"] = CommandSpec(handle_export_opportunity, True)


def handle_mark_delivered(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.deliver(args.report_id)


COMMANDS["mark-delivered"] = CommandSpec(handle_mark_delivered, True)


def handle_confirm_resume(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    root = agent.root
    path = args.file.resolve()
    if not path.is_file():
        raise WorkflowError("The designated resume file does not exist.")
    variant = getattr(args, "variant", None)
    if variant:
        agent.settings.setdefault("resume_variants", {})[variant] = str(path)
    else:
        agent.settings["resume_path"] = str(path)
    write_json(root / "config" / "settings.json", agent.settings)
    resume = (
        agent.resume_for(
            {
                "role": {
                    "cloud": "Cloud Engineer",
                    "backend": "Backend Engineer",
                    "sde": "Software Engineer",
                }[variant]
            }
        )
        if variant
        else agent.resume()
    )
    attestation = {
        "resume_sha256": resume["sha256"],
        "profile_sha256": digest(agent.profile),
        "resume_profile_sha256": resume_profile_digest(agent.profile),
        "confirmed_at": timestamp(),
        "provenance": "Candidate explicitly attested reconciliation; software did not parse or verify PDF claims.",
    }
    agent.store.set_setting(
        "resume_attestation:variant:" + variant if variant else "resume_attestation",
        attestation,
    )
    agent.store.event(None, "RESUME_PROFILE_RECONCILIATION", "USER_CONFIRMED", attestation)
    agent.prepare()
    return attestation


COMMANDS["confirm-resume"] = CommandSpec(handle_confirm_resume, True)


def handle_resume_text(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .cli import output_path

    root = agent.root
    path = output_path(root, args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "PROVISIONAL — supplied profile; reconcile against designated resume before external use.\n\n"
        + export_resume_text(agent.profile),
        encoding="utf-8",
    )
    return {"path": str(path), "provisional": True, "designated_attachment_changed": False}


COMMANDS["resume-text"] = CommandSpec(handle_resume_text, True)


def handle_accept_pay_exception(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    agent.accept_salary_exception(args.id)
    return {"accepted": args.id, "application_approved": False}


COMMANDS["accept-pay-exception"] = CommandSpec(handle_accept_pay_exception, True)


def handle_resolve_repost(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.store.get(args.id)
    agent.store.set_setting(f"repost_review:{row['id']}", row["material_hash"])
    agent.store.event(
        row["id"],
        "POTENTIAL_REPOST_REVIEWED",
        "USER_CONFIRMED_DISTINCT",
        {"evidence": args.evidence},
    )
    return {"reviewed_distinct": args.id, "application_approved": False}


COMMANDS["resolve-repost"] = CommandSpec(handle_resolve_repost, True)


def handle_record_consent(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.store.get(args.id)
    job = row["payload"]
    if args.question not in job.get("required_consents", []):
        raise WorkflowError("Consent must match an exact displayed mandatory question.")
    job.setdefault("consent_answers", {})[args.question] = args.answer == "yes"
    agent.store.upsert(job)
    agent.store.event(row["id"], "CONSENT_ANSWER", args.answer, {"question": args.question})
    return {
        "opportunity_id": args.id,
        "question": args.question,
        "answer": args.answer,
        "application_approved": False,
    }


COMMANDS["record-consent"] = CommandSpec(handle_record_consent, True)


def handle_record_answer(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.store.get(args.id)
    job = row["payload"]
    if args.question not in job.get("required_questions", []):
        raise WorkflowError("Answer must match an exact displayed required screening question.")
    if re.search(
        r"password|one.time|\botp\b|government.?id|aadhaar|aadhar|passport|bank|ifsc|account.?number",
        args.question,
        re.I,
    ):
        raise WorkflowError(
            "Do not submit credentials, government IDs or banking details to this workflow."
        )
    confirmed = {
        "answer": args.answer,
        "evidence": args.evidence,
        "confirmed_at": timestamp(),
        "provenance": "Explicit user answer",
    }
    job.setdefault("confirmed_answers", {})[args.question] = confirmed
    agent.store.upsert(job)
    agent.store.event(
        row["id"],
        "REQUIRED_SCREENING_ANSWER",
        "USER_CONFIRMED",
        {"question": args.question, "evidence": args.evidence},
    )
    return {"opportunity_id": args.id, "question": args.question, "application_approved": False}


COMMANDS["record-answer"] = CommandSpec(handle_record_answer, True)


def handle_approve(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    if args.all:
        if args.id or "follow_up" in args.channels:
            raise WorkflowError("Use --all only for initial channels and omit an opportunity ID.")
        report = agent.store.db.execute(
            "SELECT * FROM reports WHERE delivered_at IS NOT NULL ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not report:
            raise WorkflowError("No report has been delivered for review.")
        ids = [
            key
            for key, value in json.loads(report["opportunity_ids"]).items()
            if value["shortlisted"] and agent.store.get(key)["state"] == "AWAITING_APPROVAL"
        ]
        if not ids:
            raise WorkflowError(
                "The latest delivered report has no initial actions awaiting approval."
            )
        # Validate every item first, avoiding a partially approved batch.
        agent.prepare()
        for job_id in ids:
            row = agent.store.get(job_id)
            snapshot = json.loads(report["opportunity_ids"])[job_id]
            if (
                row["package"]["blockers"]
                or row["evaluation"]["status"] != "READY_FOR_REVIEW"
                or snapshot["package_hash"] != row["package_hash"]
                or not set(args.channels) <= {a["channel"] for a in row["package"]["actions"]}
            ):
                raise WorkflowError(
                    f"{job_id} has blockers or lacks a requested channel; approve individually."
                )
        return [agent.approve(job_id, args.channels) for job_id in ids]
    if not args.id or ("follow_up" in args.channels and args.channels != ["follow_up"]):
        raise WorkflowError(
            "Specify an opportunity ID. Approve follow_up separately from initial actions."
        )
    return agent.approve(args.id, args.channels)


COMMANDS["approve"] = CommandSpec(handle_approve, True)


def handle_recheck(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    agent.active()
    row = agent.store.get(args.id)
    job = row["payload"]
    at = timestamp()
    job["last_verified_at"] = at
    live = job.get("eligibility_gates", {}).get("live_vacancy", {})
    if live.get("status") != "PASS":
        raise WorkflowError(
            "Import source-supported live-vacancy evidence first; recheck cannot turn UNKNOWN into PASS."
        )
    live["checked_at"] = at
    agent.store.upsert(job)
    current = agent.store.get(args.id)
    agent.store.set_setting(
        f"recheck:{row['id']}",
        {
            "checked_at": at,
            "material_hash": current["material_hash"],
            "evidence": args.evidence,
        },
    )
    agent.store.event(
        row["id"],
        "MANUAL_PRE_EXECUTION_RECHECK",
        "USER_CONFIRMED",
        {"evidence": args.evidence, "checked_at": at},
    )
    return {
        "checked_at": at,
        "provenance": "User attested official vacancy, eligibility, deadline, destination and duplicate-history checks.",
        "expires_in_minutes": 5,
    }


COMMANDS["recheck"] = CommandSpec(handle_recheck, True)


def handle_handoff(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    return agent.handoff(args.id, args.channel)


COMMANDS["handoff"] = CommandSpec(handle_handoff, True)


def handle_record_result(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.record_result(args.token, args.outcome, args.evidence, args.occurred_at)
    return {
        key: row[key]
        for key in (
            "opportunity_id",
            "state",
            "portal_status",
            "outreach_status",
            "follow_up_due_at",
            "follow_up_status",
        )
    }


COMMANDS["record-result"] = CommandSpec(handle_record_result, True)


def handle_check_status(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.store.get(args.id)
    check = {
        "status": args.status,
        "checked_at": timestamp(),
        "evidence": args.evidence,
        "provenance": "User-provided status check",
    }
    agent.store.db.execute(
        "UPDATE opportunities SET status_check=? WHERE id=?", (json.dumps(check), row["id"])
    )
    status = {
        "replied": "REPLIED",
        "rejected": "REJECTED",
        "withdrawn": "WITHDRAWN",
        "interview": "INTERVIEW",
        "closed": "CLOSED",
    }.get(args.status)
    if status:
        agent.store.db.execute(
            "UPDATE opportunities SET state=?,follow_up_status='NOT_ELIGIBLE' WHERE id=?",
            (status, row["id"]),
        )
        agent.store.invalidate(row["id"], status)
    agent.store.event(row["id"], "STATUS_CHECK", args.status, check)
    return check


COMMANDS["check-status"] = CommandSpec(handle_check_status, True)


def handle_skip(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    row = agent.store.get(args.id)
    agent.store.db.execute(
        "UPDATE opportunities SET state='DISMISSED',follow_up_status='NOT_ELIGIBLE' WHERE id=?",
        (row["id"],),
    )
    agent.store.invalidate(row["id"], "DISMISSED")
    return {"dismissed": args.id}


COMMANDS["skip"] = CommandSpec(handle_skip, True)


def handle_pause(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    command = args.command
    agent.store.set_setting("paused", command == "pause")
    agent.store.event(None, "WORKFLOW_" + command.upper(), "RECORDED")
    return {
        "paused": command == "pause",
        "external_schedules_changed": False,
        "note": "The ledger blocks discovery and new handoffs while paused. Disable any installed Windows task separately; its triggers were not changed here. Expired approvals remain expired.",
    }


COMMANDS["pause"] = CommandSpec(handle_pause, True)
COMMANDS["resume"] = CommandSpec(handle_pause, True)


def handle_schedule_template(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    from .cli import output_path, schedule_template

    root = agent.root
    return schedule_template(root, output_path(root, args.output))


COMMANDS["schedule-template"] = CommandSpec(handle_schedule_template, True)


def dispatch(args: argparse.Namespace, agent: Agent) -> Record | list[Record]:
    spec = COMMANDS.get(args.command)
    if not spec:
        raise WorkflowError("Unsupported command.")
    return spec.handler(args, agent)
