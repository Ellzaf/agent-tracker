"""Durable local JSONL queue."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_tracker.errors import QueueError
from agent_tracker.serialization import strict_json_dumps, strict_json_loads


@dataclass(frozen=True, slots=True)
class QueueEvent:
    path: Path
    event: dict[str, Any]
    attempt_count: int = 0
    next_retry_at: float | None = None
    last_retry_reason: str | None = None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    path: Path
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class QueueHealth:
    pending: int
    uploaded: int
    failed: int
    quarantined: int
    pending_bytes: int
    max_queue_bytes: int
    uploaded_bytes: int = 0
    failed_bytes: int = 0
    quarantined_bytes: int = 0
    oldest_pending_age_seconds: float | None = None
    newest_pending_age_seconds: float | None = None
    retryable_failed: int = 0
    permanent_failed: int = 0
    retryable_pending: int = 0
    next_retry_seconds: float | None = None
    last_upload_attempt_at: str | None = None
    last_upload_status: str | None = None
    last_upload_reason: str | None = None
    estimated_batches_remaining: int | None = None


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
        self.lock_path = root / ".flush.lock"
        self.upload_state_path = root / "upload-state.json"
        for directory in (
            self.pending_dir,
            self.uploaded_dir,
            self.failed_dir,
            self.quarantine_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        event: dict[str, Any],
        *,
        dedupe_idempotency_key: bool = False,
    ) -> Path:
        return self.enqueue_result(
            event,
            dedupe_idempotency_key=dedupe_idempotency_key,
        ).path

    def enqueue_result(
        self,
        event: dict[str, Any],
        *,
        dedupe_idempotency_key: bool = False,
    ) -> EnqueueResult:
        if dedupe_idempotency_key and (
            existing := self._pending_idempotency_key_path(
                str(event.get("idempotency_key") or "")
            )
        ):
            return EnqueueResult(path=existing, duplicate=True)
        event_id = str(event.get("event_id") or "unknown")
        line = strict_json_dumps(event) + "\n"
        self._enforce_disk_cap(extra_bytes=len(line.encode("utf-8")))
        final_path = _queue_path(self.pending_dir, event_id)
        _atomic_write_text(final_path, line)
        return EnqueueResult(path=final_path, duplicate=False)

    def quarantine(self, event: dict[str, Any], *, reason: str) -> Path:
        event_id = str(event.get("event_id") or "unknown")
        final_path = _queue_path(self.quarantine_dir, event_id)
        record = {"reason": reason, "event": event}
        _atomic_write_text(final_path, strict_json_dumps(record) + "\n")
        return final_path

    def pending(self, *, limit: int, max_bytes: int) -> list[QueueEvent]:
        selected: list[QueueEvent] = []
        total_bytes = 0
        now = time.time()
        for path in sorted(self.pending_dir.glob("*.jsonl")):
            metadata = self._retry_metadata(path)
            if _retry_not_due(metadata, now=now):
                break
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
            selected.append(
                QueueEvent(
                    path=path,
                    event=event,
                    attempt_count=_metadata_attempt_count(metadata),
                    next_retry_at=_metadata_next_retry_at(metadata),
                    last_retry_reason=_metadata_reason(metadata),
                )
            )
            total_bytes += size
            if len(selected) >= limit:
                break
        return selected

    def mark_uploaded(self, item: QueueEvent) -> None:
        self._move(item.path, self.uploaded_dir)

    def mark_failed(self, item: QueueEvent, *, permanent: bool) -> None:
        suffix = ".permanent" if permanent else ".retryable"
        self._move(item.path, self.failed_dir, suffix=suffix)

    def defer_retry(
        self,
        item: QueueEvent,
        *,
        reason: str,
        retry_after_seconds: float | None,
        fallback_delay_seconds: float,
    ) -> None:
        delay = (
            retry_after_seconds
            if retry_after_seconds is not None
            else fallback_delay_seconds
        )
        delay = max(0.0, delay)
        metadata = {
            "attempt_count": item.attempt_count + 1,
            "last_reason": reason,
            "last_attempt_at": _utc_now_iso(),
            "next_retry_at": time.time() + delay,
        }
        _atomic_write_text(
            _metadata_path(item.path),
            strict_json_dumps(metadata) + "\n",
        )

    def record_upload_attempt(
        self,
        *,
        status: str,
        reason_code: str | None,
    ) -> None:
        state = {
            "last_upload_attempt_at": _utc_now_iso(),
            "last_upload_status": status,
            "last_upload_reason": reason_code,
        }
        _atomic_write_text(self.upload_state_path, strict_json_dumps(state) + "\n")

    @contextmanager
    def flush_lock(
        self,
        *,
        timeout_seconds: float = 0.0,
        stale_seconds: float = 300.0,
    ) -> Iterator[bool]:
        acquired = self._acquire_lock(
            timeout_seconds=timeout_seconds,
            stale_seconds=stale_seconds,
        )
        try:
            yield acquired
        finally:
            if acquired:
                with suppress(OSError):
                    self.lock_path.unlink()

    def health(self, *, max_batch_events: int | None = None) -> QueueHealth:
        pending_files = list(self.pending_dir.glob("*.jsonl"))
        uploaded_files = list(self.uploaded_dir.glob("*.jsonl*"))
        failed_files = list(self.failed_dir.glob("*.jsonl*"))
        quarantine_files = list(self.quarantine_dir.glob("*.jsonl*"))
        now = time.time()
        pending_ages = [now - path.stat().st_mtime for path in pending_files]
        retry_metadata = [
            metadata
            for path in pending_files
            if (metadata := self._retry_metadata(path))
        ]
        next_retry_values = [
            next_retry_at
            for metadata in retry_metadata
            if (next_retry_at := _metadata_next_retry_at(metadata)) is not None
            and next_retry_at > now
        ]
        upload_state = self._upload_state()
        return QueueHealth(
            pending=len(pending_files),
            uploaded=len(uploaded_files),
            failed=len(failed_files),
            quarantined=len(quarantine_files),
            pending_bytes=sum(path.stat().st_size for path in pending_files),
            max_queue_bytes=self.max_queue_bytes,
            uploaded_bytes=sum(path.stat().st_size for path in uploaded_files),
            failed_bytes=sum(path.stat().st_size for path in failed_files),
            quarantined_bytes=sum(path.stat().st_size for path in quarantine_files),
            oldest_pending_age_seconds=max(pending_ages) if pending_ages else None,
            newest_pending_age_seconds=min(pending_ages) if pending_ages else None,
            retryable_failed=len(
                [path for path in failed_files if ".retryable" in path.name]
            ),
            permanent_failed=len(
                [path for path in failed_files if ".permanent" in path.name]
            ),
            retryable_pending=len(retry_metadata),
            next_retry_seconds=(
                min(next_retry_values) - now if next_retry_values else None
            ),
            last_upload_attempt_at=_state_text(upload_state, "last_upload_attempt_at"),
            last_upload_status=_state_text(upload_state, "last_upload_status"),
            last_upload_reason=_state_text(upload_state, "last_upload_reason"),
            estimated_batches_remaining=_estimated_batches(
                len(pending_files), max_batch_events
            ),
        )

    def _move(self, path: Path, target_dir: Path, *, suffix: str = "") -> None:
        target = target_dir / f"{path.stem}{suffix}.jsonl"
        counter = 1
        while target.exists():
            target = target_dir / f"{path.stem}{suffix}.{counter}.jsonl"
            counter += 1
        os.replace(path, target)
        with suppress(OSError):
            _metadata_path(path).unlink()

    def _enforce_disk_cap(self, *, extra_bytes: int = 0) -> None:
        pending_bytes = sum(
            path.stat().st_size for path in self.pending_dir.glob("*.jsonl")
        )
        if pending_bytes + extra_bytes > self.max_queue_bytes:
            raise QueueError("local queue disk cap exceeded")

    def _pending_idempotency_key_path(self, idempotency_key: str) -> Path | None:
        if not idempotency_key:
            return None
        for path in sorted(self.pending_dir.glob("*.jsonl")):
            try:
                value = strict_json_loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (
                isinstance(value, Mapping)
                and value.get("idempotency_key") == idempotency_key
            ):
                return path
        return None

    def _retry_metadata(self, path: Path) -> dict[str, Any]:
        metadata_path = _metadata_path(path)
        if not metadata_path.is_file():
            return {}
        try:
            raw = metadata_path.read_text(encoding="utf-8")
            value = strict_json_loads(raw)
        except Exception:
            with suppress(OSError):
                metadata_path.unlink()
            return {}
        return value if isinstance(value, dict) else {}

    def _upload_state(self) -> dict[str, Any]:
        if not self.upload_state_path.is_file():
            return {}
        try:
            value = strict_json_loads(
                self.upload_state_path.read_text(encoding="utf-8")
            )
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _acquire_lock(
        self,
        *,
        timeout_seconds: float,
        stale_seconds: float,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            try:
                fd = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._lock_is_stale(stale_seconds=stale_seconds):
                    with suppress(OSError):
                        self.lock_path.unlink()
                    continue
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)
                continue
            except OSError as exc:
                raise QueueError(
                    f"failed to create queue lock: {self.lock_path}"
                ) from exc
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    strict_json_dumps(
                        {"pid": os.getpid(), "created_at": _utc_now_iso()}
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            return True

    def _lock_is_stale(self, *, stale_seconds: float) -> bool:
        try:
            age = time.time() - self.lock_path.stat().st_mtime
        except OSError:
            return True
        return age > stale_seconds


def _safe_name(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in value
    )
    return safe[:120].strip("_") or "unknown"


def _queue_path(directory: Path, event_id: str) -> Path:
    return directory / f"{time.time_ns()}_{uuid4().hex}_{_safe_name(event_id)}.jsonl"


def _metadata_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.retry.json")


def _retry_not_due(metadata: Mapping[str, Any], *, now: float) -> bool:
    next_retry_at = _metadata_next_retry_at(metadata)
    return next_retry_at is not None and next_retry_at > now


def _metadata_attempt_count(metadata: Mapping[str, Any]) -> int:
    value = metadata.get("attempt_count", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _metadata_next_retry_at(metadata: Mapping[str, Any]) -> float | None:
    value = metadata.get("next_retry_at")
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return None


def _metadata_reason(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("last_reason")
    return value if isinstance(value, str) and value else None


def _state_text(state: Mapping[str, Any], key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and value else None


def _estimated_batches(pending_count: int, max_batch_events: int | None) -> int | None:
    if max_batch_events is None or max_batch_events < 1:
        return None
    return (pending_count + max_batch_events - 1) // max_batch_events


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


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
