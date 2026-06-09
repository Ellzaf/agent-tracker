"""Reporting-grade validation and readiness checks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from agent_tracker.constants import SCHEMA_VERSION, SDK_LANGUAGE, SDK_NAME, SDK_VERSION
from agent_tracker.errors import SchemaValidationError
from agent_tracker.ids import new_event_id
from agent_tracker.serialization import hash_text, utc_now_iso

REPORTING_EVENT_TYPES = {
    "agent.build.recorded",
    "order.intent.recorded",
    "decision.outcome.recorded",
    "position.snapshot.recorded",
    "capital.flow.recorded",
    "performance.snapshot.recorded",
    "strategy.context.recorded",
}

DIAGNOSTIC_EVENT_TYPES = {
    "opportunity.board.recorded",
    "opportunity.candidate.reviewed",
    "setup.profile.recorded",
    "action.outcome.recorded",
    "evaluation.epoch.started",
    "evaluation.epoch.member.completed",
    "diagnostic.check.completed",
}

REPORTING_REQUIRED_PAYLOAD_FIELDS: dict[str, set[str]] = {
    "agent.build.recorded": {"build_id", "config_hash", "risk_gate_version"},
    "order.intent.recorded": {
        "order_intent_id",
        "decision_id",
        "symbol",
        "side",
        "intended_quantity",
    },
    "decision.outcome.recorded": {"decision_id", "outcome_kind"},
    "position.snapshot.recorded": {
        "portfolio_kind",
        "position_id",
        "symbol",
        "quantity",
    },
    "capital.flow.recorded": {
        "capital_flow_id",
        "flow_kind",
        "amount",
        "asset",
        "currency",
        "session_date",
        "included_in_trading_pnl",
    },
    "performance.snapshot.recorded": {
        "period_kind",
        "period_start",
        "period_end",
        "session_date",
    },
    "strategy.context.recorded": {"strategy_id"},
}

DIAGNOSTIC_REQUIRED_PAYLOAD_FIELDS: dict[str, set[str]] = {
    "opportunity.board.recorded": {"board_id", "scope"},
    "opportunity.candidate.reviewed": {
        "candidate_id",
        "board_id",
        "review_status",
    },
    "setup.profile.recorded": {
        "setup_profile_id",
        "primary_regime",
        "entry_permission",
    },
    "action.outcome.recorded": {"action_id", "action_kind", "status"},
    "evaluation.epoch.started": {"epoch_id", "epoch_kind", "context_hash"},
    "evaluation.epoch.member.completed": {
        "epoch_id",
        "member_id",
        "expected",
        "state",
    },
    "diagnostic.check.completed": {
        "check_id",
        "check_family",
        "status",
        "severity",
    },
}

ORDER_SIDES = {"buy", "sell", "short", "cover"}
OPEN_CLOSE_EFFECTS = {"open", "increase", "reduce", "close", "unknown"}
FILL_SOURCES = {"paper", "shadow", "replay", "manual_import"}
FLOW_KINDS = {
    "deposit",
    "withdrawal",
    "transfer_in",
    "transfer_out",
    "conversion",
    "fee",
    "dividend",
    "interest",
    "tax",
    "adjustment",
}
OUTCOME_KINDS = {
    "filled",
    "rejected",
    "no_order",
    "deferred",
    "expired",
    "cancelled",
    "manual_override",
    "replayed",
}
SESSION_STATES = {"pre_market", "regular", "after_hours", "closed"}
OPPORTUNITY_REVIEW_STATUSES = {
    "candidate_present",
    "included_candidate",
    "included_review_only",
    "not_in_candidate_set",
    "targeted",
    "model_omitted",
    "optimizer_planned",
    "optimizer_skipped",
    "risk_rejected",
    "excluded_avoid",
    "excluded_unresearched",
    "excluded_stale",
    "excluded_source_quality",
    "excluded_data_quality",
    "excluded_candidate_limit",
    "excluded_cash",
    "excluded_factor_cap",
    "excluded_whole_share",
    "backfill_unknown",
    "other",
}
ENTRY_REGIMES = {
    "trend_continuation",
    "breakout_retest",
    "ma_band_range_trade",
    "false_breakout_prone",
    "falling_knife_reversal_pending",
    "overextended_momentum",
    "stale_or_research_blocked",
    "wait",
    "custom",
}
ENTRY_PERMISSIONS = {
    "eligible_starter",
    "eligible_scale_add",
    "wait_for_retest",
    "range_trade_only",
    "research_first",
    "avoid_new_buy",
    "risk_review_only",
    "custom",
}
ACTION_OUTCOME_STATUSES = {
    "planned",
    "executed",
    "skipped",
    "clipped",
    "rejected",
    "deferred",
}
EVALUATION_MEMBER_STATES = {
    "completed",
    "failed",
    "skipped",
    "timeout",
    "schema_failed",
    "not_runnable",
}
DIAGNOSTIC_CHECK_STATUSES = {
    "passed",
    "warning",
    "failed",
    "not_applicable",
}
DIAGNOSTIC_CHECK_FAMILIES = {
    "market_data",
    "numeric_domain",
    "setup_profile",
    "opportunity_coverage",
    "risk_gate",
    "decision_lifecycle",
    "replay",
    "build_release",
    "privacy",
    "source_quality",
    "memory",
    "cost",
    "data_contract",
    "custom",
}
PROFILE_SHAPE_STATUSES = {
    "canonical",
    "normalized_from_top_level",
    "normalized_from_nested",
    "defensive_default",
    "missing",
    "custom",
}
BACKFILL_STATUSES = {
    "not_needed",
    "completed",
    "partial",
    "failed",
    "not_started",
    "not_applicable",
}
DATA_CONTRACT_STATUSES = {
    "passed",
    "warning",
    "failed",
    "not_applicable",
}
_STRICT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPORTING_IDENTITY_FIELDS = {
    "action_id",
    "asset",
    "build_id",
    "capital_flow_id",
    "candidate_id",
    "board_id",
    "capacity_kind",
    "check_family",
    "check_id",
    "config_hash",
    "currency",
    "decision_id",
    "epoch_id",
    "fill_id",
    "member_id",
    "order_intent_id",
    "period_kind",
    "portfolio_kind",
    "position_id",
    "risk_gate_version",
    "sdk_contract_version",
    "strategy_id",
    "symbol",
}
_NON_NEGATIVE_EXTENSION_NUMBER_FIELDS = {
    "average_price",
    "case_count",
    "candidate_count",
    "candidate_limit_count",
    "coverage_penalty",
    "data_quality_score",
    "drawdown_pct",
    "excluded_count",
    "failed_count",
    "fail_count",
    "executed_notional",
    "executed_quantity",
    "expected_member_count",
    "fees",
    "freshness_seconds",
    "full_universe_count",
    "falling_knife_score",
    "false_breakout_score",
    "holding_period_seconds",
    "intended_price",
    "intended_quantity",
    "member_count",
    "nan_count",
    "negative_infinity_count",
    "negative_volume_count",
    "non_finite_count",
    "non_positive_price_count",
    "invalid_bar_count",
    "invalid_ohlc_relation_count",
    "leader_review_coverage_pct",
    "leader_review_expected_count",
    "leader_review_recorded_count",
    "market_price",
    "max_drawdown_pct",
    "planned_reward_risk",
    "positive_infinity_count",
    "pass_count",
    "passed_count",
    "price",
    "projected_post_action_weight",
    "range_quality_score",
    "rank",
    "recommended_starter_cap_pct",
    "remaining_capacity_after",
    "remaining_capacity_before",
    "requested_notional",
    "requested_quantity",
    "requested_weight",
    "return_base",
    "reviewed_count",
    "review_universe_count",
    "sample_count",
    "selected_symbol_count",
    "source_bar_count",
    "source_confidence",
    "stale_count",
    "symbol_count",
    "tape_attention_count",
    "tape_attention_excluded_count",
    "tape_attention_included_count",
    "target_weight",
    "trend_quality_score",
    "urgent_research_count",
    "usable_bar_count",
    "warning_count",
    "zero_price_count",
    "zero_volume_count",
}
_SIGNED_EXTENSION_NUMBER_FIELDS = {
    "compounded_return_pct",
    "cost_basis",
    "flow_adjusted_equity_change",
    "market_value",
    "net_pnl_amount",
    "planned_risk_amount",
    "planned_risk_pct",
    "raw_equity_change",
    "realized_pnl",
    "session_return_pct",
    "one_day_return_pct",
    "one_hour_return_pct",
    "benchmark_return_pct",
    "data_quality_score_delta",
    "signed_distance_max",
    "signed_distance_min",
    "signed_return_max",
    "signed_return_min",
    "trading_pnl_amount",
    "trading_pnl_pct",
    "unrealized_pnl",
}
_BOOL_EXTENSION_FIELDS = {
    "clipped",
    "blocks_release",
    "changed_since_last_replay",
    "entry_permission_present",
    "entry_regime_present",
    "excluded_by_candidate_limit",
    "leader_accountability_present",
    "loaded_after_restart",
    "post_change_verification_required",
    "restart_safe",
    "risk_reduction",
    "scored",
    "reviewed_by_model",
    "reviewed_by_optimizer",
    "selection_summary_present",
    "signed_fields_present",
    "tape_attention",
    "targeted_by_optimizer",
}
_STRING_LIST_FIELDS = {
    "allowed_entry_modes",
    "affected_fields",
    "affected_symbols",
    "blocked_entry_modes",
    "evidence_event_ids",
    "evidence_run_ids",
    "migration_ids",
    "reason_codes",
    "scenario_tags",
    "symbols_missing",
}
_SCORE_FIELDS = {
    "data_quality_score",
    "falling_knife_score",
    "false_breakout_score",
    "range_quality_score",
    "trend_quality_score",
}


@dataclass(frozen=True, slots=True)
class ReportingReadiness:
    """Result of checking whether event data can support hosted statistics."""

    event_count: int
    can_compute_closed_trade_stats: bool
    can_compute_net_pnl: bool
    can_compute_flow_adjusted_pnl: bool
    can_compute_strategy_stats: bool
    can_compute_prompt_drift_stats: bool
    can_compare_shadow_agents: bool
    can_diagnose_opportunity_coverage: bool
    can_diagnose_setup_regimes: bool
    can_diagnose_action_outcomes: bool
    can_compare_evaluation_epochs: bool
    can_generate_repair_prompts: bool
    can_score_arena: bool
    can_publish_proof: bool
    missing_fields: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def strict_reporting_ready(self) -> bool:
        return not self.missing_fields

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "can_compute_closed_trade_stats": self.can_compute_closed_trade_stats,
            "can_compute_net_pnl": self.can_compute_net_pnl,
            "can_compute_flow_adjusted_pnl": self.can_compute_flow_adjusted_pnl,
            "can_compute_strategy_stats": self.can_compute_strategy_stats,
            "can_compute_prompt_drift_stats": self.can_compute_prompt_drift_stats,
            "can_compare_shadow_agents": self.can_compare_shadow_agents,
            "can_diagnose_opportunity_coverage": (
                self.can_diagnose_opportunity_coverage
            ),
            "can_diagnose_setup_regimes": self.can_diagnose_setup_regimes,
            "can_diagnose_action_outcomes": self.can_diagnose_action_outcomes,
            "can_compare_evaluation_epochs": self.can_compare_evaluation_epochs,
            "can_generate_repair_prompts": self.can_generate_repair_prompts,
            "can_score_arena": self.can_score_arena,
            "can_publish_proof": self.can_publish_proof,
            "missing_fields": list(self.missing_fields),
            "warnings": list(self.warnings),
            "strict_reporting_ready": self.strict_reporting_ready,
        }


@dataclass(frozen=True, slots=True)
class TierReadiness:
    """Feature readiness for Ellzaf Agent Free, Basic, and Pro."""

    event_count: int
    free_ready: bool
    basic_ready: bool
    pro_ready: bool
    free_gaps: tuple[str, ...]
    basic_gaps: tuple[str, ...]
    pro_gaps: tuple[str, ...]
    data_quality_score: int
    privacy_score: int
    stat_coverage_score: int
    repair_prompt_score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "free_ready": self.free_ready,
            "basic_ready": self.basic_ready,
            "pro_ready": self.pro_ready,
            "free_gaps": list(self.free_gaps),
            "basic_gaps": list(self.basic_gaps),
            "pro_gaps": list(self.pro_gaps),
            "data_quality_score": self.data_quality_score,
            "privacy_score": self.privacy_score,
            "stat_coverage_score": self.stat_coverage_score,
            "repair_prompt_score": self.repair_prompt_score,
        }


@dataclass(frozen=True, slots=True)
class DecisionFlowReadiness:
    """Readiness for decision-flow diagnostics and website explanations."""

    event_count: int
    ready: bool
    diagnostic_event_count: int
    failed_diagnostic_count: int
    warning_diagnostic_count: int
    can_check_numeric_domains: bool
    can_check_market_data_quality: bool
    can_check_setup_profile_persistence: bool
    can_check_opportunity_coverage: bool
    can_check_fresh_run_proof: bool
    numeric_domain_score: int
    market_data_contract_score: int
    setup_profile_persistence_score: int
    opportunity_coverage_score: int
    fresh_run_proof_score: int
    decision_flow_readiness_score: int
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]
    failed_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "ready": self.ready,
            "diagnostic_event_count": self.diagnostic_event_count,
            "failed_diagnostic_count": self.failed_diagnostic_count,
            "warning_diagnostic_count": self.warning_diagnostic_count,
            "can_check_numeric_domains": self.can_check_numeric_domains,
            "can_check_market_data_quality": self.can_check_market_data_quality,
            "can_check_setup_profile_persistence": (
                self.can_check_setup_profile_persistence
            ),
            "can_check_opportunity_coverage": self.can_check_opportunity_coverage,
            "can_check_fresh_run_proof": self.can_check_fresh_run_proof,
            "numeric_domain_score": self.numeric_domain_score,
            "market_data_contract_score": self.market_data_contract_score,
            "setup_profile_persistence_score": self.setup_profile_persistence_score,
            "opportunity_coverage_score": self.opportunity_coverage_score,
            "fresh_run_proof_score": self.fresh_run_proof_score,
            "decision_flow_readiness_score": self.decision_flow_readiness_score,
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "failed_checks": list(self.failed_checks),
        }


@dataclass(frozen=True, slots=True)
class AgenticSecurityReadiness:
    """Readiness for agentic safety and security telemetry."""

    event_count: int
    ready: bool
    prompt_injection_coverage: bool
    sensitive_information_coverage: bool
    tool_policy_coverage: bool
    memory_provenance_coverage: bool
    cost_budget_coverage: bool
    excessive_agency_warnings: tuple[str, ...]
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "ready": self.ready,
            "prompt_injection_coverage": self.prompt_injection_coverage,
            "sensitive_information_coverage": self.sensitive_information_coverage,
            "tool_policy_coverage": self.tool_policy_coverage,
            "memory_provenance_coverage": self.memory_provenance_coverage,
            "cost_budget_coverage": self.cost_budget_coverage,
            "excessive_agency_warnings": list(self.excessive_agency_warnings),
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True, slots=True)
class ProofReadiness:
    """Readiness for a public proof page or trust badge."""

    event_count: int
    ready: bool
    environment_modes: tuple[str, ...]
    risk_gates_present: bool
    replay_tests_present: bool
    source_quality_present: bool
    prompt_or_build_versions_present: bool
    privacy_safe: bool
    guarantee_language_present: bool
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "ready": self.ready,
            "environment_modes": list(self.environment_modes),
            "risk_gates_present": self.risk_gates_present,
            "replay_tests_present": self.replay_tests_present,
            "source_quality_present": self.source_quality_present,
            "prompt_or_build_versions_present": self.prompt_or_build_versions_present,
            "privacy_safe": self.privacy_safe,
            "guarantee_language_present": self.guarantee_language_present,
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ArenaReadiness:
    """Readiness for benchmark or market-regime challenge scoring."""

    event_count: int
    ready: bool
    scenario_tags_present: bool
    market_regime_present: bool
    session_dates_present: bool
    drawdown_metrics_present: bool
    survival_metrics_present: bool
    replay_results_present: bool
    evaluation_epochs_present: bool
    evaluation_member_coverage_present: bool
    strategy_tags_present: bool
    position_state_present: bool
    portfolio_state_present: bool
    gaps: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "ready": self.ready,
            "scenario_tags_present": self.scenario_tags_present,
            "market_regime_present": self.market_regime_present,
            "session_dates_present": self.session_dates_present,
            "drawdown_metrics_present": self.drawdown_metrics_present,
            "survival_metrics_present": self.survival_metrics_present,
            "replay_results_present": self.replay_results_present,
            "evaluation_epochs_present": self.evaluation_epochs_present,
            "evaluation_member_coverage_present": (
                self.evaluation_member_coverage_present
            ),
            "strategy_tags_present": self.strategy_tags_present,
            "position_state_present": self.position_state_present,
            "portfolio_state_present": self.portfolio_state_present,
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
        }


def validate_reporting_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    """Validate event-specific reporting fields when a reporting event is used."""

    required_fields = REPORTING_REQUIRED_PAYLOAD_FIELDS.get(
        event_type
    ) or DIAGNOSTIC_REQUIRED_PAYLOAD_FIELDS.get(event_type)
    if required_fields is None:
        _validate_reporting_extensions(event_type, payload)
        return

    missing = sorted(required_fields - set(payload))
    if missing:
        raise SchemaValidationError(
            f"payload missing reporting fields for {event_type}: {', '.join(missing)}"
        )

    if event_type == "order.intent.recorded":
        _require_nonempty(payload, "order_intent_id")
        _require_nonempty(payload, "decision_id")
        _require_nonempty(payload, "symbol")
        _require_enum(payload, "side", ORDER_SIDES)
        _require_number(payload, "intended_quantity", minimum=Decimal("0"))
        _require_optional_number(payload, "intended_price", minimum=Decimal("0"))
    elif event_type == "decision.outcome.recorded":
        _require_nonempty(payload, "decision_id")
        _require_enum(payload, "outcome_kind", OUTCOME_KINDS)
        _require_optional_bool(payload, "followed_plan")
        _require_optional_bool(payload, "changed_by_risk_gate")
        _require_optional_bool(payload, "changed_by_operator")
    elif event_type == "position.snapshot.recorded":
        _require_nonempty(payload, "portfolio_kind")
        _require_nonempty(payload, "position_id")
        _require_nonempty(payload, "symbol")
        _require_number(payload, "quantity")
        _require_optional_number(payload, "average_price", minimum=Decimal("0"))
        _require_optional_number(payload, "market_price", minimum=Decimal("0"))
        _require_optional_number(payload, "market_value")
    elif event_type == "capital.flow.recorded":
        _require_nonempty(payload, "capital_flow_id")
        _require_nonempty(payload, "asset")
        _require_nonempty(payload, "currency")
        _require_enum(payload, "flow_kind", FLOW_KINDS)
        _require_number(payload, "amount")
        _require_bool(payload, "included_in_trading_pnl")
        _require_date(payload, "session_date")
    elif event_type == "performance.snapshot.recorded":
        _require_nonempty(payload, "period_kind")
        _require_date(payload, "session_date")
        _require_date(payload, "period_start")
        _require_date(payload, "period_end")
        _require_optional_number(payload, "trading_pnl_amount")
        _require_optional_number(payload, "flow_adjusted_equity_change")
        _require_optional_number(payload, "return_base")
        _require_optional_number(payload, "compounded_return_pct")
    elif event_type == "strategy.context.recorded":
        _require_nonempty(payload, "strategy_id")
        _require_optional_number(payload, "planned_risk_amount")
        _require_optional_number(payload, "planned_risk_pct")
        _require_optional_number(payload, "planned_reward_risk")
    elif event_type == "agent.build.recorded":
        _require_nonempty(payload, "build_id")
        _require_nonempty(payload, "config_hash")
        _require_nonempty(payload, "risk_gate_version")
    elif event_type == "opportunity.board.recorded":
        _require_nonempty(payload, "board_id")
        _require_nonempty(payload, "scope")
        _require_optional_number(payload, "candidate_count", minimum=Decimal("0"))
        _require_optional_number(payload, "reviewed_count", minimum=Decimal("0"))
        _require_optional_number(payload, "excluded_count", minimum=Decimal("0"))
    elif event_type == "opportunity.candidate.reviewed":
        _require_nonempty(payload, "candidate_id")
        _require_nonempty(payload, "board_id")
        _require_enum(payload, "review_status", OPPORTUNITY_REVIEW_STATUSES)
        _require_optional_number(payload, "rank", minimum=Decimal("0"))
    elif event_type == "setup.profile.recorded":
        _require_nonempty(payload, "setup_profile_id")
        _require_enum(payload, "primary_regime", ENTRY_REGIMES)
        _require_enum(payload, "entry_permission", ENTRY_PERMISSIONS)
    elif event_type == "action.outcome.recorded":
        _require_nonempty(payload, "action_id")
        _require_nonempty(payload, "action_kind")
        _require_enum(payload, "status", ACTION_OUTCOME_STATUSES)
        _require_optional_number(payload, "requested_notional", minimum=Decimal("0"))
        _require_optional_number(payload, "executed_notional", minimum=Decimal("0"))
        _require_optional_bool(payload, "clipped")
        _require_optional_bool(payload, "risk_reduction")
    elif event_type == "evaluation.epoch.started":
        _require_nonempty(payload, "epoch_id")
        _require_nonempty(payload, "epoch_kind")
        _require_nonempty(payload, "context_hash")
        _require_optional_number(payload, "expected_member_count", minimum=Decimal("0"))
        _require_optional_number(payload, "candidate_count", minimum=Decimal("0"))
    elif event_type == "evaluation.epoch.member.completed":
        _require_nonempty(payload, "epoch_id")
        _require_nonempty(payload, "member_id")
        _require_bool(payload, "expected")
        _require_enum(payload, "state", EVALUATION_MEMBER_STATES)
        _require_optional_bool(payload, "scored")
        _require_optional_number(payload, "coverage_penalty", minimum=Decimal("0"))
    elif event_type == "diagnostic.check.completed":
        _require_nonempty(payload, "check_id")
        _require_enum(payload, "check_family", DIAGNOSTIC_CHECK_FAMILIES)
        _require_enum(payload, "status", DIAGNOSTIC_CHECK_STATUSES)
        _require_nonempty(payload, "severity")
        _require_optional_number(payload, "sample_count", minimum=Decimal("0"))
        _require_optional_number(payload, "failed_count", minimum=Decimal("0"))
        _require_optional_number(payload, "warning_count", minimum=Decimal("0"))
        _require_optional_number(payload, "data_quality_score_delta")

    _validate_reporting_extensions(event_type, payload)


def assess_reporting_readiness(
    events: Iterable[Mapping[str, Any]],
) -> ReportingReadiness:
    """Assess which hosted statistics can be computed from emitted events."""

    event_list = list(events)
    missing: set[str] = set()
    warnings: set[str] = set()

    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for event in event_list:
        by_type.setdefault(str(event.get("event_type", "")), []).append(event)

    payloads = [
        payload
        for event in event_list
        if isinstance(payload := event.get("payload"), Mapping)
    ]
    fill_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "paper.fill.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    performance_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "performance.snapshot.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    replay_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "replay.result.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    llm_started_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "llm.call.started"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    opportunity_board_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "opportunity.board.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    candidate_review_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "opportunity.candidate.reviewed"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    setup_profile_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "setup.profile.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    action_outcome_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "action.outcome.recorded"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    evaluation_epoch_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "evaluation.epoch.started"
        and isinstance(payload := event.get("payload"), Mapping)
    ]
    evaluation_member_payloads = [
        payload
        for event in event_list
        if event.get("event_type") == "evaluation.epoch.member.completed"
        and isinstance(payload := event.get("payload"), Mapping)
    ]

    has_reporting_event_types = {
        event_type
        for event_type in REPORTING_REQUIRED_PAYLOAD_FIELDS
        if by_type.get(event_type)
    }
    missing_reporting_types = (
        REPORTING_REQUIRED_PAYLOAD_FIELDS.keys() - has_reporting_event_types
    )
    for event_type in sorted(missing_reporting_types):
        missing.add(f"event_type:{event_type}")

    can_compute_closed_trade_stats = any(
        _has_fields(
            payload,
            {
                "position_id",
                "open_close_effect",
                "quantity",
                "price",
                "session_date",
            },
        )
        and payload.get("open_close_effect") in {"reduce", "close"}
        for payload in fill_payloads
    )
    if not can_compute_closed_trade_stats:
        missing.add("closed_trade_lifecycle")

    can_compute_net_pnl = any(
        _has_any(payload, {"net_pnl_amount", "trading_pnl_amount"})
        and _has_any(payload, {"fees", "fees_included"})
        for payload in performance_payloads + fill_payloads
    )
    if not can_compute_net_pnl:
        missing.add("net_pnl_with_fees")

    can_compute_flow_adjusted_pnl = bool(by_type.get("capital.flow.recorded")) and any(
        _has_fields(
            payload,
            {
                "flow_adjusted_equity_change",
                "return_base",
                "compounded_return_pct",
                "session_date",
            },
        )
        for payload in performance_payloads
    )
    if not can_compute_flow_adjusted_pnl:
        missing.add("flow_adjusted_pnl")

    can_compute_strategy_stats = any(
        _has_any(payload, {"strategy_id", "strategy_name", "setup"})
        for payload in payloads
    )
    if not can_compute_strategy_stats:
        missing.add("strategy_or_setup")

    can_compute_prompt_drift_stats = any(
        _has_fields(payload, {"prompt_family", "prompt_version", "prompt_hash"})
        for payload in llm_started_payloads
    ) and any(
        _has_any(payload, {"prompt_hash", "prompt_version", "replay_suite_version"})
        for payload in replay_payloads
    )
    if not can_compute_prompt_drift_stats:
        missing.add("prompt_hash_and_replay")

    can_compare_shadow_agents = any(
        event.get("environment") == "shadow"
        or (
            isinstance(event.get("payload"), Mapping)
            and event["payload"].get("portfolio_kind") == "shadow"
        )
        for event in event_list
    ) and any(
        _has_any(payload, {"max_drawdown_pct", "drawdown_pct", "failed_run_count"})
        for payload in payloads
    )
    if not can_compare_shadow_agents:
        warnings.add("shadow_comparison_metadata")

    can_diagnose_opportunity_coverage = bool(opportunity_board_payloads) and any(
        _has_fields(payload, {"candidate_id", "board_id", "review_status"})
        for payload in candidate_review_payloads
    )
    if not can_diagnose_opportunity_coverage:
        warnings.add("opportunity_review_metadata")

    can_diagnose_setup_regimes = any(
        _has_fields(payload, {"setup_profile_id", "primary_regime", "entry_permission"})
        for payload in setup_profile_payloads
    ) or any(
        _has_fields(payload, {"primary_regime", "entry_permission"})
        for payload in payloads
    )
    if not can_diagnose_setup_regimes:
        warnings.add("setup_regime_metadata")

    can_diagnose_action_outcomes = any(
        _has_fields(payload, {"action_id", "action_kind", "status"})
        for payload in action_outcome_payloads
    )
    if not can_diagnose_action_outcomes:
        warnings.add("action_outcome_metadata")

    can_compare_evaluation_epochs = bool(evaluation_epoch_payloads) and any(
        _has_fields(payload, {"epoch_id", "member_id", "expected", "state"})
        for payload in evaluation_member_payloads
    )
    if not can_compare_evaluation_epochs:
        warnings.add("evaluation_epoch_metadata")

    can_generate_repair_prompts = any(
        isinstance(event.get("payload"), Mapping)
        and _has_any(event["payload"], {"mistake_family", "component", "severity"})
        for event in event_list
    ) and any(
        by_type.get(event_type)
        for event_type in ("risk.check.completed", "error.recorded")
    )
    if not can_generate_repair_prompts:
        missing.add("repair_prompt_evidence")

    can_score_arena = can_compute_flow_adjusted_pnl and any(
        _has_any(payload, {"scenario_tags", "max_drawdown_pct", "case_count"})
        for payload in replay_payloads + performance_payloads
    )
    if not can_score_arena:
        warnings.add("arena_scoring_metadata")

    can_publish_proof = (
        bool(by_type.get("agent.build.recorded"))
        and bool(by_type.get("risk.check.completed"))
        and bool(by_type.get("replay.result.recorded"))
    )
    if not can_publish_proof:
        warnings.add("proof_page_metadata")

    return ReportingReadiness(
        event_count=len(event_list),
        can_compute_closed_trade_stats=can_compute_closed_trade_stats,
        can_compute_net_pnl=can_compute_net_pnl,
        can_compute_flow_adjusted_pnl=can_compute_flow_adjusted_pnl,
        can_compute_strategy_stats=can_compute_strategy_stats,
        can_compute_prompt_drift_stats=can_compute_prompt_drift_stats,
        can_compare_shadow_agents=can_compare_shadow_agents,
        can_diagnose_opportunity_coverage=can_diagnose_opportunity_coverage,
        can_diagnose_setup_regimes=can_diagnose_setup_regimes,
        can_diagnose_action_outcomes=can_diagnose_action_outcomes,
        can_compare_evaluation_epochs=can_compare_evaluation_epochs,
        can_generate_repair_prompts=can_generate_repair_prompts,
        can_score_arena=can_score_arena,
        can_publish_proof=can_publish_proof,
        missing_fields=tuple(sorted(missing)),
        warnings=tuple(sorted(warnings)),
    )


def assess_tier_readiness(events: Iterable[Mapping[str, Any]]) -> TierReadiness:
    """Assess hosted Free, Basic, and Pro feature readiness."""

    event_list = list(events)
    reporting = assess_reporting_readiness(event_list)
    event_types = {str(event.get("event_type", "")) for event in event_list}
    payloads = _payloads(event_list)

    free_gaps: set[str] = set()
    basic_gaps: set[str] = set()
    pro_gaps: set[str] = set()

    if not event_list:
        free_gaps.add("no_events")
    if not {"agent.run.started", "agent.run.completed"} <= event_types:
        free_gaps.add("run_timeline")
    if any(_privacy_flag(event, "contains_prompt_text") for event in event_list):
        free_gaps.add("raw_prompt_text_present")
    if any(_privacy_flag(event, "contains_output_text") for event in event_list):
        free_gaps.add("raw_output_text_present")

    basic_requirements = {
        "decision.proposed": "decisions",
        "risk.check.completed": "risk_checks",
        "paper.fill.recorded": "paper_fills",
        "position.snapshot.recorded": "positions",
        "performance.snapshot.recorded": "performance",
        "capital.flow.recorded": "capital_flows",
    }
    for event_type, gap in basic_requirements.items():
        if event_type not in event_types:
            basic_gaps.add(gap)
    if not reporting.can_compute_closed_trade_stats:
        basic_gaps.add("closed_trade_stats")
    if not reporting.can_compute_net_pnl:
        basic_gaps.add("net_pnl")
    if not reporting.can_compute_flow_adjusted_pnl:
        basic_gaps.add("flow_adjusted_pnl")
    if not reporting.can_compute_strategy_stats:
        basic_gaps.add("strategy_stats")

    pro_requirements = {
        "agent.build.recorded": "agent_build",
        "replay.result.recorded": "replay_results",
        "llm.call.started": "prompt_versions",
        "decision.outcome.recorded": "decision_outcomes",
    }
    for event_type, gap in pro_requirements.items():
        if event_type not in event_types:
            pro_gaps.add(gap)
    if not reporting.can_generate_repair_prompts:
        pro_gaps.add("repair_prompt_evidence")
    if not reporting.can_compute_prompt_drift_stats:
        pro_gaps.add("prompt_drift")
    if not any(_has_any(payload, {"mistake_family"}) for payload in payloads):
        pro_gaps.add("mistake_taxonomy")

    free_ready = not free_gaps
    basic_ready = free_ready and not basic_gaps
    pro_ready = basic_ready and not pro_gaps

    return TierReadiness(
        event_count=len(event_list),
        free_ready=free_ready,
        basic_ready=basic_ready,
        pro_ready=pro_ready,
        free_gaps=tuple(sorted(free_gaps)),
        basic_gaps=tuple(sorted(basic_gaps)),
        pro_gaps=tuple(sorted(pro_gaps)),
        data_quality_score=_score_from_gaps(free_gaps | basic_gaps | pro_gaps),
        privacy_score=100 if not (free_gaps & _PRIVACY_GAPS) else 60,
        stat_coverage_score=_score_from_gaps(basic_gaps),
        repair_prompt_score=_score_from_gaps(pro_gaps),
    )


def assess_decision_flow_readiness(
    events: Iterable[Mapping[str, Any]],
) -> DecisionFlowReadiness:
    """Assess whether telemetry can explain agent decision-flow quality."""

    event_list = list(events)
    checks = _decision_flow_check_specs(event_list)
    diagnostics = _diagnostic_payloads(event_list)
    failed_diagnostics = tuple(
        sorted(
            str(payload.get("check_id"))
            for payload in diagnostics
            if payload.get("status") == "failed" and _present(payload.get("check_id"))
        )
    )
    warning_diagnostics = tuple(
        sorted(
            str(payload.get("check_id"))
            for payload in diagnostics
            if payload.get("status") == "warning" and _present(payload.get("check_id"))
        )
    )
    gaps = {str(check["gap"]) for check in checks if not check["can_check"]}
    inferred_failed = {
        str(check["check_id"]) for check in checks if check["status"] == "failed"
    }
    inferred_warnings = {
        str(check["warning"])
        for check in checks
        if check["status"] == "warning" and _present(check.get("warning"))
    }
    failed_checks = tuple(sorted(set(failed_diagnostics) | inferred_failed))
    warnings = set(warning_diagnostics) | inferred_warnings
    if event_list and not diagnostics:
        warnings.add("diagnostic_checks_not_emitted")

    check_by_id = {str(check["check_id"]): check for check in checks}
    score_values = [int(check["score"]) for check in checks]
    readiness_score = int(sum(score_values) / len(score_values)) if score_values else 0

    return DecisionFlowReadiness(
        event_count=len(event_list),
        ready=not gaps and not failed_checks,
        diagnostic_event_count=len(diagnostics),
        failed_diagnostic_count=len(failed_diagnostics),
        warning_diagnostic_count=len(warning_diagnostics),
        can_check_numeric_domains=bool(
            check_by_id["decision_flow.numeric_domain"]["can_check"]
        ),
        can_check_market_data_quality=bool(
            check_by_id["decision_flow.market_data_quality"]["can_check"]
        ),
        can_check_setup_profile_persistence=bool(
            check_by_id["decision_flow.setup_profile_persistence"]["can_check"]
        ),
        can_check_opportunity_coverage=bool(
            check_by_id["decision_flow.opportunity_coverage"]["can_check"]
        ),
        can_check_fresh_run_proof=bool(
            check_by_id["decision_flow.fresh_run_proof"]["can_check"]
        ),
        numeric_domain_score=int(check_by_id["decision_flow.numeric_domain"]["score"]),
        market_data_contract_score=int(
            check_by_id["decision_flow.market_data_quality"]["score"]
        ),
        setup_profile_persistence_score=int(
            check_by_id["decision_flow.setup_profile_persistence"]["score"]
        ),
        opportunity_coverage_score=int(
            check_by_id["decision_flow.opportunity_coverage"]["score"]
        ),
        fresh_run_proof_score=int(
            check_by_id["decision_flow.fresh_run_proof"]["score"]
        ),
        decision_flow_readiness_score=readiness_score,
        gaps=tuple(sorted(gaps)),
        warnings=tuple(sorted(warnings)),
        failed_checks=failed_checks,
    )


def build_decision_flow_diagnostic_events(
    events: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build privacy-safe diagnostic check events from existing telemetry."""

    event_list = list(events)
    if not event_list:
        return []
    reference = event_list[0]
    checks = _decision_flow_check_specs(event_list)
    symbols = _event_symbols(event_list)
    run_ids = _event_run_ids(event_list)
    evidence_event_ids = _event_ids(event_list)
    output: list[dict[str, Any]] = []
    fingerprint_seed = "|".join(evidence_event_ids[:100]) or str(len(event_list))
    for check in checks:
        check_id = str(check["check_id"])
        idempotency_hash = hash_text(
            f"{check_id}|{fingerprint_seed}|{len(event_list)}"
        ).split(":", maxsplit=1)[-1][:24]
        output.append(
            {
                "schema_version": SCHEMA_VERSION,
                "event_id": new_event_id(),
                "idempotency_key": f"diagnostic/{check_id}/{idempotency_hash}",
                "project": str(reference.get("project") or "local"),
                "agent_id": str(reference.get("agent_id") or "local-agent"),
                "run_id": str(reference.get("run_id") or "run_diagnostics"),
                "span_id": None,
                "parent_span_id": None,
                "event_type": "diagnostic.check.completed",
                "occurred_at": utc_now_iso(),
                "environment": str(reference.get("environment") or "paper"),
                "symbols": symbols,
                "payload": _compact_payload(
                    {
                        "check_id": check_id,
                        "check_name": check["check_name"],
                        "check_family": check["check_family"],
                        "status": check["status"],
                        "severity": _severity_for_check_status(str(check["status"])),
                        "component": check["component"],
                        "mistake_family": check["mistake_family"],
                        "money_impact": check["money_impact"],
                        "blocking_status": check["blocking_status"],
                        "resolution_status": check["resolution_status"],
                        "next_safe_action": check["next_safe_action"],
                        "observed": check["observed"],
                        "expected": check["expected"],
                        "sample_count": len(event_list),
                        "failed_count": 1 if check["status"] == "failed" else 0,
                        "warning_count": 1 if check["status"] == "warning" else 0,
                        "evidence_event_ids": evidence_event_ids[:25],
                        "evidence_run_ids": run_ids[:25],
                        "data_quality_score_delta": check["data_quality_score_delta"],
                    }
                ),
                "privacy": {
                    "full_io": False,
                    "redaction_version": "none",
                    "contains_prompt_text": False,
                    "contains_output_text": False,
                    "contains_broker_payload": False,
                    "contains_account_identifier": False,
                    "truncated": False,
                },
                "sdk": {
                    "name": SDK_NAME,
                    "version": SDK_VERSION,
                    "language": SDK_LANGUAGE,
                },
            }
        )
    return output


def assess_agentic_security_readiness(
    events: Iterable[Mapping[str, Any]],
) -> AgenticSecurityReadiness:
    """Assess whether events carry enough security context for Pro diagnostics."""

    event_list = list(events)
    payloads = _payloads(event_list)
    gaps: set[str] = set()
    warnings: set[str] = set()

    prompt_injection = any(
        _contains_text(payload, "prompt_injection")
        or payload.get("mistake_family") == "security.prompt_injection"
        or payload.get("prompt_injection_tested") is True
        for payload in payloads
    )
    if not prompt_injection:
        gaps.add("prompt_injection_coverage")

    sensitive_info = any(
        _privacy_flag(event, "contains_account_identifier")
        or _privacy_flag(event, "contains_broker_payload")
        for event in event_list
    ) or any(
        _has_any(payload, {"account_scope_hash", "resource_uri_hash"})
        for payload in payloads
    )
    if not sensitive_info:
        gaps.add("sensitive_information_coverage")

    tool_policy = any(
        _has_any(payload, {"tool_policy_id", "tool_allowed", "tool_scope"})
        for payload in payloads
    )
    if not tool_policy:
        gaps.add("tool_policy_coverage")

    memory_provenance = any(
        _has_any(payload, {"memory_scope", "context_provenance", "freshness_seconds"})
        for payload in payloads
    )
    if not memory_provenance:
        gaps.add("memory_provenance_coverage")

    cost_budget = any(
        _has_any(payload, {"budget_policy_id", "budget_remaining", "estimated_cost"})
        for payload in payloads
    ) or any(event.get("event_type") == "cost.usage.recorded" for event in event_list)
    if not cost_budget:
        gaps.add("cost_budget_coverage")

    for payload in payloads:
        if payload.get("approval_required") is True and not payload.get(
            "approval_observed"
        ):
            warnings.add("approval_required_without_observed_approval")

    return AgenticSecurityReadiness(
        event_count=len(event_list),
        ready=not gaps and not warnings,
        prompt_injection_coverage=prompt_injection,
        sensitive_information_coverage=sensitive_info,
        tool_policy_coverage=tool_policy,
        memory_provenance_coverage=memory_provenance,
        cost_budget_coverage=cost_budget,
        excessive_agency_warnings=tuple(sorted(warnings)),
        gaps=tuple(sorted(gaps)),
    )


def assess_proof_readiness(events: Iterable[Mapping[str, Any]]) -> ProofReadiness:
    """Assess whether telemetry can support a conservative public proof page."""

    event_list = list(events)
    event_types = {str(event.get("event_type", "")) for event in event_list}
    payloads = _payloads(event_list)
    environments = tuple(
        sorted({str(event.get("environment")) for event in event_list})
    )
    gaps: set[str] = set()
    warnings: set[str] = set()

    risk_gates = "risk.check.completed" in event_types
    replay_tests = "replay.result.recorded" in event_types
    source_quality = "source.claim.recorded" in event_types or any(
        _has_any(payload, {"source_confidence", "source_quality", "claim_type"})
        for payload in payloads
    )
    prompt_or_build_versions = "agent.build.recorded" in event_types or any(
        _has_any(payload, {"prompt_hash", "prompt_version", "config_hash"})
        for payload in payloads
    )
    privacy_safe = not any(
        _privacy_flag(event, "contains_prompt_text")
        or _privacy_flag(event, "contains_output_text")
        or _privacy_flag(event, "contains_broker_payload")
        or _privacy_flag(event, "contains_account_identifier")
        for event in event_list
    )
    guarantee_language = any(
        _contains_guarantee_language(payload) for payload in payloads
    )

    if not event_list:
        gaps.add("no_events")
    if not environments:
        gaps.add("environment_clarity")
    if "development" in environments:
        gaps.add("non_proof_environment")
    if not risk_gates:
        gaps.add("risk_gates")
    if not replay_tests:
        gaps.add("replay_tests")
    if not source_quality:
        gaps.add("source_quality")
    if not prompt_or_build_versions:
        gaps.add("prompt_or_build_versions")
    if not privacy_safe:
        gaps.add("privacy_safe")
    if guarantee_language:
        gaps.add("guarantee_language")
    if "live_observe" in environments:
        warnings.add("live_observe_requires_clear_no_execution_claim")

    return ProofReadiness(
        event_count=len(event_list),
        ready=not gaps,
        environment_modes=environments,
        risk_gates_present=risk_gates,
        replay_tests_present=replay_tests,
        source_quality_present=source_quality,
        prompt_or_build_versions_present=prompt_or_build_versions,
        privacy_safe=privacy_safe,
        guarantee_language_present=guarantee_language,
        gaps=tuple(sorted(gaps)),
        warnings=tuple(sorted(warnings)),
    )


def assess_arena_readiness(events: Iterable[Mapping[str, Any]]) -> ArenaReadiness:
    """Assess whether telemetry can support fair benchmark challenge scoring."""

    event_list = list(events)
    event_types = {str(event.get("event_type", "")) for event in event_list}
    payloads = _payloads(event_list)
    gaps: set[str] = set()
    warnings: set[str] = set()

    scenario_tags = any(_has_any(payload, {"scenario_tags"}) for payload in payloads)
    market_regime = any(
        _has_any(payload, {"market_regime", "session_state", "market_phase"})
        for payload in payloads
    )
    session_dates = any(_has_any(payload, {"session_date"}) for payload in payloads)
    drawdown_metrics = any(
        _has_any(payload, {"max_drawdown_pct", "drawdown_pct"}) for payload in payloads
    )
    survival_metrics = any(
        _has_any(payload, {"survival_status", "failed_run_count", "case_count"})
        for payload in payloads
    )
    replay_results = "replay.result.recorded" in event_types
    evaluation_epochs = "evaluation.epoch.started" in event_types
    evaluation_member_coverage = "evaluation.epoch.member.completed" in event_types
    strategy_tags = any(
        _has_any(payload, {"strategy_id", "strategy_name", "setup"})
        for payload in payloads
    )
    position_state = "position.snapshot.recorded" in event_types
    portfolio_state = "portfolio.snapshot.recorded" in event_types

    requirements = {
        "scenario_tags": scenario_tags,
        "market_regime": market_regime,
        "session_dates": session_dates,
        "drawdown_metrics": drawdown_metrics,
        "survival_metrics": survival_metrics,
        "replay_results": replay_results,
        "evaluation_epochs": evaluation_epochs,
        "evaluation_member_coverage": evaluation_member_coverage,
        "strategy_tags": strategy_tags,
        "position_state": position_state,
        "portfolio_state": portfolio_state,
    }
    for gap, present in requirements.items():
        if not present:
            gaps.add(gap)
    if event_list and not any(
        event.get("environment") == "replay" for event in event_list
    ):
        warnings.add("no_replay_environment_events")

    return ArenaReadiness(
        event_count=len(event_list),
        ready=not gaps,
        scenario_tags_present=scenario_tags,
        market_regime_present=market_regime,
        session_dates_present=session_dates,
        drawdown_metrics_present=drawdown_metrics,
        survival_metrics_present=survival_metrics,
        replay_results_present=replay_results,
        evaluation_epochs_present=evaluation_epochs,
        evaluation_member_coverage_present=evaluation_member_coverage,
        strategy_tags_present=strategy_tags,
        position_state_present=position_state,
        portfolio_state_present=portfolio_state,
        gaps=tuple(sorted(gaps)),
        warnings=tuple(sorted(warnings)),
    )


def build_repair_pack(
    events: Iterable[Mapping[str, Any]],
    *,
    max_findings: int = 5,
) -> dict[str, Any]:
    """Build a deterministic local repair evidence pack."""

    event_list = list(events)
    tier = assess_tier_readiness(event_list)
    security = assess_agentic_security_readiness(event_list)
    findings = _repair_findings(event_list, tier, security)[:max_findings]
    return {
        "version": "2026-06-08",
        "event_count": len(event_list),
        "tier_readiness": tier.to_dict(),
        "agentic_security_readiness": security.to_dict(),
        "findings": findings,
        "prompt": _repair_prompt(findings),
    }


def build_dataset_items(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Create sanitized dataset items from event evidence."""

    items: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event.get("event_type") not in {
            "risk.check.completed",
            "trade.rejected",
            "error.recorded",
            "replay.result.recorded",
            "decision.outcome.recorded",
            "opportunity.candidate.reviewed",
            "action.outcome.recorded",
            "evaluation.epoch.member.completed",
        }:
            continue
        items.append(
            {
                "dataset_item_id": f"ds_{event.get('event_id')}",
                "event_id": event.get("event_id"),
                "run_id": event.get("run_id"),
                "event_type": event.get("event_type"),
                "symbols": list(event.get("symbols", [])),
                "environment": event.get("environment"),
                "session_date": payload.get("session_date"),
                "mistake_family": payload.get("mistake_family"),
                "component": payload.get("component"),
                "expected_invariant": _expected_invariant(event),
            }
        )
    return items


def build_eval_plan(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a deterministic eval plan from telemetry events."""

    dataset = build_dataset_items(events)
    checks = sorted(
        {
            item["expected_invariant"]
            for item in dataset
            if item.get("expected_invariant")
        }
    )
    return {
        "version": "2026-06-08",
        "dataset_item_count": len(dataset),
        "deterministic_checks": checks,
        "llm_judge_required": False,
        "privacy_policy": "hash_or_reference_only",
        "dataset_event_ids": [item["event_id"] for item in dataset],
    }


def build_experiment_manifest(
    repair_pack: Mapping[str, Any],
    *,
    changes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic experiment manifest from a local repair pack."""

    declared_changes = dict(changes or {})
    findings = repair_pack.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    axes = _experiment_axes(findings, declared_changes)
    warnings: list[str] = []
    if not declared_changes:
        warnings.append("no_declared_changes")
    if len(axes) > 1 and not declared_changes.get("multi_axis_reason"):
        warnings.append("multi_axis_experiment_requires_explicit_reason")
    return {
        "version": "2026-06-08",
        "source": "agent-tracker-repair-pack",
        "repair_pack_version": repair_pack.get("version"),
        "event_count": repair_pack.get("event_count", 0),
        "finding_count": len(findings),
        "comparison_axes": axes,
        "declared_changes": declared_changes,
        "fixed_context": {
            "broker_execution": "unchanged",
            "risk_gate_enforcement": "unchanged_unless_declared_and_tested",
            "market_data_source": "unchanged_unless_declared",
            "telemetry_privacy_policy": "hash_or_reference_only",
        },
        "required_checks": [
            "agent-tracker validate-jsonl events.jsonl --profile strict-reporting",
            "agent-tracker repair-pack events.jsonl --output repair-pack.json",
            "run repo replay or regression suite for affected findings",
        ],
        "llm_judge_required": False,
        "privacy_policy": "hash_or_reference_only",
        "warnings": warnings,
    }


def _decision_flow_check_specs(
    events: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    payloads = _payloads(events)
    diagnostics = _diagnostic_payloads(events)
    event_types = {str(event.get("event_type", "")) for event in events}

    numeric_surface = _has_diagnostic_family(diagnostics, {"numeric_domain"}) or any(
        _has_any(
            payload,
            {
                "signed_fields_present",
                "signed_return_min",
                "signed_return_max",
                "signed_distance_min",
                "signed_distance_max",
                "non_finite_count",
                "nan_count",
                "positive_infinity_count",
                "negative_infinity_count",
                "zero_price_count",
                "zero_volume_count",
            },
        )
        for payload in payloads
    )
    numeric_failed = _any_positive_number(
        payloads,
        {
            "non_finite_count",
            "nan_count",
            "positive_infinity_count",
            "negative_infinity_count",
        },
    )
    numeric_warning = _any_false_bool(payloads, {"signed_fields_present"}) or (
        not numeric_surface and bool(events)
    )
    numeric_status = _diagnostic_status_or_default(
        diagnostics,
        families={"numeric_domain"},
        failed=numeric_failed,
        warning=numeric_warning,
        present=numeric_surface,
    )

    market_surface = (
        _has_diagnostic_family(diagnostics, {"market_data", "data_contract"})
        or "market.snapshot.recorded" in event_types
        or any(
            _has_any(
                payload,
                {
                    "source_bar_count",
                    "usable_bar_count",
                    "invalid_bar_count",
                    "invalid_ohlc_relation_count",
                    "zero_price_count",
                    "zero_volume_count",
                    "non_positive_price_count",
                    "data_contract_status",
                    "freshness_seconds",
                },
            )
            for payload in payloads
        )
    )
    market_failed = _any_positive_number(
        payloads,
        {
            "invalid_bar_count",
            "invalid_ohlc_relation_count",
            "non_positive_price_count",
            "negative_volume_count",
        },
    ) or any(payload.get("data_contract_status") == "failed" for payload in payloads)
    market_warning = _any_positive_number(
        payloads,
        {"stale_count", "zero_price_count", "zero_volume_count"},
    ) or any(payload.get("data_contract_status") == "warning" for payload in payloads)
    market_status = _diagnostic_status_or_default(
        diagnostics,
        families={"market_data", "data_contract"},
        failed=market_failed,
        warning=market_warning,
        present=market_surface,
    )

    setup_surface = (
        _has_diagnostic_family(diagnostics, {"setup_profile"})
        or "setup.profile.recorded" in event_types
        or any(
            _has_any(
                payload,
                {
                    "primary_regime",
                    "entry_permission",
                    "profile_shape_status",
                    "entry_regime_present",
                    "entry_permission_present",
                    "loaded_after_restart",
                    "backfill_status",
                },
            )
            for payload in payloads
        )
    )
    setup_failed = any(
        payload.get("profile_shape_status") == "missing"
        or payload.get("backfill_status") == "failed"
        or payload.get("entry_regime_present") is False
        or payload.get("entry_permission_present") is False
        for payload in payloads
    )
    setup_warning = any(
        payload.get("profile_shape_status")
        in {"defensive_default", "normalized_from_top_level"}
        or payload.get("backfill_status") in {"partial", "not_started"}
        or payload.get("loaded_after_restart") is False
        for payload in payloads
    )
    setup_status = _diagnostic_status_or_default(
        diagnostics,
        families={"setup_profile"},
        failed=setup_failed,
        warning=setup_warning,
        present=setup_surface,
    )

    board_surface = "opportunity.board.recorded" in event_types
    candidate_surface = "opportunity.candidate.reviewed" in event_types
    opportunity_surface = _has_diagnostic_family(
        diagnostics, {"opportunity_coverage"}
    ) or (board_surface and candidate_surface)
    opportunity_failed = any(
        payload.get("data_contract_status") == "failed"
        and _has_any(payload, {"board_id", "candidate_id"})
        for payload in payloads
    )
    opportunity_warning = (
        _any_true_bool(payloads, {"excluded_by_candidate_limit"})
        or _any_positive_number(
            payloads,
            {"candidate_limit_count", "tape_attention_excluded_count"},
        )
        or _any_less_than(payloads, "leader_review_coverage_pct", Decimal("100"))
        or any(
            payload.get("review_status")
            in {"model_omitted", "excluded_candidate_limit", "backfill_unknown"}
            for payload in payloads
        )
    )
    opportunity_status = _diagnostic_status_or_default(
        diagnostics,
        families={"opportunity_coverage"},
        failed=opportunity_failed,
        warning=opportunity_warning,
        present=opportunity_surface,
    )

    build_surface = "agent.build.recorded" in event_types
    replay_surface = "replay.result.recorded" in event_types
    fresh_surface = _has_diagnostic_family(
        diagnostics, {"build_release", "replay"}
    ) or (build_surface and replay_surface)
    fresh_failed = any(
        payload.get("changed_since_last_replay") is True
        and not payload.get("post_change_verification_required")
        for payload in payloads
    ) or any(
        str(payload.get("status", "")).lower() in {"failed", "error"}
        and _has_any(payload, {"suite_name", "replay_suite_version"})
        for payload in payloads
    )
    fresh_warning = build_surface and not replay_surface
    fresh_status = _diagnostic_status_or_default(
        diagnostics,
        families={"build_release", "replay"},
        failed=fresh_failed,
        warning=fresh_warning,
        present=fresh_surface,
    )

    return [
        _decision_check_spec(
            check_id="decision_flow.numeric_domain",
            check_name="Numeric domain contract",
            check_family="numeric_domain",
            component="data_contract",
            can_check=numeric_surface,
            status=numeric_status,
            gap="numeric_domain_evidence",
            warning="numeric_domain_contract_warning",
            mistake_family="market.numeric_domain_confused",
            next_safe_action="run_test",
            observed={
                "numeric_surface_present": numeric_surface,
                "non_finite_present": numeric_failed,
                "signed_fields_warning": numeric_warning,
            },
            expected={"finite_numbers": True, "signed_fields_preserved": True},
            money_impact="possible",
        ),
        _decision_check_spec(
            check_id="decision_flow.market_data_quality",
            check_name="Market data quality contract",
            check_family="market_data",
            component="market_data",
            can_check=market_surface,
            status=market_status,
            gap="market_data_quality_evidence",
            warning="market_data_contract_warning",
            mistake_family="market.non_finite_ohlcv",
            next_safe_action="run_test",
            observed={
                "market_surface_present": market_surface,
                "invalid_market_data_present": market_failed,
                "stale_or_zero_data_present": market_warning,
            },
            expected={
                "fresh_market_data": True,
                "valid_ohlc_relationships": True,
                "positive_prices": True,
            },
            money_impact="possible",
        ),
        _decision_check_spec(
            check_id="decision_flow.setup_profile_persistence",
            check_name="Setup profile persistence",
            check_family="setup_profile",
            component="decision_flow",
            can_check=setup_surface,
            status=setup_status,
            gap="setup_profile_persistence_evidence",
            warning="setup_profile_persistence_warning",
            mistake_family="entry.profile_persistence_missing",
            next_safe_action="repair_artifact",
            observed={
                "setup_surface_present": setup_surface,
                "missing_or_failed_profile": setup_failed,
                "profile_shape_warning": setup_warning,
            },
            expected={
                "primary_regime_present": True,
                "entry_permission_present": True,
                "restart_safe": True,
            },
            money_impact="possible",
        ),
        _decision_check_spec(
            check_id="decision_flow.opportunity_coverage",
            check_name="Opportunity coverage accountability",
            check_family="opportunity_coverage",
            component="decision_flow",
            can_check=opportunity_surface,
            status=opportunity_status,
            gap="opportunity_coverage_evidence",
            warning="opportunity_coverage_warning",
            mistake_family="opportunity.candidate_limit_hidden",
            next_safe_action="repair_artifact",
            observed={
                "board_surface_present": board_surface,
                "candidate_surface_present": candidate_surface,
                "coverage_warning": opportunity_warning,
            },
            expected={
                "board_recorded": True,
                "candidate_reviews_recorded": True,
                "omissions_explained": True,
            },
            money_impact="possible",
        ),
        _decision_check_spec(
            check_id="decision_flow.fresh_run_proof",
            check_name="Fresh run and replay proof",
            check_family="build_release",
            component="diagnostics",
            can_check=fresh_surface,
            status=fresh_status,
            gap="fresh_run_proof_evidence",
            warning="fresh_run_proof_warning",
            mistake_family="release.fresh_run_missing",
            next_safe_action="run_test",
            observed={
                "build_surface_present": build_surface,
                "replay_surface_present": replay_surface,
                "unverified_change_present": fresh_failed,
            },
            expected={
                "build_recorded": True,
                "replay_recorded": True,
                "changed_builds_replayed": True,
            },
            money_impact="blocked" if fresh_failed else "possible",
        ),
    ]


def _decision_check_spec(
    *,
    check_id: str,
    check_name: str,
    check_family: str,
    component: str,
    can_check: bool,
    status: str,
    gap: str,
    warning: str,
    mistake_family: str,
    next_safe_action: str,
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
    money_impact: str,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "check_name": check_name,
        "check_family": check_family,
        "component": component,
        "can_check": can_check,
        "status": status,
        "score": _decision_check_score(can_check=can_check, status=status),
        "gap": gap,
        "warning": warning,
        "mistake_family": mistake_family if status != "passed" else None,
        "next_safe_action": next_safe_action if status != "passed" else "observe",
        "observed": dict(observed),
        "expected": dict(expected),
        "money_impact": money_impact if status != "passed" else "none",
        "blocking_status": "trading_blocked"
        if status == "failed"
        else "workflow_deferred"
        if status == "warning"
        else "non_blocking",
        "resolution_status": "open" if status != "passed" else "resolved",
        "data_quality_score_delta": -25
        if status == "failed"
        else (-8 if status == "warning" else 0),
    }


def _decision_check_score(*, can_check: bool, status: str) -> int:
    if status == "failed":
        return 0
    if status == "warning":
        return 72 if can_check else 40
    if status == "passed":
        return 100
    return 0


def _diagnostic_status_or_default(
    diagnostics: list[Mapping[str, Any]],
    *,
    families: set[str],
    failed: bool,
    warning: bool,
    present: bool,
) -> str:
    family_statuses = [
        str(payload.get("status"))
        for payload in diagnostics
        if payload.get("check_family") in families
    ]
    if "failed" in family_statuses or failed:
        return "failed"
    if "warning" in family_statuses or warning:
        return "warning"
    if present or "passed" in family_statuses:
        return "passed"
    return "warning"


def _diagnostic_payloads(
    events: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        payload
        for event in events
        if event.get("event_type") == "diagnostic.check.completed"
        and isinstance(payload := event.get("payload"), Mapping)
    ]


def _has_diagnostic_family(
    diagnostics: Iterable[Mapping[str, Any]],
    families: set[str],
) -> bool:
    return any(payload.get("check_family") in families for payload in diagnostics)


def _event_ids(events: Iterable[Mapping[str, Any]]) -> list[str]:
    return _unique_strings(event.get("event_id") for event in events)


def _event_run_ids(events: Iterable[Mapping[str, Any]]) -> list[str]:
    return _unique_strings(event.get("run_id") for event in events)


def _event_symbols(events: Iterable[Mapping[str, Any]]) -> list[str]:
    values: list[str] = []
    for event in events:
        symbols = event.get("symbols")
        if isinstance(symbols, list):
            values.extend(str(symbol).upper() for symbol in symbols if symbol)
    return _unique_strings(values)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _compact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _any_positive_number(
    payloads: Iterable[Mapping[str, Any]],
    fields: set[str],
) -> bool:
    for payload in payloads:
        for field in fields:
            value = _optional_decimal(payload.get(field))
            if value is not None and value > 0:
                return True
    return False


def _any_less_than(
    payloads: Iterable[Mapping[str, Any]],
    field: str,
    threshold: Decimal,
) -> bool:
    for payload in payloads:
        value = _optional_decimal(payload.get(field))
        if value is not None and value < threshold:
            return True
    return False


def _any_false_bool(
    payloads: Iterable[Mapping[str, Any]],
    fields: set[str],
) -> bool:
    return any(payload.get(field) is False for payload in payloads for field in fields)


def _any_true_bool(
    payloads: Iterable[Mapping[str, Any]],
    fields: set[str],
) -> bool:
    return any(payload.get(field) is True for payload in payloads for field in fields)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _severity_for_check_status(status: str) -> str:
    if status == "failed":
        return "error"
    if status == "warning":
        return "warning"
    return "info"


def _validate_reporting_extensions(
    _event_type: str, payload: Mapping[str, Any]
) -> None:
    if "side" in payload:
        _require_enum(payload, "side", ORDER_SIDES)
    if "open_close_effect" in payload:
        _require_enum(payload, "open_close_effect", OPEN_CLOSE_EFFECTS)
    if "fill_source" in payload:
        _require_enum(payload, "fill_source", FILL_SOURCES)
    if "session_state" in payload:
        _require_enum(payload, "session_state", SESSION_STATES)
    if "review_status" in payload:
        _require_enum(payload, "review_status", OPPORTUNITY_REVIEW_STATUSES)
    if "primary_regime" in payload:
        _require_enum(payload, "primary_regime", ENTRY_REGIMES)
    if "entry_permission" in payload:
        _require_enum(payload, "entry_permission", ENTRY_PERMISSIONS)
    if "status" in payload and _event_type == "action.outcome.recorded":
        _require_enum(payload, "status", ACTION_OUTCOME_STATUSES)
    if "state" in payload and _event_type == "evaluation.epoch.member.completed":
        _require_enum(payload, "state", EVALUATION_MEMBER_STATES)
    if "check_family" in payload:
        _require_enum(payload, "check_family", DIAGNOSTIC_CHECK_FAMILIES)
    if "status" in payload and _event_type == "diagnostic.check.completed":
        _require_enum(payload, "status", DIAGNOSTIC_CHECK_STATUSES)
    if "profile_shape_status" in payload:
        _require_enum(payload, "profile_shape_status", PROFILE_SHAPE_STATUSES)
    if "backfill_status" in payload:
        _require_enum(payload, "backfill_status", BACKFILL_STATUSES)
    if "data_contract_status" in payload:
        _require_enum(payload, "data_contract_status", DATA_CONTRACT_STATUSES)
    if "session_date" in payload:
        _require_date(payload, "session_date")
    if "linked_event_ids" in payload:
        linked = payload["linked_event_ids"]
        if not isinstance(linked, list) or not all(
            isinstance(item, str) and item.startswith("evt_") for item in linked
        ):
            raise SchemaValidationError("linked_event_ids must contain event IDs")
    for field in _BOOL_EXTENSION_FIELDS:
        _require_optional_bool(payload, field)
    for field in _STRING_LIST_FIELDS:
        _require_optional_string_list(payload, field)
    if _event_type == "paper.fill.recorded":
        _require_optional_number(payload, "quantity", minimum=Decimal("0"))
    for field in _REPORTING_IDENTITY_FIELDS:
        if field in payload and payload[field] is not None:
            _require_nonempty(payload, field)
    for field in _NON_NEGATIVE_EXTENSION_NUMBER_FIELDS:
        if field in _SCORE_FIELDS:
            _require_optional_number(
                payload, field, minimum=Decimal("0"), maximum=Decimal("100")
            )
        else:
            _require_optional_number(payload, field, minimum=Decimal("0"))
    for field in _SIGNED_EXTENSION_NUMBER_FIELDS:
        _require_optional_number(payload, field)


def _require_nonempty(payload: Mapping[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), str) or not str(payload[field]).strip():
        raise SchemaValidationError(f"{field} must be a non-empty string")


def _require_bool(payload: Mapping[str, Any], field: str) -> None:
    if not isinstance(payload.get(field), bool):
        raise SchemaValidationError(f"{field} must be boolean")


def _require_optional_bool(payload: Mapping[str, Any], field: str) -> None:
    if field in payload and not isinstance(payload.get(field), bool):
        raise SchemaValidationError(f"{field} must be boolean")


def _require_enum(payload: Mapping[str, Any], field: str, allowed: set[str]) -> None:
    value = payload.get(field)
    if value not in allowed:
        raise SchemaValidationError(
            f"{field} must be one of: {', '.join(sorted(allowed))}"
        )


def _require_number(
    payload: Mapping[str, Any],
    field: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> None:
    value = _decimal(payload.get(field), field=field)
    if minimum is not None and value < minimum:
        raise SchemaValidationError(f"{field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SchemaValidationError(f"{field} must be <= {maximum}")


def _require_optional_number(
    payload: Mapping[str, Any],
    field: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> None:
    if field in payload and payload[field] is not None:
        _require_number(payload, field, minimum=minimum, maximum=maximum)


def _require_optional_string_list(payload: Mapping[str, Any], field: str) -> None:
    if field not in payload or payload[field] is None:
        return
    value = payload[field]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise SchemaValidationError(f"{field} must be a list of non-empty strings")


def _require_date(payload: Mapping[str, Any], field: str) -> None:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise SchemaValidationError(f"{field} must be an ISO date")
    if not _STRICT_DATE_RE.fullmatch(value):
        raise SchemaValidationError(f"{field} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise SchemaValidationError(f"{field} must be an ISO date") from exc


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SchemaValidationError(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SchemaValidationError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise SchemaValidationError(f"{field} must be finite")
    return parsed


def _has_fields(payload: Mapping[str, Any], fields: set[str]) -> bool:
    return all(_present(payload.get(field)) for field in fields)


def _has_any(payload: Mapping[str, Any], fields: set[str]) -> bool:
    return any(_present(payload.get(field)) for field in fields)


def _present(value: Any) -> bool:
    return value is not None and value != ""


_PRIVACY_GAPS = {"raw_prompt_text_present", "raw_output_text_present"}


def _payloads(events: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        payload
        for event in events
        if isinstance(payload := event.get("payload"), Mapping)
    ]


def _privacy_flag(event: Mapping[str, Any], field: str) -> bool:
    privacy = event.get("privacy")
    return isinstance(privacy, Mapping) and privacy.get(field) is True


def _score_from_gaps(gaps: set[str]) -> int:
    return max(0, 100 - len(gaps) * 12)


def _contains_text(payload: Mapping[str, Any], needle: str) -> bool:
    lowered = needle.lower()
    for value in payload.values():
        if isinstance(value, str) and lowered in value.lower():
            return True
        if isinstance(value, list) and any(
            isinstance(item, str) and lowered in item.lower() for item in value
        ):
            return True
    return False


def _repair_findings(
    events: list[Mapping[str, Any]],
    tier: TierReadiness,
    security: AgenticSecurityReadiness,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    readiness_findings: list[dict[str, Any]] = []
    for gap in tier.pro_gaps:
        readiness_findings.append(
            {
                "finding_id": f"tier.pro.{gap}",
                "severity": "warning",
                "target_surface": "pro_readiness",
                "message": f"Pro readiness is missing {gap}.",
                "evidence_event_ids": [],
                "suggested_tests": ["validate strict reporting sample"],
            }
        )
    for gap in tier.basic_gaps:
        readiness_findings.append(
            {
                "finding_id": f"tier.basic.{gap}",
                "severity": "warning",
                "target_surface": "basic_stats",
                "message": f"Basic statistics are missing {gap}.",
                "evidence_event_ids": [],
                "suggested_tests": ["run reporting-readiness"],
            }
        )
    for gap in security.gaps:
        readiness_findings.append(
            {
                "finding_id": f"security.{gap}",
                "severity": "warning",
                "target_surface": "agentic_security",
                "message": f"Security readiness is missing {gap}.",
                "evidence_event_ids": [],
                "suggested_tests": ["add mocked tool or memory safety event"],
            }
        )
    mistake_events = [
        event
        for event in events
        if isinstance(event.get("payload"), Mapping)
        and event["payload"].get("mistake_family")
    ]
    for event in mistake_events:
        payload = event["payload"]
        findings.append(
            {
                "finding_id": f"mistake.{payload.get('mistake_family')}",
                "severity": payload.get("severity", "warning"),
                "target_surface": payload.get("component", "agent"),
                "message": f"Recorded mistake family {payload.get('mistake_family')}.",
                "evidence_event_ids": [event.get("event_id")],
                "suggested_tests": ["add or rerun a replay case for this finding"],
            }
        )
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event_id = event.get("event_id")
        event_type = event.get("event_type")
        if event_type == "opportunity.candidate.reviewed" and payload.get(
            "review_status"
        ) in {
            "model_omitted",
            "optimizer_skipped",
            "not_in_candidate_set",
            "excluded_stale",
            "excluded_source_quality",
            "excluded_data_quality",
        }:
            findings.append(
                {
                    "finding_id": f"opportunity.{payload.get('review_status')}",
                    "severity": "warning",
                    "target_surface": "opportunity_review",
                    "message": (
                        f"Candidate review recorded {payload.get('review_status')}."
                    ),
                    "evidence_event_ids": [event_id],
                    "suggested_tests": [
                        "add a replay proving candidates are reviewed or excluded"
                    ],
                }
            )
        if event_type == "action.outcome.recorded" and payload.get("status") in {
            "skipped",
            "clipped",
            "rejected",
            "deferred",
        }:
            findings.append(
                {
                    "finding_id": f"action.{payload.get('status')}",
                    "severity": "warning",
                    "target_surface": "action_outcome",
                    "message": f"Action outcome recorded {payload.get('status')}.",
                    "evidence_event_ids": [event_id],
                    "suggested_tests": ["add a capacity and skip-reason regression"],
                }
            )
        if event_type == "evaluation.epoch.member.completed" and payload.get(
            "state"
        ) in {"failed", "skipped", "timeout", "schema_failed", "not_runnable"}:
            findings.append(
                {
                    "finding_id": f"evaluation.{payload.get('state')}",
                    "severity": "warning",
                    "target_surface": "evaluation_epoch",
                    "message": (
                        "Evaluation member recorded "
                        f"{payload.get('state')} in an expected epoch."
                    ),
                    "evidence_event_ids": [event_id],
                    "suggested_tests": ["add a fair-epoch coverage regression"],
                }
            )
    return [*findings, *readiness_findings]


def _repair_prompt(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return (
            "Review this agent-tracker event set. Required telemetry is present. "
            "Preserve trading behavior and add regression tests for any change."
        )
    lines = [
        "Use these Agent Tracker findings to improve telemetry and tests.",
        "",
        "Rules:",
        "- Preserve trading behavior.",
        "- Do not add broker execution.",
        "- Do not weaken risk gates.",
        "- Do not include secrets, account IDs, broker payloads, raw prompts, "
        "or raw outputs.",
        "",
        "Findings:",
    ]
    for finding in findings:
        evidence = ", ".join(str(item) for item in finding["evidence_event_ids"])
        evidence_text = f" Evidence: {evidence}." if evidence else ""
        lines.append(f"- {finding['finding_id']}: {finding['message']}{evidence_text}")
    lines.extend(
        [
            "",
            "Add focused tests, run Agent Tracker validation, and keep uploads mocked.",
        ]
    )
    return "\n".join(lines)


def _expected_invariant(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type", ""))
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    if event_type == "risk.check.completed" and payload.get("approved") is False:
        return "risk_block_preserved"
    if event_type == "trade.rejected":
        return "rejected_trade_remains_rejected"
    if event_type == "error.recorded":
        return "error_path_has_safe_terminal_state"
    if event_type == "replay.result.recorded":
        return "replay_suite_passes_or_reports_failures"
    if event_type == "decision.outcome.recorded":
        return "decision_outcome_links_to_evidence"
    if event_type == "opportunity.candidate.reviewed":
        return "candidate_review_reason_preserved"
    if event_type == "action.outcome.recorded":
        return "skipped_or_clipped_action_reason_preserved"
    if event_type == "evaluation.epoch.member.completed":
        return "evaluation_epoch_coverage_preserved"
    return "agent_behavior_preserved"


def _contains_guarantee_language(payload: Mapping[str, Any]) -> bool:
    phrases = {
        "guaranteed profit",
        "risk-free",
        "risk free",
        "cannot lose",
        "can't lose",
        "sure profit",
        "guaranteed return",
    }
    for value in payload.values():
        if isinstance(value, str):
            lowered = value.lower()
            if any(phrase in lowered for phrase in phrases):
                return True
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and any(
                    phrase in item.lower() for phrase in phrases
                ):
                    return True
    return False


def _experiment_axes(
    findings: list[Any],
    declared_changes: Mapping[str, Any],
) -> list[str]:
    axes = {
        str(key).split("_", maxsplit=1)[0]
        for key, value in declared_changes.items()
        if value is not None and key != "multi_axis_reason"
    }
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        text = " ".join(
            str(finding.get(field, ""))
            for field in ("finding_id", "target_surface", "message")
        ).lower()
        if "prompt" in text:
            axes.add("prompt")
        if "risk" in text:
            axes.add("risk_gate")
        if "source" in text:
            axes.add("source_pipeline")
        if "memory" in text:
            axes.add("memory_policy")
        if "tool" in text or "mcp" in text:
            axes.add("tool_policy")
        if "cost" in text or "model" in text:
            axes.add("model_provider")
    return sorted(axes or {"unspecified"})
