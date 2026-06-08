from __future__ import annotations

from pathlib import Path

import pytest

from agent_tracker import (
    AgentBuildPayload,
    AgentTracker,
    CapitalFlowPayload,
    Config,
    DecisionOutcomePayload,
    OrderIntentPayload,
    PaperFillPayload,
    PerformanceSnapshotPayload,
    PortfolioSnapshotPayload,
    PositionSnapshotPayload,
    PromptVersionPayload,
    ReplayResultPayload,
    StrategyContextPayload,
)
from agent_tracker.errors import SchemaValidationError
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_typed_payload_builders_emit_valid_events(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    events = [
        client.event(
            "order.intent.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=OrderIntentPayload(
                order_intent_id="intent_1",
                decision_id="decision_1",
                symbol="NVDA",
                side="buy",
                intended_quantity="2",
                intended_price="100.00",
                session_date="2026-06-07",
            ).to_payload(),
        ),
        client.event(
            "decision.outcome.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=DecisionOutcomePayload(
                decision_id="decision_1",
                outcome_kind="filled",
                followed_plan=True,
            ).to_payload(),
        ),
        client.event(
            "paper.fill.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=PaperFillPayload(
                fill_id="fill_1",
                position_id="pos_1",
                symbol="NVDA",
                side="buy",
                open_close_effect="open",
                quantity="2",
                price="100.00",
                fees="0.25",
                fill_source="paper",
                session_date="2026-06-07",
            ).to_payload(),
        ),
        client.event(
            "portfolio.snapshot.recorded",
            run_id="run_builders",
            payload=PortfolioSnapshotPayload(
                portfolio_kind="paper",
                equity="10000.00",
                cash="9800.00",
                buying_power="9800.00",
            ).to_payload(),
        ),
        client.event(
            "position.snapshot.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=PositionSnapshotPayload(
                portfolio_kind="paper",
                position_id="pos_1",
                symbol="NVDA",
                quantity="2",
                average_price="100.00",
            ).to_payload(),
        ),
        client.event(
            "capital.flow.recorded",
            run_id="run_builders",
            payload=CapitalFlowPayload(
                capital_flow_id="flow_1",
                flow_kind="deposit",
                amount="1000.00",
                asset="USD",
                currency="USD",
                session_date="2026-06-07",
            ).to_payload(),
        ),
        client.event(
            "performance.snapshot.recorded",
            run_id="run_builders",
            payload=PerformanceSnapshotPayload(
                period_kind="daily",
                period_start="2026-06-07",
                period_end="2026-06-07",
                session_date="2026-06-07",
                trading_pnl_amount="9.75",
                net_pnl_amount="9.75",
                fees="0.25",
            ).to_payload(),
        ),
        client.event(
            "strategy.context.recorded",
            run_id="run_builders",
            payload=StrategyContextPayload(
                strategy_id="strat_1",
                setup="gap_hold",
                market_regime="risk_on",
            ).to_payload(),
        ),
        client.event(
            "agent.build.recorded",
            run_id="run_builders",
            payload=AgentBuildPayload(
                build_id="build_1",
                config_hash="sha256:config",
                risk_gate_version="risk-1",
            ).to_payload(),
        ),
        client.event(
            "replay.result.recorded",
            run_id="run_builders",
            payload=ReplayResultPayload(
                suite_name="weekend",
                status="succeeded",
                case_count=3,
                prompt_hash="sha256:prompt",
                scenario_tags=["stale_market_data"],
            ).to_payload(),
        ),
        client.event(
            "llm.call.started",
            run_id="run_builders",
            payload=PromptVersionPayload(
                provider="openai",
                model="example",
                prompt_family="allocation",
                prompt_version="2026-06-07",
                prompt_hash="sha256:prompt",
            ).to_payload(),
        ),
    ]

    assert_valid_agent_tracker_events(events)


def test_typed_payload_builders_reject_invalid_reporting_values() -> None:
    with pytest.raises(SchemaValidationError):
        OrderIntentPayload(
            order_intent_id="intent_1",
            decision_id="decision_1",
            symbol="NVDA",
            side="hold",
            intended_quantity="1",
        ).to_payload()

    with pytest.raises(ValueError):
        PromptVersionPayload(
            provider="",
            model="example",
            prompt_family="allocation",
            prompt_version="2026-06-07",
            prompt_hash="sha256:prompt",
        ).to_payload()
