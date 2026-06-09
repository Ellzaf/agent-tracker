from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tracker.constants import DEFAULT_MAX_EVENT_BYTES, SUPPORTED_EVENT_TYPES
from agent_tracker.errors import SchemaValidationError
from agent_tracker.resources import list_resource_names, read_json_resource
from agent_tracker.schema import validate_event
from agent_tracker.taxonomy import (
    allowed_mistake_families,
    taxonomy_values,
    validate_mistake_family,
)
from agent_tracker.testing import assert_valid_agent_tracker_events


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
        assert schema["$id"].startswith("https://ellzaf.com/schemas/agent-tracker/")


def test_taxonomies_are_package_data_and_have_expected_escape_hatch() -> None:
    families = allowed_mistake_families()

    assert "source.truncated_or_missing_evidence" in families
    assert "portfolio.buying_power_as_cash" in families
    assert "pnl.deposit_as_profit" in families
    assert "shadow.unfair_cadence" in families
    assert "market.numeric_domain_confused" in families
    assert "entry.profile_persistence_missing" in families
    assert "opportunity.candidate_limit_hidden" in families
    assert "release.fresh_run_missing" in families
    assert "risk_gate" in taxonomy_values("component")
    assert "decision_flow" in taxonomy_values("component")
    assert "data_contract" in taxonomy_values("component")

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
    assert_valid_agent_tracker_events(
        fixtures,
        require_mistake_family_for_mistakes=True,
    )


def test_reporting_fixtures_pass_strict_reporting_profile() -> None:
    fixtures = [
        read_json_resource("schemas", "fixtures", "reporting", name)
        for name in list_resource_names("schemas", "fixtures", "reporting")
    ]

    assert len(fixtures) >= 10
    for event in fixtures:
        validate_event(event, max_event_bytes=DEFAULT_MAX_EVENT_BYTES)
    assert_valid_agent_tracker_events(fixtures, profile="strict-reporting")


def test_batch_contract_fixtures_are_package_data_and_validated() -> None:
    valid_batches = [
        read_json_resource("schemas", "fixtures", "batches", "valid", name)
        for name in list_resource_names("schemas", "fixtures", "batches", "valid")
    ]
    invalid_batches = [
        read_json_resource("schemas", "fixtures", "batches", "invalid", name)
        for name in list_resource_names("schemas", "fixtures", "batches", "invalid")
    ]

    assert len(valid_batches) == 1
    assert len(invalid_batches) == 2
    for batch in valid_batches:
        assert batch["batch_id"].startswith("batch_")
        assert batch["events"]
        assert_valid_agent_tracker_events(batch["events"])


def test_upload_response_contract_fixtures_are_package_data() -> None:
    responses = {
        name: read_json_resource("schemas", "fixtures", "upload-responses", name)
        for name in list_resource_names("schemas", "fixtures", "upload-responses")
    }

    assert {
        "accepted.json",
        "partial-permanent-rejection.json",
        "retryable-rejection.json",
        "invalid-count-mismatch.json",
    } <= set(responses)
    for name, response in responses.items():
        assert set(response) == {"accepted", "duplicates", "rejected"}
        assert isinstance(response["rejected"], list)
        if name != "invalid-count-mismatch.json":
            total = (
                response["accepted"]
                + response["duplicates"]
                + len(response["rejected"])
            )
            assert total == 1


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
        assert_valid_agent_tracker_events(
            [event],
            require_mistake_family_for_mistakes=True,
        )


def test_json_files_are_parseable_from_installed_package() -> None:
    root = Path(__file__).parents[1] / "src" / "agent_tracker" / "schemas"
    for path in root.rglob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))


def test_json_schemas_validate_with_jsonschema_when_available() -> None:
    jsonschema = pytest.importorskip("jsonschema")

    envelope = read_json_resource("schemas", "event-envelope.schema.json")
    validator = jsonschema.Draft202012Validator(
        envelope,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    jsonschema.Draft202012Validator.check_schema(envelope)
    jsonschema.Draft202012Validator.check_schema(
        read_json_resource("schemas", "batch.schema.json")
    )
    for name in list_resource_names("schemas", "event-types"):
        jsonschema.Draft202012Validator.check_schema(
            read_json_resource("schemas", "event-types", name)
        )
    for name in list_resource_names("schemas", "fixtures", "valid"):
        validator.validate(read_json_resource("schemas", "fixtures", "valid", name))
    for name in list_resource_names("schemas", "fixtures", "reporting"):
        validator.validate(read_json_resource("schemas", "fixtures", "reporting", name))
    batch_schema = read_json_resource("schemas", "batch.schema.json")
    batch_schema["properties"]["events"]["items"] = envelope
    batch_validator = jsonschema.Draft202012Validator(
        batch_schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    for name in list_resource_names("schemas", "fixtures", "batches", "valid"):
        batch_validator.validate(
            read_json_resource("schemas", "fixtures", "batches", "valid", name)
        )
    for name in list_resource_names("schemas", "fixtures", "batches", "invalid"):
        with pytest.raises(jsonschema.ValidationError):
            batch_validator.validate(
                read_json_resource("schemas", "fixtures", "batches", "invalid", name)
            )

    valid_event = read_json_resource(
        "schemas", "fixtures", "valid", "cash-only-risk-block.json"
    )
    for field, value in [
        ("event_id", "evt_"),
        ("run_id", "run_"),
        ("idempotency_key", "bad key"),
        ("project", "paper/agent"),
        ("project", "paper agent"),
        ("agent_id", "agent\\one"),
    ]:
        invalid_event = dict(valid_event)
        invalid_event[field] = value
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(invalid_event)
