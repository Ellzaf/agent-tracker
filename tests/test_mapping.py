from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_tracker.cli import main
from agent_tracker.mapping import (
    MappingError,
    events_from_mapping_config,
    export_mapped_events,
)
from agent_tracker.sink import read_jsonl_events
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_mapping_exports_jsonl_rows_with_validation_and_redaction(
    tmp_path: Path,
) -> None:
    key = "ellzaf_trk_mEwmt6sY0vVFHE8vOPWCkLKnzfyGFnQgLZo1B7qM"
    rows = tmp_path / "risk.jsonl"
    rows.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "run_id": "run_jsonl_1",
                        "symbol": "nvda",
                        "approved": "false",
                        "reasons": '["stale_market_data"]',
                        "message": f"configured key {key}",
                    }
                ),
                json.dumps(
                    {
                        "run_id": "run_bad_1",
                        "symbol": "msft",
                        "approved": "false",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = {
        "project": "paper-agent",
        "agent_id": "custom-agent",
        "sources": [
            {
                "kind": "jsonl",
                "path": "risk.jsonl",
                "event_type": "risk.check.completed",
                "run_id_field": "run_id",
                "symbols_field": "symbol",
                "payload_defaults": {"risk_check_kind": "deterministic"},
                "fields": {
                    "approved": {"path": "approved", "type": "bool"},
                    "reasons": {"path": "reasons", "type": "list", "required": False},
                    "message": {"path": "message", "required": False},
                },
            }
        ],
    }

    events, warnings, skipped, source_counts = events_from_mapping_config(
        config, root=tmp_path
    )

    assert len(events) == 1
    assert skipped == 1
    assert source_counts == {"risk.jsonl": 1}
    assert warnings == ["risk.jsonl row 2: SchemaValidationError"]
    assert key not in repr(events)
    assert events[0]["symbols"] == ["NVDA"]
    assert events[0]["payload"]["approved"] is False
    assert events[0]["payload"]["reasons"] == ["stale_market_data"]
    assert_valid_agent_tracker_events(events)


def test_mapping_exports_csv_and_cli_summary(tmp_path: Path, capsys: object) -> None:
    (tmp_path / "risk.csv").write_text(
        "\n".join(
            [
                "run_id,symbol,approved,reasons,checked_at",
                "run_csv_1,MSFT,true,,2026-06-07T00:00:00Z",
                "run_csv_2,NVDA,false,max_position_pct,2026-06-07T00:01:00Z",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "ellzaf-mapping.toml"
    config.write_text(
        """
project = "paper-agent"
agent_id = "custom-agent"
environment = "paper"

[[sources]]
name = "risk-checks"
kind = "csv"
path = "risk.csv"
event_type = "risk.check.completed"
run_id_field = "run_id"
occurred_at_field = "checked_at"
symbols_field = "symbol"

[sources.payload_defaults]
risk_check_kind = "deterministic"

[sources.fields]
approved = { path = "approved", type = "bool" }
reasons = { path = "reasons", type = "list", required = false }
""".strip(),
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"

    assert main(["map-events", "--config", str(config), "--output", str(output)]) == 0

    summary = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    events = read_jsonl_events(output)
    assert summary["exported"] == 2
    assert summary["source_counts"] == {"risk-checks": 2}
    assert [event["run_id"] for event in events] == ["run_csv_1", "run_csv_2"]
    assert events[0]["payload"]["approved"] is True
    assert events[1]["payload"]["reasons"] == ["max_position_pct"]
    assert_valid_agent_tracker_events(events)


def test_mapping_supports_json_array_and_sqlite_with_config_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "decisions.json").write_text(
        json.dumps(
            [
                {
                    "run_id": "run_decision_1",
                    "symbol": "NVDA",
                    "decision_kind": "target_weight",
                    "action": "increase",
                }
            ]
        ),
        encoding="utf-8",
    )
    database = tmp_path / "events.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "create table replay_results "
        "(run_id text, suite_name text, status text, case_count integer)"
    )
    connection.execute(
        "insert into replay_results values (?, ?, ?, ?)",
        ("run_replay_1", "weekend-regression", "succeeded", 3),
    )
    connection.commit()
    connection.close()
    config = tmp_path / "mapping.json"
    config.write_text(
        json.dumps(
            {
                "project": "paper-agent",
                "agent_id": "custom-agent",
                "sources": [
                    {
                        "kind": "json_array",
                        "path": "decisions.json",
                        "event_type": "decision.proposed",
                        "run_id_field": "run_id",
                        "symbols_field": "symbol",
                        "fields": {
                            "decision_kind": "decision_kind",
                            "action": "action",
                            "symbol": "symbol",
                        },
                    },
                    {
                        "kind": "sqlite_query",
                        "database_path": "events.sqlite",
                        "query": (
                            "select run_id, suite_name, status, case_count "
                            "from replay_results"
                        ),
                        "event_type": "replay.result.recorded",
                        "run_id_field": "run_id",
                        "fields": {
                            "suite_name": "suite_name",
                            "status": "status",
                            "case_count": {"path": "case_count", "type": "int"},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "mapped.jsonl"

    summary = export_mapped_events(config, output)
    events = read_jsonl_events(output)

    assert summary.exported == 2
    assert summary.source_counts == {"decisions.json": 1, "events.sqlite": 1}
    assert {event["event_type"] for event in events} == {
        "decision.proposed",
        "replay.result.recorded",
    }
    assert events[1]["payload"]["case_count"] == 3
    assert_valid_agent_tracker_events(events)


def test_mapping_rejects_unsafe_paths_and_mutating_sql(tmp_path: Path) -> None:
    config = {
        "project": "paper-agent",
        "sources": [
            {
                "kind": "csv",
                "path": "../private.csv",
                "event_type": "risk.check.completed",
                "fields": {"approved": "approved"},
            }
        ],
    }

    with pytest.raises(MappingError, match="inside the mapping config directory"):
        events_from_mapping_config(config, root=tmp_path)

    database = tmp_path / "events.sqlite"
    sqlite3.connect(database).close()
    config["sources"][0] = {
        "kind": "sqlite_query",
        "database_path": "events.sqlite",
        "query": "delete from events",
        "event_type": "risk.check.completed",
        "fields": {"approved": "approved"},
    }

    with pytest.raises(MappingError, match="read-only"):
        events_from_mapping_config(config, root=tmp_path)
