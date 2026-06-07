"""Reporting-grade validation and readiness checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ellzaf_agent.errors import SchemaValidationError

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
        _require_enum(payload, "side", ORDER_SIDES)
        _require_number(payload, "intended_quantity", minimum=Decimal("0"))
        _require_optional_number(payload, "intended_price", minimum=Decimal("0"))
    elif event_type == "decision.outcome.recorded":
        _require_enum(payload, "outcome_kind", OUTCOME_KINDS)
        _require_optional_bool(payload, "followed_plan")
        _require_optional_bool(payload, "changed_by_risk_gate")
        _require_optional_bool(payload, "changed_by_operator")
    elif event_type == "position.snapshot.recorded":
        _require_number(payload, "quantity")
        _require_optional_number(payload, "average_price", minimum=Decimal("0"))
        _require_optional_number(payload, "market_price", minimum=Decimal("0"))
        _require_optional_number(payload, "market_value")
    elif event_type == "capital.flow.recorded":
        _require_enum(payload, "flow_kind", FLOW_KINDS)
        _require_number(payload, "amount")
        _require_bool(payload, "included_in_trading_pnl")
        _require_date(payload, "session_date")
    elif event_type == "performance.snapshot.recorded":
        _require_date(payload, "session_date")
        _require_date(payload, "period_start")
        _require_date(payload, "period_end")
        _require_optional_number(payload, "trading_pnl_amount")
        _require_optional_number(payload, "flow_adjusted_equity_change")
        _require_optional_number(payload, "return_base")
        _require_optional_number(payload, "compounded_return_pct")
    elif event_type == "strategy.context.recorded":
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
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise SchemaValidationError(f"{field} must be an ISO date") from exc


def _decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SchemaValidationError(f"{field} must be numeric")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SchemaValidationError(f"{field} must be numeric") from exc


def _has_fields(payload: Mapping[str, Any], fields: set[str]) -> bool:
    return all(_present(payload.get(field)) for field in fields)


def _has_any(payload: Mapping[str, Any], fields: set[str]) -> bool:
    return any(_present(payload.get(field)) for field in fields)


def _present(value: Any) -> bool:
    return value is not None and value != ""
