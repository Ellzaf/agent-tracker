"""Public Ellzaf Agent Tracker client API."""

from __future__ import annotations

import asyncio
import atexit
import functools
import hashlib
import inspect
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, ParamSpec, TypeVar

from agent_tracker.config import Config
from agent_tracker.events import build_event, new_run, new_span
from agent_tracker.ids import Sequence
from agent_tracker.queue import LocalQueue, QueueHealth
from agent_tracker.redaction import redact_event
from agent_tracker.schema import validate_event
from agent_tracker.serialization import hash_text
from agent_tracker.upload import BatchUploader, FlushSummary, Transport

P = ParamSpec("P")
R = TypeVar("R")
SymbolsValue = list[str] | tuple[str, ...] | None
SymbolsExtractor = Callable[[tuple[Any, ...], Mapping[str, Any], Any], SymbolsValue]
SymbolsInput = SymbolsValue | SymbolsExtractor
MetadataInput = (
    Mapping[str, Any]
    | Callable[[tuple[Any, ...], Mapping[str, Any], Any], Mapping[str, Any] | None]
    | None
)
ResultHook = Callable[["Run", Any], None]
ExceptionHook = Callable[["Run", BaseException], None]


class AgentTracker:
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
        self._events_by_run: dict[str, int] = {}
        self._events_today = 0
        self._event_day: date | None = None
        self._budget_warning_emitted = False
        self._flush_lock = threading.RLock()
        self._background_stop = threading.Event()
        self._background_thread: threading.Thread | None = None
        self._atexit_registered = False

    @classmethod
    def from_env(
        cls,
        *,
        project: str | None = None,
        environment: str | None = None,
        agent_id: str | None = None,
    ) -> AgentTracker:
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
        _bypass_budget: bool = False,
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
        should_enqueue = (
            self.config.telemetry_enabled
            and self.queue is not None
            and (
                _bypass_budget
                or (
                    self._should_capture(redacted)
                    and self._within_event_budgets(redacted)
                )
            )
        )
        if should_enqueue:
            enqueue_result = self.queue.enqueue_result(
                redacted,
                dedupe_idempotency_key=self.config.dedupe_idempotency_keys,
            )
            if not enqueue_result.duplicate:
                self._record_captured_event(redacted)
        elif (
            self.config.telemetry_enabled
            and self.queue is not None
            and not _bypass_budget
            and not self._budget_warning_emitted
            and self._budget_exhausted(redacted)
        ):
            self._budget_warning_emitted = True
            self.event(
                "error.recorded",
                run_id=resolved_run_id,
                payload={
                    "error_kind": "telemetry_budget_exhausted",
                    "message": "local telemetry event budget exhausted",
                    "component": "cost",
                    "severity": "warning",
                    "resolution_status": "open",
                    "next_safe_action": "observe",
                    "dropped_event_type": event_type,
                },
                _bypass_budget=True,
            )
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
        symbols: SymbolsInput = None,
        metadata: MetadataInput = None,
        store_full_io: bool | None = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
        on_result: ResultHook | None = None,
        on_exception: ExceptionHook | None = None,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        if flush_mode not in {"sync", "thread", "never"}:
            raise ValueError("flush_mode must be 'sync', 'thread', or 'never'")

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                    merged_metadata = _trace_metadata(
                        func.__name__, args, kwargs, metadata
                    )
                    resolved_symbols = _trace_symbols(args, kwargs, symbols)
                    try:
                        async with self.arun(
                            run_type=run_type,
                            symbols=resolved_symbols,
                            metadata=merged_metadata,
                            store_full_io=store_full_io,
                        ) as active_run:
                            try:
                                result = await func(*args, **kwargs)
                            except BaseException as exc:
                                _run_exception_hook(active_run, on_exception, exc)
                                raise
                            _run_result_hook(active_run, on_result, result)
                            return result
                    finally:
                        if flush_after:
                            await self._flush_after_async(flush_mode)

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                merged_metadata = _trace_metadata(func.__name__, args, kwargs, metadata)
                resolved_symbols = _trace_symbols(args, kwargs, symbols)
                try:
                    with self.run(
                        run_type=run_type,
                        symbols=resolved_symbols,
                        metadata=merged_metadata,
                        store_full_io=store_full_io,
                    ) as active_run:
                        try:
                            result = func(*args, **kwargs)
                        except BaseException as exc:
                            _run_exception_hook(active_run, on_exception, exc)
                            raise
                        _run_result_hook(active_run, on_result, result)
                        return result
                finally:
                    if flush_after:
                        self._flush_after(flush_mode)

            return wrapper

        return decorator

    def wrap_agent(
        self,
        agent: Any,
        *,
        methods: list[str] | tuple[str, ...],
        run_type: str,
        symbols: SymbolsInput = None,
        metadata: MetadataInput = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Any:
        originals = _agent_tracker_originals(agent)
        for method_name in methods:
            method = getattr(agent, method_name)
            if getattr(method, "_agent_tracker_wrapped", False):
                raise ValueError(f"{method_name} is already wrapped")
            wrapped = self.trace(
                run_type=run_type,
                symbols=symbols,
                metadata=metadata,
                flush_after=flush_after,
                flush_mode=flush_mode,
            )(method)
            wrapped._agent_tracker_wrapped = True  # type: ignore[attr-defined]
            originals.setdefault(method_name, method)
            setattr(agent, method_name, wrapped)
        return agent

    def wrap_tool_call(
        self,
        func: Callable[P, R],
        *,
        tool_name: Any,
        status: Any = "succeeded",
        run_type: str = "tool_call",
        symbols: SymbolsInput = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            run.tool_call(
                tool_name=str(_resolve_result_value(tool_name, result)),
                status=str(_resolve_result_value(status, result)),
            )

        return self.trace(
            run_type=run_type,
            symbols=symbols,
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    def wrap_llm_call(
        self,
        func: Callable[P, R],
        *,
        provider: Any,
        model: Any,
        status: Any = "succeeded",
        run_type: str = "llm_call",
        symbols: SymbolsInput = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            run.llm_call(
                provider=str(_resolve_result_value(provider, result)),
                model=str(_resolve_result_value(model, result)),
                status=str(_resolve_result_value(status, result)),
            )

        return self.trace(
            run_type=run_type,
            symbols=symbols,
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    def wrap_risk_gate(
        self,
        func: Callable[P, R],
        *,
        approved: Any,
        reasons: Any = None,
        risk_check_kind: str = "deterministic",
        run_type: str = "risk_gate",
        symbols: SymbolsInput = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            resolved_approved = bool(_resolve_result_value(approved, result))
            payload: dict[str, Any] = {}
            resolved_reasons = _resolve_result_value(reasons, result)
            if resolved_reasons is not None:
                payload["reasons"] = list(resolved_reasons)
            run.risk_check(
                risk_check_kind=risk_check_kind,
                approved=resolved_approved,
                **payload,
            )

        return self.trace(
            run_type=run_type,
            symbols=symbols,
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    def wrap_decision(
        self,
        func: Callable[P, R],
        *,
        decision_kind: Any,
        action: Any,
        symbol: Any = None,
        run_type: str = "decision",
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            payload: dict[str, Any] = {}
            resolved_symbol = _resolve_result_value(symbol, result)
            if resolved_symbol is not None:
                payload["symbol"] = str(resolved_symbol)
            run.decision_proposed(
                decision_kind=str(_resolve_result_value(decision_kind, result)),
                action=str(_resolve_result_value(action, result)),
                **payload,
            )

        return self.trace(
            run_type=run_type,
            symbols=lambda _args, _kwargs, result: _symbol_list(
                _resolve_result_value(symbol, result)
            ),
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    def wrap_paper_broker(
        self,
        func: Callable[P, R],
        *,
        symbol: Any,
        side: Any,
        quantity: Any = None,
        price: Any = None,
        fill_id: Any = None,
        run_type: str = "paper_fill",
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            run.paper_fill(
                symbol=str(_resolve_result_value(symbol, result)),
                side=str(_resolve_result_value(side, result)),
                quantity=_resolve_result_value(quantity, result),
                price=_resolve_result_value(price, result),
                fill_id=_resolve_result_value(fill_id, result),
            )

        return self.trace(
            run_type=run_type,
            symbols=lambda _args, _kwargs, result: _symbol_list(
                _resolve_result_value(symbol, result)
            ),
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    def wrap_replay_suite(
        self,
        func: Callable[P, R],
        *,
        suite_name: Any,
        status: Any,
        case_count: Any,
        run_type: str = "replay",
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Callable[P, R]:
        def record(run: Run, result: Any) -> None:
            run.replay_result(
                suite_name=str(_resolve_result_value(suite_name, result)),
                status=str(_resolve_result_value(status, result)),
                case_count=int(_resolve_result_value(case_count, result)),
            )

        return self.trace(
            run_type=run_type,
            on_result=record,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )(func)

    @contextmanager
    def instrument(
        self,
        agent: Any,
        *,
        methods: list[str] | tuple[str, ...],
        run_type: str,
        symbols: SymbolsInput = None,
        metadata: MetadataInput = None,
        flush_after: bool = False,
        flush_mode: str = "sync",
    ) -> Any:
        before = dict(_agent_tracker_originals(agent))
        self.wrap_agent(
            agent,
            methods=methods,
            run_type=run_type,
            symbols=symbols,
            metadata=metadata,
            flush_after=flush_after,
            flush_mode=flush_mode,
        )
        try:
            yield agent
        finally:
            originals = _agent_tracker_originals(agent)
            for name in methods:
                original = originals.get(name)
                if original is not None:
                    setattr(agent, name, original)
            originals.clear()
            originals.update(before)

    def flush(
        self,
        *,
        dry_run: bool = False,
        raise_on_error: bool = False,
    ) -> FlushSummary:
        if self.queue is None:
            return FlushSummary(
                0,
                0,
                0,
                0,
                0,
                skipped=True,
                status="skipped",
                reason_code="queue_disabled",
                message="local queue is disabled",
                dry_run=dry_run,
            )
        try:
            with self._flush_lock:
                return self.uploader.flush(
                    self.queue, dry_run=dry_run, raise_on_error=raise_on_error
                )
        except Exception as exc:
            if raise_on_error:
                raise
            pending = self.queue.health().pending
            return FlushSummary(
                attempted=pending,
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=pending,
                status="retryable_failed",
                reason_code="unexpected_flush_error",
                message=str(exc) or type(exc).__name__,
                dry_run=dry_run,
            )

    async def aflush(self) -> FlushSummary:
        return await asyncio.to_thread(self.flush)

    def flush_all(
        self,
        *,
        max_batches: int | None = None,
        dry_run: bool = False,
        raise_on_error: bool = False,
    ) -> FlushSummary:
        if self.queue is None:
            return FlushSummary(
                0,
                0,
                0,
                0,
                0,
                skipped=True,
                status="skipped",
                reason_code="queue_disabled",
                message="local queue is disabled",
                dry_run=dry_run,
            )
        try:
            with self._flush_lock:
                return self.uploader.flush_all(
                    self.queue,
                    max_batches=max_batches,
                    dry_run=dry_run,
                    raise_on_error=raise_on_error,
                )
        except Exception as exc:
            if raise_on_error:
                raise
            pending = self.queue.health().pending
            return FlushSummary(
                attempted=pending,
                accepted=0,
                duplicates=0,
                rejected=0,
                retryable=pending,
                status="retryable_failed",
                reason_code="unexpected_flush_error",
                message=str(exc) or type(exc).__name__,
                dry_run=dry_run,
            )

    async def aflush_all(
        self,
        *,
        max_batches: int | None = None,
        dry_run: bool = False,
        raise_on_error: bool = False,
    ) -> FlushSummary:
        return await asyncio.to_thread(
            self.flush_all,
            max_batches=max_batches,
            dry_run=dry_run,
            raise_on_error=raise_on_error,
        )

    def close(self, *, timeout_seconds: float | None = None) -> FlushSummary:
        self.stop_background_flush(timeout_seconds=timeout_seconds)
        return self.flush_all()

    def start_background_flush(
        self,
        *,
        interval_seconds: float | None = None,
    ) -> None:
        interval = (
            self.config.flush_interval_seconds
            if interval_seconds is None
            else interval_seconds
        )
        if interval <= 0 or self.queue is None:
            return
        if self._background_thread and self._background_thread.is_alive():
            return
        self._background_stop.clear()
        self._background_thread = threading.Thread(
            target=self._background_flush_loop,
            args=(interval,),
            name="agent-tracker-flush",
            daemon=True,
        )
        self._background_thread.start()

    def stop_background_flush(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self._background_stop.set()
        thread = self._background_thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout_seconds)

    @contextmanager
    def auto_flush(self, *, interval_seconds: float | None = None) -> Any:
        self.start_background_flush(interval_seconds=interval_seconds)
        try:
            yield self
        finally:
            self.stop_background_flush()
            self.flush_all()

    def enable_atexit_flush(self) -> None:
        if self._atexit_registered:
            return
        atexit.register(self.close)
        self._atexit_registered = True

    def queue_health(self) -> QueueHealth | None:
        if self.queue is None:
            return None
        return self.queue.health(max_batch_events=self.config.max_batch_events)

    def _should_capture(self, event: Mapping[str, Any]) -> bool:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        if event_type in {
            "agent.run.started",
            "agent.run.completed",
            "trade.rejected",
            "paper.fill.recorded",
            "position.snapshot.recorded",
            "portfolio.snapshot.recorded",
            "capital.flow.recorded",
            "performance.snapshot.recorded",
            "replay.result.recorded",
        }:
            return True
        if self.config.always_capture_errors and event_type == "error.recorded":
            return True
        if (
            self.config.always_capture_risk_blocks
            and event_type == "risk.check.completed"
            and payload_map.get("approved") is False
        ):
            return True
        if self.config.sample_rate >= 1:
            return True
        if self.config.sample_rate <= 0:
            return False
        seed = str(event.get("idempotency_key") or event.get("event_id") or "")
        value = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
        return (value / 0xFFFFFFFFFFFFFFFF) < self.config.sample_rate

    def _within_event_budgets(self, event: Mapping[str, Any]) -> bool:
        self._reset_event_day_if_needed()
        if (
            self.config.max_events_per_day is not None
            and self._events_today >= self.config.max_events_per_day
        ):
            return False
        run_id = str(event.get("run_id") or "")
        return not (
            self.config.max_events_per_run is not None
            and self._events_by_run.get(run_id, 0) >= self.config.max_events_per_run
        )

    def _budget_exhausted(self, event: Mapping[str, Any]) -> bool:
        del event
        self._reset_event_day_if_needed()
        return (
            self.config.max_events_per_day is not None
            and self._events_today >= self.config.max_events_per_day
        ) or (
            self.config.max_events_per_run is not None
            and any(
                count >= self.config.max_events_per_run
                for count in self._events_by_run.values()
            )
        )

    def _record_captured_event(self, event: Mapping[str, Any]) -> None:
        self._reset_event_day_if_needed()
        self._events_today += 1
        run_id = str(event.get("run_id") or "")
        self._events_by_run[run_id] = self._events_by_run.get(run_id, 0) + 1

    def _reset_event_day_if_needed(self) -> None:
        today = datetime.now(UTC).date()
        if self._event_day != today:
            self._event_day = today
            self._events_today = 0
            self._events_by_run.clear()
            self._budget_warning_emitted = False

    def _flush_after(self, flush_mode: str) -> None:
        if flush_mode == "never":
            return
        if flush_mode == "thread":
            threading.Thread(
                target=self.flush_all,
                name="agent-tracker-flush-once",
                daemon=True,
            ).start()
            return
        self.flush_all()

    async def _flush_after_async(self, flush_mode: str) -> None:
        if flush_mode == "never":
            return
        if flush_mode == "thread":
            self._flush_after(flush_mode)
            return
        await self.aflush_all()

    def _background_flush_loop(self, interval_seconds: float) -> None:
        while not self._background_stop.wait(interval_seconds):
            self.flush_all()


class Run:
    """Run-scoped telemetry context."""

    def __init__(
        self,
        client: AgentTracker,
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


def _trace_metadata(
    function_name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    metadata: MetadataInput,
) -> dict[str, Any]:
    call_metadata = {
        "function": function_name,
        "input_hash": hash_text(repr((args, kwargs))),
    }
    if callable(metadata):
        try:
            extracted = metadata(args, kwargs, None)
        except Exception as exc:
            return {
                **call_metadata,
                "metadata_extractor_error": type(exc).__name__,
            }
        return {**dict(extracted or {}), **call_metadata}
    return {**dict(metadata or {}), **call_metadata}


def _trace_symbols(
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    symbols: SymbolsInput,
) -> SymbolsValue:
    if callable(symbols):
        try:
            return symbols(args, kwargs, None)
        except Exception:
            return None
    return symbols


def _run_result_hook(run: Run, hook: ResultHook | None, result: Any) -> None:
    if hook is None:
        return
    try:
        hook(run, result)
    except Exception as exc:
        run.error(
            error_kind="result_hook_failed",
            message=str(exc) or type(exc).__name__,
            component="integration",
            severity="warning",
            resolution_status="open",
            next_safe_action="observe",
        )


def _run_exception_hook(
    run: Run,
    hook: ExceptionHook | None,
    original_exc: BaseException,
) -> None:
    if hook is None:
        return
    try:
        hook(run, original_exc)
    except Exception as exc:
        run.error(
            error_kind="exception_hook_failed",
            message=str(exc) or type(exc).__name__,
            component="integration",
            severity="warning",
            resolution_status="open",
            next_safe_action="observe",
        )


def _agent_tracker_originals(agent: Any) -> dict[str, Any]:
    originals = getattr(agent, "_agent_tracker_originals", None)
    if isinstance(originals, dict):
        return originals
    originals = {}
    agent._agent_tracker_originals = originals
    return originals


def _resolve_result_value(value: Any, result: Any) -> Any:
    if callable(value):
        return value(result)
    return value


def _symbol_list(value: Any) -> SymbolsValue:
    if value is None:
        return None
    return [str(value)]
