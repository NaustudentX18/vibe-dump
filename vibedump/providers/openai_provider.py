"""OpenAI-compatible chat-completions adapter.

Covers OpenAI, OpenRouter, NVIDIA NIM, Groq, and MiniMax - they all use the
same `/v1/chat/completions` shape with bearer auth. Subclasses only need to
override the default base URL and the env var that holds the API key.
"""

from __future__ import annotations

import os
from typing import Any

from .base import HTTPError, ProviderHealth, post_json


class OpenAICompatLLM:
    """Base class for OpenAI-compatible chat-completions providers."""

    name: str = "openai"
    env_key: str = "OPENAI_API_KEY"
    default_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o-mini"

    def __init__(
        self,
        *,
        key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.key = key if key is not None else os.environ.get(self.env_key)
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.model = model or self.default_model
        self.timeout = timeout
        self.configured = bool(self.key)

    def _headers(self) -> dict[str, str]:
        if not self.key:
            raise HTTPError(f"{self.name}: missing {self.env_key}")
        return {"Authorization": f"Bearer {self.key}"}

    def complete(self, prompt: str) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        response = post_json(
            f"{self.base_url}/chat/completions",
            body,
            headers=self._headers(),
            timeout=self.timeout,
        )
        choices = response.get("choices") or []
        if not choices:
            raise HTTPError(f"{self.name}: empty choices in response")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPError(f"{self.name}: missing message content")
        return content

    def health(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(self.name, False, f"missing {self.env_key}")
        return ProviderHealth(
            self.name,
            True,
            f"ready (model={self.model}, base={self.base_url})",
        )
