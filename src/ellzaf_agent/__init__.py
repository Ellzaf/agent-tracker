"""Ellzaf Agent telemetry SDK."""

from ellzaf_agent.client import Ellzaf, Run
from ellzaf_agent.config import Config
from ellzaf_agent.errors import (
    EllzafError,
    QueueError,
    RedactionError,
    SchemaValidationError,
    UploadError,
)
from ellzaf_agent.integration import (
    EllzafIntegrationReport,
    IntegrationSurface,
    SourceRef,
    emit_integration_report,
)
from ellzaf_agent.reporting import ReportingReadiness, assess_reporting_readiness
from ellzaf_agent.sink import JsonlSink

__all__ = [
    "Config",
    "Ellzaf",
    "EllzafError",
    "EllzafIntegrationReport",
    "IntegrationSurface",
    "JsonlSink",
    "QueueError",
    "RedactionError",
    "ReportingReadiness",
    "Run",
    "SchemaValidationError",
    "SourceRef",
    "UploadError",
    "assess_reporting_readiness",
    "emit_integration_report",
]

__version__ = "0.1.0"
