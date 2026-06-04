"""Provider registry factory for fake-first development."""

from .base import ProviderRegistry
from .llm import FakeLLM
from .stt import FakeSTT
from .tts import FakeTTS


def fake_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.stt["fake"] = FakeSTT()
    registry.llm["fake"] = FakeLLM()
    registry.tts["fake"] = FakeTTS()
    return registry

__all__ = ["ProviderRegistry", "fake_registry", "FakeSTT", "FakeLLM", "FakeTTS"]
