"""llama.cpp provider — local LLM via llama.cpp OpenAI-compatible server."""

from __future__ import annotations

from ccos.providers.openai_compat import OpenAICompatProvider


class LlamaCppProvider(OpenAICompatProvider):
    """Provider for llama.cpp local server.

    Default base URL: http://localhost:8080/v1
    Start the server with:
        ./llama-server -m <path/to/model.gguf>
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "sk-xxx",
        timeout: float = 600.0,
    ):
        # llama.cpp accepts any non-empty api_key when --api-key is not set on the server
        super().__init__(
            api_key=api_key or "sk-xxx",
            base_url=base_url,
            provider_name="llamacpp",
            timeout=timeout,
        )

    @property
    def supports_thinking(self) -> bool:
        return False
