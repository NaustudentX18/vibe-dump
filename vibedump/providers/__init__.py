"""Provider registry factories.

Two factories:
- `fake_registry()` for tests + zero-config dev (no network, no keys).
- `build_registry()` for runtime: wires real adapters, reads API keys from
  env, and silently drops providers whose required key is missing.
"""

from __future__ import annotations

import os

from .base import HTTPError, LLMProvider, ProviderHealth, ProviderRegistry
from .gemini import GeminiLLM
from .groq import GroqLLM
from .llm import FakeLLM
from .local_pc import LocalPcLLM
from .minimax import MiniMaxLLM
from .nvidia import NvidiaLLM
from .openai_provider import OpenAICompatLLM
from .openrouter import OpenRouterLLM
from .piper_tts import PiperTTS
from .stt import FakeSTT
from .tts import FakeTTS
from .whisper_stt import WhisperSTT


def fake_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm["fake"] = FakeLLM()
    registry.tts["fake"] = FakeTTS()

    # Add the fake variants of the real audio adapters if their helper
    # modules are importable. Any failure here is non-fatal — fake-only
    # callers (tests, zero-config dev) still get the original fakes.
    try:
        registry.stt["whisper_fake"] = WhisperSTT.fake()
    except Exception:  # noqa: BLE001 - defensive: any failure means skip
        pass
    try:
        registry.tts["piper_fake"] = PiperTTS.fake()
    except Exception:  # noqa: BLE001
        pass
    return registry


def build_registry(*, include_local_pc: bool = True) -> ProviderRegistry:
    """Wire real providers. Missing keys -> provider omitted, no failure."""
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.tts["fake"] = FakeTTS()

    cloud_providers: list[LLMProvider] = [
        OpenAICompatLLM(),
        OpenRouterLLM(),
        NvidiaLLM(),
        GroqLLM(),
        MiniMaxLLM(),
        GeminiLLM(),
    ]
    for provider in cloud_providers:
        if getattr(provider, "configured", True):
            registry.llm[provider.name] = provider

    if include_local_pc:
        local = LocalPcLLM()
        registry.llm[local.name] = local

    # If literally nothing configured, fall back to fake so the UI still works.
    if not registry.llm:
        registry.llm["fake"] = FakeLLM()

    # Real audio adapters. Their heavy deps (faster-whisper, piper) are
    # imported lazily inside the adapter; the constructor always succeeds
    # so the gate is `.health().ok`, which probes the import without
    # forcing us to actually load the model.
    try:
        whisper = WhisperSTT()
        if whisper.health().ok:
            registry.stt["whisper"] = whisper
    except Exception:  # noqa: BLE001 - defensive: any failure means skip
        pass
    try:
        piper = PiperTTS()
        if piper.health().ok:
            registry.tts["piper"] = piper
    except Exception:  # noqa: BLE001
        pass
    return registry


def has_any_real_key() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "NVIDIA_API_KEY",
            "GROQ_API_KEY",
            "MINIMAX_API_KEY",
            "GEMINI_API_KEY",
        )
    )


__all__ = [
    "HTTPError",
    "ProviderHealth",
    "ProviderRegistry",
    "fake_registry",
    "build_registry",
    "has_any_real_key",
    "FakeLLM",
    "FakeSTT",
    "FakeTTS",
    "WhisperSTT",
    "PiperTTS",
    "OpenAICompatLLM",
    "OpenRouterLLM",
    "NvidiaLLM",
    "GroqLLM",
    "MiniMaxLLM",
    "GeminiLLM",
    "LocalPcLLM",
]
