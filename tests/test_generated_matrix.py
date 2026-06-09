from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from math import inf, nan
from pathlib import Path

from agent_tracker import AgentTracker, Config
from agent_tracker.constants import SUPPORTED_ENVIRONMENTS, SUPPORTED_EVENT_TYPES


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
        "agent.build.recorded": {
            "build_id": f"build_{variant}",
            "config_hash": "sha256:build",
            "risk_gate_version": "risk-1",
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
        "opportunity.board.recorded": {
            "board_id": f"board_{variant}",
            "scope": "full_universe",
            "candidate_count": variant,
            "reviewed_count": variant,
        },
        "opportunity.candidate.reviewed": {
            "candidate_id": f"candidate_{variant}",
            "board_id": f"board_{variant}",
            "symbol": "msft",
            "review_status": "optimizer_skipped",
            "reason_code": "capacity_limit",
        },
        "setup.profile.recorded": {
            "setup_profile_id": f"setup_profile_{variant}",
            "symbol": "msft",
            "primary_regime": "trend_continuation",
            "entry_permission": "eligible_starter",
            "trend_quality_score": "72",
        },
        "decision.proposed": {
            "decision_kind": "target_weight",
            "action": "increase",
            "symbol": "msft",
        },
        "action.outcome.recorded": {
            "action_id": f"action_{variant}",
            "action_kind": "rebalance",
            "status": "clipped",
            "symbol": "msft",
            "requested_notional": "1000.00",
            "executed_notional": "600.00",
            "clipped": True,
        },
        "order.intent.recorded": {
            "order_intent_id": f"intent_{variant}",
            "decision_id": f"decision_{variant}",
            "symbol": "msft",
            "side": "buy",
            "intended_quantity": "1",
            "intended_price": "100.00",
            "open_close_effect": "open",
        },
        "decision.outcome.recorded": {
            "decision_id": f"decision_{variant}",
            "outcome_kind": "filled",
            "linked_event_ids": [],
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
        "position.snapshot.recorded": {
            "portfolio_kind": "paper",
            "position_id": f"position_{variant}",
            "symbol": "nvda",
            "quantity": "1",
        },
        "portfolio.snapshot.recorded": {"portfolio_kind": "paper"},
        "capital.flow.recorded": {
            "capital_flow_id": f"flow_{variant}",
            "flow_kind": "deposit",
            "amount": "100.00",
            "asset": "USD",
            "currency": "USD",
            "session_date": "2026-06-07",
            "included_in_trading_pnl": False,
        },
        "performance.snapshot.recorded": {
            "period_kind": "daily",
            "period_start": "2026-06-07",
            "period_end": "2026-06-07",
            "session_date": "2026-06-07",
        },
        "strategy.context.recorded": {
            "strategy_id": f"strategy_{variant}",
            "setup": "matrix",
        },
        "evaluation.epoch.started": {
            "epoch_id": f"epoch_{variant}",
            "epoch_kind": "shadow_comparison",
            "context_hash": "sha256:context",
            "expected_member_count": 3,
        },
        "evaluation.epoch.member.completed": {
            "epoch_id": f"epoch_{variant}",
            "member_id": f"member_{variant}",
            "expected": True,
            "state": "completed",
            "coverage_penalty": "0",
        },
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
                        client = AgentTracker(
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

    assert scenario_count == (
        len(SUPPORTED_ENVIRONMENTS)
        * len(SUPPORTED_EVENT_TYPES)
        * len(symbols_variants)
        * len(privacy_modes)
        * 8
    )
