"""Event construction helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_tracker.config import Config
from agent_tracker.constants import (
    SCHEMA_VERSION,
    SDK_LANGUAGE,
    SDK_NAME,
    SDK_VERSION,
)
from agent_tracker.ids import new_event_id, new_run_id, new_span_id
from agent_tracker.schema import normalize_symbols
from agent_tracker.serialization import to_jsonable, utc_now_iso


def new_run() -> str:
    return new_run_id()


def new_span() -> str:
    return new_span_id()


def build_event(
    config: Config,
    *,
    event_type: str,
    run_id: str,
    sequence: int,
    payload: Mapping[str, Any] | None = None,
    symbols: list[str] | tuple[str, ...] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    event_id: str | None = None,
    idempotency_key: str | None = None,
    occurred_at: str | None = None,
    privacy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_event_id = event_id or new_event_id()
    resolved_idempotency_key = idempotency_key or (
        f"{config.project}/{config.agent_id}/{run_id}/{event_type}/{sequence:08d}"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": resolved_event_id,
        "idempotency_key": resolved_idempotency_key,
        "project": config.project,
        "agent_id": config.agent_id,
        "run_id": run_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "event_type": event_type,
        "occurred_at": occurred_at or utc_now_iso(),
        "received_at": None,
        "environment": config.environment,
        "symbols": normalize_symbols(symbols),
        "payload": to_jsonable(dict(payload or {})),
        "privacy": dict(privacy or {}),
        "sdk": {
            "name": SDK_NAME,
            "version": SDK_VERSION,
            "language": SDK_LANGUAGE,
        },
    }
