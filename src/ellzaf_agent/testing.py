"""Test helpers for user integrations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ellzaf_agent.constants import DEFAULT_MAX_EVENT_BYTES
from ellzaf_agent.errors import SchemaValidationError
from ellzaf_agent.redaction import (
    ACCOUNT_KEY_HINTS,
    BROKER_PAYLOAD_KEYS,
    OUTPUT_TEXT_KEYS,
    PROMPT_TEXT_KEYS,
    SECRET_PATTERNS,
)
from ellzaf_agent.reporting import assess_reporting_readiness
from ellzaf_agent.schema import validate_event

_EBOOK_REQUIRED_EVENT_TYPES = {
    "agent.run.started",
    "agent.run.completed",
    "llm.call.started",
    "llm.call.completed",
    "tool.call.completed",
    "source.claim.recorded",
    "market.snapshot.recorded",
    "memory.read.completed",
    "decision.proposed",
    "risk.check.completed",
    "trade.rejected",
    "paper.fill.recorded",
    "portfolio.snapshot.recorded",
    "replay.result.recorded",
    "cost.usage.recorded",
    "error.recorded",
}


def assert_valid_ellzaf_events(
    events: Iterable[Mapping[str, Any]],
    *,
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
    required_event_types: set[str] | frozenset[str] | None = None,
    profile: str | None = None,
    allow_full_io: bool = False,
    require_mistake_family_for_mistakes: bool = False,
) -> None:
    normalized_profile = _normalize_profile(profile)
    event_list = list(events)
    if not event_list:
        raise AssertionError("expected at least one Ellzaf event")

    seen_event_types: set[str] = set()
    for index, event in enumerate(event_list):
        try:
            validate_event(event, max_event_bytes=max_event_bytes)
        except SchemaValidationError as exc:
            raise AssertionError(f"event {index} failed validation: {exc}") from exc
        seen_event_types.add(str(event["event_type"]))
        _assert_private(event, index=index, allow_full_io=allow_full_io)
        if require_mistake_family_for_mistakes:
            _assert_structured_mistake(event, index=index)

    required = set(required_event_types or set())
    if normalized_profile in {"ebook", "aitrade"} and required_event_types is None:
        required = set(_EBOOK_REQUIRED_EVENT_TYPES)
    missing = sorted(required - seen_event_types)
    if missing:
        raise AssertionError(
            f"missing required Ellzaf event types: {', '.join(missing)}"
        )
    if normalized_profile == "strict-reporting":
        _assert_reporting_ready(event_list)
    if normalized_profile == "strict-arena":
        _assert_reporting_ready(event_list)
        readiness = assess_reporting_readiness(event_list)
        if not readiness.can_score_arena:
            raise AssertionError("events are missing arena scoring metadata")
    if normalized_profile == "strict-proof":
        _assert_reporting_ready(event_list)
        readiness = assess_reporting_readiness(event_list)
        if not readiness.can_publish_proof:
            raise AssertionError("events are missing proof-page metadata")


def _normalize_profile(profile: str | None) -> str | None:
    return profile.replace("_", "-") if profile else None


def _assert_reporting_ready(events: list[Mapping[str, Any]]) -> None:
    readiness = assess_reporting_readiness(events)
    if not readiness.strict_reporting_ready:
        missing = ", ".join(readiness.missing_fields)
        raise AssertionError(f"events are missing reporting data: {missing}")


def _assert_private(
    event: Mapping[str, Any],
    *,
    index: int,
    allow_full_io: bool,
) -> None:
    privacy = event.get("privacy", {})
    if not isinstance(privacy, Mapping):
        raise AssertionError(f"event {index} privacy must be an object")
    if privacy.get("full_io") and not allow_full_io:
        raise AssertionError(f"event {index} stores full prompt/output text")
    _scan_for_forbidden(event, index=index, path="$")
    _scan_payload_keys(event.get("payload", {}), index=index, path="payload")


def _scan_for_forbidden(value: Any, *, index: int, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _scan_for_forbidden(item, index=index, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for offset, item in enumerate(value):
            _scan_for_forbidden(item, index=index, path=f"{path}[{offset}]")
        return
    if isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise AssertionError(
                    f"event {index} contains forbidden material at {path}"
                )


def _scan_payload_keys(value: Any, *, index: int, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in PROMPT_TEXT_KEYS | OUTPUT_TEXT_KEYS and not _is_redacted_hash(
                item
            ):
                raise AssertionError(
                    f"event {index} contains unredacted IO at {path}.{key}"
                )
            if (
                lowered in BROKER_PAYLOAD_KEYS
                or any(
                    hint == lowered or lowered.endswith(f"_{hint}")
                    for hint in ACCOUNT_KEY_HINTS
                )
            ) and not _is_redacted_hash(item):
                raise AssertionError(
                    f"event {index} contains unredacted broker/account data "
                    f"at {path}.{key}"
                )
            _scan_payload_keys(item, index=index, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for offset, item in enumerate(value):
            _scan_payload_keys(item, index=index, path=f"{path}[{offset}]")


def _is_redacted_hash(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("redacted") is True
        and isinstance(value.get("sha256"), str)
        and str(value["sha256"]).startswith("sha256:")
    )


def _assert_structured_mistake(event: Mapping[str, Any], *, index: int) -> None:
    payload = event.get("payload", {})
    if not isinstance(payload, Mapping):
        return
    structured_keys = {
        "component",
        "severity",
        "money_impact",
        "blocking_status",
        "resolution_status",
        "next_safe_action",
    }
    if (
        event.get("event_type") == "error.recorded" or structured_keys & set(payload)
    ) and "mistake_family" not in payload:
        raise AssertionError(f"event {index} is missing payload.mistake_family")
