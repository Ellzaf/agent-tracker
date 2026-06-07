"""User-facing JSONL export sink."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ellzaf_agent.constants import DEFAULT_MAX_EVENT_BYTES
from ellzaf_agent.errors import QueueError
from ellzaf_agent.redaction import redact_event
from ellzaf_agent.schema import validate_event
from ellzaf_agent.serialization import strict_json_dumps, strict_json_loads


class JsonlSink:
    """Write redacted, validated Ellzaf events to a JSONL file."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        append: bool = True,
        fsync: bool = False,
    ) -> None:
        self.path = Path(path)
        self.max_event_bytes = max_event_bytes
        self.fsync = fsync
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            fd = os.open(self.path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            os.close(fd)

    def write(
        self,
        event: Mapping[str, Any],
        *,
        store_full_io: bool | None = None,
    ) -> dict[str, Any]:
        privacy = event.get("privacy", {})
        full_io = (
            bool(privacy.get("full_io", False))
            if isinstance(privacy, Mapping) and store_full_io is None
            else bool(store_full_io)
        )
        redacted = redact_event(event, store_full_io=full_io).value
        validate_event(redacted, max_event_bytes=self.max_event_bytes)
        line = strict_json_dumps(redacted) + "\n"
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise QueueError(f"failed to write JSONL sink: {self.path}") from exc
        return redacted

    def write_many(
        self,
        events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        store_full_io: bool | None = None,
    ) -> list[dict[str, Any]]:
        return [self.write(event, store_full_io=store_full_io) for event in events]


def read_jsonl_events(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = strict_json_loads(line)
            if not isinstance(value, dict):
                raise QueueError(f"JSONL line {line_number} is not an object")
            events.append(value)
    return events
