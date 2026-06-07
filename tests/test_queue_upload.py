from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from ellzaf_agent import Config, Ellzaf
from ellzaf_agent.errors import QueueError
from ellzaf_agent.queue import LocalQueue
from ellzaf_agent.serialization import strict_json_dumps


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
    import ellzaf_agent.queue as queue_module

    monkeypatch.setattr(queue_module.time, "time_ns", lambda: 123)
    queue = LocalQueue(tmp_path, max_queue_bytes=1_000_000)

    first = queue.enqueue({"event_id": "evt_same"})
    second = queue.enqueue({"event_id": "evt_same"})

    assert first != second
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 2


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

    client = Ellzaf(
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
    assert captured["url"] == "https://api.ellzaf.com/v1/events/batch"
    assert captured["headers"]["Content-Encoding"] == "gzip"  # type: ignore[index]
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

    client = Ellzaf(
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

    client = Ellzaf(
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

    client = Ellzaf(
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

    client = Ellzaf(
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

    client = Ellzaf(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )
    client.event("risk.check.completed", run_id="run_retry", payload={"approved": True})

    summary = client.flush()

    assert summary.retryable == 1
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1


def test_missing_api_key_skips_upload_but_keeps_local_jsonl(tmp_path: Path) -> None:
    client = Ellzaf(Config(project="paper-agent", queue_dir=tmp_path, api_key=None))
    client.event("risk.check.completed", run_id="run_local", payload={"approved": True})

    summary = client.flush()

    assert summary.skipped is True
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) == 1
