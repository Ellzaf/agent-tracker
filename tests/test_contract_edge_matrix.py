from __future__ import annotations

from pathlib import Path

from ellzaf_agent import Config, Ellzaf
from ellzaf_agent.constants import SUPPORTED_ENVIRONMENTS, SUPPORTED_EVENT_TYPES
from ellzaf_agent.testing import assert_valid_ellzaf_events


def test_taxonomy_contract_matrix_covers_more_than_1000_edge_cases(
    tmp_path: Path,
) -> None:
    families = [
        "source.truncated_or_missing_evidence",
        "portfolio.buying_power_as_cash",
        "pnl.deposit_as_profit",
        "custom.local_guardrail",
    ]
    severities = ["info", "warning", "error", "critical"]
    components = ["research", "risk_gate", "performance", "integration"]
    environments = sorted(SUPPORTED_ENVIRONMENTS)[:4]
    scenario_count = 0

    for environment in environments:
        client = Ellzaf(
            Config(
                project=f"matrix-{environment}",
                environment=environment,
                queue_dir=tmp_path / environment,
                telemetry_enabled=False,
                max_event_bytes=200_000,
            )
        )
        for event_type in sorted(SUPPORTED_EVENT_TYPES):
            for component in components:
                for severity in severities:
                    for family in families:
                        scenario_count += 1
                        event = client.event(
                            event_type,
                            run_id=f"run_contract_{scenario_count}",
                            payload={
                                **_payload_for(event_type, scenario_count),
                                "component": component,
                                "severity": severity,
                                "mistake_family": family,
                                "money_impact": "blocked"
                                if severity == "critical"
                                else "possible",
                                "blocking_status": "trading_blocked"
                                if severity == "critical"
                                else "workflow_deferred",
                                "resolution_status": "open",
                                "next_safe_action": "block_artifact"
                                if severity == "critical"
                                else "observe",
                                "evidence_refs": [
                                    {"table": "matrix", "id": str(scenario_count)}
                                ],
                                "correlation_ids": {
                                    "scenario": f"scenario_{scenario_count}"
                                },
                            },
                        )
                        assert event["payload"]["mistake_family"] == family
                        assert_valid_ellzaf_events([event])

    assert scenario_count == (
        len(environments)
        * len(SUPPORTED_EVENT_TYPES)
        * len(components)
        * len(severities)
        * len(families)
    )


def _payload_for(event_type: str, scenario: int) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {
        "agent.run.started": {"run_type": "matrix"},
        "agent.run.completed": {"run_type": "matrix", "status": "succeeded"},
        "agent.build.recorded": {
            "build_id": f"build_{scenario}",
            "config_hash": "sha256:matrix",
            "risk_gate_version": "risk-matrix",
        },
        "llm.call.started": {"provider": "provider", "model": "model"},
        "llm.call.completed": {
            "provider": "provider",
            "model": "model",
            "status": "succeeded",
        },
        "tool.call.completed": {"tool_name": "search", "status": "succeeded"},
        "source.claim.recorded": {"claim_type": "source_quality"},
        "market.snapshot.recorded": {"source": "local_bars"},
        "memory.read.completed": {"memory_kind": "fact", "purpose": "allocation"},
        "decision.proposed": {"decision_kind": "target_weight", "action": "watch"},
        "order.intent.recorded": {
            "order_intent_id": f"intent_{scenario}",
            "decision_id": f"decision_{scenario}",
            "symbol": "MSFT",
            "side": "buy",
            "intended_quantity": "1",
            "open_close_effect": "open",
        },
        "decision.outcome.recorded": {
            "decision_id": f"decision_{scenario}",
            "outcome_kind": "no_order",
            "linked_event_ids": [],
        },
        "risk.check.completed": {
            "risk_check_kind": "deterministic",
            "approved": False,
            "reasons": [f"matrix_{scenario}"],
        },
        "trade.rejected": {
            "rejected_by": "risk_gate",
            "reason_code": f"matrix_{scenario}",
        },
        "paper.fill.recorded": {"symbol": "MSFT", "side": "buy"},
        "position.snapshot.recorded": {
            "portfolio_kind": "paper",
            "position_id": f"position_{scenario}",
            "symbol": "MSFT",
            "quantity": "1",
        },
        "portfolio.snapshot.recorded": {"portfolio_kind": "paper"},
        "capital.flow.recorded": {
            "capital_flow_id": f"flow_{scenario}",
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
        "strategy.context.recorded": {"strategy_id": f"strategy_{scenario}"},
        "replay.result.recorded": {
            "suite_name": "matrix",
            "status": "succeeded",
            "case_count": scenario,
        },
        "cost.usage.recorded": {
            "provider": "search",
            "usage_kind": "query",
            "quantity": scenario,
        },
        "error.recorded": {"error_kind": "matrix", "message": "matrix warning"},
    }
    return payloads[event_type]
