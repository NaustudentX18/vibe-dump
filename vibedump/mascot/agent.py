"""SmartMascot: event-driven mascot stub.

The SmartMascot listens to the :class:`~vibedump.events.EventBus` and
updates its :class:`~vibedump.mascot.state.MascotState` accordingly.
The rendering side-effects (PIL image generation, WebSocket push) are
intentionally decoupled and handled by separate modules so the mascot
can be tested without display dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .state import MascotMood, MascotState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...vibedump.events import EventBus


_EVENT_MOOD_MAP: dict[str, MascotMood] = {
    "dump.status": MascotMood.THINKING,
    "dump.assistant.ask": MascotMood.LISTENING,
    "blueprint.generated": MascotMood.HAPPY,
    "agent.trace": MascotMood.IDLE,
}


class SmartMascot:
    """Stub mascot that tracks pipeline state via event bus subscriptions.

    Parameters
    ----------
    bus:
        Optional :class:`~vibedump.events.EventBus`.  When provided the
        mascot can be driven by calling :meth:`on_event` with events
        pulled from the bus.  The bus itself is *not* subscribed to
        automatically — callers drive the loop.
    """

    def __init__(self, bus: "EventBus | None" = None) -> None:
        self._bus = bus
        self.state = MascotState()

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def on_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Update internal state in response to a bus event."""
        payload = payload or {}
        mood = _EVENT_MOOD_MAP.get(event_type)

        if event_type == "dump.status":
            status = payload.get("status", "")
            mood = {
                "listening": MascotMood.LISTENING,
                "thinking": MascotMood.THINKING,
                "ready": MascotMood.HAPPY,
            }.get(status, MascotMood.IDLE)
            dump_id = payload.get("dump_id")
            self.state.update(mood=mood, label=status.capitalize(), dump_id=dump_id)
        elif mood is not None:
            self.state.update(mood=mood)
        # Unknown events are silently ignored.

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Return the mascot to the IDLE state."""
        self.state = MascotState()

    @property
    def mood(self) -> MascotMood:
        return self.state.mood
