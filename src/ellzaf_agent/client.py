"""Public Ellzaf Agent client API."""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import Callable, Mapping
from typing import Any, ParamSpec, TypeVar

from ellzaf_agent.config import Config
from ellzaf_agent.events import build_event, new_run, new_span
from ellzaf_agent.ids import Sequence
from ellzaf_agent.queue import LocalQueue, QueueHealth
from ellzaf_agent.redaction import redact_event
from ellzaf_agent.schema import validate_event
from ellzaf_agent.serialization import hash_text
from ellzaf_agent.upload import BatchUploader, FlushSummary, Transport

P = ParamSpec("P")
R = TypeVar("R")


class Ellzaf:
    """Telemetry client for self-built AI trading agents."""

    def __init__(
        self,
        config: Config,
        *,
        queue: LocalQueue | None = None,
        uploader: BatchUploader | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self._sequence = Sequence()
        self.queue = queue
        if self.queue is None and config.queue_dir is not None:
            self.queue = LocalQueue(
                config.queue_dir, max_queue_bytes=config.max_queue_bytes
            )
        self.uploader = uploader or BatchUploader(config, transport=transport)

    @classmethod
    def from_env(
        cls,
        *,
        project: str | None = None,
        environment: str | None = None,
        agent_id: str | None = None,
    ) -> Ellzaf:
        return cls(
            Config.from_env(project=project, environment=environment, agent_id=agent_id)
        )

    def event(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        payload: Mapping[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        idempotency_key: str | None = None,
        event_id: str | None = None,
        occurred_at: str | None = None,
        store_full_io: bool | None = None,
    ) -> dict[str, Any]:
        resolved_run_id = run_id or new_run()
        normalized_payload = _payload_with_defaults(event_type, payload)
        event = build_event(
            self.config,
            event_type=event_type,
            run_id=resolved_run_id,
            sequence=self._sequence.next(),
            payload=normalized_payload,
            symbols=symbols,
            span_id=span_id,
            parent_span_id=parent_span_id,
            idempotency_key=idempotency_key,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        redacted = redact_event(
            event,
            store_full_io=self.config.store_full_io
            if store_full_io is None
            else store_full_io,
        ).value
        validate_event(redacted, max_event_bytes=self.config.max_event_bytes)
        if self.config.telemetry_enabled and self.queue is not None:
            self.queue.enqueue(redacted)
        return redacted

    def run(
        self,
        *,
        run_type: str,
        symbols: list[str] | tuple[str, ...] | None = None,
        trigger: str | None = None,
        profile: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        store_full_io: bool | None = None,
    ) -> Run:
        return Run(
            self,
            run_type=run_type,
            symbols=symbols,
            trigger=trigger,
            profile=profile,
            metadata=metadata,
            store_full_io=store_full_io,
        )

    def arun(self, **kwargs: Any) -> Run:
        return self.run(**kwargs)

    def trace(
        self,
        *,
        run_type: str,
        symbols: list[str] | tuple[str, ...] | None = None,
        metadata: Mapping[str, Any] | None = None,
        store_full_io: bool | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                    call_metadata = {
                        "function": func.__name__,
                        "input_hash": hash_text(repr((args, kwargs))),
                    }
                    merged_metadata = {**dict(metadata or {}), **call_metadata}
                    async with self.arun(
                        run_type=run_type,
                        symbols=symbols,
                        metadata=merged_metadata,
                        store_full_io=store_full_io,
                    ):
                        return await func(*args, **kwargs)

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                call_metadata = {
                    "function": func.__name__,
                    "input_hash": hash_text(repr((args, kwargs))),
                }
                merged_metadata = {**dict(metadata or {}), **call_metadata}
                with self.run(
                    run_type=run_type,
                    symbols=symbols,
                    metadata=merged_metadata,
                    store_full_io=store_full_io,
                ):
                    return func(*args, **kwargs)

            return wrapper

        return decorator

    def flush(self) -> FlushSummary:
        if self.queue is None:
            return FlushSummary(0, 0, 0, 0, 0, skipped=True)
        try:
            return self.uploader.flush(self.queue)
        except Exception:
            pending = self.queue.health().pending
            return FlushSummary(
                attempted=pending,
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=pending,
            )

    async def aflush(self) -> FlushSummary:
        return await asyncio.to_thread(self.flush)

    def queue_health(self) -> QueueHealth | None:
        if self.queue is None:
            return None
        return self.queue.health()


class Run:
    """Run-scoped telemetry context."""

    def __init__(
        self,
        client: Ellzaf,
        *,
        run_type: str,
        symbols: list[str] | tuple[str, ...] | None,
        trigger: str | None,
        profile: str | None,
        metadata: Mapping[str, Any] | None,
        store_full_io: bool | None,
    ) -> None:
        self.client = client
        self.run_type = run_type
        self.symbols = symbols
        self.trigger = trigger
        self.profile = profile
        self.metadata = dict(metadata or {})
        self.store_full_io = store_full_io
        self.run_id = new_run()
        self.span_id = new_span()
        self._completed = False
        self._started_at = 0.0

    def __enter__(self) -> Run:
        self._started_at = time.monotonic()
        self.event(
            "agent.run.started",
            payload={
                "run_type": self.run_type,
                "trigger": self.trigger,
                "profile": self.profile,
                "metadata": self.metadata,
            },
        )
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, _tb: Any
    ) -> bool:
        try:
            if exc is not None:
                self.error(
                    error_kind=exc_type.__name__ if exc_type else "exception",
                    message=str(exc) or repr(exc),
                    retryable=False,
                    component="harness",
                )
                self.complete(
                    status="failed",
                    final_action="error",
                    final_reason=type(exc).__name__,
                )
            elif not self._completed:
                self.complete(status="succeeded")
        except Exception:
            if exc is None:
                raise
        return False

    async def __aenter__(self) -> Run:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        return self.__exit__(exc_type, exc, tb)

    def event(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        return self.client.event(
            event_type,
            run_id=self.run_id,
            symbols=symbols or self.symbols,
            payload=payload,
            span_id=span_id or self.span_id,
            parent_span_id=parent_span_id,
            occurred_at=occurred_at,
            store_full_io=self.store_full_io,
        )

    def complete(
        self,
        *,
        status: str = "succeeded",
        final_action: str | None = None,
        final_reason: str | None = None,
        error_count: int = 0,
        warning_count: int = 0,
        **extra: Any,
    ) -> dict[str, Any] | None:
        if self._completed:
            return None
        self._completed = True
        duration_ms = (
            int((time.monotonic() - self._started_at) * 1000) if self._started_at else 0
        )
        payload = {
            "run_type": self.run_type,
            "status": status,
            "duration_ms": duration_ms,
            "final_action": final_action,
            "final_reason": final_reason,
            "error_count": error_count,
            "warning_count": warning_count,
            **extra,
        }
        return self.event("agent.run.completed", payload=payload)

    def prompt_version(
        self, *, family: str, version: str, prompt_hash: str, **extra: Any
    ) -> dict[str, Any]:
        return self.event(
            "llm.call.started",
            payload={
                "run_type": self.run_type,
                "provider": extra.pop("provider", "unknown"),
                "model": extra.pop("model", "unknown"),
                "prompt_family": family,
                "prompt_version": version,
                "prompt_hash": prompt_hash,
                **extra,
            },
        )

    def llm_call(
        self, *, provider: str, model: str, status: str = "succeeded", **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "llm.call.completed",
            payload={"provider": provider, "model": model, "status": status, **payload},
        )

    def tool_call(
        self, *, tool_name: str, status: str, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "tool.call.completed",
            payload={"tool_name": tool_name, "status": status, **payload},
        )

    def source_claim(self, *, claim_type: str, **payload: Any) -> dict[str, Any]:
        return self.event(
            "source.claim.recorded",
            payload={"claim_type": claim_type, **payload},
            symbols=[payload["symbol"]] if "symbol" in payload else self.symbols,
        )

    def market_snapshot(self, *, source: str, **payload: Any) -> dict[str, Any]:
        return self.event(
            "market.snapshot.recorded",
            payload={"source": source, **payload},
        )

    def memory_read(
        self, *, memory_kind: str, purpose: str, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "memory.read.completed",
            payload={"memory_kind": memory_kind, "purpose": purpose, **payload},
        )

    def decision_proposed(
        self, *, decision_kind: str, action: str, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "decision.proposed",
            payload={"decision_kind": decision_kind, "action": action, **payload},
            symbols=[payload["symbol"]] if "symbol" in payload else self.symbols,
        )

    def order_intent(
        self,
        *,
        order_intent_id: str,
        decision_id: str,
        symbol: str,
        side: str,
        intended_quantity: Any,
        intended_price: Any | None = None,
        currency: str | None = "USD",
        open_close_effect: str | None = "unknown",
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "order.intent.recorded",
            payload=_compact(
                {
                    "order_intent_id": order_intent_id,
                    "decision_id": decision_id,
                    "symbol": symbol,
                    "side": side,
                    "intended_quantity": intended_quantity,
                    "intended_price": intended_price,
                    "currency": currency,
                    "open_close_effect": open_close_effect,
                    **payload,
                }
            ),
            symbols=[symbol],
        )

    def decision_outcome(
        self,
        *,
        decision_id: str,
        outcome_kind: str,
        outcome_reason: str | None = None,
        linked_event_ids: list[str] | tuple[str, ...] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "decision.outcome.recorded",
            payload=_compact(
                {
                    "decision_id": decision_id,
                    "outcome_kind": outcome_kind,
                    "outcome_reason": outcome_reason,
                    "linked_event_ids": list(linked_event_ids or []),
                    **payload,
                }
            ),
            symbols=[payload["symbol"]] if "symbol" in payload else self.symbols,
        )

    def risk_check(
        self, *, risk_check_kind: str = "deterministic", approved: bool, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "risk.check.completed",
            payload={
                "risk_check_kind": risk_check_kind,
                "approved": approved,
                **payload,
            },
        )

    def trade_rejected(
        self, *, rejected_by: str, reason_code: str, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "trade.rejected",
            payload={"rejected_by": rejected_by, "reason_code": reason_code, **payload},
            symbols=[payload["symbol"]] if "symbol" in payload else self.symbols,
        )

    def paper_fill(
        self,
        *,
        symbol: str,
        side: str,
        fill_id: str | None = None,
        position_id: str | None = None,
        order_intent_id: str | None = None,
        open_close_effect: str | None = None,
        quantity: Any | None = None,
        price: Any | None = None,
        fees: Any | None = None,
        currency: str | None = None,
        fill_source: str | None = None,
        session_date: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        if quantity is None and "qty" in payload:
            quantity = payload["qty"]
        return self.event(
            "paper.fill.recorded",
            payload=_compact(
                {
                    "symbol": symbol,
                    "side": side,
                    "fill_id": fill_id,
                    "position_id": position_id,
                    "order_intent_id": order_intent_id,
                    "open_close_effect": open_close_effect,
                    "quantity": quantity,
                    "price": price,
                    "fees": fees,
                    "currency": currency,
                    "fill_source": fill_source,
                    "session_date": session_date,
                    **payload,
                }
            ),
            symbols=[symbol],
        )

    def portfolio_snapshot(
        self, *, portfolio_kind: str, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "portfolio.snapshot.recorded",
            payload={"portfolio_kind": portfolio_kind, **payload},
        )

    def position_snapshot(
        self,
        *,
        portfolio_kind: str,
        position_id: str,
        symbol: str,
        quantity: Any,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "position.snapshot.recorded",
            payload={
                "portfolio_kind": portfolio_kind,
                "position_id": position_id,
                "symbol": symbol,
                "quantity": quantity,
                **payload,
            },
            symbols=[symbol],
        )

    def capital_flow(
        self,
        *,
        capital_flow_id: str,
        flow_kind: str,
        amount: Any,
        asset: str,
        currency: str,
        session_date: str,
        included_in_trading_pnl: bool = False,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "capital.flow.recorded",
            payload={
                "capital_flow_id": capital_flow_id,
                "flow_kind": flow_kind,
                "amount": amount,
                "asset": asset,
                "currency": currency,
                "session_date": session_date,
                "included_in_trading_pnl": included_in_trading_pnl,
                **payload,
            },
        )

    def performance_snapshot(
        self,
        *,
        period_kind: str,
        period_start: str,
        period_end: str,
        session_date: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "performance.snapshot.recorded",
            payload={
                "period_kind": period_kind,
                "period_start": period_start,
                "period_end": period_end,
                "session_date": session_date,
                **payload,
            },
        )

    def replay_result(
        self, *, suite_name: str, status: str, case_count: int, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "replay.result.recorded",
            payload={
                "suite_name": suite_name,
                "status": status,
                "case_count": case_count,
                **payload,
            },
        )

    def agent_build(
        self,
        *,
        build_id: str,
        config_hash: str,
        risk_gate_version: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "agent.build.recorded",
            payload={
                "build_id": build_id,
                "config_hash": config_hash,
                "risk_gate_version": risk_gate_version,
                **payload,
            },
        )

    def strategy_context(
        self,
        *,
        strategy_id: str,
        strategy_name: str | None = None,
        setup: str | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.event(
            "strategy.context.recorded",
            payload=_compact(
                {
                    "strategy_id": strategy_id,
                    "strategy_name": strategy_name,
                    "setup": setup,
                    **payload,
                }
            ),
            symbols=symbols or self.symbols,
        )

    def cost_usage(
        self, *, provider: str, usage_kind: str, quantity: int, **payload: Any
    ) -> dict[str, Any]:
        return self.event(
            "cost.usage.recorded",
            payload={
                "provider": provider,
                "usage_kind": usage_kind,
                "quantity": quantity,
                **payload,
            },
        )

    def error(self, *, error_kind: str, message: str, **payload: Any) -> dict[str, Any]:
        return self.event(
            "error.recorded",
            payload={"error_kind": error_kind, "message": message, **payload},
        )

    def final_action(
        self, *, action: str, reason: str | None = None, **payload: Any
    ) -> dict[str, Any] | None:
        return self.complete(final_action=action, final_reason=reason, **payload)


def _payload_with_defaults(
    event_type: str,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = dict(payload or {})
    if event_type == "risk.check.completed":
        result.setdefault("risk_check_kind", "deterministic")
    if event_type == "llm.call.completed":
        result.setdefault("status", "succeeded")
    if event_type == "tool.call.completed":
        result.setdefault("status", "succeeded")
    return result


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
