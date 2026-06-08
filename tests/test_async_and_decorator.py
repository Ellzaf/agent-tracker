from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

from agent_tracker import AgentTracker, Config


def read_pending(tmp_path: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "pending").glob("*.jsonl"))
    ]


def test_async_run_context_and_aflush(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    async def scenario() -> None:
        async with client.arun(run_type="market_move_triage", symbols=["tsla"]) as run:
            run.tool_call(tool_name="local_helper", status="succeeded")

    asyncio.run(scenario())
    summary = asyncio.run(client.aflush())

    assert summary.skipped is True
    assert [event["event_type"] for event in read_pending(tmp_path)] == [
        "agent.run.started",
        "tool.call.completed",
        "agent.run.completed",
    ]


def test_trace_decorator_records_sync_function(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    @client.trace(run_type="session_homework", symbols=["amd"])
    def build_plan(value: int) -> int:
        return value + 1

    assert build_plan(2) == 3
    started = read_pending(tmp_path)[0]
    assert started["payload"]["metadata"]["function"] == "build_plan"
    assert started["payload"]["metadata"]["input_hash"].startswith("sha256:")


def test_trace_decorator_records_async_function(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    @client.trace(run_type="session_homework")
    async def build_plan(value: int) -> int:
        return value + 1

    assert asyncio.run(build_plan(4)) == 5
    assert read_pending(tmp_path)[-1]["event_type"] == "agent.run.completed"


def test_trace_decorator_flush_after_drains_after_completion(tmp_path: Path) -> None:
    uploaded_types: list[str] = []

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        uploaded_types.extend(event["event_type"] for event in payload["events"])
        return (
            200,
            json.dumps(
                {
                    "accepted": len(payload["events"]),
                    "duplicates": 0,
                    "rejected": [],
                }
            ).encode("utf-8"),
        )

    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key="project-key"),
        transport=transport,
    )

    @client.trace(run_type="session_homework", flush_after=True)
    def build_plan(value: int) -> int:
        return value + 1

    assert build_plan(2) == 3
    assert uploaded_types == ["agent.run.started", "agent.run.completed"]
    assert read_pending(tmp_path) == []
