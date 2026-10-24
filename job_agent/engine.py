from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .common import WorkflowError
from .configuration import load_configuration
from .locking import run_lock as run_lock
from .packages import resume_version
from .packaging import PackageService
from .services.approval import ApprovalService
from .services.discovery import DiscoveryService
from .services.evaluation import EvaluationService
from .services.execution import ExecutionService
from .services.reporting import ReportingService
from .sources import fetch_board as fetch_board
from .store import Store
from .transport import ResilientHTTP
from .types import HTTPClient, Record


class Agent:
    """Compatibility facade over independently testable application services."""

    resume_for: Callable[..., Record]
    preflight: Callable[..., Record]
    import_jobs: Callable[..., list[tuple[str, bool]]]
    prepare: Callable[..., list[Record]]
    discover: Callable[..., Record]
    _discover: Callable[..., Record]
    _source_failed: Callable[..., None]
    review_queue: Callable[..., Record]
    daily: Callable[..., Record]
    merge_discovery: Callable[..., Iterator[Record]]
    report: Callable[..., Record]
    review_hash: Callable[..., str]
    deliver: Callable[..., Record]
    scope_hash: Callable[..., str]
    approve: Callable[..., Record]
    accept_salary_exception: Callable[..., None]
    refresh_for_execution: Callable[..., Record]
    handoff: Callable[..., Record]
    record_result: Callable[..., Record]
    reconcile_state: Callable[..., None]
    follow_up_eligible: Callable[..., None]
    status: Callable[..., Record]
    weekly_summary: Callable[..., Record]

    def __init__(self, root: Path, http: HTTPClient | None = None) -> None:
        self.root = root.resolve()
        self.settings, self.profile = load_configuration(self.root)
        self.store = Store(
            root / "data" / "ledger.sqlite3", self.settings.get("company_domain_aliases")
        )
        self.http = http or ResilientHTTP(self.root / "data/http-cache")
        self._services: dict[str, Any] = {}
        self._services["resume_for"] = PackageService(self).resume_for
        service: Any = EvaluationService(self)
        self._services["import_jobs"] = service.import_jobs
        self._services["prepare"] = service.prepare
        self._services["review_queue"] = service.review_queue
        service = DiscoveryService(self)
        self._services["discover"] = service.discover
        self._services["_discover"] = service._discover
        self._services["_source_failed"] = service._source_failed
        self._services["daily"] = service.daily
        self._services["merge_discovery"] = service.merge_discovery
        service = ReportingService(self)
        self._services["report"] = service.report
        self._services["review_hash"] = service.review_hash
        self._services["deliver"] = service.deliver
        self._services["status"] = service.status
        self._services["weekly_summary"] = service.weekly_summary
        self._services["preflight"] = service.preflight
        service = ApprovalService(self)
        self._services["scope_hash"] = service.scope_hash
        self._services["approve"] = service.approve
        self._services["accept_salary_exception"] = service.accept_salary_exception
        service = ExecutionService(self)
        self._services["refresh_for_execution"] = service.refresh_for_execution
        self._services["handoff"] = service.handoff
        self._services["record_result"] = service.record_result
        self._services["reconcile_state"] = service.reconcile_state
        self._services["follow_up_eligible"] = service.follow_up_eligible

    def close(self) -> None:
        self.store.close()

    def active(self) -> None:
        if self.store.setting("paused", False):
            raise WorkflowError(
                "Workflow is paused. Use resume to enable new discovery and handoffs."
            )

    def resume(self) -> Record:
        return resume_version(
            self.root, self.settings, self.profile, self.store.setting("resume_attestation")
        )

    def __getattr__(self, name: str) -> Any:
        services = self.__dict__.get("_services", {})
        if name in services:
            return services[name]
        raise AttributeError(name)

    def fetch_board(self, source: Record, http: HTTPClient | None = None) -> list[Record]:
        return fetch_board(source, http or self.http)
