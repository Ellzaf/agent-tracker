"""Normalized Ellzaf telemetry taxonomies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from typing import Any

from ellzaf_agent.errors import SchemaValidationError
from ellzaf_agent.resources import read_json_resource

_CUSTOM_FAMILY_RE = re.compile(r"^custom\.[a-z0-9][a-z0-9_.-]{0,95}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_COMPONENT_FIELDS = {
    "component": "components",
    "severity": "severity",
    "money_impact": "money_impact",
    "blocking_status": "blocking_status",
    "resolution_status": "resolution_status",
    "next_safe_action": "next_safe_action",
    "coverage_status": "coverage_status",
}


@cache
def mistake_family_taxonomy() -> dict[str, Any]:
    return read_json_resource("schemas", "taxonomies", "mistake-families.json")


@cache
def component_taxonomy() -> dict[str, Any]:
    return read_json_resource("schemas", "taxonomies", "component-taxonomy.json")


@cache
def privacy_redaction_rules() -> dict[str, Any]:
    return read_json_resource("schemas", "taxonomies", "privacy-redaction-rules.json")


@cache
def allowed_mistake_families() -> frozenset[str]:
    values: set[str] = set()
    for family in mistake_family_taxonomy()["families"]:
        values.update(str(item) for item in family["values"])
    return frozenset(values)


def taxonomy_values(field: str) -> frozenset[str]:
    key = _COMPONENT_FIELDS.get(field, field)
    values = component_taxonomy().get(key)
    if not isinstance(values, list):
        raise KeyError(field)
    return frozenset(str(item) for item in values)


def is_custom_mistake_family(value: str) -> bool:
    return bool(_CUSTOM_FAMILY_RE.fullmatch(value))


def validate_mistake_family(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise SchemaValidationError("payload.mistake_family must be a non-empty string")
    if value in allowed_mistake_families() or is_custom_mistake_family(value):
        return
    raise SchemaValidationError(f"unsupported mistake_family: {value}")


def validate_taxonomy_fields(payload: Mapping[str, Any]) -> None:
    errors: list[str] = []

    if "mistake_family" in payload:
        try:
            validate_mistake_family(payload["mistake_family"])
        except SchemaValidationError as exc:
            errors.append(str(exc))

    for field, taxonomy_key in _COMPONENT_FIELDS.items():
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or not value:
            errors.append(f"payload.{field} must be a non-empty string")
            continue
        if value not in taxonomy_values(taxonomy_key):
            errors.append(f"unsupported {field}: {value}")

    for field in ("evidence_refs", "correlation_ids"):
        if field in payload and not _is_reference_container(payload[field]):
            errors.append(f"payload.{field} must be an object, list, or string")

    if "account_scope_hash" in payload:
        value = payload["account_scope_hash"]
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            errors.append("payload.account_scope_hash must be a sha256 hash")

    if errors:
        raise SchemaValidationError("invalid taxonomy fields: " + "; ".join(errors))


def _is_reference_container(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and key for key in value)
    if isinstance(value, list):
        return all(_is_reference_container(item) for item in value)
    return False
