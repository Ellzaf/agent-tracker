"""Local event validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ellzaf_agent.constants import (
    COMPLETION_STATUSES,
    SCHEMA_VERSION,
    SUPPORTED_ENVIRONMENTS,
    SUPPORTED_EVENT_TYPES,
)
from ellzaf_agent.errors import SchemaValidationError
from ellzaf_agent.serialization import strict_json_dumps
from ellzaf_agent.taxonomy import validate_taxonomy_fields

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

EVENT_REQUIRED_PAYLOAD_FIELDS: dict[str, set[str]] = {
    "agent.run.started": {"run_type"},
    "agent.run.completed": {"run_type", "status"},
    "llm.call.started": {"provider", "model"},
    "llm.call.completed": {"provider", "model", "status"},
    "tool.call.completed": {"tool_name", "status"},
    "source.claim.recorded": {"claim_type"},
    "market.snapshot.recorded": {"source"},
    "memory.read.completed": {"memory_kind", "purpose"},
    "decision.proposed": {"decision_kind", "action"},
    "risk.check.completed": {"risk_check_kind", "approved"},
    "trade.rejected": {"rejected_by", "reason_code"},
    "paper.fill.recorded": {"symbol", "side"},
    "portfolio.snapshot.recorded": {"portfolio_kind"},
    "replay.result.recorded": {"suite_name", "status", "case_count"},
    "cost.usage.recorded": {"provider", "usage_kind", "quantity"},
    "error.recorded": {"error_kind", "message"},
}


def normalize_symbols(symbols: list[str] | tuple[str, ...] | None) -> list[str]:
    if not symbols:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        item = str(symbol).strip().upper()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def validate_event(event: Mapping[str, Any], *, max_event_bytes: int) -> None:
    required_fields = {
        "schema_version",
        "event_id",
        "idempotency_key",
        "project",
        "agent_id",
        "run_id",
        "event_type",
        "occurred_at",
        "environment",
        "symbols",
        "payload",
        "privacy",
        "sdk",
    }
    missing = sorted(required_fields - set(event))
    if missing:
        raise SchemaValidationError(f"missing event fields: {', '.join(missing)}")

    if event["schema_version"] != SCHEMA_VERSION:
        raise SchemaValidationError("unsupported schema_version")
    if not _safe_prefixed_string(event["event_id"], prefix="evt_"):
        raise SchemaValidationError("event_id must be a non-empty evt_ string")
    if not _nonempty_string(event["idempotency_key"]):
        raise SchemaValidationError("idempotency_key must be non-empty")
    if any(char.isspace() for char in event["idempotency_key"]):
        raise SchemaValidationError("idempotency_key must not contain whitespace")
    if not _nonempty_string(event["project"]):
        raise SchemaValidationError("project must be non-empty")
    if _contains_forbidden_identifier_char(event["project"]):
        raise SchemaValidationError("project contains unsupported characters")
    if not _nonempty_string(event["agent_id"]):
        raise SchemaValidationError("agent_id must be non-empty")
    if _contains_forbidden_identifier_char(event["agent_id"]):
        raise SchemaValidationError("agent_id contains unsupported characters")
    if not _safe_prefixed_string(event["run_id"], prefix="run_"):
        raise SchemaValidationError("run_id must be a non-empty run_ string")

    event_type = event["event_type"]
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise SchemaValidationError(f"unsupported event_type: {event_type}")
    if event["environment"] not in SUPPORTED_ENVIRONMENTS:
        raise SchemaValidationError(f"unsupported environment: {event['environment']}")
    _validate_timestamp(event["occurred_at"])

    if not isinstance(event["symbols"], list):
        raise SchemaValidationError("symbols must be a list")
    if not all(isinstance(item, str) and item for item in event["symbols"]):
        raise SchemaValidationError("symbols must contain non-empty strings")
    if len(event["symbols"]) != len(set(event["symbols"])):
        raise SchemaValidationError("symbols must be unique")

    payload = event["payload"]
    if not isinstance(payload, dict):
        raise SchemaValidationError("payload must be an object")
    _validate_payload(event_type, payload)
    validate_taxonomy_fields(payload)

    if not isinstance(event["privacy"], dict):
        raise SchemaValidationError("privacy must be an object")
    for field in (
        "full_io",
        "redaction_version",
        "contains_prompt_text",
        "contains_output_text",
        "contains_broker_payload",
        "contains_account_identifier",
        "truncated",
    ):
        if field not in event["privacy"]:
            raise SchemaValidationError(f"privacy.{field} is required")

    if not isinstance(event["sdk"], dict):
        raise SchemaValidationError("sdk must be an object")
    for field in ("name", "version", "language"):
        if not _nonempty_string(event["sdk"].get(field)):
            raise SchemaValidationError(f"sdk.{field} is required")

    size = len(strict_json_dumps(event).encode("utf-8"))
    if size > max_event_bytes:
        raise SchemaValidationError(f"event exceeds max_event_bytes: {size}")


def _validate_payload(event_type: str, payload: dict[str, Any]) -> None:
    missing = sorted(EVENT_REQUIRED_PAYLOAD_FIELDS[event_type] - set(payload))
    if missing:
        raise SchemaValidationError(
            f"payload missing required fields for {event_type}: {', '.join(missing)}"
        )

    if (
        event_type == "agent.run.completed"
        and payload["status"] not in COMPLETION_STATUSES
    ):
        raise SchemaValidationError("agent.run.completed status is unsupported")
    if event_type == "risk.check.completed":
        if not isinstance(payload.get("approved"), bool):
            raise SchemaValidationError("risk.check.completed approved must be boolean")
        if payload.get("approved") is False and not payload.get("reasons"):
            raise SchemaValidationError("rejected risk checks must include reasons")
    if event_type == "replay.result.recorded":
        for field in ("case_count",):
            if not isinstance(payload.get(field), int) or payload[field] < 0:
                raise SchemaValidationError(f"{field} must be a non-negative integer")


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise SchemaValidationError("occurred_at must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchemaValidationError("occurred_at must be parseable") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None:
        raise SchemaValidationError("occurred_at must include a UTC offset")
    if offset != timedelta(0):
        raise SchemaValidationError("occurred_at must be UTC")


def _nonempty_string(value: Any, *, prefix: str | None = None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return prefix is None or value.startswith(prefix)


def _safe_prefixed_string(value: Any, *, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(value) > len(prefix)
        and bool(_SAFE_ID_RE.fullmatch(value))
    )


def _contains_forbidden_identifier_char(value: str) -> bool:
    return any(char.isspace() or char in {"/", "\\", "\x00"} for char in value)
