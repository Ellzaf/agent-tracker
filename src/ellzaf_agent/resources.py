"""Package resource helpers."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def read_text_resource(*parts: str) -> str:
    resource = resources.files("ellzaf_agent").joinpath(*parts)
    return resource.read_text(encoding="utf-8")


def read_json_resource(*parts: str) -> Any:
    return json.loads(read_text_resource(*parts))


def list_resource_names(*parts: str) -> list[str]:
    resource = resources.files("ellzaf_agent").joinpath(*parts)
    return sorted(item.name for item in resource.iterdir() if item.is_file())
