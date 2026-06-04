"""Gemini adapter (Google generative language API, different shape).

Uses the `generateContent` endpoint with `?key=...` auth. Response payload
follows `{candidates: [{content: {parts: [{text: ...}]}}]}`.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from .base import HTTPError, ProviderHealth, post_json


class GeminiLLM:
    name = "gemini"
    env_key = "GEMINI_API_KEY"
    default_base_url = "https://generativelanguage.googleapis.com"
    default_model = "gemini-1.5-flash"

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

    def complete(self, prompt: str) -> str:
        if not self.key:
            raise HTTPError(f"gemini: missing {self.env_key}")
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4},
        }
        query = urlencode({"key": self.key})
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent?{query}"
        response = post_json(url, body, timeout=self.timeout)
        candidates = response.get("candidates") or []
        if not candidates:
            raise HTTPError("gemini: empty candidates in response")
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict)]
        merged = "".join(text_chunks).strip()
        if not merged:
            raise HTTPError("gemini: missing text in response")
        return merged

    def health(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(self.name, False, f"missing {self.env_key}")
        return ProviderHealth(
            self.name,
            True,
            f"ready (model={self.model}, base={self.base_url})",
        )
