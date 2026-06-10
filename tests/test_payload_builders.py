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
    HoldingExitStatePayload,
    OpportunityBoardPayload,
    OrderIntentPayload,
    PairwiseRotationReviewPayload,
    PaperFillPayload,
    PerformanceSnapshotPayload,
    PortfolioSnapshotPayload,
    PositionSnapshotPayload,
    PromptVersionPayload,
    ReplayResultPayload,
    SetupProfilePayload,
    StrategyContextPayload,
    SymbolBehaviorStatePayload,
    ThresholdReplayPayload,
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
            "strategy.context.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=SymbolBehaviorStatePayload(
                strategy_id="state_NVDA_1",
                symbol="NVDA",
                model_version="behavior-v1",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                data_contract_status="passed",
                data_quality_score="98",
                liquidity_score="91",
                trend_quality_score="84",
                false_breakout_score="12",
                winner_continuation_score="77",
                expected_return_r_session="0.42",
                expected_downside_r_session="-0.18",
                source_refs=["bars:NVDA:2026-06-07T14:30:00Z"],
            ).to_payload(),
        ),
        client.event(
            "strategy.context.recorded",
            run_id="run_builders",
            symbols=["NVDA"],
            payload=HoldingExitStatePayload(
                strategy_id="holding_NVDA_1",
                symbol="NVDA",
                current_weight="0.42",
                unrealized_pnl_r="-0.35",
                support_break_score="81",
                cut_loss_score="84",
                expected_recovery_r="0.12",
                recommended_exit_state="trim_or_exit",
                trim_to_cap_allowed=True,
                winner_add_allowed=False,
                average_down_allowed=False,
                source_refs=["bars:NVDA:2026-06-07T14:30:00Z"],
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
            "opportunity.candidate.reviewed",
            run_id="run_builders",
            symbols=["NVDA", "MSFT"],
            payload=PairwiseRotationReviewPayload(
                candidate_id="rotation_NVDA_MSFT_1",
                board_id="board_1",
                review_status="included_review_only",
                holding_symbol="NVDA",
                candidate_symbol="MSFT",
                rank="1",
                passes_threshold=True,
                u_hold_r="0.15",
                u_enter_r="0.72",
                rotation_cost_r="0.08",
                delta_u_r="0.49",
                theta_rotation_r="0.30",
                holding_exit_state="weakening",
                candidate_entry_state="trend_continuation",
                primary_reasons=["candidate edge clears switch threshold"],
                data_contract_status="passed",
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
            "replay.result.recorded",
            run_id="run_builders",
            payload=ThresholdReplayPayload(
                suite_name="rotation-threshold-replay",
                status="succeeded",
                case_count=320,
                threshold_policy_version="switch-threshold-v1",
                replay_suite_version="behavior-replay-v1",
                scenario_tags=["rotation", "cut_loss"],
                threshold_pass_count="82",
                selected_review_count="45",
                selected_bad_count="8",
                selected_bad_rate="0.1777",
                bad_rate="0.34",
                activation_allowed=True,
                activation_scope="observe_only",
                leakage_guard_passed=True,
                lookahead_guard_passed=True,
                outcome_window_closed=True,
                average_selected_forward_return_pct="1.8",
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

    with pytest.raises(SchemaValidationError):
        SymbolBehaviorStatePayload(
            strategy_id="state_1",
            symbol="NVDA",
            trend_quality_score="101",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        PairwiseRotationReviewPayload(
            candidate_id="rotation_1",
            board_id="board_1",
            review_status="included_review_only",
            holding_symbol="NVDA",
            candidate_symbol="MSFT",
            passes_threshold="yes",
        ).to_payload()

    with pytest.raises(SchemaValidationError):
        ThresholdReplayPayload(
            suite_name="rotation-threshold-replay",
            status="succeeded",
            case_count=10,
            threshold_policy_version="switch-v1",
            selected_bad_rate="-0.1",
        ).to_payload()
