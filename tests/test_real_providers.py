"""Tests for the real LLM provider adapters (M3).

HTTP is stubbed via monkeypatching `urllib.request.urlopen` so the suite
runs offline. Each test asserts: which URL was hit, which auth header
was sent, and what the provider returns / raises.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest

from vibedump.providers import (
    GeminiLLM,
    GroqLLM,
    HTTPError,
    LocalPcLLM,
    MiniMaxLLM,
    NvidiaLLM,
    OpenAICompatLLM,
    OpenRouterLLM,
    build_registry,
    fake_registry,
    has_any_real_key,
)
from vibedump.providers.base import post_json


def _fake_urlopen(payload: dict[str, Any], status: int = 200):
    """Build a context-manager urlopen stub that returns the given JSON."""

    body = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = status

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        return response

    return _urlopen, response


def test_post_json_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["url"] = _request.full_url
        captured["method"] = _request.method
        captured["body"] = json.loads(_request.data)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    out = post_json("https://example.com/x", {"hello": "world"})
    assert out == {"ok": True}
    assert captured["url"] == "https://example.com/x"
    assert captured["method"] == "POST"
    assert captured["body"] == {"hello": "world"}


def test_post_json_http_error_includes_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            _request.full_url, 401, "Unauthorized", None, io.BytesIO(b"nope")  # type: ignore[arg-type]
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    with pytest.raises(HTTPError) as exc:
        post_json("https://example.com/x", {})
    assert "401" in str(exc.value)


def test_post_json_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        raise urllib.error.URLError("dns dead")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    with pytest.raises(HTTPError) as exc:
        post_json("https://example.com/x", {})
    assert "dns dead" in str(exc.value)


def test_openai_compat_missing_key_marks_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = OpenAICompatLLM()
    health = p.health()
    assert health.ok is False
    assert "OPENAI_API_KEY" in health.detail
    with pytest.raises(HTTPError):
        p.complete("hi")


def test_openai_compat_returns_message_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    urlopen, _ = _fake_urlopen(
        {"choices": [{"message": {"role": "assistant", "content": "hello back"}}]}
    )
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    p = OpenAICompatLLM(model="gpt-x")
    out = p.complete("hi")
    assert out == "hello back"
    health = p.health()
    assert health.ok is True
    assert "gpt-x" in health.detail


def test_openai_compat_raises_on_empty_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    urlopen, _ = _fake_urlopen({"choices": []})
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    p = OpenAICompatLLM()
    with pytest.raises(HTTPError) as exc:
        p.complete("hi")
    assert "empty choices" in str(exc.value)


def test_openai_compat_sends_bearer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["auth"] = _request.headers.get("Authorization")
        captured["url"] = _request.full_url
        return io.BytesIO(b'{"choices": [{"message": {"content": "ok"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    OpenAICompatLLM().complete("hi")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["url"].endswith("/chat/completions")


def test_openrouter_uses_specific_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["url"] = _request.full_url
        return io.BytesIO(b'{"choices": [{"message": {"content": "ok"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    OpenRouterLLM().complete("hi")
    assert captured["url"].startswith("https://openrouter.ai/api/v1/")


def test_nvidia_groq_minimax_have_distinct_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nv")
    monkeypatch.setenv("GROQ_API_KEY", "gq")
    monkeypatch.setenv("MINIMAX_API_KEY", "mm")
    assert NvidiaLLM().default_base_url.startswith("https://integrate.api.nvidia.com")
    assert GroqLLM().default_base_url.startswith("https://api.groq.com")
    assert MiniMaxLLM().default_base_url.startswith("https://api.minimax.io")


def test_gemini_uses_query_param_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gkey")
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["url"] = _request.full_url
        captured["body"] = json.loads(_request.data)
        return io.BytesIO(
            b'{"candidates": [{"content": {"parts": [{"text": "from gem"}]}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    p = GeminiLLM(model="gem-x")
    out = p.complete("hi")
    assert out == "from gem"
    assert "key=gkey" in captured["url"]
    assert "models/gem-x" in captured["url"]
    assert captured["body"]["contents"][0]["parts"][0]["text"] == "hi"


def test_gemini_raises_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = GeminiLLM()
    with pytest.raises(HTTPError):
        p.complete("hi")
    assert p.health().ok is False


def test_local_pc_no_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBEDUMP_PC_BASE_URL", raising=False)
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["url"] = _request.full_url
        captured["auth"] = _request.headers.get("Authorization")
        return io.BytesIO(b'{"choices": [{"message": {"content": "lan"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    p = LocalPcLLM()
    out = p.complete("hi")
    assert out == "lan"
    assert captured["auth"] is None
    assert captured["url"].endswith("/v1/chat/completions")
    assert p.health().ok is True


def test_local_pc_env_overrides_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBEDUMP_PC_BASE_URL", "http://100.127.91.97:11434")
    captured: dict[str, Any] = {}

    def _urlopen(_request, timeout=15.0):  # noqa: ARG001
        captured["url"] = _request.full_url
        return io.BytesIO(b'{"choices": [{"message": {"content": "x"}}]}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    LocalPcLLM().complete("hi")
    assert "100.127.91.97" in captured["url"]


def test_build_registry_omits_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wipe every key so only local_pc (and the fallback fake) survives.
    for var in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "MINIMAX_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    registry = build_registry(include_local_pc=False)
    names = {p.name for p in registry.llm.values()}
    assert names == {"fake"}

    registry = build_registry(include_local_pc=True)
    names = {p.name for p in registry.llm.values()}
    assert "local_pc" in names


def test_build_registry_includes_configured_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "mm")
    registry = build_registry(include_local_pc=False)
    names = {p.name for p in registry.llm.values()}
    assert "minimax" in names
    assert "openai" not in names
    assert "gemini" not in names


def test_has_any_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "MINIMAX_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    assert has_any_real_key() is False
    monkeypatch.setenv("GROQ_API_KEY", "gq")
    assert has_any_real_key() is True


def test_fake_registry_unchanged() -> None:
    """Regression: fake registry must still be the zero-config default.

    As of M7 Phase 3 the registry also exposes the fake variants of the
    real audio adapters (``whisper_fake`` / ``piper_fake``) so the
    dashboard / tests can target them by name.
    """
    registry = fake_registry()
    assert {p.name for p in registry.health()} == {"fake", "whisper_fake", "piper_fake"}
