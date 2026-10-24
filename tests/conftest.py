from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from job_agent.common import digest, read_json, timestamp
from job_agent.engine import Agent

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def job():
    result = copy.deepcopy(
        read_json(PROJECT / "examples" / "opportunity.sample.json")["opportunities"][0]
    )
    result["last_verified_at"] = timestamp()
    result["posting_date"] = timestamp()[:10]
    for gate in result["eligibility_gates"].values():
        gate["checked_at"] = timestamp()
    return result


@pytest.fixture
def agent(tmp_path):
    (tmp_path / "config").mkdir()
    for name in ("settings.json", "profile.json"):
        shutil.copy2(PROJECT / "config" / name, tmp_path / "config" / name)
    instance = Agent(tmp_path)
    # A text fixture stands in for an already reconciled resume; no resume is fabricated for live use.
    resume = tmp_path / "Pawan_resume.pdf"
    resume.write_bytes(b"synthetic test resume attachment")
    version = instance.resume()
    instance.store.set_setting(
        "resume_attestation",
        {"resume_sha256": version["sha256"], "profile_sha256": digest(instance.profile)},
    )
    yield instance
    instance.close()


@pytest.fixture
def prepared(agent, job):
    job_id, _ = agent.store.upsert(job)
    report = agent.report()
    agent.deliver(report["report_id"])
    row = agent.store.get(job_id)
    agent.store.set_setting(
        f"recheck:{row['id']}",
        {
            "checked_at": timestamp(),
            "material_hash": row["material_hash"],
            "evidence": "Synthetic recheck",
        },
    )
    return agent, job_id
