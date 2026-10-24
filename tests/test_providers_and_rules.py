import json

import pytest

from job_agent.adapters import SmartRecruitersAdapter, WorkableAdapter
from job_agent.common import WorkflowError
from job_agent.notifications import deliver_notification, scan_notifications
from job_agent.rules import GateRule, PolicyDAG
from job_agent.sources import ats_target, fetch_board


@pytest.mark.parametrize(
    "provider,board_url",
    [
        ("smartrecruiters", "https://jobs.smartrecruiters.com/synthetic"),
        ("workable", "https://apply.workable.com/synthetic"),
    ],
)
def test_new_provider_requires_employer_link_and_preserves_application_url(provider, board_url):
    source = {
        "company": "Synthetic",
        "official_website": "https://example.com",
        "official_careers_url": "https://example.com/careers",
        "provider": provider,
        "board": "synthetic",
    }
    assert ats_target(board_url) == {"provider": provider, "board": "synthetic"}

    class HTTP:
        def get(self, url, *, as_json=False):
            if not as_json:
                return {"url": url, "text": f'<a href="{board_url}">Careers</a>'}
            if provider == "workable":
                return {
                    "jobs": [
                        {
                            "shortcode": "123",
                            "title": "Backend Intern",
                            "location": {"location_str": "India"},
                            "description": "Python",
                            "url": board_url + "/j/123",
                            "application_url": board_url + "/j/123/apply",
                            "employment_type": "Internship",
                        }
                    ]
                }
            if "offset=" in url:
                return {"content": [{"id": "123"}], "totalFound": 1}
            return {
                "id": "123",
                "name": "Backend Intern",
                "postingUrl": board_url + "/123",
                "applyUrl": board_url + "/123/apply",
                "location": {"city": "Bengaluru", "country": "India"},
                "typeOfEmployment": {"label": "Internship"},
                "jobAd": {"sections": {"jobDescription": {"text": "Python"}}},
            }

    result = fetch_board(source, HTTP())
    assert result[0]["application_url"].endswith("/apply")
    assert result[0]["eligibility_gates"]["application_route"]["status"] == "UNKNOWN"


def test_unexpected_workable_shape_and_repeated_smartrecruiters_pages_fail_explicitly():
    class BadHTTP:
        def get(self, url, *, as_json=False):
            return {"content": [{"id": "123"}], "totalFound": 1000, "id": "123"}

    with pytest.raises(WorkflowError, match="widget"):
        WorkableAdapter().fetch({"board": "synthetic"}, BadHTTP())
    with pytest.raises(WorkflowError, match="repeated"):
        SmartRecruitersAdapter().fetch({"board": "synthetic"}, BadHTTP())


def test_rule_dag_orders_dependencies_rejects_cycles_and_preserves_unknown():
    order = []

    def rule(name, status):
        def evaluate():
            order.append(name)
            return {"status": status}

        return evaluate

    result = PolicyDAG(
        [
            GateRule("route", ("identity",), rule("route", "UNKNOWN")),
            GateRule("identity", (), rule("identity", "PASS")),
        ]
    ).run()
    assert order == ["identity", "route"] and result["route"]["status"] == "UNKNOWN"
    with pytest.raises(WorkflowError, match="cycle"):
        PolicyDAG([GateRule("a", ("b",), lambda: {}), GateRule("b", ("a",), lambda: {})]).run()
    with pytest.raises(WorkflowError, match="Duplicate"):
        PolicyDAG([GateRule("a", (), lambda: {}), GateRule("a", (), lambda: {})])
    with pytest.raises(WorkflowError, match="unsupported"):
        PolicyDAG([GateRule("a", (), lambda: {"status": "maybe"})]).run()


def test_great_match_notices_are_idempotent_and_do_not_approve(agent, job):
    job["tier"] = "GREAT"
    agent.store.upsert(job)
    agent.prepare()
    scan_notifications(agent)
    scan_notifications(agent)
    assert (
        agent.store.db.execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='STRONG_MATCH'"
        ).fetchone()[0]
        == 1
    )
    assert agent.store.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_webhook_notifications_require_explicit_enablement_and_use_selected_destination(
    monkeypatch,
):
    calls = []

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def send(request, **kwargs):
        calls.append(request)
        return Response()

    monkeypatch.setattr("job_agent.notifications.urlopen", send)
    with pytest.raises(WorkflowError, match="disabled"):
        deliver_notification(
            "Example alert", {"channel": "webhook", "url": "https://example.com/hook"}
        )
    deliver_notification(
        "Example alert", {"channel": "webhook", "url": "https://example.com/hook", "enabled": True}
    )
    assert calls[0].full_url == "https://example.com/hook"
    assert json.loads(calls[0].data)["content"] == "Example alert"
