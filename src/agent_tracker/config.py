"""Configuration loading for Ellzaf Agent Tracker."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from pathlib import Path

from agent_tracker.constants import (
    DEFAULT_ENDPOINT,
    DEFAULT_ENVIRONMENT,
    DEFAULT_FLUSH_INTERVAL_SECONDS,
    DEFAULT_GZIP_ENABLED,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_BATCH_BYTES,
    DEFAULT_MAX_BATCH_EVENTS,
    DEFAULT_MAX_EVENT_BYTES,
    DEFAULT_MAX_QUEUE_BYTES,
    DEFAULT_QUEUE_DIR,
    DEFAULT_SAMPLE_RATE,
    SUPPORTED_ENVIRONMENTS,
)
from agent_tracker.errors import ConfigError


def _bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"invalid boolean value: {value!r}")


def _int(value: str | None, *, default: int, minimum: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"invalid integer value: {value!r}") from exc
    if parsed < minimum:
        raise ConfigError(f"value must be >= {minimum}: {value!r}")
    return parsed


def _optional_int(value: str | None, *, minimum: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"invalid integer value: {value!r}") from exc
    if parsed < minimum:
        raise ConfigError(f"value must be >= {minimum}: {value!r}")
    return parsed


def _float(value: str | None, *, default: float, minimum: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigError(f"invalid float value: {value!r}") from exc
    if parsed < minimum:
        raise ConfigError(f"value must be >= {minimum}: {value!r}")
    return parsed


def _sample_rate(value: str | None, *, default: float) -> float:
    parsed = _float(value, default=default, minimum=0.0)
    if parsed > 1.0:
        raise ConfigError(f"value must be <= 1.0: {value!r}")
    return parsed


def _contains_forbidden_identifier_char(value: str) -> bool:
    return any(char.isspace() or char in {"/", "\\", "\x00"} for char in value)


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration for a telemetry client."""

    project: str
    environment: str = DEFAULT_ENVIRONMENT
    agent_id: str = "local-agent"
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str | None = None
    telemetry_enabled: bool = True
    store_full_io: bool = False
    gzip_enabled: bool = DEFAULT_GZIP_ENABLED
    sample_rate: float = DEFAULT_SAMPLE_RATE
    always_capture_errors: bool = True
    always_capture_risk_blocks: bool = True
    dedupe_idempotency_keys: bool = False
    queue_dir: Path | None = Path(DEFAULT_QUEUE_DIR)
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES
    max_batch_events: int = DEFAULT_MAX_BATCH_EVENTS
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES
    max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES
    max_events_per_run: int | None = None
    max_events_per_day: int | None = None
    max_upload_bytes_per_day: int | None = None
    flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS

    @classmethod
    def from_env(
        cls,
        *,
        project: str | None = None,
        environment: str | None = None,
        agent_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Config:
        source = environ if env is None else env
        resolved_project = project or source.get("ELLZAF_PROJECT")
        if not resolved_project:
            raise ConfigError("project is required")

        queue_dir_raw = source.get("ELLZAF_QUEUE_DIR", DEFAULT_QUEUE_DIR)
        queue_dir = Path(queue_dir_raw) if queue_dir_raw.strip() else None
        api_key = source.get("ELLZAF_API_KEY") or None
        telemetry_default = bool(api_key or queue_dir)

        return cls(
            project=resolved_project,
            environment=environment
            or source.get("ELLZAF_ENVIRONMENT")
            or DEFAULT_ENVIRONMENT,
            agent_id=agent_id or source.get("ELLZAF_AGENT_ID") or "local-agent",
            endpoint=source.get("ELLZAF_ENDPOINT") or DEFAULT_ENDPOINT,
            api_key=api_key,
            telemetry_enabled=_bool(
                source.get("ELLZAF_TELEMETRY_ENABLED"),
                default=telemetry_default,
            ),
            store_full_io=_bool(source.get("ELLZAF_STORE_FULL_IO"), default=False),
            gzip_enabled=_bool(
                source.get("ELLZAF_GZIP"),
                default=DEFAULT_GZIP_ENABLED,
            ),
            sample_rate=_sample_rate(
                source.get("ELLZAF_SAMPLE_RATE"),
                default=DEFAULT_SAMPLE_RATE,
            ),
            always_capture_errors=_bool(
                source.get("ELLZAF_ALWAYS_CAPTURE_ERRORS"),
                default=True,
            ),
            always_capture_risk_blocks=_bool(
                source.get("ELLZAF_ALWAYS_CAPTURE_RISK_BLOCKS"),
                default=True,
            ),
            dedupe_idempotency_keys=_bool(
                source.get("ELLZAF_DEDUPE_IDEMPOTENCY_KEYS"),
                default=False,
            ),
            queue_dir=queue_dir,
            max_event_bytes=_int(
                source.get("ELLZAF_MAX_EVENT_BYTES"),
                default=DEFAULT_MAX_EVENT_BYTES,
                minimum=1024,
            ),
            max_batch_events=_int(
                source.get("ELLZAF_MAX_BATCH_EVENTS"),
                default=DEFAULT_MAX_BATCH_EVENTS,
                minimum=1,
            ),
            max_batch_bytes=_int(
                source.get("ELLZAF_MAX_BATCH_BYTES"),
                default=DEFAULT_MAX_BATCH_BYTES,
                minimum=1024,
            ),
            max_queue_bytes=_int(
                source.get("ELLZAF_MAX_QUEUE_BYTES"),
                default=DEFAULT_MAX_QUEUE_BYTES,
                minimum=1024,
            ),
            max_events_per_run=_optional_int(
                source.get("ELLZAF_MAX_EVENTS_PER_RUN"),
                minimum=1,
            ),
            max_events_per_day=_optional_int(
                source.get("ELLZAF_MAX_EVENTS_PER_DAY"),
                minimum=1,
            ),
            max_upload_bytes_per_day=_optional_int(
                source.get("ELLZAF_MAX_UPLOAD_BYTES_PER_DAY"),
                minimum=1024,
            ),
            flush_interval_seconds=_float(
                source.get("ELLZAF_FLUSH_INTERVAL_SECONDS"),
                default=DEFAULT_FLUSH_INTERVAL_SECONDS,
                minimum=0.0,
            ),
            http_timeout_seconds=_float(
                source.get("ELLZAF_HTTP_TIMEOUT_SECONDS"),
                default=DEFAULT_HTTP_TIMEOUT_SECONDS,
                minimum=0.1,
            ),
        )

    def __post_init__(self) -> None:
        project = self.project.strip()
        environment = self.environment.strip()
        agent_id = self.agent_id.strip()
        endpoint = self.endpoint.strip().rstrip("/")

        if not project:
            raise ConfigError("project is required")
        if _contains_forbidden_identifier_char(project):
            raise ConfigError("project contains unsupported characters")
        if environment not in SUPPORTED_ENVIRONMENTS:
            raise ConfigError(f"unsupported environment: {environment}")
        if not agent_id:
            raise ConfigError("agent_id is required")
        if _contains_forbidden_identifier_char(agent_id):
            raise ConfigError("agent_id contains unsupported characters")
        if not endpoint:
            raise ConfigError("endpoint is required")
        if self.max_event_bytes < 1024:
            raise ConfigError("max_event_bytes must be >= 1024")
        if self.max_batch_events < 1:
            raise ConfigError("max_batch_events must be >= 1")
        if self.max_batch_bytes < 1024:
            raise ConfigError("max_batch_bytes must be >= 1024")
        if self.max_queue_bytes < 1024:
            raise ConfigError("max_queue_bytes must be >= 1024")
        if self.max_event_bytes > self.max_batch_bytes:
            raise ConfigError("max_event_bytes must be <= max_batch_bytes")
        if not 0 <= self.sample_rate <= 1:
            raise ConfigError("sample_rate must be between 0 and 1")
        if self.max_events_per_run is not None and self.max_events_per_run < 1:
            raise ConfigError("max_events_per_run must be >= 1")
        if self.max_events_per_day is not None and self.max_events_per_day < 1:
            raise ConfigError("max_events_per_day must be >= 1")
        if (
            self.max_upload_bytes_per_day is not None
            and self.max_upload_bytes_per_day < 1024
        ):
            raise ConfigError("max_upload_bytes_per_day must be >= 1024")
        if self.flush_interval_seconds < 0:
            raise ConfigError("flush_interval_seconds must be >= 0")
        if self.http_timeout_seconds <= 0:
            raise ConfigError("http_timeout_seconds must be > 0")

        object.__setattr__(self, "project", project)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "endpoint", endpoint)
