from __future__ import annotations

import csv
from pathlib import Path

from agent_tracker.mapping import export_mapped_events
from agent_tracker.sink import read_jsonl_events
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_mapping_release_matrix_covers_1200_custom_agent_rows(
    tmp_path: Path,
) -> None:
    bool_values = ["1", "true", "yes", "y", "0", "false", "no", "n"]
    symbols = ["nvda", "MSFT", " brk.b ", "btc-usd"]
    key = "ellzaf_trk_TESTONLY000000000000000000000000"
    rows_path = tmp_path / "risk_checks.csv"
    with rows_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "symbol",
                "approved",
                "reasons",
                "checked_at",
                "message",
            ],
        )
        writer.writeheader()
        for index in range(1200):
            raw_bool = bool_values[index % len(bool_values)]
            approved = raw_bool.lower() in {"1", "true", "yes", "y"}
            writer.writerow(
                {
                    "run_id": f"run_mapping_matrix_{index}",
                    "symbol": symbols[index % len(symbols)],
                    "approved": raw_bool,
                    "reasons": ""
                    if approved
                    else "max_position_pct,stale_market_data",
                    "checked_at": "2026-06-07T00:00:00Z",
                    "message": (
                        f"matrix row {index} key {key}" if index % 17 == 0 else ""
                    ),
                }
            )
    config = tmp_path / "mapping.toml"
    config.write_text(
        """
project = "paper-agent"
agent_id = "matrix-agent"
environment = "paper"

[[sources]]
name = "risk-check-matrix"
kind = "csv"
path = "risk_checks.csv"
event_type = "risk.check.completed"
run_id_field = "run_id"
occurred_at_field = "checked_at"
symbols_field = "symbol"

[sources.payload_defaults]
risk_check_kind = "deterministic"

[sources.fields]
approved = { path = "approved", type = "bool" }
reasons = { path = "reasons", type = "list", required = false }
message = { path = "message", required = false }
""".strip(),
        encoding="utf-8",
    )
    output = tmp_path / "events.jsonl"

    summary = export_mapped_events(config, output)
    events = read_jsonl_events(output)

    assert summary.exported == 1200
    assert summary.skipped == 0
    assert summary.source_counts == {"risk-check-matrix": 1200}
    assert len(events) == 1200
    assert key not in repr(events)
    assert {event["symbols"][0] for event in events} == {
        "NVDA",
        "MSFT",
        "BRK.B",
        "BTC-USD",
    }
    assert all(
        event["payload"].get("reasons")
        for event in events
        if event["payload"]["approved"] is False
    )
    assert_valid_agent_tracker_events(events)
