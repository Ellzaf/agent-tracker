from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agent_tracker import AgentTracker, Config
from agent_tracker.reporting import (
    ACTION_OUTCOME_STATUSES,
    ENTRY_PERMISSIONS,
    ENTRY_REGIMES,
    EVALUATION_MEMBER_STATES,
    OPPORTUNITY_REVIEW_STATUSES,
)
from agent_tracker.resources import read_json_resource
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_diagnostic_telemetry_matrix_covers_more_than_1000_edge_cases(
    tmp_path: Path,
) -> None:
    client = AgentTracker(
        Config(
            project="diagnostic-matrix",
            queue_dir=tmp_path,
            telemetry_enabled=False,
            max_event_bytes=200_000,
        )
    )
    action_statuses = sorted(ACTION_OUTCOME_STATUSES)
    member_states = sorted(EVALUATION_MEMBER_STATES)
    scenario_count = 0

    for review_status in sorted(OPPORTUNITY_REVIEW_STATUSES):
        for primary_regime in sorted(ENTRY_REGIMES):
            for entry_permission in sorted(ENTRY_PERMISSIONS):
                scenario_count += 1
                suffix = f"{scenario_count:04d}"
                run_id = f"run_diag_{suffix}"
                symbol = f"SYM{scenario_count % 997}"
                events = [
                    client.event(
                        "opportunity.board.recorded",
                        run_id=run_id,
                        payload={
                            "board_id": f"board_{suffix}",
                            "scope": "full_universe",
                            "candidate_count": str(scenario_count % 250),
                            "reviewed_count": str(scenario_count % 50),
                            "excluded_count": str(scenario_count % 25),
                            "stale_count": str(scenario_count % 10),
                        },
                    ),
                    client.event(
                        "opportunity.candidate.reviewed",
                        run_id=run_id,
                        symbols=[symbol],
                        payload={
                            "candidate_id": f"candidate_{suffix}",
                            "board_id": f"board_{suffix}",
                            "symbol": symbol,
                            "review_status": review_status,
                            "rank": str(scenario_count % 100),
                            "data_quality_score": str(scenario_count % 101),
                            "reason_codes": [f"reason_{scenario_count % 17}"],
                        },
                    ),
                    client.event(
                        "setup.profile.recorded",
                        run_id=run_id,
                        symbols=[symbol],
                        payload={
                            "setup_profile_id": f"setup_{suffix}",
                            "symbol": symbol,
                            "primary_regime": primary_regime,
                            "entry_permission": entry_permission,
                            "allowed_entry_modes": ["starter"],
                            "blocked_entry_modes": ["blind_chase"],
                            "trend_quality_score": str(scenario_count % 101),
                            "false_breakout_score": str((scenario_count * 3) % 101),
                        },
                    ),
                    client.event(
                        "action.outcome.recorded",
                        run_id=run_id,
                        symbols=[symbol],
                        payload={
                            "action_id": f"action_{suffix}",
                            "action_kind": "allocation",
                            "status": action_statuses[
                                scenario_count % len(action_statuses)
                            ],
                            "symbol": symbol,
                            "requested_notional": str(scenario_count % 10_000),
                            "executed_notional": str(scenario_count % 5_000),
                            "remaining_capacity_before": str(
                                10_000 + scenario_count
                            ),
                            "remaining_capacity_after": str(scenario_count % 10_000),
                            "clipped": scenario_count % 2 == 0,
                            "risk_reduction": scenario_count % 3 == 0,
                        },
                    ),
                    client.event(
                        "evaluation.epoch.started",
                        run_id=run_id,
                        payload={
                            "epoch_id": f"epoch_{suffix}",
                            "epoch_kind": "same_input_model_comparison",
                            "context_hash": "sha256:context",
                            "candidate_count": str(scenario_count % 250),
                            "selected_symbol_count": str(scenario_count % 200),
                            "expected_member_count": "4",
                        },
                    ),
                    client.event(
                        "evaluation.epoch.member.completed",
                        run_id=run_id,
                        payload={
                            "epoch_id": f"epoch_{suffix}",
                            "member_id": f"member_{scenario_count % 13}",
                            "expected": True,
                            "state": member_states[
                                scenario_count % len(member_states)
                            ],
                            "coverage_penalty": str(scenario_count % 100),
                            "scored": scenario_count % 5 != 0,
                        },
                    ),
                ]
                assert_valid_agent_tracker_events(events)

    assert scenario_count == (
        len(OPPORTUNITY_REVIEW_STATUSES) * len(ENTRY_REGIMES) * len(ENTRY_PERMISSIONS)
    )
    assert scenario_count >= 1_000


@pytest.mark.parametrize(
    ("event_type", "payload", "bad_field", "bad_value"),
    [
        (
            "opportunity.board.recorded",
            {"board_id": "board_1", "scope": "full_universe", "candidate_count": "1"},
            "candidate_count",
            "-1",
        ),
        (
            "opportunity.candidate.reviewed",
            {
                "candidate_id": "candidate_1",
                "board_id": "board_1",
                "review_status": "candidate_present",
                "data_quality_score": "99.5",
            },
            "data_quality_score",
            "101",
        ),
        (
            "setup.profile.recorded",
            {
                "setup_profile_id": "setup_1",
                "primary_regime": "trend_continuation",
                "entry_permission": "eligible_starter",
                "trend_quality_score": "100.0",
            },
            "trend_quality_score",
            "100.1",
        ),
        (
            "action.outcome.recorded",
            {
                "action_id": "action_1",
                "action_kind": "allocation",
                "status": "clipped",
                "requested_notional": "1.00",
            },
            "requested_notional",
            "-0.01",
        ),
        (
            "evaluation.epoch.started",
            {
                "epoch_id": "epoch_1",
                "epoch_kind": "comparison",
                "context_hash": "sha256:context",
                "expected_member_count": "1",
            },
            "expected_member_count",
            "-1",
        ),
        (
            "evaluation.epoch.member.completed",
            {
                "epoch_id": "epoch_1",
                "member_id": "member_1",
                "expected": True,
                "state": "completed",
                "coverage_penalty": "0",
            },
            "coverage_penalty",
            "-0.5",
        ),
    ],
)
def test_new_event_type_schemas_reject_bad_numeric_strings(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, Any],
    bad_field: str,
    bad_value: str,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = read_json_resource("schemas", "event-types", f"{event_type}.schema.json")
    schema = deepcopy(schema)
    schema["allOf"][0] = read_json_resource("schemas", "event-envelope.schema.json")
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    client = AgentTracker(
        Config(project="schema-matrix", queue_dir=tmp_path, telemetry_enabled=False)
    )
    event = client.event(event_type, run_id="run_schema_matrix", payload=payload)

    validator.validate(event)
    invalid = deepcopy(event)
    invalid["payload"][bad_field] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid)
