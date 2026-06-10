from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tracker import AgentTracker, Config
from agent_tracker.errors import SchemaValidationError


def make_client(tmp_path: Path, *, environment: str = "paper") -> AgentTracker:
    return AgentTracker(
        Config(
            project="paper-agent",
            environment=environment,
            queue_dir=tmp_path,
            telemetry_enabled=True,
            max_event_bytes=200_000,
        )
    )


def read_pending(tmp_path: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "pending").glob("*.jsonl"))
    ]


def test_manual_event_defaults_and_symbol_normalization(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    event = client.event(
        "risk.check.completed",
        run_id="run_test",
        symbols=["nvda", "NVDA", " ", "msft"],
        payload={"approved": False, "reasons": ["stale_market_data"]},
    )

    assert event["payload"]["risk_check_kind"] == "deterministic"
    assert event["symbols"] == ["NVDA", "MSFT"]
    assert read_pending(tmp_path)[0]["event_id"] == event["event_id"]


def test_invalid_event_type_is_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with pytest.raises(SchemaValidationError):
        client.event("not.real", payload={})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "evt_"),
        ("event_id", "evt_bad space"),
        ("event_id", "evt_bad/slash"),
        ("event_id", "evt_bad\\slash"),
        ("run_id", "run_"),
        ("run_id", "run_bad space"),
        ("run_id", "run_bad/slash"),
        ("run_id", "run_bad\\slash"),
        ("idempotency_key", "bad key"),
        ("idempotency_key", "bad\tkey"),
    ],
)
def test_event_rejects_unsafe_ids(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    client = make_client(tmp_path)
    kwargs = {
        "event_id": "evt_safe",
        "run_id": "run_safe",
        "idempotency_key": "safe/key",
    }
    kwargs[field] = value

    with pytest.raises(SchemaValidationError):
        client.event(
            "risk.check.completed",
            payload={"approved": True},
            **kwargs,
        )


@pytest.mark.parametrize(
    "occurred_at",
    [
        "2026-06-07T06:30:00Z",
        "2026-06-07T06:30:00+00:00",
        "2026-06-07T06:30:00.123456+00:00",
    ],
)
def test_event_accepts_utc_timestamps(tmp_path: Path, occurred_at: str) -> None:
    client = make_client(tmp_path)

    event = client.event(
        "risk.check.completed",
        payload={"approved": True},
        occurred_at=occurred_at,
    )

    assert event["occurred_at"] == occurred_at


@pytest.mark.parametrize(
    "occurred_at",
    [
        "2026-06-07T06:30:00",
        "2026-06-07",
        "2026-06-07T14:30:00+08:00",
        "not-a-time",
    ],
)
def test_event_rejects_non_utc_or_naive_timestamps(
    tmp_path: Path, occurred_at: str
) -> None:
    client = make_client(tmp_path)

    with pytest.raises(SchemaValidationError):
        client.event(
            "risk.check.completed",
            payload={"approved": True},
            occurred_at=occurred_at,
        )


def test_rejected_risk_check_requires_reason(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with pytest.raises(SchemaValidationError):
        client.event("risk.check.completed", payload={"approved": False})


def test_run_context_emits_start_and_completion(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with client.run(run_type="portfolio_allocation", symbols=["aapl"]) as run:
        run.llm_call(provider="openai", model="example-model", output_hash="sha256:x")
        run.risk_check(approved=True)

    events = read_pending(tmp_path)
    assert [event["event_type"] for event in events] == [
        "agent.run.started",
        "llm.call.completed",
        "risk.check.completed",
        "agent.run.completed",
    ]
    assert len({event["run_id"] for event in events}) == 1


def test_run_context_reraises_user_exception_and_records_failure(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)

    with pytest.raises(RuntimeError), client.run(run_type="research_report"):
        raise RuntimeError("model failed")

    event_types = [event["event_type"] for event in read_pending(tmp_path)]
    assert event_types == [
        "agent.run.started",
        "error.recorded",
        "agent.run.completed",
    ]
    assert read_pending(tmp_path)[-1]["payload"]["status"] == "failed"


def test_final_action_completes_only_once(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with client.run(run_type="portfolio_allocation") as run:
        run.final_action(action="no_order", reason="risk_gate_rejected")

    completions = [
        event
        for event in read_pending(tmp_path)
        if event["event_type"] == "agent.run.completed"
    ]
    assert len(completions) == 1
    assert completions[0]["payload"]["final_action"] == "no_order"


def test_sampling_keeps_errors_and_risk_blocks_when_rate_is_zero(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            telemetry_enabled=True,
            sample_rate=0,
            max_event_bytes=200_000,
        )
    )

    client.event(
        "tool.call.completed",
        run_id="run_sample",
        payload={"tool_name": "scanner", "status": "succeeded"},
    )
    client.event(
        "risk.check.completed",
        run_id="run_sample",
        payload={"approved": False, "reasons": ["stale_market_data"]},
    )
    client.event(
        "error.recorded",
        run_id="run_sample",
        payload={"error_kind": "timeout", "message": "provider timeout"},
    )
    client.event(
        "diagnostic.check.completed",
        run_id="run_sample",
        payload={
            "check_id": "decision_flow.numeric_domain",
            "check_family": "numeric_domain",
            "status": "warning",
            "severity": "warning",
            "component": "data_contract",
        },
    )
    client.event(
        "diagnostic.check.completed",
        run_id="run_sample",
        payload={
            "check_id": "behavior.activation_gate",
            "check_family": "replay",
            "status": "passed",
            "severity": "info",
            "component": "diagnostics",
            "activation_allowed": True,
            "activation_scope": "observe_only",
        },
    )

    event_types = [event["event_type"] for event in read_pending(tmp_path)]
    assert event_types == [
        "risk.check.completed",
        "error.recorded",
        "diagnostic.check.completed",
        "diagnostic.check.completed",
    ]


def test_run_helper_emits_diagnostic_check(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with client.run(run_type="diagnostic", symbols=["NVDA"]) as run:
        event = run.diagnostic_check(
            check_id="decision_flow.opportunity_coverage",
            check_family="opportunity_coverage",
            status="failed",
            severity="error",
            component="decision_flow",
            mistake_family="opportunity.candidate_limit_hidden",
            money_impact="possible",
            blocking_status="workflow_deferred",
            resolution_status="open",
            next_safe_action="repair_artifact",
            observed={"candidate_limit_count": 10},
            expected={"omissions_explained": True},
        )

    assert event["event_type"] == "diagnostic.check.completed"
    assert event["payload"]["check_family"] == "opportunity_coverage"
    assert read_pending(tmp_path)[1]["event_id"] == event["event_id"]


def test_event_budget_drops_optional_events_and_records_warning(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            telemetry_enabled=True,
            max_events_per_run=1,
            max_event_bytes=200_000,
        )
    )

    client.event(
        "tool.call.completed",
        run_id="run_budget",
        payload={"tool_name": "scanner", "status": "succeeded"},
    )
    dropped = client.event(
        "tool.call.completed",
        run_id="run_budget",
        payload={"tool_name": "scanner", "status": "succeeded"},
    )

    events = read_pending(tmp_path)
    assert dropped["event_type"] == "tool.call.completed"
    assert [event["event_type"] for event in events] == [
        "tool.call.completed",
        "error.recorded",
    ]
    assert events[-1]["payload"]["error_kind"] == "telemetry_budget_exhausted"
