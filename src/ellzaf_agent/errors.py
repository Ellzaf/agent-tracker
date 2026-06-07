"""Typed exceptions raised by the Ellzaf Agent SDK."""


class EllzafError(Exception):
    """Base exception for SDK-controlled errors."""


class ConfigError(EllzafError):
    """Raised when configuration is invalid."""


class SchemaValidationError(EllzafError, ValueError):
    """Raised when an event fails local schema validation."""


class RedactionError(EllzafError):
    """Raised when redaction cannot make an event safe to store or upload."""


class QueueError(EllzafError):
    """Raised when the local queue cannot persist or read telemetry safely."""


class UploadError(EllzafError):
    """Raised when upload cannot complete due to transport or server behavior."""
