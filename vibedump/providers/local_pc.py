"""Local PC Ollama adapter.

Talks to the desktop's Ollama server over the LAN. Ollama exposes an
OpenAI-compatible `/v1/chat/completions` endpoint and does not require an API
key, so this is the cheapest path for offline / privacy-sensitive dumps.
"""

from __future__ import annotations

import os
from typing import Any

from .base import HTTPError, ProviderHealth, post_json


class LocalPcLLM:
    name = "local_pc"
    env_key = "VIBEDUMP_PC_BASE_URL"
    default_base_url = "http://desktop-ujsii52.local:11434"
    default_model = "qwen3-14b-agent"
    configured = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # No API key - LAN only. env override exists for switching tailscale IPs.
        self.base_url = (
            base_url
            or os.environ.get(self.env_key)
            or self.default_base_url
        ).rstrip("/")
        self.model = model or os.environ.get("VIBEDUMP_PC_MODEL") or self.default_model
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        try:
            response = post_json(
                f"{self.base_url}/v1/chat/completions",
                body,
                timeout=self.timeout,
            )
        except HTTPError:
            raise
        choices = response.get("choices") or []
        if not choices:
            raise HTTPError("local_pc: empty choices in response")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPError("local_pc: missing message content")
        return content

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            self.name,
            True,
            f"ready (model={self.model}, base={self.base_url})",
        )
