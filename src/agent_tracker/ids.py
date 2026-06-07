"""Identifier helpers."""

from __future__ import annotations

from itertools import count
from threading import Lock
from uuid import uuid4


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def new_run_id() -> str:
    return f"run_{uuid4().hex}"


def new_span_id() -> str:
    return f"span_{uuid4().hex}"


class Sequence:
    """Thread-safe monotonic sequence counter for idempotency keys."""

    def __init__(self, start: int = 1) -> None:
        self._counter = count(start)
        self._lock = Lock()

    def next(self) -> int:
        with self._lock:
            return next(self._counter)
