from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tracker import AgentTracker, Config
from agent_tracker.errors import SchemaValidationError
from agent_tracker.reporting import (
    assess_agentic_security_readiness,
    assess_reporting_readiness,
    assess_tier_readiness,
    build_dataset_items,
    build_eval_plan,
    build_repair_pack,
)
from agent_tracker.resources import list_resource_names, read_json_resource
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_reporting_helpers_emit_strict_ready_events(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(
            project="paper-agent",
            queue_dir=tmp_path,
            telemetry_enabled=False,
            max_event_bytes=200_000,
        )
    )
    events: list[dict] = []

    with client.run(run_type="portfolio_allocation", symbols=["NVDA"]) as run:
        events.append(
            run.agent_build(
                build_id="build_1",
                config_hash="sha256:config",
                risk_gate_version="risk-1",
            )
        )
        events.append(
            run.strategy_context(
                strategy_id="strat_breakout",
                strategy_name="Breakout",
                setup="gap_hold",
                planned_risk_pct="0.5",
            )
        )
        events.append(
            run.prompt_version(
                family="allocation",
                version="2026-06-07",
                prompt_hash="sha256:prompt",
                provider="openai",
                model="example",
            )
        )
        order = run.order_intent(
            order_intent_id="intent_1",
            decision_id="decision_1",
            symbol="NVDA",
            side="sell",
            intended_quantity="2",
            intended_price="101.00",
            open_close_effect="close",
            strategy_id="strat_breakout",
            setup="gap_hold",
            session_date="2026-06-07",
        )
        events.append(order)
        events.append(
            run.risk_check(
                approved=False,
                risk_check_id="risk_1",
                order_intent_id="intent_1",
                reasons=["open_session_stale_bars"],
                component="market_data",
                severity="warning",
                mistake_family="market.open_session_stale_bars",
                money_impact="blocked",
                blocking_status="trading_blocked",
                resolution_status="open",
                next_safe_action="block_artifact",
            )
        )
        fill = run.paper_fill(
            fill_id="fill_1",
            position_id="pos_1",
            order_intent_id="intent_1",
            symbol="NVDA",
            side="sell",
            open_close_effect="close",
            quantity="2",
            price="101.00",
            fees="0.25",
            currency="USD",
            fill_source="paper",
            session_date="2026-06-07",
            strategy_id="strat_breakout",
            setup="gap_hold",
        )
        events.append(fill)
        events.append(
            run.position_snapshot(
                portfolio_kind="paper",
                position_id="pos_1",
                symbol="NVDA",
                quantity="0",
                average_price="96.00",
                market_price="101.00",
                realized_pnl="9.75",
                strategy_id="strat_breakout",
                setup="gap_hold",
            )
        )
        events.append(
            run.capital_flow(
                capital_flow_id="flow_1",
                flow_kind="deposit",
                amount="1000.00",
                asset="USD",
                currency="USD",
                session_date="2026-06-07",
                included_in_trading_pnl=False,
            )
        )
        events.append(
            run.performance_snapshot(
                period_kind="daily",
                period_start="2026-06-07",
                period_end="2026-06-07",
                session_date="2026-06-07",
                flow_adjusted_equity_change="9.75",
                trading_pnl_amount="9.75",
                net_pnl_amount="9.75",
                fees="0.25",
                return_base="1000.00",
                compounded_return_pct="0.98",
                max_drawdown_pct="1.2",
            )
        )
        events.append(
            run.replay_result(
                suite_name="open-session-stale-bars",
                status="succeeded",
                case_count=3,
                prompt_hash="sha256:prompt",
                prompt_version="2026-06-07",
                replay_suite_version="replay-1",
                scenario_tags=["stale_market_data"],
            )
        )
        events.append(
            run.decision_outcome(
                decision_id="decision_1",
                outcome_kind="filled",
                linked_event_ids=[order["event_id"], fill["event_id"]],
                symbol="NVDA",
                followed_plan=True,
                changed_by_risk_gate=False,
                changed_by_operator=False,
            )
        )

    assert_valid_agent_tracker_events(events, profile="strict-reporting")
    readiness = assess_reporting_readiness(events)
    assert readiness.strict_reporting_ready is True
    assert readiness.to_dict()["strict_reporting_ready"] is True
    assert readiness.can_compute_closed_trade_stats is True
    assert readiness.can_generate_repair_prompts is True
    tier = assess_tier_readiness(events)
    assert tier.free_ready is False
    assert tier.basic_ready is False
    assert "run_timeline" in tier.free_gaps
    assert "decisions" in tier.basic_gaps


def test_strict_reporting_reports_missing_data() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
        if name != "capital-flow.json"
    ]

    with pytest.raises(AssertionError, match=r"capital\.flow\.recorded"):
        assert_valid_agent_tracker_events(events, profile="strict-reporting")

    readiness = assess_reporting_readiness(events)
    assert "event_type:capital.flow.recorded" in readiness.missing_fields


def test_reporting_fixtures_are_strict_reporting_ready() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
    ]

    assert_valid_agent_tracker_events(events, profile="strict-reporting")
    assert assess_reporting_readiness(events).can_publish_proof is True
    tier = assess_tier_readiness(events)
    assert tier.event_count == len(events)
    assert tier.stat_coverage_score == 88
    assert tier.basic_gaps == ("decisions",)
    assert "run_timeline" in tier.free_gaps


def test_tier_readiness_accepts_complete_run_timeline(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=True)
    )
    events: list[dict] = []
    with client.run(run_type="portfolio_allocation", symbols=["NVDA"]) as run:
        events.append(
            run.decision_proposed(
                decision_kind="target_weight",
                action="hold",
                symbol="NVDA",
            )
        )
        events.append(run.risk_check(approved=True))

    queued = [
        event
        for event in read_jsonl_events_from_queue(tmp_path)
        if event["event_type"] in {"agent.run.started", "agent.run.completed"}
    ]
    tier = assess_tier_readiness([*queued, *events])
    assert tier.free_ready is True
    assert tier.basic_ready is False


def read_jsonl_events_from_queue(path: Path) -> list[dict]:
    return [
        json.loads(item.read_text(encoding="utf-8"))
        for item in sorted((path / "pending").glob("*.jsonl"))
    ]


def test_agentic_security_readiness_detects_gaps_and_evidence(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    event = client.event(
        "tool.call.completed",
        run_id="run_security",
        payload={
            "tool_name": "mcp_fetch",
            "status": "succeeded",
            "tool_policy_id": "policy_1",
            "tool_allowed": True,
            "resource_uri_hash": "sha256:" + "0" * 64,
            "budget_remaining": "10",
            "prompt_injection_tested": True,
        },
    )

    readiness = assess_agentic_security_readiness([event])

    assert readiness.prompt_injection_coverage is True
    assert readiness.tool_policy_coverage is True
    assert readiness.cost_budget_coverage is True
    assert "memory_provenance_coverage" in readiness.gaps


def test_repair_pack_dataset_and_eval_plan_are_deterministic() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "valid", "stale-market-tape.json"),
        read_json_resource("schemas", "fixtures", "reporting", "risk-check.json"),
    ]

    pack = build_repair_pack(events)
    dataset = build_dataset_items(events)
    plan = build_eval_plan(events)

    assert pack["event_count"] == 2
    assert pack["findings"]
    assert "Preserve trading behavior" in pack["prompt"]
    assert dataset
    assert dataset[0]["expected_invariant"]
    assert plan["dataset_item_count"] == len(dataset)
    assert plan["llm_judge_required"] is False


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "order.intent.recorded",
            {
                "order_intent_id": "intent_1",
                "decision_id": "decision_1",
                "symbol": "NVDA",
                "side": "hold",
                "intended_quantity": "1",
            },
        ),
        (
            "capital.flow.recorded",
            {
                "capital_flow_id": "flow_1",
                "flow_kind": "deposit",
                "amount": "100.00",
                "asset": "USD",
                "currency": "USD",
                "session_date": "bad-date",
                "included_in_trading_pnl": False,
            },
        ),
        (
            "capital.flow.recorded",
            {
                "capital_flow_id": "flow_1",
                "flow_kind": "deposit",
                "amount": "Infinity",
                "asset": "USD",
                "currency": "USD",
                "session_date": "2026-06-07",
                "included_in_trading_pnl": False,
            },
        ),
        (
            "order.intent.recorded",
            {
                "order_intent_id": "intent_1",
                "decision_id": "decision_1",
                "symbol": "",
                "side": "buy",
                "intended_quantity": "1",
            },
        ),
        (
            "order.intent.recorded",
            {
                "order_intent_id": "intent_1",
                "decision_id": "decision_1",
                "symbol": "NVDA",
                "side": "buy",
                "intended_quantity": "NaN",
            },
        ),
        (
            "performance.snapshot.recorded",
            {
                "period_kind": "daily",
                "period_start": "20260607",
                "period_end": "2026-06-07",
                "session_date": "2026-06-07",
            },
        ),
        (
            "paper.fill.recorded",
            {
                "symbol": "NVDA",
                "side": "buy",
                "quantity": "not-a-number",
                "price": "101.00",
            },
        ),
    ],
)
def test_reporting_payload_validation_rejects_bad_values(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )

    with pytest.raises(SchemaValidationError):
        client.event(event_type, run_id="run_reporting_bad", payload=payload)


def test_reporting_accepts_legitimate_negative_position_and_return_values(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )

    position = client.event(
        "position.snapshot.recorded",
        run_id="run_reporting_signed",
        payload={
            "portfolio_kind": "paper",
            "position_id": "pos_short",
            "symbol": "NVDA",
            "quantity": "-3",
            "market_value": "-303.00",
            "unrealized_pnl": "-9.25",
        },
    )
    performance = client.event(
        "performance.snapshot.recorded",
        run_id="run_reporting_signed",
        payload={
            "period_kind": "daily",
            "period_start": "2026-06-07",
            "period_end": "2026-06-07",
            "session_date": "2026-06-07",
            "trading_pnl_amount": "-9.25",
            "trading_pnl_pct": "-0.92",
            "compounded_return_pct": "-0.92",
        },
    )

    assert position["payload"]["quantity"] == "-3"
    assert performance["payload"]["trading_pnl_pct"] == "-0.92"


def test_reporting_helpers_redact_sensitive_nested_fields(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )

    with client.run(run_type="portfolio_allocation") as run:
        event = run.position_snapshot(
            portfolio_kind="paper",
            position_id="pos_1",
            symbol="NVDA",
            quantity="1",
            broker_payload={"account_id": "acctABCDEF123456"},
        )

    assert event["payload"]["broker_payload"]["redacted"] is True
    assert event["privacy"]["contains_broker_payload"] is True


def test_reporting_helpers_scrub_local_paths(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )

    with client.run(run_type="portfolio_allocation") as run:
        event = run.position_snapshot(
            portfolio_kind="paper",
            position_id="pos_1",
            symbol="NVDA",
            quantity="1",
            notes="inspect /home/example/private.db",
        )

    assert "/home/example/private.db" not in str(event)
