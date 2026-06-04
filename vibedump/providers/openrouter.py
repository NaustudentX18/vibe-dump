"""OpenRouter adapter (OpenAI-compatible)."""

from __future__ import annotations

from .openai_provider import OpenAICompatLLM


class OpenRouterLLM(OpenAICompatLLM):
    name = "openrouter"
    env_key = "OPENROUTER_API_KEY"
    default_base_url = "https://openrouter.ai/api/v1"
    default_model = "meta-llama/llama-3.1-8b-instruct:free"
