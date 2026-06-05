"""Vibe-Dump skills subsystem.

Provides :class:`SkillRegistry` for registering and invoking named skills,
and :mod:`~vibedump.skills.sandbox` for validating skill source code before
registration.
"""

from __future__ import annotations

from .registry import SkillEntry, SkillRegistry
from .sandbox import ValidationResult, validate_skill_source

__all__ = [
    "SkillEntry",
    "SkillRegistry",
    "ValidationResult",
    "validate_skill_source",
]
