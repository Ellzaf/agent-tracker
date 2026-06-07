from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from math import inf, nan
from pathlib import Path

from ellzaf_agent import Config, Ellzaf
from ellzaf_agent.constants import SUPPORTED_ENVIRONMENTS, SUPPORTED_EVENT_TYPES


def payload_for(event_type: str, variant: int) -> dict:
    common = {
        "metadata": {
            "decimal": Decimal("1.23"),
            "when": datetime(2026, 6, 7, 14, 30, tzinfo=UTC),
            "set": {"b", "a"},
            "nan": nan,
            "inf": inf,
            "bytes": b"binary-market-snapshot",
            "local_path": Path("/home/example/private-agent/state.db"),
            "tool_error": "Authorization: Bearer abcdefghijklmnop",
        }
    }
    payloads = {
        "agent.run.started": {"run_type": "portfolio_allocation"},
        "agent.run.completed": {
            "run_type": "portfolio_allocation",
            "status": "succeeded",
        },
        "llm.call.started": {
            "provider": "openai",
            "model": "example",
            "prompt": "secret api_key=abcdef123456",
        },
        "llm.call.completed": {
            "provider": "openai",
            "model": "example",
            "status": "succeeded",
            "output": "answer",
        },
        "tool.call.completed": {"tool_name": "search", "status": "succeeded"},
        "source.claim.recorded": {"claim_type": "financial_result", "symbol": "aapl"},
        "market.snapshot.recorded": {
            "source": "stored_5m_bars",
            "freshness_seconds": variant,
        },
        "memory.read.completed": {"memory_kind": "lesson", "purpose": "allocation"},
        "decision.proposed": {
            "decision_kind": "target_weight",
            "action": "increase",
            "symbol": "msft",
        },
        "risk.check.completed": {
            "approved": variant % 2 == 0,
            "reasons": ["matrix_reject"],
        },
        "trade.rejected": {
            "rejected_by": "risk_gate",
            "reason_code": "max_position_pct",
            "symbol": "nvda",
        },
        "paper.fill.recorded": {"symbol": "nvda", "side": "buy"},
        "portfolio.snapshot.recorded": {"portfolio_kind": "paper"},
        "replay.result.recorded": {
            "suite_name": "matrix",
            "status": "passed",
            "case_count": variant,
        },
        "cost.usage.recorded": {
            "provider": "search",
            "usage_kind": "query",
            "quantity": variant + 1,
        },
        "error.recorded": {"error_kind": "timeout", "message": "provider timeout"},
    }
    return {**payloads[event_type], **common}


def test_generated_event_matrix_covers_more_than_1000_edge_cases(
    tmp_path: Path,
) -> None:
    symbols_variants = [
        [],
        ["aapl", "AAPL", " "],
        ["brk.b", "btc-usd"],
        ["msft", "nvda", "tsla"],
    ]
    privacy_modes = [False, True]
    scenario_count = 0

    for env_index, environment in enumerate(sorted(SUPPORTED_ENVIRONMENTS)):
        for event_index, event_type in enumerate(sorted(SUPPORTED_EVENT_TYPES)):
            for symbols in symbols_variants:
                for store_full_io in privacy_modes:
                    for variant in range(8):
                        scenario_count += 1
                        client = Ellzaf(
                            Config(
                                project=f"paper-agent-{env_index}",
                                environment=environment,
                                queue_dir=tmp_path / f"q-{scenario_count}",
                                telemetry_enabled=False,
                                max_event_bytes=200_000,
                            )
                        )
                        event = client.event(
                            event_type,
                            run_id=f"run_matrix_{scenario_count}",
                            symbols=symbols,
                            payload=payload_for(event_type, event_index + variant),
                            store_full_io=store_full_io,
                        )
                        assert event["environment"] == environment
                        assert event["event_type"] == event_type
                        assert event["privacy"]["full_io"] is store_full_io
                        dumped = str(event)
                        assert "Bearer abcdefghijklmnop" not in dumped
                        assert "/home/example/private-agent" not in dumped
                        assert event["payload"]["metadata"]["nan"] is None
                        assert event["payload"]["metadata"]["inf"] is None
                        assert event["payload"]["metadata"]["bytes"]["redacted"] is True

    assert scenario_count == 5120
