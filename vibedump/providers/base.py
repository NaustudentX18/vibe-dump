"""Provider interfaces, registry, and HTTP helper.

The Pi Zero 2 W has 512MB of RAM, so HTTP transport stays on the standard
library (`urllib`) instead of pulling in `httpx` / `requests`. Each real
provider is a thin subclass of an OpenAI-compat or Gemini-shape client.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


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


class HTTPError(RuntimeError):
    """Wraps a non-2xx HTTP response from a provider."""


def post_json(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST JSON, return parsed response. Raises HTTPError on non-2xx.

    Uses stdlib only. `timeout` is the urllib read timeout in seconds; the
    connect timeout is fixed at 5s to keep a hung DNS lookup from blocking
    the event loop.
    """
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "vibe-dump/0.3",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise HTTPError(f"{exc.code} {exc.reason}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPError(f"connection error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HTTPError("request timed out") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPError(f"non-JSON response: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise HTTPError("response was not a JSON object")
    return parsed


def health_from_exception(name: str, exc: BaseException) -> ProviderHealth:
    """Map an exception to a health record with the cause embedded."""
    return ProviderHealth(name, False, f"{type(exc).__name__}: {exc}")
