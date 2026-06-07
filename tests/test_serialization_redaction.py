from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from math import inf, nan
from pathlib import Path

import pytest

from ellzaf_agent.redaction import REDACTION_TEXT, redact_payload
from ellzaf_agent.serialization import strict_json_dumps, to_jsonable


class Side(Enum):
    BUY = "buy"


@dataclass
class Demo:
    amount: Decimal


def test_to_jsonable_handles_common_edge_values() -> None:
    value = {
        "decimal": Decimal("1.23"),
        "datetime": datetime(2026, 6, 7, 14, 30, tzinfo=UTC),
        "date": datetime(2026, 6, 7, tzinfo=UTC).date(),
        "enum": Side.BUY,
        "set": {"b", "a"},
        "bytes": b"abc",
        "nan": nan,
        "inf": inf,
        "path": Path("/tmp/example"),
        "dataclass": Demo(amount=Decimal("4.56")),
    }

    jsonable = to_jsonable(value)
    assert jsonable["decimal"] == "1.23"
    assert jsonable["datetime"] == "2026-06-07T14:30:00Z"
    assert jsonable["enum"] == "buy"
    assert jsonable["set"] == ["a", "b"]
    assert jsonable["bytes"]["redacted"] is True
    assert jsonable["bytes"]["byte_count"] == 3
    assert "data" not in jsonable["bytes"]
    assert jsonable["nan"] is None
    assert jsonable["inf"] is None
    assert strict_json_dumps(jsonable)


def test_redaction_hashes_prompt_and_output_by_default() -> None:
    result = redact_payload(
        {
            "prompt": "explain this with api_key=super-secret-value",
            "output": "answer",
        },
        store_full_io=False,
    )

    assert result.value["prompt"]["redacted"] is True
    assert result.value["output"]["redacted"] is True
    assert result.privacy["contains_prompt_text"] is False
    assert result.privacy["contains_output_text"] is False


def test_redaction_allows_full_io_but_still_scrubs_secrets() -> None:
    result = redact_payload(
        {"prompt": "Authorization: Bearer abcdefghijklmnop"},
        store_full_io=True,
    )

    assert REDACTION_TEXT in result.value["prompt"]
    assert result.privacy["contains_prompt_text"] is True


def test_redaction_scrubs_account_and_broker_payloads() -> None:
    result = redact_payload(
        {
            "broker_payload": {"order_id": "ord_1234567890ABCDEF"},
            "account_number": "acct_1234567890ABCDEF",
        },
        store_full_io=True,
    )

    assert result.value["broker_payload"]["redacted"] is True
    assert result.value["account_number"]["redacted"] is True
    assert result.privacy["contains_broker_payload"] is True
    assert result.privacy["contains_account_identifier"] is True


def test_redaction_preserves_existing_redacted_hashes() -> None:
    existing = {
        "sha256": "sha256:" + "0" * 64,
        "chars": 120,
        "redacted": True,
    }

    result = redact_payload(
        {
            "prompt": existing,
            "account_id": existing,
            "broker_payload": existing,
        },
        store_full_io=False,
    )

    assert result.value["prompt"] == existing
    assert result.value["account_id"] == existing
    assert result.value["broker_payload"] == existing


@pytest.mark.parametrize(
    "secret",
    [
        "api_key=abcdef1234567890",
        "Authorization: Bearer abcdefghijklmnop",
        "sk-live123456789012345",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "/home/user/private/project/file.py",
        r"C:\\Users\\name\\secret.txt",
    ],
)
def test_redaction_secret_matrix(secret: str) -> None:
    result = redact_payload({"message": f"tool failed: {secret}"}, store_full_io=True)
    assert secret not in result.value["message"]
    assert REDACTION_TEXT in result.value["message"]
