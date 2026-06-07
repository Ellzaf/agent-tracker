from __future__ import annotations

import json
from pathlib import Path

import pytest

from ellzaf_agent.constants import DEFAULT_MAX_EVENT_BYTES, SUPPORTED_EVENT_TYPES
from ellzaf_agent.errors import SchemaValidationError
from ellzaf_agent.resources import list_resource_names, read_json_resource
from ellzaf_agent.schema import validate_event
from ellzaf_agent.taxonomy import (
    allowed_mistake_families,
    taxonomy_values,
    validate_mistake_family,
)
from ellzaf_agent.testing import assert_valid_ellzaf_events


def test_public_schema_files_exist_for_every_event_type() -> None:
    names = set(list_resource_names("schemas", "event-types"))

    assert names == {
        f"{event_type}.schema.json" for event_type in SUPPORTED_EVENT_TYPES
    }

    for name in names | {"event-envelope.schema.json", "batch.schema.json"}:
        if name.endswith(".schema.json") and name in names:
            schema = read_json_resource("schemas", "event-types", name)
        else:
            schema = read_json_resource("schemas", name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("https://ellzaf.com/schemas/ellzaf-agent/")


def test_taxonomies_are_package_data_and_have_expected_escape_hatch() -> None:
    families = allowed_mistake_families()

    assert "source.truncated_or_missing_evidence" in families
    assert "portfolio.buying_power_as_cash" in families
    assert "pnl.deposit_as_profit" in families
    assert "shadow.unfair_cadence" in families
    assert "risk_gate" in taxonomy_values("component")

    validate_mistake_family("custom.local_failure")
    with pytest.raises(SchemaValidationError):
        validate_mistake_family("source.not_real")


def test_valid_fixtures_pass_runtime_and_privacy_validation() -> None:
    fixtures = [
        read_json_resource("schemas", "fixtures", "valid", name)
        for name in list_resource_names("schemas", "fixtures", "valid")
    ]

    assert len(fixtures) == 5
    for event in fixtures:
        validate_event(event, max_event_bytes=DEFAULT_MAX_EVENT_BYTES)
    assert_valid_ellzaf_events(
        fixtures,
        require_mistake_family_for_mistakes=True,
    )


@pytest.mark.parametrize(
    "name",
    list_resource_names("schemas", "fixtures", "invalid"),
)
def test_invalid_fixtures_fail_for_the_expected_contract_reason(name: str) -> None:
    event = read_json_resource("schemas", "fixtures", "invalid", name)

    if name == "unsupported-mistake-family.json":
        with pytest.raises(SchemaValidationError):
            validate_event(event, max_event_bytes=DEFAULT_MAX_EVENT_BYTES)
        return

    with pytest.raises(AssertionError):
        assert_valid_ellzaf_events(
            [event],
            require_mistake_family_for_mistakes=True,
        )


def test_json_files_are_parseable_from_installed_package() -> None:
    root = Path(__file__).parents[1] / "src" / "ellzaf_agent" / "schemas"
    for path in root.rglob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))
