"""Composable eligibility rule ordering; unknown evidence never becomes a pass."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .common import WorkflowError
from .types import Record


@dataclass(frozen=True)
class GateRule:
    name: str
    dependencies: tuple[str, ...]
    evaluate: Callable[[], Record]


class PolicyDAG:
    def __init__(self, rules: list[GateRule]) -> None:
        self.rules = {item.name: item for item in rules}
        if len(self.rules) != len(rules):
            raise WorkflowError("Duplicate eligibility rule.")

    def run(self) -> dict[str, Record]:
        result: dict[str, Record] = {}
        active: set[str] = set()

        def evaluate(name: str) -> None:
            if name in result:
                return
            if name in active or name not in self.rules:
                raise WorkflowError("Eligibility rules contain a cycle or missing dependency.")
            active.add(name)
            rule = self.rules[name]
            for dependency in rule.dependencies:
                evaluate(dependency)
            value = rule.evaluate()
            if value.get("status") not in {"PASS", "FAIL", "UNKNOWN"}:
                raise WorkflowError("Eligibility rule returned an unsupported status.")
            result[name] = value
            active.remove(name)

        for name in self.rules:
            evaluate(name)
        return result


def ordered_gates(
    names: tuple[str, ...],
    validator: Callable[[str], Record],
    dependencies: Mapping[str, list[str]],
) -> dict[str, Record]:
    def bind(name: str) -> Callable[[], Record]:
        def run() -> Record:
            return validator(name)

        return run

    return PolicyDAG(
        [GateRule(name, tuple(dependencies.get(name, [])), bind(name)) for name in names]
    ).run()
