from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EventCollector:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: str, **payload) -> None:
        self.events.append({"event": event, **payload})

    def names(self) -> list[str]:
        return [str(event["event"]) for event in self.events]
