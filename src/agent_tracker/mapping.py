"""Declarative event mapping for custom Python trading agents."""

from __future__ import annotations

import csv
import sqlite3
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_tracker.client import AgentTracker
from agent_tracker.config import Config
from agent_tracker.redaction import REDACTION_TEXT, SECRET_PATTERNS
from agent_tracker.serialization import strict_json_loads
from agent_tracker.sink import JsonlSink


class MappingError(ValueError):
    """Raised when a mapping configuration is invalid."""


@dataclass(frozen=True, slots=True)
class MappingExportSummary:
    exported: int
    skipped: int
    warnings: tuple[str, ...]
    output: str
    source_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exported": self.exported,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "output": self.output,
            "source_counts": dict(self.source_counts),
        }


def export_mapped_events(
    config_path: str | Path,
    output: str | Path,
) -> MappingExportSummary:
    config = _load_config(Path(config_path))
    root = Path(config_path).resolve().parent
    events, warnings, skipped, source_counts = events_from_mapping_config(
        config, root=root
    )
    JsonlSink(output, append=False).write_many(events)
    return MappingExportSummary(
        exported=len(events),
        skipped=skipped,
        warnings=tuple(warnings),
        output=str(output),
        source_counts=source_counts,
    )


def events_from_mapping_config(
    config: Mapping[str, Any],
    *,
    root: str | Path = ".",
) -> tuple[list[dict[str, Any]], list[str], int, dict[str, int]]:
    root_path = Path(root).resolve()
    project = _required_text(config, "project")
    agent_id = str(config.get("agent_id") or "custom-agent")
    environment = str(config.get("environment") or "paper")
    client = AgentTracker(
        Config(
            project=project,
            agent_id=agent_id,
            environment=environment,
            queue_dir=None,
            telemetry_enabled=False,
            max_event_bytes=int(config.get("max_event_bytes") or 200_000),
        )
    )
    sources = config.get("sources")
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        raise MappingError("config.sources must be an array")

    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_counts: dict[str, int] = {}
    skipped = 0
    for source_index, raw_source in enumerate(sources):
        if not isinstance(raw_source, Mapping):
            raise MappingError(f"sources[{source_index}] must be an object")
        source = dict(raw_source)
        label = _source_label(source, source_index=source_index)
        rows = _read_rows(source, root=root_path)
        source_counts.setdefault(label, 0)
        for row_index, row in enumerate(rows, start=1):
            try:
                events.append(_event_from_row(client, source, row))
                source_counts[label] += 1
            except Exception as exc:
                skipped += 1
                warnings.append(f"{label} row {row_index}: {type(exc).__name__}")
    return events, warnings, skipped, source_counts


def _load_config(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix == ".json":
        value = strict_json_loads(raw.decode("utf-8"))
    elif path.suffix == ".toml":
        value = tomllib.loads(raw.decode("utf-8"))
    else:
        raise MappingError("mapping config must be .toml or .json")
    if not isinstance(value, dict):
        raise MappingError("mapping config must be an object")
    return value


def _read_rows(source: Mapping[str, Any], *, root: Path) -> list[Mapping[str, Any]]:
    kind = _required_text(source, "kind")
    if kind == "jsonl":
        path = _source_path(source, root=root, key="path")
        return [
            _ensure_row(strict_json_loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if kind == "json_array":
        path = _source_path(source, root=root, key="path")
        value = strict_json_loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise MappingError("json_array source must contain an array")
        return [_ensure_row(item) for item in value]
    if kind == "csv":
        path = _source_path(source, root=root, key="path")
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if kind == "sqlite_query":
        return _read_sqlite_rows(source, root=root)
    raise MappingError(f"unsupported source kind: {kind}")


def _event_from_row(
    client: AgentTracker,
    source: Mapping[str, Any],
    row: Mapping[str, Any],
) -> dict[str, Any]:
    event_type = _required_text(source, "event_type")
    fields = source.get("fields")
    if not isinstance(fields, Mapping):
        raise MappingError("source.fields must be an object")
    payload = _payload_from_row(source, fields, row)
    run_id = _source_or_field(source, row, "run_id", "run_id_field")
    occurred_at = _source_or_field(source, row, "occurred_at", "occurred_at_field")
    event_id = _source_or_field(source, row, "event_id", "event_id_field")
    idempotency_key = _source_or_field(
        source, row, "idempotency_key", "idempotency_key_field"
    )
    symbols = _symbols_from_source(source, row, payload)
    return client.event(
        event_type,
        run_id=run_id,
        symbols=symbols,
        payload=payload,
        occurred_at=occurred_at,
        event_id=event_id,
        idempotency_key=idempotency_key,
    )


def _symbols_from_source(
    source: Mapping[str, Any],
    row: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> list[str] | None:
    if source.get("symbols"):
        raw_symbols = source["symbols"]
    elif source.get("symbols_field"):
        raw_symbols = _field(row, str(source["symbols_field"]))
    else:
        raw_symbols = payload.get("symbol")
    if raw_symbols is None:
        return None
    if isinstance(raw_symbols, list):
        return [str(item) for item in raw_symbols if str(item).strip()]
    return [item.strip() for item in str(raw_symbols).split(",") if item.strip()]


def _source_or_field(
    source: Mapping[str, Any],
    row: Mapping[str, Any],
    value_key: str,
    field_key: str,
) -> str | None:
    if source.get(value_key) is not None:
        return str(source[value_key])
    if source.get(field_key) is not None:
        value = _field(row, str(source[field_key]))
        return None if value is None else str(value)
    return None


def _field(row: Mapping[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise MappingError(f"missing field: {path}")
        value = value[part]
    return value


def _normalize_payload_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped[0] in {"[", "{"}:
            try:
                decoded = strict_json_loads(stripped)
            except ValueError:
                decoded = None
            else:
                return _normalize_payload_value(key, decoded)
        lowered = stripped.lower()
        if key in _BOOLEAN_FIELDS:
            if lowered in {"1", "true", "yes", "y"}:
                return True
            if lowered in {"0", "false", "no", "n"}:
                return False
        if key in {"reasons", "scenario_tags", "linked_event_ids"}:
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return stripped
    if isinstance(value, list):
        return [_normalize_payload_value(key, item) for item in value]
    if isinstance(value, Mapping):
        return {str(item_key): item_value for item_key, item_value in value.items()}
    return value


def _required_text(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{key} is required")
    return value


def _ensure_row(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MappingError("row must be an object")
    return value


def _source_path(source: Mapping[str, Any], *, root: Path, key: str) -> Path:
    raw_path = _required_text(source, key)
    path = Path(raw_path)
    if path.is_absolute():
        raise MappingError(f"{key} must be relative to the mapping config")
    resolved = (root / path).resolve()
    if not _is_relative_to(resolved, root):
        raise MappingError(f"{key} must stay inside the mapping config directory")
    if not resolved.is_file():
        raise MappingError(f"source path not found: {resolved.name}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_sqlite_rows(
    source: Mapping[str, Any],
    *,
    root: Path,
) -> list[Mapping[str, Any]]:
    database_path = _source_path(source, root=root, key="database_path")
    query = _required_text(source, "query").strip()
    if not query.lower().startswith(("select ", "with ")):
        raise MappingError(
            "sqlite_query.query must be a read-only SELECT or WITH query"
        )
    if ";" in query.rstrip(";"):
        raise MappingError("sqlite_query.query must contain one statement")
    params = source.get("params") or []
    if not isinstance(params, Sequence) or isinstance(params, str | bytes):
        raise MappingError("sqlite_query.params must be an array when provided")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(query, tuple(params))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _payload_from_row(
    source: Mapping[str, Any],
    fields: Mapping[str, Any],
    row: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    defaults = source.get("payload_defaults") or source.get("defaults") or {}
    if defaults:
        if not isinstance(defaults, Mapping):
            raise MappingError("source.payload_defaults must be an object")
        payload.update({str(key): value for key, value in defaults.items()})
    for target, spec in fields.items():
        field = _field_spec(spec)
        key = str(target)
        try:
            raw_value = (
                _field(row, field.path) if field.path is not None else field.default
            )
        except MappingError:
            if field.default is not _MISSING:
                raw_value = field.default
            elif field.required:
                raise
            else:
                continue
        value = _coerce_value(key, raw_value, field.type)
        if value is None and field.required:
            raise MappingError(f"missing field: {field.path or key}")
        if value is not None:
            payload[key] = value
    return payload


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    path: str | None
    default: Any
    required: bool
    type: str | None


class _Missing:
    pass


_MISSING = _Missing()


def _field_spec(spec: Any) -> _FieldSpec:
    if isinstance(spec, str):
        return _FieldSpec(path=spec, default=_MISSING, required=True, type=None)
    if not isinstance(spec, Mapping):
        raise MappingError("source.fields entries must be strings or objects")
    path = spec.get("path")
    if path is not None and not isinstance(path, str):
        raise MappingError("field.path must be a string")
    if path is None and "default" not in spec:
        raise MappingError("field object must include path or default")
    required = spec.get("required", True)
    if not isinstance(required, bool):
        raise MappingError("field.required must be boolean")
    value_type = spec.get("type")
    if value_type is not None and value_type not in _FIELD_TYPES:
        raise MappingError(f"unsupported field.type: {value_type}")
    if value_type is not None and not isinstance(value_type, str):
        raise MappingError("field.type must be a string")
    return _FieldSpec(
        path=path,
        default=spec.get("default", _MISSING),
        required=required,
        type=value_type,
    )


def _coerce_value(key: str, value: Any, value_type: str | None) -> Any:
    if value is _MISSING:
        return None
    if value_type is None:
        return _normalize_payload_value(key, value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    if value_type == "text":
        return str(value)
    if value_type == "bool":
        return _coerce_bool(value)
    if value_type == "int":
        return _coerce_int(value)
    if value_type == "float":
        return _coerce_float(value)
    if value_type == "list":
        return _coerce_list(value)
    if value_type == "json":
        return strict_json_loads(value) if isinstance(value, str) else value
    if value_type == "number_string":
        return str(value)
    raise MappingError(f"unsupported field.type: {value_type}")


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise MappingError("boolean field must be true or false")


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        raise MappingError("integer field must not be boolean")
    try:
        return int(str(value))
    except ValueError as exc:
        raise MappingError("integer field must be numeric") from exc


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        raise MappingError("float field must not be boolean")
    try:
        return float(str(value))
    except ValueError as exc:
        raise MappingError("float field must be numeric") from exc


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        decoded = strict_json_loads(value)
        if isinstance(decoded, list):
            return decoded
        raise MappingError("list field JSON must decode to an array")
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _source_label(source: Mapping[str, Any], *, source_index: int) -> str:
    if isinstance(source.get("name"), str) and str(source["name"]).strip():
        raw_label = str(source["name"])
    elif isinstance(source.get("path"), str):
        raw_label = Path(str(source["path"])).name
    elif isinstance(source.get("database_path"), str):
        raw_label = Path(str(source["database_path"])).name
    else:
        raw_label = f"source_{source_index}"
    return _sanitize_warning_text(raw_label)[:120] or f"source_{source_index}"


def _sanitize_warning_text(value: str) -> str:
    sanitized = value
    for pattern in SECRET_PATTERNS:
        sanitized = pattern.sub(REDACTION_TEXT, sanitized)
    return sanitized


_BOOLEAN_FIELDS = {
    "approved",
    "changed_by_operator",
    "changed_by_risk_gate",
    "followed_plan",
    "included_in_trading_pnl",
    "tool_allowed",
}

_FIELD_TYPES = {"text", "bool", "int", "float", "list", "json", "number_string"}
