"""NVIDIA NIM adapter (OpenAI-compatible)."""

from __future__ import annotations

from .openai_provider import OpenAICompatLLM


class NvidiaLLM(OpenAICompatLLM):
    name = "nvidia"
    env_key = "NVIDIA_API_KEY"
    default_base_url = "https://integrate.api.nvidia.com/v1"
    default_model = "meta/llama-3.1-70b-instruct"
