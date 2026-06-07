"""Strict JSON serialization helpers."""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any


def hash_text(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return f"sha256:{sha256(data).hexdigest()}"


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def to_jsonable(value: Any) -> Any:
    """Convert common Python values into strict JSON-compatible values."""

    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, bytes):
        return {
            "encoding": "bytes",
            "byte_count": len(value),
            "sha256": hash_text(value),
            "redacted": True,
        }
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return [to_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, tuple | list):
        return [to_jsonable(item) for item in value]
    return repr(value)


def strict_json_dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def strict_json_loads(value: str) -> Any:
    return json.loads(value)
