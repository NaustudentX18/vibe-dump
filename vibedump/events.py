"""Small in-process event bus for the one-process architecture."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Condition
from typing import Any, Deque


@dataclass(frozen=True, slots=True)
class Event:
    event_type: str
    payload: dict[str, Any]
    created_at: str


class EventBus:
    """Bounded thread-safe event queue for SSE/dashboard updates."""

    def __init__(self, maxlen: int = 256) -> None:
        self._events: Deque[Event] = deque(maxlen=maxlen)
        self._condition = Condition()

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(
            event_type=event_type,
            payload=payload or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._condition:
            self._events.append(event)
            self._condition.notify_all()
        return event

    def snapshot(self) -> list[Event]:
        with self._condition:
            return list(self._events)
