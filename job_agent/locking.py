from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .common import WorkflowError


@contextmanager
def run_lock(root: Path) -> Iterator[None]:
    """OS-managed lock is released on crashes; the file itself need not be deleted."""
    path = root / "data" / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkflowError(
                "Another run holds the ledger lock. Wait for it to finish."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
