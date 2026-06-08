"""Reporting-grade validation and readiness checks."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from agent_tracker.errors import SchemaValidationError

REPORTING_EVENT_TYPES = {
    "agent.build.recorded",
    "order.intent.recorded",
    "decision.outcome.recorded",
    "position.snapshot.recorded",
    "capital.flow.recorded",
    "performance.snapshot.recorded",
    "strategy.context.recorded",
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
_STRICT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPORTING_IDENTITY_FIELDS = {
    "asset",
    "build_id",
    "capital_flow_id",
    "config_hash",
    "currency",
    "decision_id",
    "fill_id",
    "order_intent_id",
    "period_kind",
    "portfolio_kind",
    "position_id",
    "risk_gate_version",
    "strategy_id",
    "symbol",
}
_NON_NEGATIVE_EXTENSION_NUMBER_FIELDS = {
    "average_price",
    "case_count",
    "drawdown_pct",
    "fees",
    "freshness_seconds",
    "holding_period_seconds",
    "intended_price",
    "intended_quantity",
    "market_price",
    "max_drawdown_pct",
    "planned_reward_risk",
    "price",
    "return_base",
    "source_confidence",
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
    "trading_pnl_amount",
    "trading_pnl_pct",
    "unrealized_pnl",
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


def validate_reporting_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    """Validate event-specific reporting fields when a reporting event is used."""

    if event_type not in REPORTING_REQUIRED_PAYLOAD_FIELDS:
        _validate_reporting_extensions(event_type, payload)
        return

    missing = sorted(REPORTING_REQUIRED_PAYLOAD_FIELDS[event_type] - set(payload))
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
    if "session_date" in payload:
        _require_date(payload, "session_date")
    if "linked_event_ids" in payload:
        linked = payload["linked_event_ids"]
        if not isinstance(linked, list) or not all(
            isinstance(item, str) and item.startswith("evt_") for item in linked
        ):
            raise SchemaValidationError("linked_event_ids must contain event IDs")
    if _event_type == "paper.fill.recorded":
        _require_optional_number(payload, "quantity", minimum=Decimal("0"))
    for field in _REPORTING_IDENTITY_FIELDS:
        if field in payload and payload[field] is not None:
            _require_nonempty(payload, field)
    for field in _NON_NEGATIVE_EXTENSION_NUMBER_FIELDS:
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
) -> None:
    value = _decimal(payload.get(field), field=field)
    if minimum is not None and value < minimum:
        raise SchemaValidationError(f"{field} must be >= {minimum}")


def _require_optional_number(
    payload: Mapping[str, Any],
    field: str,
    *,
    minimum: Decimal | None = None,
) -> None:
    if field in payload and payload[field] is not None:
        _require_number(payload, field, minimum=minimum)


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
    for gap in tier.pro_gaps:
        findings.append(
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
        findings.append(
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
        findings.append(
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
    return findings


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
    return "agent_behavior_preserved"
