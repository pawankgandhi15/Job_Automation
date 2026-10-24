"""Role-specific attachment selection and validated outreach templates."""

from __future__ import annotations

import re
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from .common import WorkflowError
from .policy import role_family
from .types import Record

if TYPE_CHECKING:
    from .services.context import AgentContext


class PackageService:
    def __init__(self, agent: AgentContext) -> None:
        self.agent = agent

    def resume_for(self, job: Record) -> Record:
        from .packages import resume_version

        family = role_family(job["role"])
        selected = self.agent.settings.get("resume_variants", {}).get(family)
        if not selected or selected == self.agent.settings["resume_path"]:
            return self.agent.resume()
        settings = {**self.agent.settings, "resume_path": selected}
        attestation = self.agent.store.setting("resume_attestation:variant:" + family)
        return resume_version(self.agent.root, settings, self.agent.profile, attestation)


def render_template(family: str, context: dict[str, str], *, follow_up: bool = False) -> str:
    filename = "follow_up" if follow_up else family
    if filename not in {"cloud", "backend", "sde", "follow_up"}:
        raise WorkflowError("Unsupported draft template.")
    source = (Path(__file__).with_name("templates") / (filename + ".txt")).read_text(
        encoding="utf-8"
    )
    try:
        rendered = Template(source).substitute(context).strip()
    except (ValueError, KeyError) as exc:
        raise WorkflowError("Draft template contains missing or invalid placeholders.") from exc
    if re.search(r"\{\{|\}\}|\$\{|\bTODO\b", rendered):
        raise WorkflowError("Rendered draft contains unresolved placeholders.")
    return rendered
