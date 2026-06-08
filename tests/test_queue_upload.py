from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from agent_tracker import AgentTracker, Config
from agent_tracker.constants import SDK_USER_AGENT
from agent_tracker.errors import QueueError
from agent_tracker.queue import LocalQueue
from agent_tracker.serialization import strict_json_dumps, strict_json_loads


def test_queue_quarantines_partial_and_corrupt_rows(tmp_path: Path) -> None:
    queue = LocalQueue(tmp_path, max_queue_bytes=1_000_000)
    (tmp_path / "pending" / "partial.jsonl").write_text(
        '{"event_id":"evt_partial"}', encoding="utf-8"
    )
    (tmp_path / "pending" / "corrupt.jsonl").write_text("{bad}\n", encoding="utf-8")

    assert queue.pending(limit=10, max_bytes=1_000_000) == []
    health = queue.health()
    assert health.pending == 0
    assert health.quarantined == 2


def test_queue_paths_remain_unique_when_clock_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_tracker.queue as queue_module

    monkeypatch.setattr(queue_module.time, "time_ns", lambda: 123)
    queue = LocalQueue(tmp_path, max_queue_bytes=1_000_000)

    first = queue.enqueue({"event_id": "evt_same"})
    second = queue.enqueue({"event_id": "evt_same"})

    assert first != second
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 2


def test_queue_can_dedupe_pending_idempotency_keys(tmp_path: Path) -> None:
    queue = LocalQueue(tmp_path, max_queue_bytes=1_000_000)
    first = {"event_id": "evt_first", "idempotency_key": "same-key"}
    second = {"event_id": "evt_second", "idempotency_key": "same-key"}

    first_path = queue.enqueue(first, dedupe_idempotency_key=True)
    second_path = queue.enqueue(second, dedupe_idempotency_key=True)

    assert first_path == second_path
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_client_dedupe_does_not_consume_run_event_budget(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            dedupe_idempotency_keys=True,
            max_events_per_run=2,
        )
    )

    first = client.event(
        "risk.check.completed",
        run_id="run_dedupe_budget",
        idempotency_key="same-risk-check",
        payload={"approved": True},
    )
    second = client.event(
        "risk.check.completed",
        run_id="run_dedupe_budget",
        idempotency_key="same-risk-check",
        payload={"approved": True},
    )
    third = client.event(
        "error.recorded",
        run_id="run_dedupe_budget",
        payload={"error_kind": "runtime", "message": "still captured"},
    )

    pending = list((tmp_path / "pending").glob("*.jsonl"))
    assert first["idempotency_key"] == second["idempotency_key"]
    assert len(pending) == 2
    assert third["event_type"] == "error.recorded"


def test_queue_rejects_event_that_would_exceed_disk_cap(tmp_path: Path) -> None:
    first_event = {"event_id": "evt_first"}
    first_event_bytes = len((strict_json_dumps(first_event) + "\n").encode("utf-8"))
    queue = LocalQueue(tmp_path, max_queue_bytes=first_event_bytes)

    queue.enqueue(first_event)

    with pytest.raises(QueueError, match="disk cap"):
        queue.enqueue({"event_id": "evt_second"})

    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_queue_rejects_single_event_larger_than_disk_cap(tmp_path: Path) -> None:
    queue = LocalQueue(tmp_path, max_queue_bytes=32)

    with pytest.raises(QueueError, match="disk cap"):
        queue.enqueue({"event_id": "evt_big", "payload": "x" * 128})

    assert list((tmp_path / "pending").glob("*.jsonl")) == []


def test_quarantine_preserves_repeated_event_ids(tmp_path: Path) -> None:
    queue = LocalQueue(tmp_path, max_queue_bytes=1_000_000)

    first = queue.quarantine({"event_id": "evt_repeated"}, reason="first")
    second = queue.quarantine({"event_id": "evt_repeated"}, reason="second")

    assert first != second
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "quarantine").glob("*.jsonl"))
    ]
    assert {record["reason"] for record in records} == {"first", "second"}


def test_flush_uploads_accepted_batch_and_moves_files(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def transport(
        url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, bytes]:
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["payload"] = json.loads(gzip.decompress(body).decode("utf-8"))
        return 200, b'{"accepted":1,"duplicates":0,"rejected":[]}'

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed",
        run_id="run_upload",
        payload={"approved": True},
    )

    summary = client.flush()

    assert summary.accepted == 1
    assert captured["url"] == "https://ellzaf.com/v1/events/batch"
    assert captured["headers"]["Authorization"] == "Bearer project-key"  # type: ignore[index]
    assert captured["headers"]["Content-Type"] == "application/json"  # type: ignore[index]
    assert captured["headers"]["Content-Encoding"] == "gzip"  # type: ignore[index]
    assert captured["headers"]["Idempotency-Key"] == captured["payload"]["batch_id"]  # type: ignore[index]
    assert captured["headers"]["User-Agent"] == SDK_USER_AGENT  # type: ignore[index]
    assert captured["payload"]["sent_at"]  # type: ignore[index]
    assert len(captured["payload"]["events"]) == 1  # type: ignore[index]
    assert (tmp_path / "pending").exists()
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 0
    assert len(list((tmp_path / "uploaded").glob("*.jsonl"))) == 1


def test_partial_rejection_marks_permanent_failure(tmp_path: Path) -> None:
    rejected_event_id: str | None = None

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        nonlocal rejected_event_id
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        rejected_event_id = payload["events"][0]["event_id"]
        response = {
            "accepted": 0,
            "duplicates": 0,
            "rejected": [
                {
                    "event_id": rejected_event_id,
                    "code": "schema_validation_failed",
                    "message": "bad event",
                    "retryable": False,
                }
            ],
        }
        return 200, json.dumps(response).encode("utf-8")

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed", run_id="run_reject", payload={"approved": True}
    )

    summary = client.flush()

    assert summary.rejected == 1
    assert summary.accepted == 0
    assert rejected_event_id is not None
    assert len(list((tmp_path / "failed").glob("*.permanent.jsonl"))) == 1


def test_malformed_success_response_keeps_events_pending(tmp_path: Path) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        return 200, b'{"accepted":0,"duplicates":0,"rejected":[]}'

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed", run_id="run_mismatch", payload={"approved": True}
    )

    summary = client.flush()

    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


@pytest.mark.parametrize(
    "response",
    [
        b'{"accepted":"1","duplicates":0,"rejected":[]}',
        b'{"accepted":-1,"duplicates":2,"rejected":[]}',
        b'{"accepted":0,"duplicates":true,"rejected":[]}',
        b'{"accepted":0,"duplicates":0,"rejected":"bad"}',
        b'{"accepted":0,"duplicates":0,"rejected":[{}]}',
        (
            b'{"accepted":0,"duplicates":0,'
            b'"rejected":[{"event_id":"evt_other","code":"bad",'
            b'"message":"bad","retryable":false}]}'
        ),
    ],
)
def test_invalid_success_response_keeps_events_pending(
    tmp_path: Path,
    response: bytes,
) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        return 200, response

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed",
        run_id="run_invalid_response",
        payload={"approved": True},
    )

    summary = client.flush()

    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_duplicate_response_marks_uploaded_without_counting_as_accepted(
    tmp_path: Path,
) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        return 200, b'{"accepted":0,"duplicates":1,"rejected":[]}'

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed", run_id="run_duplicate", payload={"approved": True}
    )

    summary = client.flush()

    assert summary.accepted == 0
    assert summary.duplicates == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 0
    assert len(list((tmp_path / "uploaded").glob("*.jsonl"))) == 1


def test_server_error_keeps_events_pending_and_returns_retryable_summary(
    tmp_path: Path,
) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        return 500, b'{"error":"server"}'

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_retry", payload={"approved": True})

    summary = client.flush()

    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_missing_api_key_skips_upload_but_keeps_local_jsonl(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )
    client.event("risk.check.completed", run_id="run_local", payload={"approved": True})

    summary = client.flush()

    assert summary.skipped is True
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_flush_all_drains_multiple_batches(tmp_path: Path) -> None:
    batch_sizes: list[int] = []

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        batch_size = len(payload["events"])
        batch_sizes.append(batch_size)
        return (
            200,
            json.dumps(
                {"accepted": batch_size, "duplicates": 0, "rejected": []}
            ).encode("utf-8"),
        )

    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            api_key="project-key",
            max_batch_events=2,
        ),
        transport=transport,
    )
    for index in range(5):
        client.event(
            "risk.check.completed",
            run_id=f"run_drain_{index}",
            payload={"approved": True},
        )

    summary = client.flush_all()

    assert summary.attempted == 5
    assert summary.accepted == 5
    assert summary.batch_count == 3
    assert summary.stopped is True
    assert summary.stop_reason == "queue_empty"
    assert batch_sizes == [2, 2, 1]
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 0
    assert len(list((tmp_path / "uploaded").glob("*.jsonl"))) == 5


def test_flush_all_stops_on_retryable_rejection(tmp_path: Path) -> None:
    calls = 0

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        rejected = payload["events"][0]["event_id"]
        return (
            200,
            json.dumps(
                {
                    "accepted": 0,
                    "duplicates": 0,
                    "rejected": [
                        {
                            "event_id": rejected,
                            "code": "temporary_limit",
                            "message": "retry later",
                            "retryable": True,
                        }
                    ],
                }
            ).encode("utf-8"),
        )

    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            api_key="project-key",
            max_batch_events=1,
        ),
        transport=transport,
    )
    client.event(
        "risk.check.completed", run_id="run_retry_1", payload={"approved": True}
    )
    client.event(
        "risk.check.completed", run_id="run_retry_2", payload={"approved": True}
    )

    summary = client.flush_all()

    assert calls == 1
    assert summary.retryable == 1
    assert summary.retryable_rejections == 1
    assert summary.stop_reason == "retryable_pending"
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 2


def test_flush_dry_run_prepares_batch_without_transport_or_file_moves(
    tmp_path: Path,
) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        raise AssertionError("dry run must not call transport")

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_dry", payload={"approved": True})

    summary = client.flush(dry_run=True)

    assert summary.dry_run is True
    assert summary.skipped is True
    assert summary.reason_code == "dry_run"
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1
    assert len(list((tmp_path / "uploaded").glob("*.jsonl"))) == 0


def test_flush_reports_authorization_failure_as_permanent(
    tmp_path: Path,
) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        return 401, b'{"error":"bad key"}'

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_auth", payload={"approved": True})

    summary = client.flush()

    assert summary.status == "permanent_failed"
    assert summary.reason_code == "authorization_failed"
    assert summary.retryable == 0
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_flush_can_disable_gzip(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def transport(
        _url: str, headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        captured["headers"] = headers
        captured["payload"] = json.loads(body.decode("utf-8"))
        return 200, b'{"accepted":1,"duplicates":0,"rejected":[]}'

    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            api_key="project-key",
            gzip_enabled=False,
        ),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_plain", payload={"approved": True})

    summary = client.flush()

    assert summary.accepted == 1
    assert "Content-Encoding" not in captured["headers"]  # type: ignore[operator]
    assert len(captured["payload"]["events"]) == 1  # type: ignore[index]


def test_upload_byte_budget_keeps_events_pending(tmp_path: Path) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        raise AssertionError("budget exhaustion must stop before transport")

    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            api_key="project-key",
            gzip_enabled=False,
            max_event_bytes=4_096,
            max_batch_bytes=4_096,
            max_upload_bytes_per_day=1_024,
        ),
        transport=transport,
    )
    client.event(
        "tool.call.completed",
        run_id="run_budget",
        payload={"tool_name": "scanner", "status": "succeeded", "summary": "x" * 1300},
    )

    summary = client.flush()

    assert summary.status == "retryable_failed"
    assert summary.reason_code == "upload_byte_budget_exhausted"
    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_duplicate_rejected_event_ids_keep_batch_pending(tmp_path: Path) -> None:
    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        event_id = payload["events"][0]["event_id"]
        return (
            200,
            json.dumps(
                {
                    "accepted": 0,
                    "duplicates": 0,
                    "rejected": [
                        {
                            "event_id": event_id,
                            "code": "bad",
                            "message": "bad",
                            "retryable": False,
                        },
                        {
                            "event_id": event_id,
                            "code": "bad",
                            "message": "bad",
                            "retryable": False,
                        },
                    ],
                }
            ).encode("utf-8"),
        )

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed", run_id="run_dupe_rej", payload={"approved": True}
    )

    summary = client.flush()

    assert summary.reason_code == "response_count_mismatch"
    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_rate_limit_retry_after_defers_pending_event(tmp_path: Path) -> None:
    calls = 0

    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes, dict[str, str]]:
        nonlocal calls
        calls += 1
        return 429, b'{"error":"rate limited"}', {"Retry-After": "60"}

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_429", payload={"approved": True})

    first = client.flush()
    second = client.flush()
    health = client.queue.health()  # type: ignore[union-attr]

    assert first.reason_code == "rate_limited"
    assert first.retry_after_seconds == 60
    assert second.reason_code == "retry_not_due"
    assert second.retryable == 1
    assert calls == 1
    assert health.retryable_pending == 1
    assert health.next_retry_seconds is not None
    assert 0 < health.next_retry_seconds <= 60
    assert health.last_upload_status == "skipped"
    assert health.last_upload_reason == "retry_not_due"


def test_retry_metadata_allows_upload_after_due_time(tmp_path: Path) -> None:
    calls = 0

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 500, b'{"error":"server"}'
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        return (
            200,
            json.dumps(
                {"accepted": len(payload["events"]), "duplicates": 0, "rejected": []}
            ).encode("utf-8"),
        )

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event(
        "risk.check.completed",
        run_id="run_retry_due",
        payload={"approved": True},
    )

    first = client.flush()
    metadata_path = next((tmp_path / "pending").glob("*.retry.json"))
    metadata = strict_json_loads(metadata_path.read_text(encoding="utf-8"))
    metadata["next_retry_at"] = time.time() - 1
    metadata_path.write_text(strict_json_dumps(metadata) + "\n", encoding="utf-8")
    second = client.flush()

    assert first.reason_code == "server_error"
    assert second.accepted == 1
    assert calls == 2
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 0
    assert list((tmp_path / "pending").glob("*.retry.json")) == []


def test_flush_returns_structured_skip_when_queue_is_locked(tmp_path: Path) -> None:
    def transport(
        _url: str, _headers: dict[str, str], _body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        raise AssertionError("locked queue must not upload")

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_lock", payload={"approved": True})
    assert client.queue is not None

    with client.queue.flush_lock() as acquired:
        assert acquired is True
        summary = client.flush()

    assert summary.skipped is True
    assert summary.reason_code == "queue_locked"
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1
