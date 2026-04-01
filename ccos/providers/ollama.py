"""Ollama provider — local LLM via OpenAI-compatible API."""

from __future__ import annotations

from ccos.providers.openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    """Provider for Ollama local models."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        timeout: float = 600.0,
    ):
        super().__init__(
            api_key="ollama",  # Ollama doesn't need a real key
            base_url=base_url,
            provider_name="ollama",
            timeout=timeout,
        )

    @property
    def supports_thinking(self) -> bool:
        return False
