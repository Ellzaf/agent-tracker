"""Privacy and redaction helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_tracker.constants import REDACTION_VERSION
from agent_tracker.errors import RedactionError
from agent_tracker.serialization import hash_text, to_jsonable

REDACTION_TEXT = "[REDACTED]"

SECRET_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "password",
    "secret",
    "secret_key",
    "token",
    "webhook_secret",
}

PROMPT_TEXT_KEYS = {
    "prompt",
    "prompt_text",
    "raw_prompt",
    "input",
    "input_text",
    "messages",
}

OUTPUT_TEXT_KEYS = {
    "completion",
    "model_output",
    "output",
    "output_text",
    "raw_output",
    "response",
}

BROKER_PAYLOAD_KEYS = {
    "broker_payload",
    "raw_broker_payload",
    "alpaca_payload",
    "order_payload",
}

ACCOUNT_KEY_HINTS = ("account", "account_id", "account_number", "broker_account")

SECRET_PATTERNS = [
    re.compile(r"\bellzaf_trk_[A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,;]{6,}"),
    re.compile(r"\bsk-(?:live|test|proj)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk_(?:live|test|proj)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\b(?:acct|account|broker)[_-]?[A-Za-z0-9]{8,}\b"),
    re.compile(r"\b(?:order|ord)_[A-Za-z0-9]{10,}\b"),
    re.compile(r"(?:/Users|/home|/var/folders)/[^\s\"']+"),
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
]


@dataclass(slots=True)
class RedactionResult:
    value: Any
    privacy: dict[str, Any]


def redact_event(event: Mapping[str, Any], *, store_full_io: bool) -> RedactionResult:
    privacy = {
        "full_io": store_full_io,
        "redaction_version": REDACTION_VERSION,
        "contains_prompt_text": False,
        "contains_output_text": False,
        "contains_broker_payload": False,
        "contains_account_identifier": False,
        "truncated": bool(event.get("privacy", {}).get("truncated", False))
        if isinstance(event.get("privacy"), Mapping)
        else False,
    }
    redacted = _redact_value(to_jsonable(dict(event)), privacy, store_full_io)
    if _contains_forbidden(redacted):
        raise RedactionError("forbidden material remains after redaction")
    if not isinstance(redacted, dict):
        raise RedactionError("event redaction produced a non-object")
    redacted["privacy"] = privacy
    return RedactionResult(value=redacted, privacy=privacy)


def redact_payload(
    payload: Mapping[str, Any], *, store_full_io: bool
) -> RedactionResult:
    privacy = {
        "full_io": store_full_io,
        "redaction_version": REDACTION_VERSION,
        "contains_prompt_text": False,
        "contains_output_text": False,
        "contains_broker_payload": False,
        "contains_account_identifier": False,
        "truncated": False,
    }
    redacted = _redact_value(to_jsonable(dict(payload)), privacy, store_full_io)
    if _contains_forbidden(redacted):
        raise RedactionError("forbidden material remains after redaction")
    return RedactionResult(value=redacted, privacy=privacy)


def _redact_value(value: Any, privacy: dict[str, Any], store_full_io: bool) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered in SECRET_KEY_NAMES:
                result[key] = REDACTION_TEXT
                continue
            if any(
                hint == lowered or lowered.endswith(f"_{hint}")
                for hint in ACCOUNT_KEY_HINTS
            ):
                privacy["contains_account_identifier"] = True
                result[key] = _hash_or_redact(raw_item)
                continue
            if lowered in BROKER_PAYLOAD_KEYS:
                privacy["contains_broker_payload"] = True
                result[key] = _hash_or_redact(raw_item)
                continue
            if lowered in PROMPT_TEXT_KEYS:
                result[key] = _handle_io_value(
                    raw_item, store_full_io, "prompt", privacy
                )
                continue
            if lowered in OUTPUT_TEXT_KEYS:
                result[key] = _handle_io_value(
                    raw_item, store_full_io, "output", privacy
                )
                continue
            result[key] = _redact_value(raw_item, privacy, store_full_io)
        return result
    if isinstance(value, list):
        return [_redact_value(item, privacy, store_full_io) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _handle_io_value(
    value: Any,
    store_full_io: bool,
    kind: str,
    privacy: dict[str, Any],
) -> Any:
    if _is_redacted_hash(value):
        return value
    if store_full_io:
        if kind == "prompt":
            privacy["contains_prompt_text"] = True
        else:
            privacy["contains_output_text"] = True
        return _redact_value(value, privacy, store_full_io)

    text = _stable_text(value)
    return {
        "sha256": hash_text(text),
        "chars": len(text),
        "redacted": True,
    }


def _hash_or_redact(value: Any) -> dict[str, Any]:
    if _is_redacted_hash(value):
        return dict(value)
    text = _stable_text(value)
    return {
        "sha256": hash_text(text),
        "chars": len(text),
        "redacted": True,
    }


def _stable_text(value: Any) -> str:
    jsonable = to_jsonable(value)
    return jsonable if isinstance(jsonable, str) else repr(jsonable)


def _redact_string(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(REDACTION_TEXT, result)
    return result


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    return False


def _is_redacted_hash(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("redacted") is True
        and isinstance(value.get("sha256"), str)
        and str(value["sha256"]).startswith("sha256:")
    )
