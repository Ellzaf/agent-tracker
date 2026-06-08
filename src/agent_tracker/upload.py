"""Batch upload support."""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from agent_tracker.config import Config
from agent_tracker.constants import SDK_USER_AGENT
from agent_tracker.errors import UploadError
from agent_tracker.queue import LocalQueue
from agent_tracker.serialization import strict_json_dumps, utc_now_iso

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


@dataclass(frozen=True, slots=True)
class RejectedEvent:
    event_id: str | None
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class FlushSummary:
    attempted: int
    accepted: int
    duplicates: int
    rejected: int
    retryable: int
    skipped: bool = False
    status: str = "succeeded"
    reason_code: str | None = None
    message: str | None = None
    batch_count: int = 0
    retry_after_seconds: float | None = None
    permanent_rejections: int = 0
    retryable_rejections: int = 0
    stopped: bool = False
    stop_reason: str | None = None
    dry_run: bool = False


class BatchUploader:
    def __init__(
        self,
        config: Config,
        *,
        transport: Transport | None = None,
        gzip_enabled: bool | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or urllib_transport
        self.gzip_enabled = (
            config.gzip_enabled if gzip_enabled is None else gzip_enabled
        )
        self._uploaded_day: date | None = None
        self._uploaded_bytes_today = 0

    def flush(
        self,
        queue: LocalQueue,
        *,
        dry_run: bool = False,
        raise_on_error: bool = False,
    ) -> FlushSummary:
        if not self.config.api_key:
            return FlushSummary(
                attempted=0,
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=0,
                skipped=True,
                status="skipped",
                reason_code="missing_api_key",
                message="ELLZAF_API_KEY is not configured; events remain queued",
                dry_run=dry_run,
            )

        pending = queue.pending(
            limit=self.config.max_batch_events,
            max_bytes=self.config.max_batch_bytes,
        )
        if not pending:
            return FlushSummary(0, 0, 0, 0, 0, dry_run=dry_run)

        if dry_run:
            body = self._batch_body([item.event for item in pending])
            return FlushSummary(
                attempted=len(pending),
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=0,
                skipped=True,
                status="skipped",
                reason_code="dry_run",
                message=(
                    "dry run prepared a valid batch without uploading or moving "
                    f"queue files ({len(body.body)} bytes)"
                ),
                batch_count=1,
                dry_run=True,
            )

        try:
            response = self._post_batch([item.event for item in pending])
        except UploadError as exc:
            if raise_on_error:
                raise
            return _summary_from_upload_error(exc, attempted=len(pending))

        accounted_for = response.accepted + response.duplicates + len(response.rejected)
        if accounted_for != len(pending):
            exc = UploadError(
                "server response did not account for every event",
                reason_code="response_count_mismatch",
                retryable=True,
            )
            if raise_on_error:
                raise exc
            return _summary_from_upload_error(exc, attempted=len(pending))
        pending_event_ids = {str(item.event.get("event_id")) for item in pending}
        rejected_by_id = {
            item.event_id: item for item in response.rejected if item.event_id
        }
        if len(rejected_by_id) != len(response.rejected):
            exc = UploadError(
                "server response contained duplicate rejected event IDs",
                reason_code="duplicate_rejected_event_id",
                retryable=True,
            )
            if raise_on_error:
                raise exc
            return _summary_from_upload_error(exc, attempted=len(pending))
        if unknown_rejected_ids := set(rejected_by_id) - pending_event_ids:
            unknown = ", ".join(sorted(unknown_rejected_ids))
            exc = UploadError(
                f"server rejected unknown event IDs: {unknown}",
                reason_code="unknown_rejected_event_id",
                retryable=True,
            )
            if raise_on_error:
                raise exc
            return _summary_from_upload_error(exc, attempted=len(pending))
        duplicates = response.duplicates
        retryable = 0
        permanent = 0
        for item in pending:
            event_id = str(item.event.get("event_id"))
            rejection = rejected_by_id.get(event_id)
            if rejection is None:
                queue.mark_uploaded(item)
                continue
            if rejection.retryable:
                retryable += 1
                continue
            permanent += 1
            queue.mark_failed(item, permanent=True)
        return FlushSummary(
            attempted=len(pending),
            accepted=response.accepted,
            duplicates=duplicates,
            rejected=len(response.rejected),
            retryable=retryable,
            status="partial" if response.rejected else "succeeded",
            reason_code="partial_rejection" if response.rejected else None,
            message=None
            if not response.rejected
            else "server rejected one or more events",
            batch_count=1,
            permanent_rejections=permanent,
            retryable_rejections=retryable,
        )

    def flush_all(
        self,
        queue: LocalQueue,
        *,
        max_batches: int | None = None,
        dry_run: bool = False,
        raise_on_error: bool = False,
    ) -> FlushSummary:
        if max_batches is not None and max_batches < 1:
            raise ValueError("max_batches must be >= 1")

        total = FlushSummary(0, 0, 0, 0, 0, dry_run=dry_run)
        batches = 0
        while max_batches is None or batches < max_batches:
            summary = self.flush(
                queue, dry_run=dry_run, raise_on_error=raise_on_error
            )
            batches += 1 if summary.attempted else 0
            total = _merge_summaries(total, summary, batch_count=batches)
            if (
                summary.attempted == 0
                or summary.skipped
                or summary.retryable
                or summary.status in {"retryable_failed", "permanent_failed"}
            ):
                return _stopped_summary(total, summary, batches)
            if dry_run:
                return _stopped_summary(total, summary, batches)
        return FlushSummary(
            attempted=total.attempted,
            accepted=total.accepted,
            duplicates=total.duplicates,
            rejected=total.rejected,
            retryable=total.retryable,
            skipped=total.skipped,
            status="partial" if total.rejected else "succeeded",
            reason_code=total.reason_code,
            message=total.message,
            batch_count=batches,
            retry_after_seconds=total.retry_after_seconds,
            permanent_rejections=total.permanent_rejections,
            retryable_rejections=total.retryable_rejections,
            stopped=True,
            stop_reason="max_batches_reached",
            dry_run=dry_run,
        )

    def _post_batch(self, events: list[dict[str, Any]]) -> _UploadResponse:
        batch = self._batch_body(events)
        self._enforce_upload_byte_budget(len(batch.body))
        status, raw = self.transport(
            f"{self.config.endpoint}/v1/events/batch",
            batch.headers,
            batch.body,
            self.config.http_timeout_seconds,
        )
        self._record_uploaded_bytes(len(batch.body))
        if status == 429:
            raise UploadError(
                "rate limited",
                reason_code="rate_limited",
                retryable=True,
                status_code=status,
                retry_after_seconds=_retry_after_seconds(batch.headers),
            )
        if status >= 500:
            raise UploadError(
                f"server error: {status}",
                reason_code="server_error",
                retryable=True,
                status_code=status,
            )
        if status in {401, 403}:
            raise UploadError(
                f"authorization failed: {status}",
                reason_code="authorization_failed",
                retryable=False,
                status_code=status,
            )
        if status == 404:
            raise UploadError(
                "ingestion endpoint not found",
                reason_code="endpoint_not_found",
                retryable=False,
                status_code=status,
            )
        if status == 413:
            raise UploadError(
                "batch exceeds server size limit",
                reason_code="batch_too_large",
                retryable=False,
                status_code=status,
            )
        if status == 415:
            raise UploadError(
                "server rejected batch content encoding or media type",
                reason_code="unsupported_media_type",
                retryable=False,
                status_code=status,
            )
        if status >= 400:
            raise UploadError(
                f"upload failed: {status}",
                reason_code="client_error",
                retryable=False,
                status_code=status,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise UploadError(
                "server returned malformed JSON",
                reason_code="malformed_json_response",
                retryable=True,
            ) from exc
        return _UploadResponse.from_payload(payload)

    def _batch_body(self, events: list[dict[str, Any]]) -> _PreparedBatch:
        batch_id = f"batch_{uuid4().hex}"
        body = strict_json_dumps(
            {
                "batch_id": batch_id,
                "sent_at": utc_now_iso(),
                "events": events,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": batch_id,
            "User-Agent": SDK_USER_AGENT,
        }
        if self.gzip_enabled:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        return _PreparedBatch(body=body, headers=headers)

    def _enforce_upload_byte_budget(self, body_size: int) -> None:
        if self.config.max_upload_bytes_per_day is None:
            return
        self._reset_upload_day_if_needed()
        projected_bytes = self._uploaded_bytes_today + body_size
        if projected_bytes > self.config.max_upload_bytes_per_day:
            raise UploadError(
                "daily upload byte budget exhausted",
                reason_code="upload_byte_budget_exhausted",
                retryable=True,
            )

    def _record_uploaded_bytes(self, body_size: int) -> None:
        if self.config.max_upload_bytes_per_day is None:
            return
        self._reset_upload_day_if_needed()
        self._uploaded_bytes_today += body_size

    def _reset_upload_day_if_needed(self) -> None:
        today = datetime.now(UTC).date()
        if self._uploaded_day != today:
            self._uploaded_day = today
            self._uploaded_bytes_today = 0


@dataclass(frozen=True, slots=True)
class _PreparedBatch:
    body: bytes
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class _UploadResponse:
    accepted: int
    duplicates: int
    rejected: list[RejectedEvent]

    @classmethod
    def from_payload(cls, payload: Any) -> _UploadResponse:
        if not isinstance(payload, dict):
            raise UploadError("server returned a non-object upload response")
        accepted = _non_negative_int(payload.get("accepted", 0), field="accepted")
        duplicates = _non_negative_int(payload.get("duplicates", 0), field="duplicates")
        raw_rejected = payload.get("rejected", [])
        if not isinstance(raw_rejected, list):
            raise UploadError("server returned invalid rejected events")
        rejected = [
            _rejected_event_from_payload(item, index=index)
            for index, item in enumerate(raw_rejected)
        ]
        return cls(
            accepted=accepted,
            duplicates=duplicates,
            rejected=rejected,
        )


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UploadError(f"server returned invalid {field} count")
    return value


def _rejected_event_from_payload(item: Any, *, index: int) -> RejectedEvent:
    if not isinstance(item, dict):
        raise UploadError(f"server returned invalid rejected event at index {index}")
    event_id = item.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise UploadError(f"server omitted rejected event_id at index {index}")
    code = item.get("code", "server_error")
    message = item.get("message", "")
    retryable = item.get("retryable", False)
    if not isinstance(code, str) or not code:
        raise UploadError(f"server returned invalid rejection code at index {index}")
    if not isinstance(message, str):
        raise UploadError(f"server returned invalid rejection message at index {index}")
    if not isinstance(retryable, bool):
        raise UploadError(f"server returned invalid retryable flag at index {index}")
    return RejectedEvent(
        event_id=event_id,
        code=code,
        message=message,
        retryable=retryable,
    )


def _summary_from_upload_error(exc: UploadError, *, attempted: int) -> FlushSummary:
    retryable = attempted if exc.retryable else 0
    return FlushSummary(
        attempted=attempted,
        accepted=0,
        duplicates=0,
        rejected=0,
        retryable=retryable,
        status="retryable_failed" if exc.retryable else "permanent_failed",
        reason_code=exc.reason_code,
        message=str(exc),
        retry_after_seconds=exc.retry_after_seconds,
        retryable_rejections=retryable,
    )


def _merge_summaries(
    left: FlushSummary,
    right: FlushSummary,
    *,
    batch_count: int,
) -> FlushSummary:
    return FlushSummary(
        attempted=left.attempted + right.attempted,
        accepted=left.accepted + right.accepted,
        duplicates=left.duplicates + right.duplicates,
        rejected=left.rejected + right.rejected,
        retryable=left.retryable + right.retryable,
        skipped=left.skipped or right.skipped,
        status=right.status if right.status != "succeeded" else left.status,
        reason_code=right.reason_code or left.reason_code,
        message=right.message or left.message,
        batch_count=batch_count,
        retry_after_seconds=right.retry_after_seconds or left.retry_after_seconds,
        permanent_rejections=left.permanent_rejections + right.permanent_rejections,
        retryable_rejections=left.retryable_rejections + right.retryable_rejections,
        dry_run=left.dry_run or right.dry_run,
    )


def _stopped_summary(
    total: FlushSummary,
    last: FlushSummary,
    batches: int,
) -> FlushSummary:
    stop_reason = "queue_empty"
    if last.skipped:
        stop_reason = last.reason_code or "skipped"
    elif last.retryable:
        stop_reason = "retryable_pending"
    elif last.status in {"retryable_failed", "permanent_failed"}:
        stop_reason = last.reason_code or last.status
    return FlushSummary(
        attempted=total.attempted,
        accepted=total.accepted,
        duplicates=total.duplicates,
        rejected=total.rejected,
        retryable=total.retryable,
        skipped=total.skipped,
        status=total.status,
        reason_code=total.reason_code,
        message=total.message,
        batch_count=batches,
        retry_after_seconds=total.retry_after_seconds,
        permanent_rejections=total.permanent_rejections,
        retryable_rejections=total.retryable_rejections,
        stopped=True,
        stop_reason=stop_reason,
        dry_run=total.dry_run,
    )


def _retry_after_seconds(_headers: dict[str, str]) -> float | None:
    return None


def urllib_transport(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except OSError as exc:
        raise UploadError(str(exc)) from exc
