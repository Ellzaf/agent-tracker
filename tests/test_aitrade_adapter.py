from __future__ import annotations

from pathlib import Path

from agent_tracker.adapters.aitrade import AitradeExporter
from agent_tracker.sink import read_jsonl_events
from agent_tracker.testing import assert_valid_agent_tracker_events


def test_aitrade_exporter_maps_rows_to_valid_stable_events(tmp_path: Path) -> None:
    rows = _rows()
    exporter = AitradeExporter(project="paper-agent", agent_id="starter")

    first_events, first_summary = exporter.events_from_rows(rows)
    second_events, second_summary = exporter.events_from_rows(rows)

    assert first_summary.exported == len(first_events)
    assert first_summary.report.repo_profile == "aitrade"
    assert [event["event_id"] for event in first_events] == [
        event["event_id"] for event in second_events
    ]
    assert [event["idempotency_key"] for event in first_events] == [
        event["idempotency_key"] for event in second_events
    ]
    assert_valid_agent_tracker_events(first_events)

    families = {
        event["payload"].get("mistake_family")
        for event in first_events
        if event["payload"].get("mistake_family")
    }
    event_types = {event["event_type"] for event in first_events}
    assert "order.intent.recorded" in event_types
    assert "performance.snapshot.recorded" in event_types
    assert "capital.flow.recorded" in event_types
    assert "source.truncated_or_missing_evidence" in families
    assert "portfolio.buying_power_as_cash" in families
    assert "pnl.deposit_as_profit" in families
    assert "shadow.unfair_cadence" in families

    output = tmp_path / "aitrade-events.jsonl"
    summary = exporter.export_jsonl(output, rows_by_table=rows)
    exported = read_jsonl_events(output)

    assert summary.exported == len(exported)
    assert_valid_agent_tracker_events(exported)
    assert second_summary.exported == summary.exported


def test_aitrade_exporter_marks_required_missing_surfaces() -> None:
    exporter = AitradeExporter(project="paper-agent")

    _events, summary = exporter.events_from_rows({"search_usage_events": []})

    missing = set(summary.report.to_dict()["missing_required"])
    assert "llm_runs" in missing
    assert "risk_checks" in missing


def test_aitrade_exporter_accepts_empty_explicit_rows_without_database(
    tmp_path: Path,
) -> None:
    exporter = AitradeExporter(project="paper-agent")
    output = tmp_path / "empty.jsonl"

    summary = exporter.export_jsonl(output, rows_by_table={})

    assert summary.exported == 0
    assert output.read_text(encoding="utf-8") == ""


def _rows() -> dict[str, list[dict[str, object]]]:
    return {
        "llm_runs": [
            {
                "id": "llm_1",
                "run_type": "research_report",
                "symbol": "NVDA",
                "provider": "openai",
                "model": "example-model",
                "status": "schema_failed",
                "input_hash": "sha256:input",
                "output_hash": "sha256:output",
                "prompt_chars": 120,
                "output_chars": 80,
                "token_usage": {"input": 10, "output": 20},
                "validation_summary": {"error": "bad json"},
                "started_at": "2026-06-07T00:00:00Z",
                "completed_at": "2026-06-07T00:00:05Z",
            }
        ],
        "source_quality_repair_incidents": [
            {
                "id": "repair_1",
                "symbol": "NVDA",
                "status": "running",
                "provider": "parallel",
                "failure_reason": "missing financial evidence",
                "detected_at": "2026-06-07T00:01:00Z",
                "updated_at": "2026-06-07T00:02:00Z",
            }
        ],
        "research_reports": [
            {
                "id": "report_1",
                "symbol": "NVDA",
                "report_date": "2026-06-07",
                "content_markdown": "Source audit: source-limited financials.",
                "source_digest": {"quality": "source-limited"},
                "citations": [{"url_hash": "sha256:x"}],
                "created_at": "2026-06-07T00:03:00Z",
            }
        ],
        "market_tape_snapshots": [
            {
                "id": "tape_1",
                "as_of": "2026-06-07T00:04:00Z",
                "session_date": "2026-06-07",
                "market_phase": "open",
                "source": "runtime_context",
                "snapshot_scope": "context",
                "watchlist_count": 3,
                "data_quality": {"degraded": True},
            }
        ],
        "portfolio_allocation_runs": [
            {
                "id": "alloc_1",
                "state": "optimized",
                "candidate_count": 2,
                "target_count": 1,
                "created_at": "2026-06-07T00:05:00Z",
                "updated_at": "2026-06-07T00:05:10Z",
            }
        ],
        "portfolio_targets": [
            {
                "id": "target_1",
                "allocation_run_id": "alloc_1",
                "symbol": "MSFT",
                "target_weight": "0.15",
                "current_weight": "0.05",
                "confidence": "0.8",
                "status": "proposed",
                "created_at": "2026-06-07T00:06:00Z",
            }
        ],
        "risk_checks": [
            {
                "id": "risk_1",
                "order_intent_id": "order_1",
                "symbol": "MSFT",
                "approved": False,
                "reasons": ["buying power is not cash"],
                "checked_at": "2026-06-07T00:07:00Z",
            }
        ],
        "order_intents": [
            {
                "id": "order_1",
                "decision_id": "decision_1",
                "symbol": "MSFT",
                "side": "buy",
                "qty": "1",
                "intended_price": "420.50",
                "status": "submitted",
                "session_date": "2026-06-07",
                "created_at": "2026-06-07T00:07:30Z",
            }
        ],
        "trade_journal": [
            {
                "id": "fill_1",
                "symbol": "MSFT",
                "side": "buy",
                "qty": "1",
                "price": "420.50",
                "broker_order_id": "order_public_ref",
                "created_at": "2026-06-07T00:08:00Z",
            }
        ],
        "portfolio_snapshots": [
            {
                "id": "snapshot_1",
                "broker": "alpaca",
                "cash": "9000.00",
                "buying_power": "18000.00",
                "equity": "10000.00",
                "captured_at": "2026-06-07T00:09:00Z",
            }
        ],
        "portfolio_performance_scorecards": [
            {
                "id": "score_1",
                "broker": "alpaca",
                "session_date": "2026-06-07",
                "equity": "11000.00",
                "cash": "10000.00",
                "external_capital_flow": "1000.00",
                "trading_pnl_amount": "0.00",
                "trading_pnl_pct": "0.00",
                "captured_at": "2026-06-07T00:10:00Z",
            }
        ],
        "decision_replay_runs": [
            {
                "id": "replay_1",
                "suite_name": "golden",
                "status": "succeeded",
                "case_count": 3,
                "created_at": "2026-06-07T00:11:00Z",
            }
        ],
        "shadow_profile_scorecards": [
            {
                "id": "shadow_score_1",
                "profile_id": "shadow_a",
                "failed_run_count": 1,
                "trade_count": 3,
                "as_of": "2026-06-07T00:12:00Z",
            }
        ],
    }
