"""MascotState: typed state enum for the SmartMascot."""

from __future__ import annotations

from enum import Enum


class MascotMood(str, Enum):
    """High-level mood that drives the mascot's visual appearance."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    HAPPY = "happy"
    ERROR = "error"


class MascotState:
    """Holds the current observable state of the SmartMascot.

    Attributes
    ----------
    mood:
        The current :class:`MascotMood`.
    label:
        Short human-readable status text (e.g. ``"Thinking…"``).
    dump_id:
        The active dump id, or ``None`` when idle.
    """

    def __init__(
        self,
        mood: MascotMood = MascotMood.IDLE,
        label: str = "",
        dump_id: int | None = None,
    ) -> None:
        self.mood = mood
        self.label = label
        self.dump_id = dump_id

    def update(
        self,
        mood: MascotMood | None = None,
        label: str | None = None,
        dump_id: int | None = None,
    ) -> None:
        if mood is not None:
            self.mood = mood
        if label is not None:
            self.label = label
        if dump_id is not None:
            self.dump_id = dump_id

    def __repr__(self) -> str:
        return (
            f"MascotState(mood={self.mood.value!r}, label={self.label!r}, "
            f"dump_id={self.dump_id!r})"
        )
