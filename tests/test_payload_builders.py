from __future__ import annotations

from pathlib import Path

import pytest

from agent_tracker import (
    ActionOutcomePayload,
    AgentBuildPayload,
    AgentTracker,
    CandidateReviewPayload,
    CapitalFlowPayload,
    Config,
    DecisionOutcomePayload,
    DiagnosticCheckPayload,
    EvaluationEpochMemberPayload,
    EvaluationEpochPayload,
    OpportunityBoardPayload,
    OrderIntentPayload,
    PaperFillPayload,
    PerformanceSnapshotPayload,
    PortfolioSnapshotPayload,
    PositionSnapshotPayload,
    PromptVersionPayload,
    ReplayResultPayload,
    SetupProfilePayload,
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
            "opportunity.board.recorded",
            run_id="run_builders",
            payload=OpportunityBoardPayload(
                board_id="board_1",
                scope="full_universe",
                candidate_count="48",
                reviewed_count="12",
            ).to_payload(),
        ),
        client.event(
            "opportunity.candidate.reviewed",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=CandidateReviewPayload(
                candidate_id="candidate_1",
                board_id="board_1",
                symbol="NVDA",
                review_status="optimizer_skipped",
                reason_code="turnover_capacity",
            ).to_payload(),
        ),
        client.event(
            "setup.profile.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=SetupProfilePayload(
                setup_profile_id="setup_1",
                symbol="NVDA",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                allowed_entry_modes=["starter"],
                trend_quality_score="81",
            ).to_payload(),
        ),
        client.event(
            "action.outcome.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=ActionOutcomePayload(
                action_id="action_1",
                action_kind="rebalance",
                status="clipped",
                symbol="NVDA",
                requested_notional="1000.00",
                executed_notional="600.00",
                clipped=True,
            ).to_payload(),
        ),
        client.event(
            "evaluation.epoch.started",
            run_id="run_builders",
            payload=EvaluationEpochPayload(
                epoch_id="epoch_1",
                epoch_kind="model_comparison",
                context_hash="sha256:context",
                expected_member_count=2,
            ).to_payload(),
        ),
        client.event(
            "evaluation.epoch.member.completed",
            run_id="run_builders",
            payload=EvaluationEpochMemberPayload(
                epoch_id="epoch_1",
                member_id="shadow_a",
                expected=True,
                state="completed",
                coverage_penalty="0",
                scored=True,
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
        client.event(
            "diagnostic.check.completed",
            run_id="run_builders",
            payload=DiagnosticCheckPayload(
                check_id="decision_flow.numeric_domain",
                check_family="numeric_domain",
                status="passed",
                severity="info",
                component="data_contract",
                observed={"signed_fields_present": True},
                expected={"signed_fields_preserved": True},
                sample_count=10,
                failed_count=0,
                warning_count=0,
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

    with pytest.raises(SchemaValidationError):
        CandidateReviewPayload(
            candidate_id="candidate_1",
            board_id="board_1",
            review_status="made_up",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        SetupProfilePayload(
            setup_profile_id="setup_1",
            primary_regime="trend_continuation",
            entry_permission="eligible_starter",
            trend_quality_score="101",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        EvaluationEpochMemberPayload(
            epoch_id="epoch_1",
            member_id="shadow_a",
            expected=True,
            state="timeout",
            coverage_penalty="-1",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        DiagnosticCheckPayload(
            check_id="decision_flow.numeric_domain",
            check_family="numeric_domain",
            status="unknown",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        DiagnosticCheckPayload(
            check_id="decision_flow.numeric_domain",
            check_family="numeric_domain",
            status="failed",
            failed_count="-1",
        ).to_payload()
