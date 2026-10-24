"""Immutable domain events and structured, correlated local activity logs."""

from __future__ import annotations

import contextvars
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

trace_id = contextvars.ContextVar("trace_id", default="")


@dataclass(frozen=True, slots=True)
class DomainEvent:
    id: int
    opportunity_id: int | None
    action: str
    result: str
    at: str
    trace: str


class EventBus:
    def __init__(self) -> None:
        self.subscribers: list[Callable[[DomainEvent], None]] = []

    def subscribe(self, handler: Callable[[DomainEvent], None]) -> None:
        self.subscribers.append(handler)

    def publish(self, event: DomainEvent) -> None:
        for handler in self.subscribers:
            try:
                handler(event)
            except OSError:
                # A failed log/notification cannot undo or conceal a ledger action.
                continue


class ActivityLog:
    def __init__(self, path: Path) -> None:
        self.path, self.lock = path, threading.Lock()

    def __call__(self, event: DomainEvent) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": event.at,
                            "event_id": event.id,
                            "opportunity_id": event.opportunity_id,
                            "action": event.action,
                            "result": event.result,
                            "trace_id": event.trace,
                        }
                    )
                    + "\n"
                )


def correlation_id() -> str:
    return trace_id.get() or uuid.uuid4().hex
