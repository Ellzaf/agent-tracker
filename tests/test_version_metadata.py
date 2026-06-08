import tomllib
from pathlib import Path

import agent_tracker
from agent_tracker.constants import SDK_USER_AGENT, SDK_VERSION
from agent_tracker.resources import read_text_resource


def test_package_versions_stay_aligned() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]

    assert package_version == SDK_VERSION
    assert package_version == agent_tracker.__version__
    assert f"agent-tracker-python/{package_version}" == SDK_USER_AGENT


def test_package_exposes_typed_marker() -> None:
    assert read_text_resource("py.typed").strip() == ""
