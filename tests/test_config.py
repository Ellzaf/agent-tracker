from pathlib import Path

import pytest

from ellzaf_agent import Config
from ellzaf_agent.errors import ConfigError


def test_config_from_env_uses_safe_defaults(tmp_path: Path) -> None:
    config = Config.from_env(
        project="paper-agent",
        env={"ELLZAF_QUEUE_DIR": str(tmp_path), "ELLZAF_TELEMETRY_ENABLED": "true"},
    )

    assert config.project == "paper-agent"
    assert config.environment == "paper"
    assert config.queue_dir == tmp_path
    assert config.telemetry_enabled is True
    assert config.store_full_io is False


def test_config_rejects_invalid_environment() -> None:
    with pytest.raises(ConfigError):
        Config(project="paper-agent", environment="live")


def test_config_can_disable_local_queue() -> None:
    config = Config.from_env(
        project="paper-agent",
        env={"ELLZAF_QUEUE_DIR": "", "ELLZAF_TELEMETRY_ENABLED": "false"},
    )

    assert config.queue_dir is None
    assert config.telemetry_enabled is False


def test_config_rejects_bad_boolean() -> None:
    with pytest.raises(ConfigError):
        Config.from_env(
            project="paper-agent", env={"ELLZAF_TELEMETRY_ENABLED": "maybe"}
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_event_bytes": 1023},
        {"max_batch_events": 0},
        {"max_batch_bytes": 1023},
        {"max_queue_bytes": 1023},
        {"max_event_bytes": 4096, "max_batch_bytes": 2048},
        {"flush_interval_seconds": -0.1},
        {"http_timeout_seconds": 0},
    ],
)
def test_config_rejects_invalid_direct_numeric_limits(
    kwargs: dict[str, int | float],
) -> None:
    with pytest.raises(ConfigError):
        Config(project="paper-agent", **kwargs)
