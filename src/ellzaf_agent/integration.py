"""Integration coverage helpers for agent instrumentation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from ellzaf_agent.client import Ellzaf
from ellzaf_agent.constants import SCHEMA_VERSION, SUPPORTED_EVENT_TYPES
from ellzaf_agent.serialization import utc_now_iso
from ellzaf_agent.taxonomy import (
    allowed_mistake_families,
    is_custom_mistake_family,
    taxonomy_values,
)


@dataclass(frozen=True, slots=True)
class SourceRef:
    table: str | None = None
    id: str | None = None
    file: str | None = None
    field: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        if self.file and PurePosixPath(self.file).is_absolute():
            raise ValueError("SourceRef.file must be a relative path")
        if self.line is not None and self.line < 1:
            raise ValueError("SourceRef.line must be positive")
        if not any((self.table, self.id, self.file, self.field)):
            raise ValueError("SourceRef needs a table, id, file, or field")

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "table": self.table,
                "id": self.id,
                "file": self.file,
                "field": self.field,
                "line": self.line,
            }.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class IntegrationSurface:
    name: str
    event_type: str
    coverage: str
    source_refs: tuple[SourceRef, ...] = ()
    required: bool = True
    notes: tuple[str, ...] = ()
    mistake_families: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("IntegrationSurface.name is required")
        if self.event_type not in SUPPORTED_EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type}")
        if self.coverage not in taxonomy_values("coverage_status"):
            raise ValueError(f"unsupported coverage: {self.coverage}")
        unknown = [
            item
            for item in self.mistake_families
            if item not in allowed_mistake_families()
            and not is_custom_mistake_family(item)
        ]
        if unknown:
            raise ValueError(f"unsupported mistake families: {', '.join(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event_type": self.event_type,
            "coverage": self.coverage,
            "required": self.required,
            "source_refs": [item.to_dict() for item in self.source_refs],
            "notes": list(self.notes),
            "mistake_families": list(self.mistake_families),
            "tests": list(self.tests),
        }


@dataclass(frozen=True, slots=True)
class EllzafIntegrationReport:
    project: str
    surfaces: tuple[IntegrationSurface, ...]
    generated_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION
    repo_profile: str | None = None
    warnings: tuple[str, ...] = ()

    def coverage_counts(self) -> dict[str, int]:
        return dict(Counter(surface.coverage for surface in self.surfaces))

    def missing_required(self) -> list[IntegrationSurface]:
        return [
            surface
            for surface in self.surfaces
            if surface.required and surface.coverage in {"missing", "not_found"}
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project": self.project,
            "repo_profile": self.repo_profile,
            "generated_at": self.generated_at,
            "coverage_counts": self.coverage_counts(),
            "missing_required": [item.name for item in self.missing_required()],
            "warnings": list(self.warnings),
            "surfaces": [surface.to_dict() for surface in self.surfaces],
        }


def emit_integration_report(
    client: Ellzaf,
    report: EllzafIntegrationReport,
    *,
    run_id: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    missing = report.missing_required()
    payload = {
        "run_type": "ellzaf_integration_report",
        "status": "partial" if missing or report.warnings else "succeeded",
        "component": "integration",
        "severity": "warning" if missing else "info",
        "coverage_status": "partial" if missing else "implemented",
        "report": report.to_dict(),
        **dict(extra_payload or {}),
    }
    return client.event("agent.run.completed", run_id=run_id, payload=payload)
