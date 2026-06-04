"""Provider interfaces and registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    name: str
    ok: bool
    detail: str = ""


class STTProvider(Protocol):
    name: str
    def transcribe(self, audio_path: str) -> str: ...
    def health(self) -> ProviderHealth: ...


class LLMProvider(Protocol):
    name: str
    def complete(self, prompt: str) -> str: ...
    def health(self) -> ProviderHealth: ...


class TTSProvider(Protocol):
    name: str
    def synthesize(self, text: str) -> bytes: ...
    def health(self) -> ProviderHealth: ...


class ProviderRegistry:
    def __init__(self) -> None:
        self.stt: dict[str, STTProvider] = {}
        self.llm: dict[str, LLMProvider] = {}
        self.tts: dict[str, TTSProvider] = {}

    def health(self) -> list[ProviderHealth]:
        providers = [*self.stt.values(), *self.llm.values(), *self.tts.values()]
        return [provider.health() for provider in providers]
