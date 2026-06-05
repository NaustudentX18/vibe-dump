"""Vibe-Dump mascot subsystem.

Provides :class:`SmartMascot`, :class:`MascotState`, and :class:`MascotMood`.
"""

from __future__ import annotations

from .agent import SmartMascot
from .state import MascotMood, MascotState

__all__ = [
    "MascotMood",
    "MascotState",
    "SmartMascot",
]
