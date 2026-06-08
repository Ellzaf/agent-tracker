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
