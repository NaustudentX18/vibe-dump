"""Mascot rendering placeholder.

A PIL/procedural 240x280 mascot renderer is planned for Milestone 5. This MVP
returns lightweight frame descriptors so the rest of the app can be state-ready.
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import DeviceState


@dataclass(frozen=True, slots=True)
class MascotFrame:
    state: DeviceState
    label: str


class FakeMascotRenderer:
    def render(self, state: DeviceState) -> MascotFrame:
        return MascotFrame(state=state, label=f"Dumpi:{state.value}")
