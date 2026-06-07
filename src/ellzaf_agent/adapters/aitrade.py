"""Adapter for Blueprint-style and aitrade_blank-style artifact rows."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from ellzaf_agent.client import Ellzaf
from ellzaf_agent.config import Config
from ellzaf_agent.errors import EllzafError
from ellzaf_agent.integration import (
    EllzafIntegrationReport,
    IntegrationSurface,
    SourceRef,
)
from ellzaf_agent.serialization import (
    hash_text,
    strict_json_dumps,
    to_jsonable,
    utc_now_iso,
)
from ellzaf_agent.sink import JsonlSink

_DEFAULT_OCCURRED_AT = "1970-01-01T00:00:00Z"

_TABLE_ORDER_COLUMNS = {
    "prompt_versions": "created_at",
    "llm_runs": "started_at",
    "llm_tool_calls": "created_at",
    "search_usage_events": "created_at",
    "research_reports": "created_at",
    "source_quality_repair_incidents": "updated_at",
    "market_tape_snapshots": "as_of",
    "market_regime_snapshots": "as_of",
    "memory_fact_usage": "created_at",
    "portfolio_allocation_runs": "created_at",
    "portfolio_targets": "created_at",
    "portfolio_rebalance_actions": "created_at",
    "risk_checks": "checked_at",
    "order_intents": "updated_at",
    "trade_journal": "created_at",
    "portfolio_snapshots": "captured_at",
    "portfolio_performance_scorecards": "captured_at",
    "decision_replay_runs": "created_at",
    "harness_eval_runs": "created_at",
    "harness_replay_runs": "created_at",
    "shadow_allocation_runs": "created_at",
    "shadow_order_fills": "created_at",
    "shadow_profile_scorecards": "as_of",
}


class AdapterError(EllzafError):
    """Raised when an optional adapter cannot export safely."""


@dataclass(frozen=True, slots=True)
class AitradeExportSummary:
    exported: int
    table_counts: dict[str, int]
    warnings: tuple[str, ...]
    report: EllzafIntegrationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "exported": self.exported,
            "table_counts": dict(self.table_counts),
            "warnings": list(self.warnings),
            "report": self.report.to_dict(),
        }


class AitradeExporter:
    """Map starter-repo artifact rows into Ellzaf events."""

    def __init__(
        self,
        *,
        project: str = "aitrade",
        agent_id: str = "aitrade",
        environment: str = "paper",
        database_url: str | None = None,
        max_event_bytes: int = 200_000,
    ) -> None:
        self.project = project
        self.agent_id = agent_id
        self.environment = environment
        self.database_url = database_url
        self.max_event_bytes = max_event_bytes
        self._clients: dict[str, Ellzaf] = {}

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        *,
        project: str = "aitrade",
        agent_id: str = "aitrade",
        environment: str = "paper",
        max_event_bytes: int = 200_000,
    ) -> AitradeExporter:
        return cls(
            project=project,
            agent_id=agent_id,
            environment=environment,
            database_url=database_url,
            max_event_bytes=max_event_bytes,
        )

    def export_jsonl(
        self,
        output: str | Path,
        *,
        rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        limit_per_table: int = 500,
        append: bool = False,
    ) -> AitradeExportSummary:
        rows = rows_by_table or self.fetch_rows(limit_per_table=limit_per_table)
        events, summary = self.events_from_rows(rows)
        JsonlSink(
            output, max_event_bytes=self.max_event_bytes, append=append
        ).write_many(events)
        return summary

    def events_from_rows(
        self,
        rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> tuple[list[dict[str, Any]], AitradeExportSummary]:
        events: list[dict[str, Any]] = []
        warnings: list[str] = []
        table_counts: dict[str, int] = defaultdict(int)

        def add(event: dict[str, Any] | None, table: str) -> None:
            if event is None:
                return
            events.append(event)
            table_counts[table] += 1

        for table, rows in rows_by_table.items():
            for raw_row in rows:
                row = dict(raw_row)
                for event in self._events_for_table(table, row, warnings):
                    add(event, table)

        report = self.integration_report(rows_by_table, warnings=warnings)
        summary = AitradeExportSummary(
            exported=len(events),
            table_counts=dict(table_counts),
            warnings=tuple(warnings),
            report=report,
        )
        return events, summary

    def fetch_rows(
        self, *, limit_per_table: int = 500
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.database_url:
            raise AdapterError("database_url is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise AdapterError(
                "Install ellzaf-agent[aitrade] to export from Postgres"
            ) from exc

        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            for table, order_column in _TABLE_ORDER_COLUMNS.items():
                try:
                    with conn.cursor() as cursor:
                        sql = (
                            f"SELECT * FROM {table} "
                            f"ORDER BY {order_column} DESC LIMIT %s"
                        )
                        cursor.execute(
                            sql,
                            (limit_per_table,),
                        )
                        rows_by_table[table] = [dict(row) for row in cursor.fetchall()]
                except Exception:
                    conn.rollback()
                    rows_by_table[table] = []
        return rows_by_table

    def integration_report(
        self,
        rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        warnings: Sequence[str] = (),
    ) -> EllzafIntegrationReport:
        surfaces: list[IntegrationSurface] = []
        for table, event_type in _table_surface_map().items():
            rows = rows_by_table.get(table, ())
            required = table in {
                "llm_runs",
                "research_reports",
                "market_tape_snapshots",
                "portfolio_allocation_runs",
                "portfolio_targets",
                "risk_checks",
                "portfolio_snapshots",
            }
            if rows:
                coverage = "implemented"
                refs = tuple(
                    SourceRef(table=table, id=str(row.get("id", index)))
                    for index, row in enumerate(rows[:5])
                )
            else:
                coverage = "not_found" if not required else "missing"
                refs = ()
            surfaces.append(
                IntegrationSurface(
                    name=table,
                    event_type=event_type,
                    coverage=coverage,
                    required=required,
                    source_refs=refs,
                )
            )
        return EllzafIntegrationReport(
            project=self.project,
            repo_profile="aitrade",
            surfaces=tuple(surfaces),
            warnings=tuple(warnings),
        )

    def _events_for_table(
        self,
        table: str,
        row: Mapping[str, Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        handlers = {
            "prompt_versions": self._prompt_version_events,
            "llm_runs": self._llm_run_events,
            "llm_tool_calls": self._tool_call_events,
            "search_usage_events": self._search_usage_events,
            "research_reports": self._research_report_events,
            "source_quality_repair_incidents": self._source_quality_events,
            "market_tape_snapshots": self._market_tape_events,
            "market_regime_snapshots": self._market_regime_events,
            "memory_fact_usage": self._memory_usage_events,
            "portfolio_allocation_runs": self._allocation_run_events,
            "portfolio_targets": self._portfolio_target_events,
            "portfolio_rebalance_actions": self._rebalance_action_events,
            "risk_checks": self._risk_check_events,
            "order_intents": self._order_intent_events,
            "trade_journal": self._trade_journal_events,
            "portfolio_snapshots": self._portfolio_snapshot_events,
            "portfolio_performance_scorecards": self._scorecard_events,
            "decision_replay_runs": self._replay_events,
            "harness_eval_runs": self._replay_events,
            "harness_replay_runs": self._replay_events,
            "shadow_allocation_runs": self._shadow_allocation_events,
            "shadow_order_fills": self._shadow_fill_events,
            "shadow_profile_scorecards": self._shadow_scorecard_events,
        }
        handler = handlers.get(table)
        if handler is None:
            warnings.append(f"unsupported table ignored: {table}")
            return []
        return handler(table, row, warnings)

    def _prompt_version_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        run_id = _run_id(table, row)
        metadata = _jsonish(row.get("metadata"))
        provider = metadata.get("provider") if isinstance(metadata, dict) else None
        return [
            self._event(
                table,
                row,
                "llm.call.started",
                run_id=run_id,
                occurred_at=_time(row, "created_at", "updated_at"),
                payload={
                    "provider": _text(row.get("provider") or provider, "unknown"),
                    "model": _text(row.get("model"), "unknown"),
                    "prompt_family": _text(row.get("family"), "unknown"),
                    "prompt_version": _text(row.get("version"), "unknown"),
                    "prompt_hash": _text(
                        row.get("version_hash") or row.get("template_hash"),
                        "sha256:unknown",
                    ),
                    "component": "research",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _llm_run_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        run_id = _run_id(table, row)
        provider = _text(row.get("provider"), "unknown")
        model = _text(row.get("model"), "unknown")
        payload_common = {
            "provider": provider,
            "model": model,
            "run_type": _text(row.get("run_type"), "unknown"),
            "prompt_version_id": _maybe_text(row.get("prompt_version_id")),
            "input_hash": _text(row.get("input_hash"), ""),
            "output_hash": _maybe_text(row.get("output_hash")),
            "prompt_chars": _int(row.get("prompt_chars")),
            "output_chars": _int(row.get("output_chars")),
            "latency_ms": _maybe_int(row.get("latency_ms")),
            "token_usage": _jsonish(row.get("token_usage")),
            "estimated_cost": _maybe_decimal(row.get("estimated_cost")),
            "validation_summary": _jsonish(row.get("validation_summary")),
            "component": "harness",
            "severity": "info",
            "evidence_refs": _refs(table, row),
        }
        completed_payload = {
            **payload_common,
            "status": _text(row.get("status"), "succeeded"),
            **_mistake_for_llm(row),
        }
        events = [
            self._event(
                table,
                row,
                "llm.call.started",
                run_id=run_id,
                occurred_at=_time(row, "started_at", "created_at"),
                payload={k: v for k, v in payload_common.items() if v is not None},
            )
        ]
        if _text(row.get("status"), "succeeded") != "started" or row.get(
            "completed_at"
        ):
            events.append(
                self._event(
                    table,
                    row,
                    "llm.call.completed",
                    run_id=run_id,
                    occurred_at=_time(row, "completed_at", "started_at"),
                    payload={
                        k: v for k, v in completed_payload.items() if v is not None
                    },
                )
            )
        return events

    def _tool_call_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "tool.call.completed",
                run_id=_run_id("llm_runs", row, id_key="llm_run_id"),
                occurred_at=_time(row, "completed_at", "created_at", "started_at"),
                payload={
                    "tool_name": _text(row.get("tool_name"), "unknown"),
                    "provider": _maybe_text(row.get("provider")),
                    "status": "succeeded",
                    "argument_keys": sorted(_jsonish(row.get("arguments")).keys())
                    if isinstance(_jsonish(row.get("arguments")), dict)
                    else [],
                    "result_digest": _jsonish(row.get("result_digest")),
                    "component": "research",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _search_usage_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "cost.usage.recorded",
                run_id=_run_id(table, row),
                symbols=_symbols(row),
                occurred_at=_time(row, "created_at"),
                payload={
                    "provider": _text(row.get("provider"), "unknown"),
                    "usage_kind": "search_query",
                    "quantity": _int(row.get("query_count")),
                    "estimated_credits": _int(row.get("estimated_credits")),
                    "purpose": _text(row.get("purpose"), "unknown"),
                    "component": "cost",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                    **_mistake_for_cost(row),
                },
            )
        ]

    def _research_report_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        content = _text(row.get("content_markdown"), "")
        payload = {
            "claim_type": "research_report",
            "symbol": _text(row.get("symbol"), ""),
            "report_date": _maybe_text(row.get("report_date")),
            "horizon": _maybe_text(row.get("horizon")),
            "source_digest": _jsonish(row.get("source_digest")),
            "source_document_count": _count_items(row.get("source_document_ids")),
            "citation_count": _count_items(row.get("citations")),
            "score": _maybe_decimal(row.get("score")),
            "confidence": _maybe_decimal(row.get("confidence")),
            "content_hash": hash_text(content) if content else None,
            "component": "research",
            "severity": "info",
            "evidence_refs": _refs(table, row),
            **_mistake_for_source_text(content, row),
        }
        return [
            self._event(
                table,
                row,
                "source.claim.recorded",
                run_id=_run_id(table, row),
                symbols=_symbols(row),
                occurred_at=_time(row, "created_at"),
                payload={k: v for k, v in payload.items() if v is not None},
            )
        ]

    def _source_quality_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        status = _text(row.get("status"), "detected")
        return [
            self._event(
                table,
                row,
                "error.recorded",
                run_id=_run_id(table, row),
                symbols=_symbols(row),
                occurred_at=_time(row, "updated_at", "detected_at", "created_at"),
                payload={
                    "error_kind": "source_quality_repair",
                    "message": _text(row.get("failure_reason") or status, status),
                    "component": "source_collection",
                    "severity": "error"
                    if status in {"escalated", "repaired_still_blocked"}
                    else "warning",
                    "mistake_family": "source.repair_loop"
                    if status in {"running", "cooldown", "escalated"}
                    else "source.truncated_or_missing_evidence",
                    "money_impact": "possible",
                    "blocking_status": "artifact_blocked"
                    if status not in {"repaired_clean", "superseded_by_clean_report"}
                    else "non_blocking",
                    "resolution_status": _resolution_status(status),
                    "next_safe_action": "retry"
                    if status in {"detected", "queued", "running", "cooldown"}
                    else "observe",
                    "provider": _text(row.get("provider"), "unknown"),
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _market_tape_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        data_quality = _jsonish(row.get("data_quality"))
        raw = _jsonish(row.get("raw"))
        mistake = {}
        if _truthy(
            data_quality.get("degraded") if isinstance(data_quality, dict) else False
        ):
            mistake = {
                "mistake_family": "market.partial_tape_as_truth",
                "severity": "warning",
                "money_impact": "possible",
                "blocking_status": "workflow_deferred",
                "resolution_status": "open",
                "next_safe_action": "observe",
            }
        return [
            self._event(
                table,
                row,
                "market.snapshot.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "as_of", "created_at"),
                payload={
                    "source": _text(row.get("source"), "market_tape"),
                    "snapshot_scope": _text(row.get("snapshot_scope"), "context"),
                    "session_date": _maybe_text(row.get("session_date")),
                    "market_session": _text(row.get("market_phase"), "unknown"),
                    "watchlist_count": _int(row.get("watchlist_count")),
                    "data_quality": data_quality,
                    "raw_hash": hash_text(strict_json_dumps(raw)) if raw else None,
                    "component": "market_data",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                    **mistake,
                },
            )
        ]

    def _market_regime_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "market.snapshot.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "as_of", "created_at"),
                payload={
                    "source": "market_regime",
                    "primary_regime": _text(row.get("primary_regime"), "unknown"),
                    "breadth_state": _maybe_text(row.get("breadth_state")),
                    "leadership_state": _maybe_text(row.get("leadership_state")),
                    "volatility_state": _maybe_text(row.get("volatility_state")),
                    "confidence": _maybe_decimal(row.get("confidence")),
                    "warnings": _jsonish(row.get("warnings")),
                    "component": "market_data",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _memory_usage_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "memory.read.completed",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "created_at"),
                payload={
                    "memory_kind": "memory_fact",
                    "purpose": _text(row.get("purpose"), "unknown"),
                    "consumer": _maybe_text(row.get("consumer")),
                    "profile_id": _maybe_text(row.get("profile_id")),
                    "included_in_prompt": bool(row.get("included_in_prompt", True)),
                    "component": "memory",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _allocation_run_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        run_id = _run_id(table, row)
        status = _allocation_status(row)
        common = {
            "run_type": "portfolio_allocation",
            "component": "allocation",
            "cash_target_pct": _maybe_decimal(row.get("cash_target_pct")),
            "gross_target_exposure_pct": _maybe_decimal(
                row.get("gross_target_exposure_pct")
            ),
            "candidate_count": _int(row.get("candidate_count")),
            "target_count": _int(row.get("target_count")),
            "accepted_action_count": _int(row.get("accepted_action_count")),
            "skipped_action_count": _int(row.get("skipped_action_count")),
            "model": _maybe_text(row.get("model")),
            "evidence_refs": _refs(table, row),
        }
        return [
            self._event(
                table,
                row,
                "agent.run.started",
                run_id=run_id,
                occurred_at=_time(row, "created_at"),
                payload={k: v for k, v in common.items() if v is not None},
            ),
            self._event(
                table,
                row,
                "agent.run.completed",
                run_id=run_id,
                occurred_at=_time(row, "updated_at", "created_at"),
                payload={
                    **{k: v for k, v in common.items() if v is not None},
                    "status": status,
                    "final_action": _text(row.get("state"), "unknown"),
                    "severity": "error" if status == "failed" else "info",
                },
            ),
        ]

    def _portfolio_target_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "decision.proposed",
                run_id=_run_id(
                    "portfolio_allocation_runs", row, id_key="allocation_run_id"
                ),
                symbols=_symbols(row),
                occurred_at=_time(row, "created_at"),
                payload={
                    "decision_kind": "target_weight",
                    "action": _text(row.get("status"), "proposed"),
                    "symbol": _text(row.get("symbol"), ""),
                    "target_weight": _maybe_decimal(row.get("target_weight")),
                    "current_weight": _maybe_decimal(row.get("current_weight")),
                    "confidence": _maybe_decimal(row.get("confidence")),
                    "horizon": _maybe_text(row.get("horizon")),
                    "component": "allocation",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _rebalance_action_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        events = [
            self._event(
                table,
                row,
                "decision.proposed",
                run_id=_run_id(
                    "portfolio_allocation_runs", row, id_key="allocation_run_id"
                ),
                symbols=_symbols(row),
                occurred_at=_time(row, "created_at"),
                payload={
                    "decision_kind": "rebalance_action",
                    "action": _text(row.get("action") or row.get("side"), "unknown"),
                    "symbol": _text(row.get("symbol"), ""),
                    "status": _maybe_text(row.get("status")),
                    "notional": _maybe_decimal(row.get("notional")),
                    "current_weight": _maybe_decimal(row.get("current_weight")),
                    "target_weight": _maybe_decimal(row.get("target_weight")),
                    "reason": _maybe_text(row.get("reason")),
                    "component": "allocation",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                    **_mistake_for_risk_text(_text(row.get("reason"), "")),
                },
            )
        ]
        if _text(row.get("status"), "") in {"skipped", "rejected"}:
            events.append(
                self._event(
                    table,
                    row,
                    "trade.rejected",
                    run_id=_run_id(
                        "portfolio_allocation_runs", row, id_key="allocation_run_id"
                    ),
                    symbols=_symbols(row),
                    occurred_at=_time(row, "created_at"),
                    payload={
                        "rejected_by": "portfolio_rebalance",
                        "reason_code": _slug(
                            _text(row.get("reason"), "rebalance_rejected")
                        ),
                        "symbol": _text(row.get("symbol"), ""),
                        "component": "risk_gate",
                        "severity": "warning",
                        "evidence_refs": _refs(table, row),
                        **_mistake_for_risk_text(_text(row.get("reason"), "")),
                    },
                )
            )
        return events

    def _risk_check_events(
        self, table: str, row: Mapping[str, Any], warnings: list[str]
    ) -> list[dict[str, Any]]:
        approved = bool(row.get("approved", False))
        reasons = _list(row.get("reasons"))
        if not approved and not reasons:
            reasons = ["unspecified_rejection"]
            warnings.append("risk_checks row rejected without reasons")
        reason_text = " ".join(str(item) for item in reasons)
        return [
            self._event(
                table,
                row,
                "risk.check.completed",
                run_id=_run_id("order_intents", row, id_key="order_intent_id"),
                symbols=_symbols(row),
                occurred_at=_time(row, "checked_at", "created_at"),
                payload={
                    "risk_check_kind": "deterministic",
                    "approved": approved,
                    "reasons": reasons,
                    "input_summary": _jsonish(row.get("input")),
                    "component": "risk_gate",
                    "severity": "info" if approved else "warning",
                    "money_impact": "none" if approved else "blocked",
                    "blocking_status": "non_blocking"
                    if approved
                    else "trading_blocked",
                    "resolution_status": "resolved" if approved else "open",
                    "next_safe_action": "observe" if approved else "block_artifact",
                    "evidence_refs": _refs(table, row),
                    **_mistake_for_risk_text(reason_text),
                },
            )
        ]

    def _order_intent_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        status = _text(row.get("status"), "")
        if status not in {"rejected", "skipped", "cancelled", "failed"}:
            return []
        reasons = _list(row.get("risk_reasons")) or [
            _text(row.get("status"), "rejected")
        ]
        reason_text = " ".join(str(item) for item in reasons)
        return [
            self._event(
                table,
                row,
                "trade.rejected",
                run_id=_run_id("order_intents", row),
                symbols=_symbols(row),
                occurred_at=_time(row, "updated_at", "created_at"),
                payload={
                    "rejected_by": "order_intent",
                    "reason_code": _slug(reason_text),
                    "symbol": _text(row.get("symbol"), ""),
                    "side": _maybe_text(row.get("side")),
                    "order_type": _maybe_text(row.get("order_type")),
                    "component": "risk_gate",
                    "severity": "warning",
                    "money_impact": "blocked",
                    "blocking_status": "trading_blocked",
                    "resolution_status": "open",
                    "next_safe_action": "block_artifact",
                    "evidence_refs": _refs(table, row),
                    **_mistake_for_risk_text(reason_text),
                },
            )
        ]

    def _trade_journal_events(
        self, table: str, row: Mapping[str, Any], warnings: list[str]
    ) -> list[dict[str, Any]]:
        symbol = _text(row.get("symbol"), "")
        side = _text(row.get("side"), "")
        if not symbol or not side:
            warnings.append(
                "trade_journal row skipped because symbol or side is missing"
            )
            return []
        return [
            self._event(
                table,
                row,
                "paper.fill.recorded",
                run_id=_run_id(table, row),
                symbols=[symbol],
                occurred_at=_time(row, "created_at"),
                payload={
                    "symbol": symbol,
                    "side": side,
                    "qty": _maybe_decimal(row.get("qty")),
                    "price": _maybe_decimal(row.get("price")),
                    "broker_order_hash": hash_text(
                        _text(row.get("broker_order_id"), "")
                    )
                    if row.get("broker_order_id")
                    else None,
                    "component": "execution",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _portfolio_snapshot_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        return [
            self._event(
                table,
                row,
                "portfolio.snapshot.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "captured_at", "created_at"),
                payload={
                    "portfolio_kind": "paper",
                    "broker": _maybe_text(row.get("broker")),
                    "cash": _maybe_decimal(row.get("cash")),
                    "buying_power_visibility_only": _maybe_decimal(
                        row.get("buying_power")
                    ),
                    "equity": _maybe_decimal(row.get("equity")),
                    "component": "portfolio",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _scorecard_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        mistake = {}
        if _decimal(row.get("external_capital_flow")):
            mistake = {
                "mistake_family": "pnl.deposit_as_profit"
                if _decimal(row.get("external_capital_flow")) > 0
                else "pnl.withdrawal_as_loss",
                "severity": "info",
                "money_impact": "none",
                "blocking_status": "non_blocking",
                "resolution_status": "resolved",
                "next_safe_action": "observe",
            }
        return [
            self._event(
                table,
                row,
                "portfolio.snapshot.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "captured_at", "created_at"),
                payload={
                    "portfolio_kind": "performance_scorecard",
                    "broker": _maybe_text(row.get("broker")),
                    "session_date": _maybe_text(row.get("session_date")),
                    "equity": _maybe_decimal(row.get("equity")),
                    "cash": _maybe_decimal(row.get("cash")),
                    "external_capital_flow": _maybe_decimal(
                        row.get("external_capital_flow")
                    ),
                    "trading_pnl_amount": _maybe_decimal(row.get("trading_pnl_amount")),
                    "trading_pnl_pct": _maybe_decimal(row.get("trading_pnl_pct")),
                    "component": "performance",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                    **mistake,
                },
            )
        ]

    def _replay_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        raw = _jsonish(row.get("raw"))
        external = "external_api" in strict_json_dumps(raw).lower()
        return [
            self._event(
                table,
                row,
                "replay.result.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "completed_at", "created_at", "started_at"),
                payload={
                    "suite_name": _text(
                        row.get("suite_name") or row.get("name") or table, table
                    ),
                    "status": _text(row.get("status") or row.get("state"), "succeeded"),
                    "case_count": _int(
                        row.get("case_count") or row.get("result_count") or 1
                    ),
                    "component": "replay",
                    "severity": "warning" if external else "info",
                    "evidence_refs": _refs(table, row),
                    **(
                        {
                            "mistake_family": "replay.external_api_called",
                            "money_impact": "possible",
                            "blocking_status": "workflow_deferred",
                            "resolution_status": "open",
                            "next_safe_action": "run_test",
                        }
                        if external
                        else {}
                    ),
                },
            )
        ]

    def _shadow_allocation_events(
        self, table: str, row: Mapping[str, Any], warnings: list[str]
    ) -> list[dict[str, Any]]:
        del warnings
        run_id = _run_id(table, row)
        status = "failed" if row.get("failure_bucket") else "succeeded"
        common = {
            "run_type": "shadow_allocation",
            "component": "shadow",
            "profile_id": _maybe_text(row.get("profile_id")),
            "provider": _maybe_text(row.get("provider")),
            "model": _maybe_text(row.get("model")),
            "evidence_refs": _refs(table, row),
        }
        return [
            self._event(
                table,
                row,
                "agent.run.started",
                run_id=run_id,
                occurred_at=_time(row, "created_at"),
                payload=common,
                environment="shadow",
            ),
            self._event(
                table,
                row,
                "agent.run.completed",
                run_id=run_id,
                occurred_at=_time(row, "completed_at", "updated_at", "created_at"),
                payload={
                    **common,
                    "status": status,
                    "final_reason": _maybe_text(row.get("failure_bucket")),
                    "severity": "warning" if status == "failed" else "info",
                    **(
                        {
                            "mistake_family": "shadow.stale_running_state",
                            "money_impact": "none",
                            "blocking_status": "non_blocking",
                            "resolution_status": "open",
                            "next_safe_action": "retry",
                        }
                        if status == "failed"
                        else {}
                    ),
                },
                environment="shadow",
            ),
        ]

    def _shadow_fill_events(
        self, table: str, row: Mapping[str, Any], warnings: list[str]
    ) -> list[dict[str, Any]]:
        symbol = _text(row.get("symbol"), "")
        side = _text(row.get("side"), "")
        if not symbol or not side:
            warnings.append(
                "shadow_order_fills row skipped because symbol or side is missing"
            )
            return []
        return [
            self._event(
                table,
                row,
                "paper.fill.recorded",
                run_id=_run_id(table, row),
                symbols=[symbol],
                occurred_at=_time(row, "created_at", "filled_at"),
                environment="shadow",
                payload={
                    "symbol": symbol,
                    "side": side,
                    "qty": _maybe_decimal(row.get("qty")),
                    "price": _maybe_decimal(row.get("price")),
                    "profile_id": _maybe_text(row.get("profile_id")),
                    "component": "shadow",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                },
            )
        ]

    def _shadow_scorecard_events(
        self, table: str, row: Mapping[str, Any], _warnings: list[str]
    ) -> list[dict[str, Any]]:
        failed_count = _int(row.get("failed_run_count"))
        mistake = {}
        if failed_count:
            mistake = {
                "mistake_family": "shadow.unfair_cadence",
                "severity": "warning",
                "money_impact": "none",
                "blocking_status": "non_blocking",
                "resolution_status": "open",
                "next_safe_action": "observe",
            }
        return [
            self._event(
                table,
                row,
                "portfolio.snapshot.recorded",
                run_id=_run_id(table, row),
                occurred_at=_time(row, "as_of", "created_at"),
                environment="shadow",
                payload={
                    "portfolio_kind": "shadow_scorecard",
                    "profile_id": _text(row.get("profile_id"), "shadow"),
                    "return_pct": _maybe_decimal(row.get("return_pct")),
                    "max_drawdown_pct": _maybe_decimal(row.get("max_drawdown_pct")),
                    "failed_run_count": failed_count,
                    "trade_count": _int(row.get("trade_count")),
                    "component": "shadow",
                    "severity": "info",
                    "evidence_refs": _refs(table, row),
                    **mistake,
                },
            )
        ]

    def _event(
        self,
        table: str,
        row: Mapping[str, Any],
        event_type: str,
        *,
        payload: Mapping[str, Any],
        run_id: str,
        occurred_at: str,
        symbols: list[str] | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        client = self._client(environment or self.environment)
        row_id = _row_id(row)
        resolved_environment = environment or self.environment
        return client.event(
            event_type,
            run_id=run_id,
            symbols=symbols,
            occurred_at=occurred_at,
            event_id=_stable_id("evt", table, row_id, event_type, resolved_environment),
            idempotency_key=(
                f"{self.project}/{resolved_environment}/{table}/{row_id}/{event_type}"
            ),
            payload={key: value for key, value in payload.items() if value is not None},
        )

    def _client(self, environment: str) -> Ellzaf:
        client = self._clients.get(environment)
        if client is None:
            client = Ellzaf(
                Config(
                    project=self.project,
                    agent_id=self.agent_id,
                    environment=environment,
                    queue_dir=None,
                    telemetry_enabled=False,
                    max_event_bytes=self.max_event_bytes,
                )
            )
            self._clients[environment] = client
        return client


def _table_surface_map() -> dict[str, str]:
    return {
        "prompt_versions": "llm.call.started",
        "llm_runs": "llm.call.completed",
        "llm_tool_calls": "tool.call.completed",
        "search_usage_events": "cost.usage.recorded",
        "research_reports": "source.claim.recorded",
        "source_quality_repair_incidents": "error.recorded",
        "market_tape_snapshots": "market.snapshot.recorded",
        "market_regime_snapshots": "market.snapshot.recorded",
        "memory_fact_usage": "memory.read.completed",
        "portfolio_allocation_runs": "agent.run.completed",
        "portfolio_targets": "decision.proposed",
        "portfolio_rebalance_actions": "decision.proposed",
        "risk_checks": "risk.check.completed",
        "order_intents": "trade.rejected",
        "trade_journal": "paper.fill.recorded",
        "portfolio_snapshots": "portfolio.snapshot.recorded",
        "portfolio_performance_scorecards": "portfolio.snapshot.recorded",
        "decision_replay_runs": "replay.result.recorded",
        "harness_eval_runs": "replay.result.recorded",
        "harness_replay_runs": "replay.result.recorded",
        "shadow_allocation_runs": "agent.run.completed",
        "shadow_order_fills": "paper.fill.recorded",
        "shadow_profile_scorecards": "portfolio.snapshot.recorded",
    }


def _run_id(table: str, row: Mapping[str, Any], *, id_key: str = "id") -> str:
    return _stable_id("run", table, _text(row.get(id_key) or row.get("id"), "unknown"))


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = sha256(strict_json_dumps(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _row_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("id"), _stable_id("row", row))


def _time(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return _to_utc_iso(value)
    return utc_now_iso() if not row else _DEFAULT_OCCURRED_AT


def _to_utc_iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return _DEFAULT_OCCURRED_AT
        if text.endswith("Z"):
            return text
        normalized = text.replace(" ", "T")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return _DEFAULT_OCCURRED_AT
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return _DEFAULT_OCCURRED_AT


def _refs(table: str, row: Mapping[str, Any]) -> list[dict[str, str]]:
    return [{"table": table, "id": _row_id(row)}]


def _symbols(row: Mapping[str, Any]) -> list[str]:
    symbol = row.get("symbol")
    return [_text(symbol, "")] if symbol else []


def _text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _maybe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _jsonish(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return to_jsonable(value)


def _list(value: Any) -> list[Any]:
    value = _jsonish(value)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, str) and value:
        return [value]
    return []


def _count_items(value: Any) -> int:
    value = _jsonish(value)
    if value is None:
        return 0
    if isinstance(value, list | tuple | set):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int(value)


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _maybe_decimal(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(_decimal(value))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _mistake_for_llm(row: Mapping[str, Any]) -> dict[str, Any]:
    status = _text(row.get("status"), "")
    error = _text(row.get("error"), "").lower()
    if status == "schema_failed":
        return _mistake("llm.malformed_json", component="harness", severity="error")
    if status == "postcondition_failed":
        return _mistake(
            "llm.schema_valid_but_unsafe", component="harness", severity="error"
        )
    if "tool" in error and "protocol" in error:
        return _mistake(
            "llm.tool_loop_protocol_error", component="harness", severity="error"
        )
    if status == "failed":
        return {
            "severity": "error",
            "money_impact": "none",
            "blocking_status": "workflow_deferred",
            "resolution_status": "open",
            "next_safe_action": "retry",
        }
    return {}


def _mistake_for_source_text(text: str, row: Mapping[str, Any]) -> dict[str, Any]:
    haystack = f"{text} {strict_json_dumps(_jsonish(row.get('source_digest')))}".lower()
    markers = (
        "truncated",
        "source-limited",
        "source limited",
        "missing financial",
        "missing evidence",
        "provider exhausted",
        "xbrl noise",
    )
    if any(marker in haystack for marker in markers):
        return _mistake(
            "source.truncated_or_missing_evidence",
            component="research",
            severity="warning",
            money_impact="possible",
            blocking_status="artifact_blocked",
            next_safe_action="retry",
        )
    if "wrong provider" in haystack or "disabled provider" in haystack:
        return _mistake(
            "source.wrong_provider", component="research", severity="warning"
        )
    return {}


def _mistake_for_cost(row: Mapping[str, Any]) -> dict[str, Any]:
    purpose = _text(row.get("purpose"), "").lower()
    if purpose in {"", "unknown", "none"} and _int(row.get("query_count")):
        return _mistake(
            "cost.api_without_purpose", component="cost", severity="warning"
        )
    return {}


def _mistake_for_risk_text(text: str) -> dict[str, Any]:
    haystack = text.lower()
    if "buying power" in haystack or "margin" in haystack:
        return _mistake(
            "portfolio.buying_power_as_cash",
            component="risk_gate",
            severity="critical",
            money_impact="blocked",
            blocking_status="trading_blocked",
            next_safe_action="block_artifact",
        )
    if "round" in haystack or "fractional" in haystack or "whole share" in haystack:
        return _mistake("portfolio.rounding_after_cash_check", component="risk_gate")
    if "model risk" in haystack or "llm risk" in haystack:
        return _mistake("portfolio.model_as_risk_engine", component="risk_gate")
    return {}


def _mistake(
    family: str,
    *,
    component: str,
    severity: str = "warning",
    money_impact: str = "possible",
    blocking_status: str = "workflow_deferred",
    resolution_status: str = "open",
    next_safe_action: str = "observe",
) -> dict[str, Any]:
    return {
        "mistake_family": family,
        "component": component,
        "severity": severity,
        "money_impact": money_impact,
        "blocking_status": blocking_status,
        "resolution_status": resolution_status,
        "next_safe_action": next_safe_action,
    }


def _resolution_status(status: str) -> str:
    return {
        "detected": "open",
        "queued": "queued",
        "running": "retrying",
        "repaired_clean": "resolved",
        "repaired_still_blocked": "open",
        "superseded_by_clean_report": "superseded",
        "operator_notified": "open",
        "cooldown": "retrying",
        "escalated": "open",
    }.get(status, "open")


def _allocation_status(row: Mapping[str, Any]) -> str:
    state = _text(row.get("state"), "").lower()
    if state == "error":
        return "failed"
    if state in {"superseded", "no_action"}:
        return "partial"
    return "succeeded"


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value.strip()]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:80] or "rejected"
