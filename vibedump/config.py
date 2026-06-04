"""Configuration loading for the Vibe-Dump MVP.

Secrets are intentionally loaded from environment variables or local config paths;
example files only are committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProviderSelection:
    stt: str = "fake"
    llm: str = "fake"
    tts: str = "fake"


@dataclass(slots=True)
class Settings:
    data_dir: Path = Path("data")
    database_path: Path = Path("data/vibedump.sqlite3")
    providers: ProviderSelection = field(default_factory=ProviderSelection)
    local_llama_base_url: str = "http://100.127.91.97:8001/v1"
    local_ollama_base_url: str = "http://100.127.91.97:11434/v1"

    @classmethod
    def default(cls) -> "Settings":
        return cls()
