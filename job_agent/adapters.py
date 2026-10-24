"""Public ATS ports and independently testable provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import quote

from .common import WorkflowError, digest
from .types import HTTPClient, Record


class ATSAdapter(ABC):
    @abstractmethod
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]: ...


class GreenhouseAdapter(ATSAdapter):
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]:
        return list(
            http.get(
                f"https://boards-api.greenhouse.io/v1/boards/{quote(source['board'], safe='')}/jobs?content=true",
                as_json=True,
            )["jobs"]
        )


class AshbyAdapter(ATSAdapter):
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]:
        return list(
            http.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{quote(source['board'], safe='')}?includeCompensation=true",
                as_json=True,
            )["jobs"]
        )


class LeverAdapter(ATSAdapter):
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]:
        result = []
        for skip in range(0, 100000, 100):
            page = http.get(
                f"https://api.lever.co/v0/postings/{quote(source['board'], safe='')}?mode=json&skip={skip}&limit=100",
                as_json=True,
            )
            if not isinstance(page, list):
                raise WorkflowError("Unexpected Lever response.")
            result.extend(page)
            if len(page) < 100:
                return result
        raise WorkflowError("Lever pagination limit reached; discovery incomplete.")


class SmartRecruitersAdapter(ATSAdapter):
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]:
        base = f"https://api.smartrecruiters.com/v1/companies/{quote(source['board'], safe='')}/postings"
        result, pages = [], set()
        for offset in range(0, 100000, 100):
            page = http.get(f"{base}?limit=100&offset={offset}", as_json=True)
            items = page["content"]
            signature = digest(items)
            if items and signature in pages:
                raise WorkflowError(
                    "SmartRecruiters returned a repeated page; discovery incomplete."
                )
            pages.add(signature)
            for item in items:
                detail = http.get(base + "/" + quote(str(item["id"]), safe=""), as_json=True)
                if detail.get("active", True):
                    result.append(detail)
            if not items or offset + len(items) >= page["totalFound"]:
                return result
        raise WorkflowError("SmartRecruiters pagination limit reached; discovery incomplete.")


class WorkableAdapter(ATSAdapter):
    def fetch(self, source: Record, http: HTTPClient) -> list[Record]:
        data = http.get(
            f"https://www.workable.com/api/accounts/{quote(source['board'], safe='')}?details=true",
            as_json=True,
        )
        jobs = data.get("jobs")
        if not isinstance(jobs, list):
            raise WorkflowError("Unexpected Workable public-widget response; review required.")
        return jobs


ADAPTERS: dict[str, ATSAdapter] = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "smartrecruiters": SmartRecruitersAdapter(),
    "workable": WorkableAdapter(),
}
