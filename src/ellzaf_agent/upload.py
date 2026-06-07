"""Batch upload support."""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ellzaf_agent.config import Config
from ellzaf_agent.errors import UploadError
from ellzaf_agent.queue import LocalQueue
from ellzaf_agent.serialization import strict_json_dumps, utc_now_iso

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


class BatchUploader:
    def __init__(
        self,
        config: Config,
        *,
        transport: Transport | None = None,
        gzip_enabled: bool = True,
    ) -> None:
        self.config = config
        self.transport = transport or urllib_transport
        self.gzip_enabled = gzip_enabled

    def flush(self, queue: LocalQueue) -> FlushSummary:
        if not self.config.api_key:
            return FlushSummary(
                attempted=0,
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=0,
                skipped=True,
            )

        pending = queue.pending(
            limit=self.config.max_batch_events,
            max_bytes=self.config.max_batch_bytes,
        )
        if not pending:
            return FlushSummary(0, 0, 0, 0, 0)

        response = self._post_batch([item.event for item in pending])
        accounted_for = response.accepted + response.duplicates + len(response.rejected)
        if accounted_for != len(pending):
            raise UploadError("server response did not account for every event")
        pending_event_ids = {str(item.event.get("event_id")) for item in pending}
        rejected_by_id = {
            item.event_id: item for item in response.rejected if item.event_id
        }
        if len(rejected_by_id) != len(response.rejected):
            raise UploadError("server response contained duplicate rejected event IDs")
        if unknown_rejected_ids := set(rejected_by_id) - pending_event_ids:
            unknown = ", ".join(sorted(unknown_rejected_ids))
            raise UploadError(f"server rejected unknown event IDs: {unknown}")
        duplicates = response.duplicates
        retryable = 0
        for item in pending:
            event_id = str(item.event.get("event_id"))
            rejection = rejected_by_id.get(event_id)
            if rejection is None:
                queue.mark_uploaded(item)
                continue
            if rejection.retryable:
                retryable += 1
                continue
            queue.mark_failed(item, permanent=True)
        return FlushSummary(
            attempted=len(pending),
            accepted=response.accepted,
            duplicates=duplicates,
            rejected=len(response.rejected),
            retryable=retryable,
        )

    def _post_batch(self, events: list[dict[str, Any]]) -> _UploadResponse:
        body = strict_json_dumps(
            {
                "batch_id": f"batch_{uuid4().hex}",
                "sent_at": utc_now_iso(),
                "events": events,
            }
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": f"batch_{uuid4().hex}",
            "User-Agent": "ellzaf-agent-python/0.1.0",
        }
        if self.gzip_enabled:
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        status, raw = self.transport(
            f"{self.config.endpoint}/v1/events/batch",
            headers,
            body,
            self.config.http_timeout_seconds,
        )
        if status == 429:
            raise UploadError("rate limited")
        if status >= 500:
            raise UploadError(f"server error: {status}")
        if status in {401, 403}:
            raise UploadError(f"authorization failed: {status}")
        if status >= 400:
            raise UploadError(f"upload failed: {status}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise UploadError("server returned malformed JSON") from exc
        return _UploadResponse.from_payload(payload)


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
