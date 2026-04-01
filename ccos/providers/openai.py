"""OpenAI provider (GPT-4o, o1, etc.)."""

from __future__ import annotations

import os
from typing import Any

from ccos.providers.openai_compat import OpenAICompatProvider


class OpenAIProvider(OpenAICompatProvider):
    """Provider for the official OpenAI API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 600.0,
    ):
        super().__init__(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=base_url,
            provider_name="openai",
            timeout=timeout,
        )
