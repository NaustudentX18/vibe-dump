"""Vibe-Dump agent core: the openlaude runtime.

Builds on the local LLM provider registry and runs an inline tool-use
loop in pure Python. Falls back gracefully when pydantic-deep-agents is
not installed (the inline loop is the canonical implementation, so
functionality is identical either way).
"""

from __future__ import annotations

from .config import AgentConfig
from .core import OpenClaude

__all__ = ["OpenClaude", "AgentConfig"]
