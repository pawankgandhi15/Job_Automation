from copy import deepcopy
from datetime import timedelta

import pytest

from job_agent.common import WorkflowError, now, parse_time, timestamp


def add_contact(agent, job):
    job["contact"] = {
        "name": "Synthetic Recruiter",
        "destination": "recruiting@example.com",
        "recruiting_relevance": "Synthetic employer recruiting inbox",
        "status": "SOURCE_CONFIRMED",
        "source": "https://example.com/careers",
        "checked_at": timestamp(),
    }
    job_id, _ = agent.store.upsert(job)
    report = agent.report()
    agent.deliver(report["report_id"])
    row = agent.store.get(job_id)
    agent.store.set_setting(
        f"recheck:{row['id']}", {"checked_at": timestamp(), "material_hash": row["material_hash"]}
    )
    return job_id


def test_stable_ids_and_same_job_across_tracking_urls(agent, job):
    first, _ = agent.store.upsert(job)
    job["canonical_url"] += "?utm_source=other"
    second, _ = agent.store.upsert(job)
    assert first == second
    assert len(agent.store.all()) == 1
    job["job_id"] = "SYNTHETIC-002"
    assert agent.store.upsert(job)[0] != first


def test_generated_report_does_not_claim_delivery_or_suppress_new_company(agent, job):
    agent.store.upsert(job)
    a = agent.report()
    b = agent.report()
    assert a["counts"]["new_shortlist_companies"] == b["counts"]["new_shortlist_companies"] == 1
    assert agent.status()["unique_companies_reported"] == 0
    agent.deliver(a["report_id"])
    assert agent.report()["counts"]["new_shortlist_companies"] == 0


def test_watchlist_company_is_never_later_reported_as_new(agent, job):
    original = deepcopy(job)
    del job["eligibility_gates"]["academic_schedule"]
    agent.store.upsert(job)
    report = agent.report()
    agent.deliver(report["report_id"])
    agent.store.upsert(original)
    assert agent.report()["counts"]["new_shortlist_companies"] == 0


def test_unreviewed_report_cannot_authorize_execution(agent, job):
    job_id, _ = agent.store.upsert(job)
    agent.report()
    with pytest.raises(WorkflowError, match="report"):
        agent.approve(job_id, ["portal"])


def test_missing_resume_blocks_approval(prepared):
    agent, job_id = prepared
    (agent.root / "Pawan_resume.pdf").unlink()
    with pytest.raises(WorkflowError, match="blockers"):
        agent.approve(job_id, ["portal"])


def test_handoff_is_not_proof_and_approval_is_single_use(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    handoff = agent.handoff(job_id, "portal")
    assert handoff["external_action_performed_by_agent"] is False
    assert agent.status()["applications_confirmed"] == 0
    with pytest.raises(WorkflowError):
        agent.handoff(job_id, "portal")
    agent.record_result(
        handoff["token"], "confirmed", "Synthetic portal confirmation SYNTHETIC-REF"
    )
    assert agent.status()["applications_confirmed"] == 1
    row = agent.store.get(job_id)
    assert parse_time(row["follow_up_due_at"]) - parse_time(row["submitted_at"]) == timedelta(
        days=7
    )
    with pytest.raises(WorkflowError, match="already finalized"):
        agent.record_result(handoff["token"], "confirmed", "duplicate")


def test_confirmation_requires_evidence(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    token = agent.handoff(job_id, "portal")["token"]
    with pytest.raises(WorkflowError, match="evidence"):
        agent.record_result(token, "confirmed")


def test_uncertain_submission_requires_reconciliation_not_retry(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    token = agent.handoff(job_id, "portal")["token"]
    agent.record_result(token, "uncertain", "Synthetic timeout after possible submission")
    with pytest.raises(WorkflowError):
        agent.approve(job_id, ["portal"])
    with pytest.raises(WorkflowError, match="Resolve uncertainty"):
        agent.record_result(token, "failed", "timeout")
    agent.record_result(
        token, "not_submitted", "Synthetic portal application history checked: absent"
    )
    agent.approve(job_id, ["portal"])
    assert agent.handoff(job_id, "portal")["token"] != token


def test_approval_expires_after_72_hours(prepared):
    agent, job_id = prepared
    approval = agent.approve(job_id, ["portal"])
    assert (
        timedelta(hours=71, minutes=59)
        < parse_time(approval["expires_at"]) - now()
        <= timedelta(hours=72)
    )
    agent.store.db.execute(
        "UPDATE approvals SET expires_at=?", (timestamp(now() - timedelta(seconds=1)),)
    )
    with pytest.raises(WorkflowError, match="expired"):
        agent.handoff(job_id, "portal")


def test_changed_resume_invalidates_approved_package(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    (agent.root / "Pawan_resume.pdf").write_bytes(b"different synthetic resume")
    with pytest.raises(WorkflowError):
        agent.handoff(job_id, "portal")


def test_material_role_change_invalidates_approval(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    job = agent.store.get(job_id)["payload"]
    job["role"] = "Different Synthetic Role"
    agent.store.upsert(job)
    assert agent.store.db.execute("SELECT invalidated_at FROM approvals").fetchone()[0]


def test_timestamp_only_refresh_preserves_approval(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    job = agent.store.get(job_id)["payload"]
    job["last_verified_at"] = timestamp(now() + timedelta(seconds=1))
    job["eligibility_gates"]["live_vacancy"]["checked_at"] = timestamp()
    agent.store.upsert(job)
    # Use the current clock time for eligibility, preserving the exact material package.
    job["last_verified_at"] = timestamp()
    agent.store.upsert(job)
    agent.prepare()
    assert agent.store.db.execute("SELECT invalidated_at FROM approvals").fetchone()[0] is None


def test_public_contact_does_not_implicitly_authorize_email(agent, job):
    job_id = add_contact(agent, job)
    agent.approve(job_id, ["portal"])
    with pytest.raises(WorkflowError, match="approval"):
        agent.handoff(job_id, "outreach")


def test_contact_change_invalidates_outreach_only(agent, job):
    job_id = add_contact(agent, job)
    agent.approve(job_id, ["portal", "outreach"])
    job = agent.store.get(job_id)["payload"]
    job["contact"]["destination"] = "different@example.com"
    agent.store.upsert(job)
    agent.prepare()
    approvals = {
        r["channel"]: r["invalidated_at"] for r in agent.store.db.execute("SELECT * FROM approvals")
    }
    assert approvals["portal"] is None
    assert approvals["outreach"] is not None


def test_portal_success_and_outreach_failure_is_partial_success(agent, job):
    job_id = add_contact(agent, job)
    agent.approve(job_id, ["portal", "outreach"])
    portal = agent.handoff(job_id, "portal")
    agent.record_result(portal["token"], "confirmed", "Synthetic portal reference")
    outreach = agent.handoff(job_id, "outreach")
    row = agent.record_result(outreach["token"], "failed", "Synthetic send failed")
    assert row["state"] == "PARTIAL_SUCCESS"
    with pytest.raises(WorkflowError):
        agent.approve(job_id, ["portal"])


def test_portal_only_does_not_authorize_cold_follow_up(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    token = agent.handoff(job_id, "portal")["token"]
    agent.record_result(token, "confirmed", "Synthetic portal reference")
    with pytest.raises(WorkflowError, match="outreach"):
        agent.approve(job_id, ["follow_up"])


def test_follow_up_approval_window_is_anchored_to_due_date(agent, job):
    job_id = add_contact(agent, job)
    agent.approve(job_id, ["outreach"])
    token = agent.handoff(job_id, "outreach")["token"]
    agent.record_result(token, "confirmed", "Synthetic sent-message ID")
    approved = agent.approve(job_id, ["follow_up"])
    due = agent.store.get(job_id)["follow_up_due_at"]
    assert parse_time(approved["expires_at"]) - parse_time(due) == timedelta(hours=72)
    with pytest.raises(WorkflowError, match="not yet due"):
        agent.handoff(job_id, "follow_up")


def test_follow_up_unknown_reply_status_requires_reconfirmation(agent, job):
    job_id = add_contact(agent, job)
    row = agent.store.get(job_id)
    row["outreach_status"] = "OUTREACH_SENT_CONFIRMED"
    row["follow_up_due_at"] = timestamp(now() - timedelta(minutes=1))
    with pytest.raises(WorkflowError, match="Reply status unknown"):
        agent.follow_up_eligible(row)
    row["status_check"] = {"status": "unknown", "checked_at": timestamp()}
    with pytest.raises(WorkflowError):
        agent.follow_up_eligible(row)
    row["status_check"] = {"status": "unknown_reconfirmed", "checked_at": timestamp()}
    agent.follow_up_eligible(row)


def test_pause_blocks_new_work_but_keeps_status_and_evidence(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    token = agent.handoff(job_id, "portal")["token"]
    agent.store.set_setting("paused", True)
    with pytest.raises(WorkflowError, match="paused"):
        agent.report()
    agent.record_result(token, "confirmed", "Synthetic action completed before pause")
    assert agent.status()["applications_confirmed"] == 1


def test_sample_is_rejected_by_live_import(agent):
    with pytest.raises(WorkflowError, match="Sample"):
        agent.import_jobs({"sample": True, "opportunities": []})


def test_repost_with_new_id_needs_review_after_prior_submission(prepared):
    agent, job_id = prepared
    agent.approve(job_id, ["portal"])
    token = agent.handoff(job_id, "portal")["token"]
    agent.record_result(token, "confirmed", "Synthetic first vacancy confirmation")
    new_job = deepcopy(agent.store.get(job_id)["payload"])
    new_job["job_id"] = "SYNTHETIC-REPOST"
    new_id, _ = agent.store.upsert(new_job)
    agent.prepare()
    assert agent.store.get(new_id)["evaluation"]["status"] == "NEEDS_CLARIFICATION"
    assert any(
        "Potential repost" in reason for reason in agent.store.get(new_id)["evaluation"]["unknowns"]
    )


def test_pay_exception_does_not_transfer_to_changed_compensation(agent, job):
    job["tier"] = "GREAT"
    job["compensation"] = {
        "minimum": 10000,
        "currency": "INR",
        "period": "month",
        "source": job["canonical_url"],
    }
    job_id, _ = agent.store.upsert(job)
    agent.accept_salary_exception(job_id)
    agent.prepare()
    assert agent.store.get(job_id)["evaluation"]["status"] == "READY_FOR_REVIEW"
    job["compensation"]["minimum"] = 8000
    agent.store.upsert(job)
    agent.prepare()
    assert agent.store.get(job_id)["evaluation"]["status"] == "NEEDS_CLARIFICATION"


def test_weekly_summary_waits_for_history_and_labels_denominators(agent, job):
    job_id, _ = agent.store.upsert(job)
    agent.prepare()
    assert agent.status()["weekly_summary"]["available"] is False
    agent.store.db.execute(
        "UPDATE opportunities SET first_seen_at=? WHERE id=1",
        (timestamp(now() - timedelta(days=8)),),
    )
    job["job_id"] = "SYNTHETIC-NEW-WEEK"
    agent.store.upsert(job)
    agent.prepare()
    summary = agent.status()["weekly_summary"]
    assert summary["available"] is True
    assert summary["new_opportunities_denominator"] == 1
    assert summary["by_source"]["reviewed_import"]["currently_ready"] == 1
    assert summary["all_time_confirmed_opportunities_denominator"] == 0
