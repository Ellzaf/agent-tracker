"""Typed exceptions raised by the Ellzaf Agent Tracker SDK."""


class AgentTrackerError(Exception):
    """Base exception for SDK-controlled errors."""


class ConfigError(AgentTrackerError):
    """Raised when configuration is invalid."""


class SchemaValidationError(AgentTrackerError, ValueError):
    """Raised when an event fails local schema validation."""


class RedactionError(AgentTrackerError):
    """Raised when redaction cannot make an event safe to store or upload."""


class QueueError(AgentTrackerError):
    """Raised when the local queue cannot persist or read telemetry safely."""


class UploadError(AgentTrackerError):
    """Raised when upload cannot complete due to transport or server behavior."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "upload_error",
        retryable: bool = True,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
