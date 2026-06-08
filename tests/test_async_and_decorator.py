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


def test_trace_decorator_result_hook_can_emit_domain_events(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    def record_result(run, result: dict) -> None:
        run.decision_proposed(
            decision_kind="target_weight",
            action=result["action"],
            symbol=result["symbol"],
        )

    @client.trace(
        run_type="portfolio_allocation",
        symbols=lambda _args, _kwargs, _result: ["nvda"],
        on_result=record_result,
    )
    def decide() -> dict:
        return {"symbol": "NVDA", "action": "increase"}

    assert decide()["action"] == "increase"

    events = read_pending(tmp_path)
    assert [event["event_type"] for event in events] == [
        "agent.run.started",
        "decision.proposed",
        "agent.run.completed",
    ]
    assert events[0]["symbols"] == ["NVDA"]


def test_trace_decorator_result_hook_failure_does_not_change_result(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    def bad_hook(_run, _result: int) -> None:
        raise RuntimeError("mapper failed")

    @client.trace(run_type="session_homework", on_result=bad_hook)
    def build_plan() -> int:
        return 42

    assert build_plan() == 42
    events = read_pending(tmp_path)
    assert [event["event_type"] for event in events] == [
        "agent.run.started",
        "error.recorded",
        "agent.run.completed",
    ]
    assert events[1]["payload"]["error_kind"] == "result_hook_failed"


def test_trace_decorator_exception_hook_preserves_original_exception(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    def record_exception(run, exc: BaseException) -> None:
        run.error(error_kind="agent_failed", message=type(exc).__name__)

    @client.trace(run_type="session_homework", on_exception=record_exception)
    def build_plan() -> None:
        raise RuntimeError("boom")

    try:
        build_plan()
    except RuntimeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("original exception was swallowed")

    events = read_pending(tmp_path)
    assert [event["event_type"] for event in events] == [
        "agent.run.started",
        "error.recorded",
        "error.recorded",
        "agent.run.completed",
    ]
    assert events[1]["payload"]["error_kind"] == "agent_failed"
    assert events[-1]["payload"]["status"] == "failed"


def test_instrument_context_temporarily_wraps_agent_method(tmp_path: Path) -> None:
    class DemoAgent:
        def run(self) -> str:
            return "done"

    agent = DemoAgent()
    original = agent.run
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    with client.instrument(agent, methods=["run"], run_type="demo"):
        assert agent.run() == "done"
        assert agent.run is not original

    assert agent.run() == "done"
    assert read_pending(tmp_path)[0]["payload"]["run_type"] == "demo"


def test_auto_flush_context_flushes_on_exit(tmp_path: Path) -> None:
    uploaded = 0

    def transport(
        _url: str, _headers: dict[str, str], body: bytes, _timeout: float
    ) -> tuple[int, bytes]:
        nonlocal uploaded
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        uploaded += len(payload["events"])
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
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            api_key="project-key",
            flush_interval_seconds=0,
        ),
        transport=transport,
    )

    with client.auto_flush():
        client.event(
            "risk.check.completed",
            run_id="run_auto",
            payload={"approved": True},
        )

    assert uploaded == 1
    assert read_pending(tmp_path) == []


def test_domain_wrappers_emit_expected_events(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, api_key=None)
    )

    def risk_gate() -> dict:
        return {"approved": False, "reasons": ["max_position_pct"]}

    safe_risk_gate = client.wrap_risk_gate(
        risk_gate,
        approved=lambda result: result["approved"],
        reasons=lambda result: result["reasons"],
    )

    def decide() -> dict:
        return {"symbol": "NVDA", "action": "increase"}

    tracked_decide = client.wrap_decision(
        decide,
        decision_kind="target_weight",
        action=lambda result: result["action"],
        symbol=lambda result: result["symbol"],
    )

    def paper_fill() -> dict:
        return {"symbol": "NVDA", "side": "buy", "quantity": "1", "price": "100.00"}

    tracked_fill = client.wrap_paper_broker(
        paper_fill,
        symbol=lambda result: result["symbol"],
        side=lambda result: result["side"],
        quantity=lambda result: result["quantity"],
        price=lambda result: result["price"],
    )

    assert safe_risk_gate()["approved"] is False
    assert tracked_decide()["symbol"] == "NVDA"
    assert tracked_fill()["quantity"] == "1"

    event_types = [event["event_type"] for event in read_pending(tmp_path)]
    assert event_types == [
        "agent.run.started",
        "risk.check.completed",
        "agent.run.completed",
        "agent.run.started",
        "decision.proposed",
        "agent.run.completed",
        "agent.run.started",
        "paper.fill.recorded",
        "agent.run.completed",
    ]
