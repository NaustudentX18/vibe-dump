"""Hardware abstraction layer.

Milestone 0 uses fake hardware only. Real Whisplay/PiSugar integration belongs in
later milestones and must not be touched by unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import DeviceState


@dataclass(slots=True)
class FakeHardware:
    state: DeviceState = DeviceState.IDLE
    led_color: str = "off"
    last_display_text: str = ""

    def set_state(self, state: DeviceState) -> None:
        self.state = state

    def set_led(self, color: str) -> None:
        self.led_color = color

    def draw_text(self, text: str) -> None:
        self.last_display_text = text
