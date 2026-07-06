from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Scenario:
    name: str
    domain: str
    prompt: str
    context: dict[str, Any]
    setup: dict[str, Any]
    fake_model: dict[str, Any]
    expected: dict[str, Any]
    path: Path


def load_scenario(path: Path) -> Scenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Scenario(
        name=str(data["name"]),
        domain=str(data.get("domain", path.parts[-3] if len(path.parts) >= 3 else "unknown")),
        prompt=str(data["prompt"]),
        context=dict(data.get("context", {})),
        setup=dict(data.get("setup", {})),
        fake_model=dict(data["fake_model"]),
        expected=dict(data["expected"]),
        path=path,
    )
