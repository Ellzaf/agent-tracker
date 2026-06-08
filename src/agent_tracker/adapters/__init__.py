"""Optional adapters for known trading-agent repo shapes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from agent_tracker.integration import AgentTrackerIntegrationReport


class AgentTrackerAdapter(Protocol):
    """Structural protocol for Agent Tracker exporters and repo adapters."""

    name: str
    profile: str

    def integration_report(
        self,
        rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        warnings: Sequence[str] = (),
    ) -> AgentTrackerIntegrationReport:
        """Return integration coverage for known source rows."""

    def events_from_rows(
        self,
        rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> tuple[list[dict[str, Any]], Any]:
        """Map source rows into Agent Tracker events."""

    def export_jsonl(
        self,
        output: str | Path,
        **kwargs: Any,
    ) -> Any:
        """Write mapped events to JSONL and return an adapter summary."""


__all__ = ["AgentTrackerAdapter"]
