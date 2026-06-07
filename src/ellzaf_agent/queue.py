"""Durable local JSONL queue."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ellzaf_agent.errors import QueueError
from ellzaf_agent.serialization import strict_json_dumps, strict_json_loads


@dataclass(frozen=True, slots=True)
class QueueEvent:
    path: Path
    event: dict[str, Any]


@dataclass(frozen=True, slots=True)
class QueueHealth:
    pending: int
    uploaded: int
    failed: int
    quarantined: int
    pending_bytes: int
    max_queue_bytes: int


class LocalQueue:
    """Atomic one-event JSONL queue.

    Each pending file contains one JSON object plus a newline. This keeps the
    JSONL contract while allowing partial upload results to move individual
    events safely.
    """

    def __init__(self, root: Path, *, max_queue_bytes: int) -> None:
        if max_queue_bytes < 1:
            raise QueueError("max_queue_bytes must be >= 1")
        self.root = root
        self.max_queue_bytes = max_queue_bytes
        self.pending_dir = root / "pending"
        self.uploaded_dir = root / "uploaded"
        self.failed_dir = root / "failed"
        self.quarantine_dir = root / "quarantine"
        for directory in (
            self.pending_dir,
            self.uploaded_dir,
            self.failed_dir,
            self.quarantine_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def enqueue(self, event: dict[str, Any]) -> Path:
        event_id = str(event.get("event_id") or "unknown")
        line = strict_json_dumps(event) + "\n"
        self._enforce_disk_cap(extra_bytes=len(line.encode("utf-8")))
        final_path = _queue_path(self.pending_dir, event_id)
        _atomic_write_text(final_path, line)
        return final_path

    def quarantine(self, event: dict[str, Any], *, reason: str) -> Path:
        event_id = str(event.get("event_id") or "unknown")
        final_path = _queue_path(self.quarantine_dir, event_id)
        record = {"reason": reason, "event": event}
        _atomic_write_text(final_path, strict_json_dumps(record) + "\n")
        return final_path

    def pending(self, *, limit: int, max_bytes: int) -> list[QueueEvent]:
        selected: list[QueueEvent] = []
        total_bytes = 0
        for path in sorted(self.pending_dir.glob("*.jsonl")):
            try:
                raw = path.read_text(encoding="utf-8")
                if not raw.endswith("\n"):
                    self._move(path, self.quarantine_dir, suffix=".partial")
                    continue
                event = strict_json_loads(raw)
                size = len(raw.encode("utf-8"))
            except Exception as exc:
                self._move(path, self.quarantine_dir, suffix=".corrupt")
                if isinstance(exc, OSError):
                    raise QueueError(f"failed to read queue file: {path}") from exc
                continue
            if not isinstance(event, dict):
                self._move(path, self.quarantine_dir, suffix=".nonobject")
                continue
            if selected and total_bytes + size > max_bytes:
                break
            if size > max_bytes:
                self._move(path, self.failed_dir, suffix=".too_large")
                continue
            selected.append(QueueEvent(path=path, event=event))
            total_bytes += size
            if len(selected) >= limit:
                break
        return selected

    def mark_uploaded(self, item: QueueEvent) -> None:
        self._move(item.path, self.uploaded_dir)

    def mark_failed(self, item: QueueEvent, *, permanent: bool) -> None:
        suffix = ".permanent" if permanent else ".retryable"
        self._move(item.path, self.failed_dir, suffix=suffix)

    def health(self) -> QueueHealth:
        pending_files = list(self.pending_dir.glob("*.jsonl"))
        return QueueHealth(
            pending=len(pending_files),
            uploaded=len(list(self.uploaded_dir.glob("*.jsonl*"))),
            failed=len(list(self.failed_dir.glob("*.jsonl*"))),
            quarantined=len(list(self.quarantine_dir.glob("*.jsonl*"))),
            pending_bytes=sum(path.stat().st_size for path in pending_files),
            max_queue_bytes=self.max_queue_bytes,
        )

    def _move(self, path: Path, target_dir: Path, *, suffix: str = "") -> None:
        target = target_dir / f"{path.stem}{suffix}.jsonl"
        counter = 1
        while target.exists():
            target = target_dir / f"{path.stem}{suffix}.{counter}.jsonl"
            counter += 1
        os.replace(path, target)

    def _enforce_disk_cap(self, *, extra_bytes: int = 0) -> None:
        pending_bytes = sum(
            path.stat().st_size for path in self.pending_dir.glob("*.jsonl")
        )
        if pending_bytes + extra_bytes > self.max_queue_bytes:
            raise QueueError("local queue disk cap exceeded")


def _safe_name(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in value
    )
    return safe[:120].strip("_") or "unknown"


def _queue_path(directory: Path, event_id: str) -> Path:
    return directory / f"{time.time_ns()}_{uuid4().hex}_{_safe_name(event_id)}.jsonl"


def _atomic_write_text(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise QueueError(f"failed to write queue file: {path}") from exc
    finally:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)
