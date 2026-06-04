"""Groq adapter (OpenAI-compatible)."""

from __future__ import annotations

from .openai_provider import OpenAICompatLLM


class GroqLLM(OpenAICompatLLM):
    name = "groq"
    env_key = "GROQ_API_KEY"
    default_base_url = "https://api.groq.com/openai/v1"
    default_model = "llama-3.1-8b-instant"
