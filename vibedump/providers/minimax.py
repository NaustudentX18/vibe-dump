"""MiniMax adapter (OpenAI-compatible, served via api.minimax.io)."""

from __future__ import annotations

from .openai_provider import OpenAICompatLLM


class MiniMaxLLM(OpenAICompatLLM):
    name = "minimax"
    env_key = "MINIMAX_API_KEY"
    default_base_url = "https://api.minimax.io/v1"
    default_model = "MiniMax-Text-01"
