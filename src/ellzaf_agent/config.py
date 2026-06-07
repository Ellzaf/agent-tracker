"""Configuration loading for Ellzaf Agent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from pathlib import Path

from ellzaf_agent.constants import (
    DEFAULT_ENDPOINT,
    DEFAULT_ENVIRONMENT,
    DEFAULT_FLUSH_INTERVAL_SECONDS,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_BATCH_BYTES,
    DEFAULT_MAX_BATCH_EVENTS,
    DEFAULT_MAX_EVENT_BYTES,
    DEFAULT_MAX_QUEUE_BYTES,
    DEFAULT_QUEUE_DIR,
    SUPPORTED_ENVIRONMENTS,
)
from ellzaf_agent.errors import ConfigError


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
    parsed = int(value)
    if parsed < minimum:
        raise ConfigError(f"value must be >= {minimum}: {value!r}")
    return parsed


def _float(value: str | None, *, default: float, minimum: float) -> float:
    if value is None or value == "":
        return default
    parsed = float(value)
    if parsed < minimum:
        raise ConfigError(f"value must be >= {minimum}: {value!r}")
    return parsed


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
    queue_dir: Path | None = Path(DEFAULT_QUEUE_DIR)
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES
    max_batch_events: int = DEFAULT_MAX_BATCH_EVENTS
    max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES
    max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES
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
        if "/" in project:
            raise ConfigError("project must not contain '/'")
        if environment not in SUPPORTED_ENVIRONMENTS:
            raise ConfigError(f"unsupported environment: {environment}")
        if not agent_id:
            raise ConfigError("agent_id is required")
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
        if self.flush_interval_seconds < 0:
            raise ConfigError("flush_interval_seconds must be >= 0")
        if self.http_timeout_seconds <= 0:
            raise ConfigError("http_timeout_seconds must be > 0")

        object.__setattr__(self, "project", project)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "endpoint", endpoint)
