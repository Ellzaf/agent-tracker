from __future__ import annotations

from pathlib import Path

import pytest

from ellzaf_agent import Config, Ellzaf
from ellzaf_agent.errors import SchemaValidationError
from ellzaf_agent.reporting import assess_reporting_readiness
from ellzaf_agent.resources import list_resource_names, read_json_resource
from ellzaf_agent.testing import assert_valid_ellzaf_events


def test_reporting_helpers_emit_strict_ready_events(tmp_path: Path) -> None:
    client = Ellzaf(
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

    assert_valid_ellzaf_events(events, profile="strict-reporting")
    readiness = assess_reporting_readiness(events)
    assert readiness.strict_reporting_ready is True
    assert readiness.can_compute_closed_trade_stats is True
    assert readiness.can_generate_repair_prompts is True


def test_strict_reporting_reports_missing_data() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
        if name != "capital-flow.json"
    ]

    with pytest.raises(AssertionError, match=r"capital\.flow\.recorded"):
        assert_valid_ellzaf_events(events, profile="strict-reporting")

    readiness = assess_reporting_readiness(events)
    assert "event_type:capital.flow.recorded" in readiness.missing_fields


def test_reporting_fixtures_are_strict_reporting_ready() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
    ]

    assert_valid_ellzaf_events(events, profile="strict-reporting")
    assert assess_reporting_readiness(events).can_publish_proof is True


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
    ],
)
def test_reporting_payload_validation_rejects_bad_values(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
) -> None:
    client = Ellzaf(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )

    with pytest.raises(SchemaValidationError):
        client.event(event_type, run_id="run_reporting_bad", payload=payload)


def test_reporting_helpers_redact_sensitive_nested_fields(tmp_path: Path) -> None:
    client = Ellzaf(
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
    client = Ellzaf(
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
