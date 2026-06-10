from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tracker import AgentTracker, Config
from agent_tracker.errors import SchemaValidationError
from agent_tracker.reporting import (
    assess_agentic_security_readiness,
    assess_arena_readiness,
    assess_behavior_intelligence_readiness,
    assess_decision_flow_readiness,
    assess_proof_readiness,
    assess_reporting_readiness,
    assess_tier_readiness,
    build_dataset_items,
    build_decision_flow_diagnostic_events,
    build_eval_plan,
    build_experiment_manifest,
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
            run.opportunity_board(
                board_id="board_1",
                scope="full_universe",
                source="stored_bars",
                candidate_count="48",
                reviewed_count="12",
                excluded_count="3",
            )
        )
        events.append(
            run.candidate_review(
                candidate_id="candidate_1",
                board_id="board_1",
                review_status="optimizer_skipped",
                symbol="NVDA",
                reason_code="turnover_capacity",
            )
        )
        events.append(
            run.setup_profile(
                setup_profile_id="setup_1",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                symbol="NVDA",
                trend_quality_score="80",
            )
        )
        events.append(
            run.action_outcome(
                action_id="action_1",
                action_kind="rebalance",
                status="clipped",
                symbol="NVDA",
                requested_notional="1000.00",
                executed_notional="600.00",
                clipped=True,
            )
        )
        events.append(
            run.evaluation_epoch(
                epoch_id="epoch_1",
                epoch_kind="shadow_comparison",
                context_hash="sha256:context",
                expected_member_count=2,
                candidate_count=48,
            )
        )
        events.append(
            run.evaluation_epoch_member(
                epoch_id="epoch_1",
                member_id="shadow_a",
                expected=True,
                state="completed",
                coverage_penalty="0",
                scored=True,
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
    assert readiness.can_diagnose_opportunity_coverage is True
    assert readiness.can_diagnose_setup_regimes is True
    assert readiness.can_diagnose_action_outcomes is True
    assert readiness.can_compare_evaluation_epochs is True
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


def test_decision_flow_readiness_and_diagnostic_generation_are_generic(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    events: list[dict] = []
    with client.run(run_type="portfolio_allocation", symbols=["NVDA", "MSFT"]) as run:
        events.append(
            run.market_snapshot(
                source="stored_5m_bars",
                source_bar_count=240,
                usable_bar_count=240,
                invalid_bar_count=0,
                invalid_ohlc_relation_count=0,
                non_finite_count=0,
                nan_count=0,
                positive_infinity_count=0,
                negative_infinity_count=0,
                zero_price_count=0,
                zero_volume_count=0,
                signed_fields_present=True,
                signed_return_min="-4.2",
                signed_return_max="7.1",
                data_contract_status="passed",
            )
        )
        events.append(
            run.setup_profile(
                setup_profile_id="setup_1",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                symbol="NVDA",
                profile_shape_status="canonical",
                loaded_after_restart=True,
                restart_safe=True,
                backfill_status="completed",
            )
        )
        events.append(
            run.opportunity_board(
                board_id="board_1",
                scope="full_universe",
                candidate_count=20,
                reviewed_count=20,
                full_universe_count=100,
                review_universe_count=100,
                leader_review_coverage_pct="100",
                selection_summary_present=True,
                leader_accountability_present=True,
            )
        )
        events.append(
            run.candidate_review(
                candidate_id="candidate_1",
                board_id="board_1",
                review_status="included_candidate",
                symbol="NVDA",
                reviewed_by_model=True,
                reviewed_by_optimizer=True,
            )
        )
        events.append(
            run.agent_build(
                build_id="build_1",
                config_hash="sha256:config",
                risk_gate_version="risk-1",
                sdk_contract_version="0.4.0",
                changed_since_last_replay=False,
                post_change_verification_required=True,
            )
        )
        events.append(
            run.replay_result(
                suite_name="decision-flow",
                status="succeeded",
                case_count=10,
                replay_suite_version="decision-flow-1",
                build_id="build_1",
                config_hash="sha256:config",
            )
        )

    readiness = assess_decision_flow_readiness(events)
    diagnostics = build_decision_flow_diagnostic_events(events)

    assert readiness.ready is True
    assert readiness.gaps == ()
    assert readiness.failed_checks == ()
    assert len(diagnostics) == 5
    assert_valid_agent_tracker_events(diagnostics, profile="strict-diagnostics")
    combined = assess_decision_flow_readiness([*events, *diagnostics])
    assert combined.diagnostic_event_count == 5
    assert combined.decision_flow_readiness_score == 100


def test_decision_flow_readiness_detects_failing_contracts(tmp_path: Path) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    events: list[dict] = []
    with client.run(run_type="portfolio_allocation", symbols=["NVDA"]) as run:
        events.append(
            run.market_snapshot(
                source="stored_5m_bars",
                source_bar_count=100,
                usable_bar_count=97,
                non_finite_count=1,
                invalid_ohlc_relation_count=2,
                signed_fields_present=False,
                data_contract_status="failed",
            )
        )
        events.append(
            run.setup_profile(
                setup_profile_id="setup_1",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                symbol="NVDA",
                profile_shape_status="missing",
                backfill_status="failed",
                entry_regime_present=False,
            )
        )

    readiness = assess_decision_flow_readiness(events)
    diagnostics = build_decision_flow_diagnostic_events(events)

    assert readiness.ready is False
    assert "decision_flow.numeric_domain" in readiness.failed_checks
    assert "decision_flow.market_data_quality" in readiness.failed_checks
    assert "decision_flow.setup_profile_persistence" in readiness.failed_checks
    assert "opportunity_coverage_evidence" in readiness.gaps
    assert "fresh_run_proof_evidence" in readiness.gaps
    assert any(
        event["payload"]["status"] == "failed"
        and event["payload"]["check_family"] == "market_data"
        for event in diagnostics
    )
    assert_valid_agent_tracker_events(diagnostics)


def test_behavior_intelligence_readiness_supports_rotation_and_cut_loss(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    with client.run(run_type="allocation_observe", symbols=["NVDA", "MSFT"]) as run:
        events = [
            run.symbol_behavior_state(
                state_id="state_NVDA",
                symbol="NVDA",
                model_version="behavior-v1",
                primary_regime="trend_continuation",
                entry_permission="eligible_starter",
                trend_quality_score="88",
                range_quality_score="24",
                false_breakout_score="9",
                winner_continuation_score="82",
                average_down_legitimacy_score="7",
                expected_return_r_session="0.52",
                expected_downside_r_session="-0.18",
                source_refs=["bars:NVDA:2026-06-07T14:30:00Z"],
            ),
            run.holding_exit_state(
                state_id="holding_NVDA",
                symbol="NVDA",
                current_weight="-0.12",
                support_break_score="12",
                trend_break_score="8",
                relative_weakness_score="18",
                cut_loss_score="14",
                expected_recovery_r="0.35",
                recommended_exit_state="hold",
                trim_to_cap_allowed=False,
                winner_add_allowed=True,
                average_down_allowed=False,
            ),
            run.pairwise_rotation_review(
                review_id="rotation_NVDA_MSFT",
                board_id="board_1",
                holding_symbol="NVDA",
                candidate_symbol="MSFT",
                review_status="included_review_only",
                rank="1",
                passes_threshold=True,
                u_hold_r="0.15",
                u_enter_r="0.67",
                rotation_cost_r="0.05",
                delta_u_r="0.47",
                theta_rotation_r="0.30",
                primary_reasons=["candidate utility clears threshold"],
            ),
            run.threshold_replay(
                suite_name="rotation-threshold-replay",
                status="succeeded",
                case_count=300,
                threshold_policy_version="switch-threshold-v1",
                threshold_pass_count="72",
                selected_review_count="40",
                selected_bad_count="7",
                selected_bad_rate="0.175",
                leakage_guard_passed=True,
                lookahead_guard_passed=True,
                outcome_window_closed=True,
            ),
            run.activation_gate(
                check_id="behavior.activation_gate",
                status="passed",
                activation_allowed=True,
                activation_scope="observe_only",
                activation_passed_gates=["threshold_replay", "leakage_guard"],
                leakage_guard_passed=True,
            ),
        ]

    assert_valid_agent_tracker_events(events, profile="strict-behavior")
    readiness = assess_behavior_intelligence_readiness(events)
    assert readiness.ready is True
    assert readiness.can_explain_entry_regimes is True
    assert readiness.can_explain_cut_loss is True
    assert readiness.can_explain_pairwise_rotation is True
    assert readiness.can_calibrate_thresholds is True
    assert readiness.can_check_leakage_guards is True
    assert readiness.symbol_behavior_state_count == 1
    assert readiness.holding_exit_state_count == 1
    assert readiness.pairwise_rotation_review_count == 1
    dataset = build_dataset_items(events)
    invariants = {item["expected_invariant"] for item in dataset}
    assert "symbol_behavior_state_preserved" in invariants
    assert "holding_exit_state_preserved" in invariants
    assert "pairwise_rotation_threshold_preserved" in invariants
    assert "threshold_replay_preserves_leakage_guards" in invariants


def test_behavior_intelligence_readiness_reports_gaps_and_guard_failures(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    events = [
        client.event(
            "replay.result.recorded",
            run_id="run_behavior_gap",
            payload={
                "suite_name": "rotation-threshold-replay",
                "status": "failed",
                "case_count": 12,
                "threshold_policy_version": "switch-threshold-v1",
                "selected_bad_rate": "0.42",
                "threshold_pass_count": "5",
                "leakage_guard_passed": False,
            },
        )
    ]

    readiness = assess_behavior_intelligence_readiness(events)

    assert readiness.ready is False
    assert "symbol_behavior_state" in readiness.gaps
    assert "holding_exit_state" in readiness.gaps
    assert "pairwise_rotation_review" in readiness.gaps
    assert "leakage_guard_failed" in readiness.gaps
    with pytest.raises(AssertionError, match="behavior intelligence"):
        assert_valid_agent_tracker_events(events, profile="strict-behavior")


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


def test_repair_pack_uses_opportunity_action_and_epoch_evidence(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(project="paper-agent", queue_dir=tmp_path, telemetry_enabled=False)
    )
    events = []
    with client.run(run_type="diagnostic", symbols=["NVDA"]) as run:
        events.append(
            run.candidate_review(
                candidate_id="candidate_1",
                board_id="board_1",
                review_status="model_omitted",
                symbol="NVDA",
            )
        )
        events.append(
            run.action_outcome(
                action_id="action_1",
                action_kind="rebalance",
                status="skipped",
                symbol="NVDA",
                reason_code="candidate_missing",
            )
        )
        events.append(
            run.evaluation_epoch_member(
                epoch_id="epoch_1",
                member_id="shadow_a",
                expected=True,
                state="timeout",
            )
        )

    pack = build_repair_pack(events, max_findings=10)
    dataset = build_dataset_items(events)

    finding_ids = {finding["finding_id"] for finding in pack["findings"]}
    invariants = {item["expected_invariant"] for item in dataset}
    assert "opportunity.model_omitted" in finding_ids
    assert "action.skipped" in finding_ids
    assert "evaluation.timeout" in finding_ids
    assert "candidate_review_reason_preserved" in invariants
    assert "skipped_or_clipped_action_reason_preserved" in invariants
    assert "evaluation_epoch_coverage_preserved" in invariants


def test_proof_and_arena_readiness_reports_gaps_and_ready_state() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
    ]

    proof = assess_proof_readiness(events)
    arena = assess_arena_readiness(events)

    assert proof.ready is True
    assert proof.risk_gates_present is True
    assert proof.replay_tests_present is True
    assert proof.guarantee_language_present is False
    assert arena.ready is False
    assert "portfolio_state" in arena.gaps

    unsafe = dict(events[0])
    unsafe["payload"] = {
        "run_type": "proof",
        "note": "guaranteed profit with no risk-free drawdown",
    }
    unsafe["event_type"] = "agent.run.started"
    proof_with_claim = assess_proof_readiness([unsafe])
    assert proof_with_claim.guarantee_language_present is True
    assert "guarantee_language" in proof_with_claim.gaps


def test_experiment_manifest_is_deterministic_and_requires_declared_changes() -> None:
    events = [
        read_json_resource("schemas", "fixtures", "valid", "stale-market-tape.json"),
        read_json_resource("schemas", "fixtures", "reporting", "risk-check.json"),
    ]
    pack = build_repair_pack(events)

    empty_manifest = build_experiment_manifest(pack)
    changed_manifest = build_experiment_manifest(
        pack, changes={"prompt_version": "2026-06-08"}
    )

    assert empty_manifest["warnings"] == ["no_declared_changes"]
    assert "prompt" in changed_manifest["comparison_axes"]
    assert changed_manifest["declared_changes"] == {"prompt_version": "2026-06-08"}
    assert changed_manifest["fixed_context"]["broker_execution"] == "unchanged"


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
        (
            "opportunity.candidate.reviewed",
            {
                "candidate_id": "candidate_1",
                "board_id": "board_1",
                "review_status": "ignored",
            },
        ),
        (
            "setup.profile.recorded",
            {
                "setup_profile_id": "setup_1",
                "primary_regime": "trend_continuation",
                "entry_permission": "buy_now",
            },
        ),
        (
            "action.outcome.recorded",
            {
                "action_id": "action_1",
                "action_kind": "rebalance",
                "status": "clipped",
                "requested_notional": "-1",
            },
        ),
        (
            "evaluation.epoch.member.completed",
            {
                "epoch_id": "epoch_1",
                "member_id": "shadow_a",
                "expected": True,
                "state": "crashed",
            },
        ),
        (
            "diagnostic.check.completed",
            {
                "check_id": "decision_flow.numeric_domain",
                "check_family": "not_real",
                "status": "warning",
                "severity": "warning",
            },
        ),
        (
            "diagnostic.check.completed",
            {
                "check_id": "decision_flow.numeric_domain",
                "check_family": "numeric_domain",
                "status": "warning",
                "severity": "warning",
                "failed_count": "-1",
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
