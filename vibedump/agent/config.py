"""Configuration for the openlaude agent core.

`AgentConfig` is a frozen, slotted dataclass. The defaults point at the
local PC Ollama endpoint documented in `vibedump.providers.local_pc`,
so an `OpenClaude` built with `AgentConfig()` and no `llm` will resolve
to the local PC provider via the registry.
"""

from __future__ import annotations

from dataclasses import dataclass


_DEFAULT_SYSTEM_PROMPT = (
    "You are openlaude, a software-engineering agent for Vibe-Dump.\n"
    "You operate on the Vibe-Dump codebase on the Pi. Use the tools available "
    "to you to read Vibe-Dump state, then take action.\n"
    "Prefer running one tool at a time. When you have finished, respond with a "
    "plain text summary."
)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = "qwen3-14b-agent"
    base_url: str = "http://desktop-ujsii52.local:11434"
    max_steps: int = 12
    temperature: float = 0.2
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT
