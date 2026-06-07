"""Repository coverage doctor for Ellzaf Agent Tracker instrumentation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agent_tracker.integration import (
    AgentTrackerIntegrationReport,
    IntegrationSurface,
    SourceRef,
)

_TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".env",
    ".example",
    ".go",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sql",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "vendor",
}


@dataclass(frozen=True, slots=True)
class _DocFile:
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class _SurfaceCheck:
    name: str
    event_type: str
    required: bool
    strong: tuple[str, ...]
    weak: tuple[str, ...]
    mistake_families: tuple[str, ...] = ()


_SURFACE_CHECKS = (
    _SurfaceCheck(
        "watchlist_scope",
        "market.snapshot.recorded",
        True,
        ("watchlist", "universe", "allowed_symbols"),
        ("symbols", "ticker"),
    ),
    _SurfaceCheck(
        "source_collection",
        "source.claim.recorded",
        True,
        ("source_quality", "citation", "search_usage", "extract"),
        ("source", "search", "filing", "transcript"),
        ("source.truncated_or_missing_evidence", "source.unbounded_web_use"),
    ),
    _SurfaceCheck(
        "llm_calls",
        "llm.call.completed",
        True,
        ("llm_runs", "chat.completions", "responses.create", "model_call"),
        ("openai", "anthropic", "deepseek", "provider", "model"),
        ("llm.malformed_json", "llm.schema_valid_but_unsafe"),
    ),
    _SurfaceCheck(
        "prompt_versions",
        "llm.call.started",
        True,
        ("prompt_versions", "prompt_hash", "version_hash"),
        ("prompt", "model"),
        ("llm.prompt_drift", "llm.reasoning_mode_drift"),
    ),
    _SurfaceCheck(
        "market_data",
        "market.snapshot.recorded",
        True,
        ("market_bars", "ohlcv", "market_tape", "market_regime"),
        ("market_data", "quote", "bars"),
        ("market.open_session_stale_bars", "market.partial_tape_as_truth"),
    ),
    _SurfaceCheck(
        "memory_reads",
        "memory.read.completed",
        False,
        ("memory_fact_usage", "memory_context_packs", "memory_lifecycle"),
        ("memory", "context_pack"),
        ("memory.missing_lifecycle_defaults_active", "memory.supersession_missing"),
    ),
    _SurfaceCheck(
        "decisions",
        "decision.proposed",
        True,
        ("investment_decisions", "portfolio_targets", "allocation_run"),
        ("decision", "target_weight", "rationale"),
    ),
    _SurfaceCheck(
        "risk_gate",
        "risk.check.completed",
        True,
        ("risk_checks", "risk_validations", "max_position", "cash_only"),
        ("risk", "approved", "veto"),
        ("portfolio.buying_power_as_cash", "portfolio.model_as_risk_engine"),
    ),
    _SurfaceCheck(
        "rejected_trades",
        "trade.rejected",
        True,
        ("order_intents", "trade_rejected", "risk_reasons"),
        ("rejected", "blocked", "skipped"),
    ),
    _SurfaceCheck(
        "paper_fills",
        "paper.fill.recorded",
        True,
        ("trade_journal", "paper_fill", "shadow_order_fills"),
        ("fill", "broker_order"),
    ),
    _SurfaceCheck(
        "portfolio_snapshots",
        "portfolio.snapshot.recorded",
        True,
        ("portfolio_snapshots", "performance_scorecards", "flow_adjusted"),
        ("equity", "cash", "positions"),
        ("pnl.deposit_as_profit", "pnl.current_period_fallback_wrong"),
    ),
    _SurfaceCheck(
        "replay",
        "replay.result.recorded",
        True,
        ("decision_replay", "harness_eval", "golden_scenario"),
        ("replay", "pytest", "scenario"),
        ("replay.external_api_called", "harness.golden_scenario_failed"),
    ),
    _SurfaceCheck(
        "cost",
        "cost.usage.recorded",
        False,
        ("search_usage_events", "token_usage", "estimated_cost"),
        ("cost", "tokens", "credits"),
        ("cost.api_without_purpose", "cost.hidden_provider_call"),
    ),
    _SurfaceCheck(
        "operator_chat",
        "error.recorded",
        False,
        ("telegram", "operator_chat", "command_parser"),
        ("chat", "command"),
        ("chat.question_as_mutation", "chat.destructive_without_direct_command"),
    ),
    _SurfaceCheck(
        "shadow_models",
        "portfolio.snapshot.recorded",
        False,
        ("shadow_profile", "shadow_allocation_runs", "shadow_scorecard"),
        ("shadow",),
        ("shadow.unfair_cadence", "shadow.context_policy_violation"),
    ),
    _SurfaceCheck(
        "orchestration",
        "agent.run.completed",
        False,
        ("runner_jobs", "lease", "heartbeat", "hatchet"),
        ("worker", "queue", "timeout"),
        ("orchestration.timeout_no_terminal_state",),
    ),
    _SurfaceCheck(
        "redaction",
        "error.recorded",
        True,
        ("redact", "redaction", "secret_patterns"),
        ("secret", "token", "account_id"),
        ("security.secret_leak_attempt",),
    ),
    _SurfaceCheck(
        "agent_instructions",
        "agent.run.completed",
        False,
        ("AGENTS.md", "harness-progress", "session-handoff"),
        ("instructions", "definition of done"),
    ),
    _SurfaceCheck(
        "backup_restore",
        "error.recorded",
        False,
        ("backup", "restore", "pg_dump"),
        ("snapshot", "dump"),
        ("backup.missing_or_unverified",),
    ),
)


def doctor_repo(
    path: str | Path, *, max_files: int = 1000
) -> AgentTrackerIntegrationReport:
    root = Path(path).resolve()
    files = tuple(_read_repo_text(root, max_files=max_files))
    surfaces = tuple(
        _surface_from_check(root, files, check) for check in _SURFACE_CHECKS
    )
    warnings = _doctor_warnings(files)
    return AgentTrackerIntegrationReport(
        project=root.name or "repo",
        repo_profile="ebook-like",
        surfaces=surfaces,
        warnings=tuple(warnings),
    )


def format_doctor_report(report: AgentTrackerIntegrationReport) -> str:
    lines = [
        f"Ellzaf Agent Tracker doctor report for {report.project}",
        "",
        "Coverage:",
    ]
    for surface in report.surfaces:
        required = "required" if surface.required else "optional"
        refs = ", ".join(
            ref.file or ref.table or ref.id or "" for ref in surface.source_refs
        )
        suffix = f" [{refs}]" if refs else ""
        lines.append(f"- {surface.name}: {surface.coverage} ({required}){suffix}")
    if report.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in report.warnings)
    missing = report.missing_required()
    if missing:
        lines.extend(["", "Missing required surfaces:"])
        lines.extend(f"- {surface.name}" for surface in missing)
    return "\n".join(lines) + "\n"


def _read_repo_text(root: Path, *, max_files: int) -> Iterable[_DocFile]:
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if path.is_dir():
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix not in _TEXT_SUFFIXES and path.name not in {".env.example"}:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 256_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count += 1
        yield _DocFile(path=path.relative_to(root).as_posix(), text=text)


def _surface_from_check(
    root: Path,
    files: tuple[_DocFile, ...],
    check: _SurfaceCheck,
) -> IntegrationSurface:
    del root
    strong_refs = _find_refs(files, check.strong)
    weak_refs = _find_refs(files, check.weak)
    if strong_refs:
        coverage = "implemented"
        refs = strong_refs[:5]
    elif weak_refs:
        coverage = "partial"
        refs = weak_refs[:5]
    elif check.required:
        coverage = "missing"
        refs = []
    else:
        coverage = "not_found"
        refs = []
    return IntegrationSurface(
        name=check.name,
        event_type=check.event_type,
        coverage=coverage,
        required=check.required,
        source_refs=tuple(SourceRef(file=ref) for ref in refs),
        mistake_families=check.mistake_families,
        notes=tuple(_notes_for(check, coverage)),
    )


def _find_refs(files: tuple[_DocFile, ...], needles: tuple[str, ...]) -> list[str]:
    refs: list[str] = []
    lowered_needles = tuple(item.lower() for item in needles)
    for item in files:
        haystack = f"{item.path}\n{item.text}".lower()
        if any(needle in haystack for needle in lowered_needles):
            refs.append(item.path)
    return sorted(set(refs))


def _notes_for(check: _SurfaceCheck, coverage: str) -> list[str]:
    if coverage == "implemented":
        return ["Map this surface to Ellzaf events and add privacy tests."]
    if coverage == "partial":
        return ["Concepts found. Confirm event fields, ids, redaction, and tests."]
    if check.required:
        return ["Required for ebook-style trading-agent telemetry."]
    return [
        "Optional surface not found. Mark not_applicable if the repo does not use it."
    ]


def _doctor_warnings(files: tuple[_DocFile, ...]) -> list[str]:
    warnings: list[str] = []
    names = {item.path for item in files}
    if "AGENTS.md" not in names:
        warnings.append("No AGENTS.md found for coding-agent integration rules.")
    all_text = "\n".join(item.text.lower() for item in files)
    if "agent_tracker" not in all_text and "agent-tracker" not in all_text:
        warnings.append("No existing Ellzaf Agent Tracker instrumentation found.")
    if "store_full_io=true" in all_text:
        warnings.append("Full prompt/output storage appears enabled. Confirm policy.")
    return warnings
