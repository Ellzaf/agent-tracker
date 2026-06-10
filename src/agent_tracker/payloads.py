"""Typed payload builders for reporting-grade Agent Tracker events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from agent_tracker.reporting import validate_reporting_payload
from agent_tracker.serialization import to_jsonable


@dataclass(frozen=True, slots=True)
class PayloadBuilder:
    """Base class for typed payload builders."""

    event_type: ClassVar[str]

    def to_payload(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _compact(to_jsonable(asdict(self)))
        if extra:
            payload.update(_compact(to_jsonable(dict(extra))))
        validate_reporting_payload(self.event_type, payload)
        return payload


@dataclass(frozen=True, slots=True)
class OrderIntentPayload(PayloadBuilder):
    event_type: ClassVar[str] = "order.intent.recorded"

    order_intent_id: str
    decision_id: str
    symbol: str
    side: str
    intended_quantity: Any
    intended_price: Any | None = None
    currency: str | None = "USD"
    open_close_effect: str | None = "unknown"
    session_date: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionOutcomePayload(PayloadBuilder):
    event_type: ClassVar[str] = "decision.outcome.recorded"

    decision_id: str
    outcome_kind: str
    outcome_reason: str | None = None
    linked_event_ids: list[str] | None = None
    followed_plan: bool | None = None
    changed_by_risk_gate: bool | None = None
    changed_by_operator: bool | None = None


@dataclass(frozen=True, slots=True)
class PaperFillPayload(PayloadBuilder):
    event_type: ClassVar[str] = "paper.fill.recorded"

    symbol: str
    side: str
    fill_id: str | None = None
    position_id: str | None = None
    order_intent_id: str | None = None
    open_close_effect: str | None = None
    quantity: Any | None = None
    price: Any | None = None
    fees: Any | None = None
    currency: str | None = None
    fill_source: str | None = None
    session_date: str | None = None


@dataclass(frozen=True, slots=True)
class PositionSnapshotPayload(PayloadBuilder):
    event_type: ClassVar[str] = "position.snapshot.recorded"

    portfolio_kind: str
    position_id: str
    symbol: str
    quantity: Any
    average_price: Any | None = None
    market_price: Any | None = None
    market_value: Any | None = None
    realized_pnl: Any | None = None
    unrealized_pnl: Any | None = None


@dataclass(frozen=True, slots=True)
class PortfolioSnapshotPayload(PayloadBuilder):
    event_type: ClassVar[str] = "portfolio.snapshot.recorded"

    portfolio_kind: str
    equity: Any | None = None
    cash: Any | None = None
    buying_power: Any | None = None
    currency: str | None = None
    session_date: str | None = None


@dataclass(frozen=True, slots=True)
class CapitalFlowPayload(PayloadBuilder):
    event_type: ClassVar[str] = "capital.flow.recorded"

    capital_flow_id: str
    flow_kind: str
    amount: Any
    asset: str
    currency: str
    session_date: str
    included_in_trading_pnl: bool = False


@dataclass(frozen=True, slots=True)
class PerformanceSnapshotPayload(PayloadBuilder):
    event_type: ClassVar[str] = "performance.snapshot.recorded"

    period_kind: str
    period_start: str
    period_end: str
    session_date: str
    trading_pnl_amount: Any | None = None
    net_pnl_amount: Any | None = None
    fees: Any | None = None
    flow_adjusted_equity_change: Any | None = None
    return_base: Any | None = None
    compounded_return_pct: Any | None = None
    max_drawdown_pct: Any | None = None


@dataclass(frozen=True, slots=True)
class StrategyContextPayload(PayloadBuilder):
    event_type: ClassVar[str] = "strategy.context.recorded"

    strategy_id: str
    strategy_name: str | None = None
    setup: str | None = None
    market_regime: str | None = None
    planned_risk_amount: Any | None = None
    planned_risk_pct: Any | None = None
    planned_reward_risk: Any | None = None


@dataclass(frozen=True, slots=True)
class SymbolBehaviorStatePayload(PayloadBuilder):
    event_type: ClassVar[str] = "strategy.context.recorded"

    strategy_id: str
    symbol: str
    authority: str = "observe_only"
    context_kind: str = "symbol_behavior_state"
    model_version: str | None = None
    primary_regime: str | None = None
    entry_permission: str | None = None
    research_freshness_state: str | None = None
    data_contract_status: str | None = None
    data_quality_score: Any | None = None
    current_price: Any | None = None
    atr_pct: Any | None = None
    liquidity_score: Any | None = None
    spread_cost_estimate_r: Any | None = None
    slippage_cost_estimate_r: Any | None = None
    executable_edge_penalty_r: Any | None = None
    trend_quality_score: Any | None = None
    range_quality_score: Any | None = None
    false_breakout_score: Any | None = None
    falling_knife_score: Any | None = None
    winner_continuation_score: Any | None = None
    average_down_legitimacy_score: Any | None = None
    expected_return_r_session: Any | None = None
    expected_downside_r_session: Any | None = None
    source_refs: list[str] | None = None


@dataclass(frozen=True, slots=True)
class HoldingExitStatePayload(PayloadBuilder):
    event_type: ClassVar[str] = "strategy.context.recorded"

    strategy_id: str
    symbol: str
    authority: str = "observe_only"
    context_kind: str = "holding_exit_state"
    model_version: str | None = None
    current_weight: Any | None = None
    target_cap_weight: Any | None = None
    unrealized_pnl_pct: Any | None = None
    unrealized_pnl_r: Any | None = None
    trend_hold_bonus_r: Any | None = None
    range_bounce_protection_r: Any | None = None
    support_break_score: Any | None = None
    trend_break_score: Any | None = None
    relative_weakness_score: Any | None = None
    failed_reclaim_score: Any | None = None
    cut_loss_score: Any | None = None
    expected_recovery_r: Any | None = None
    recommended_exit_state: str | None = None
    sell_guard_reason: str | None = None
    trim_to_cap_allowed: bool | None = None
    winner_add_allowed: bool | None = None
    average_down_allowed: bool | None = None
    source_refs: list[str] | None = None


@dataclass(frozen=True, slots=True)
class OpportunityBoardPayload(PayloadBuilder):
    event_type: ClassVar[str] = "opportunity.board.recorded"

    board_id: str
    scope: str
    source: str | None = None
    context_hash: str | None = None
    candidate_count: Any | None = None
    reviewed_count: Any | None = None
    excluded_count: Any | None = None
    stale_count: Any | None = None
    urgent_research_count: Any | None = None


@dataclass(frozen=True, slots=True)
class CandidateReviewPayload(PayloadBuilder):
    event_type: ClassVar[str] = "opportunity.candidate.reviewed"

    candidate_id: str
    board_id: str
    review_status: str
    symbol: str | None = None
    candidate_kind: str | None = "symbol"
    source: str | None = None
    lane: str | None = None
    rank: Any | None = None
    reason_code: str | None = None
    data_quality_score: Any | None = None
    context_hash: str | None = None


@dataclass(frozen=True, slots=True)
class PairwiseRotationReviewPayload(PayloadBuilder):
    event_type: ClassVar[str] = "opportunity.candidate.reviewed"

    candidate_id: str
    board_id: str
    review_status: str
    holding_symbol: str
    candidate_symbol: str
    candidate_kind: str = "pairwise_rotation"
    source: str | None = "behavior_intelligence"
    lane: str | None = "rotation_review"
    rank: Any | None = None
    market_regime: str | None = None
    observer_decision: str | None = None
    passes_threshold: bool | None = None
    u_hold_r: Any | None = None
    u_enter_r: Any | None = None
    rotation_cost_r: Any | None = None
    delta_u_r: Any | None = None
    theta_rotation_r: Any | None = None
    holding_exit_state: str | None = None
    candidate_entry_state: str | None = None
    primary_reasons: list[str] | None = None
    blocked_reasons: list[str] | None = None
    reviewed_by_model: bool | None = False
    reviewed_by_optimizer: bool | None = False
    targeted_by_optimizer: bool | None = False
    data_contract_status: str | None = None
    context_hash: str | None = None

    def to_payload(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _compact(to_jsonable(asdict(self)))
        payload["symbol"] = payload.get("candidate_symbol")
        if extra:
            payload.update(_compact(to_jsonable(dict(extra))))
        validate_reporting_payload(self.event_type, payload)
        return payload


@dataclass(frozen=True, slots=True)
class SetupProfilePayload(PayloadBuilder):
    event_type: ClassVar[str] = "setup.profile.recorded"

    setup_profile_id: str
    primary_regime: str
    entry_permission: str
    symbol: str | None = None
    setup: str | None = None
    allowed_entry_modes: list[str] | None = None
    blocked_entry_modes: list[str] | None = None
    trend_quality_score: Any | None = None
    range_quality_score: Any | None = None
    false_breakout_score: Any | None = None
    falling_knife_score: Any | None = None
    recommended_starter_cap_pct: Any | None = None
    invalidation_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ActionOutcomePayload(PayloadBuilder):
    event_type: ClassVar[str] = "action.outcome.recorded"

    action_id: str
    action_kind: str
    status: str
    symbol: str | None = None
    decision_id: str | None = None
    order_intent_id: str | None = None
    reason_code: str | None = None
    requested_notional: Any | None = None
    executed_notional: Any | None = None
    remaining_capacity_before: Any | None = None
    remaining_capacity_after: Any | None = None
    capacity_kind: str | None = None
    clipped: bool | None = None
    risk_reduction: bool | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticCheckPayload(PayloadBuilder):
    event_type: ClassVar[str] = "diagnostic.check.completed"

    check_id: str
    check_family: str
    status: str
    severity: str = "info"
    check_name: str | None = None
    component: str | None = None
    mistake_family: str | None = None
    money_impact: str | None = None
    blocking_status: str | None = None
    resolution_status: str | None = None
    next_safe_action: str | None = None
    observed: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    sample_count: Any | None = None
    failed_count: Any | None = None
    warning_count: Any | None = None
    affected_symbols: list[str] | None = None
    affected_fields: list[str] | None = None
    evidence_event_ids: list[str] | None = None
    evidence_run_ids: list[str] | None = None
    input_snapshot_hash: str | None = None
    context_hash: str | None = None
    build_id: str | None = None
    config_hash: str | None = None
    replay_suite_version: str | None = None
    repair_hint_id: str | None = None
    data_quality_score_delta: Any | None = None


@dataclass(frozen=True, slots=True)
class EvaluationEpochPayload(PayloadBuilder):
    event_type: ClassVar[str] = "evaluation.epoch.started"

    epoch_id: str
    epoch_kind: str
    context_hash: str
    input_snapshot_hash: str | None = None
    candidate_count: Any | None = None
    selected_symbol_count: Any | None = None
    expected_member_count: Any | None = None
    market_phase: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationEpochMemberPayload(PayloadBuilder):
    event_type: ClassVar[str] = "evaluation.epoch.member.completed"

    epoch_id: str
    member_id: str
    expected: bool
    state: str
    context_hash: str | None = None
    input_snapshot_hash: str | None = None
    failure_bucket: str | None = None
    coverage_penalty: Any | None = None
    scored: bool | None = None


@dataclass(frozen=True, slots=True)
class AgentBuildPayload(PayloadBuilder):
    event_type: ClassVar[str] = "agent.build.recorded"

    build_id: str
    config_hash: str
    risk_gate_version: str
    agent_instruction_hash: str | None = None
    skill_manifest_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayResultPayload(PayloadBuilder):
    event_type: ClassVar[str] = "replay.result.recorded"

    suite_name: str
    status: str
    case_count: int
    prompt_hash: str | None = None
    prompt_version: str | None = None
    replay_suite_version: str | None = None
    scenario_tags: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ThresholdReplayPayload(PayloadBuilder):
    event_type: ClassVar[str] = "replay.result.recorded"

    suite_name: str
    status: str
    case_count: int
    threshold_policy_version: str
    replay_suite_version: str | None = None
    scenario_tags: list[str] | None = None
    threshold_pass_count: Any | None = None
    bad_rotation_count: Any | None = None
    selected_review_count: Any | None = None
    selected_bad_count: Any | None = None
    selected_bad_rate: Any | None = None
    bad_rate: Any | None = None
    activation_allowed: bool | None = None
    activation_scope: str | None = None
    leakage_guard_passed: bool | None = None
    lookahead_guard_passed: bool | None = None
    outcome_window_closed: bool | None = None
    average_selected_forward_return_pct: Any | None = None


@dataclass(frozen=True, slots=True)
class PromptVersionPayload(PayloadBuilder):
    event_type: ClassVar[str] = "llm.call.started"

    provider: str
    model: str
    prompt_family: str
    prompt_version: str
    prompt_hash: str

    def to_payload(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _compact(to_jsonable(asdict(self)))
        if extra:
            payload.update(_compact(to_jsonable(dict(extra))))
        _require_nonempty(payload, "provider")
        _require_nonempty(payload, "model")
        _require_nonempty(payload, "prompt_family")
        _require_nonempty(payload, "prompt_version")
        _require_nonempty(payload, "prompt_hash")
        return payload


def _compact(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if value is not None}


def _require_nonempty(payload: Mapping[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), str) or not str(payload[field]).strip():
        raise ValueError(f"{field} must be a non-empty string")
