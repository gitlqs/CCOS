"""Provider registry -- resolve a provider from config / CLI args / credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ccos.providers.base import LLMProvider

if TYPE_CHECKING:
    from ccos.config import Config, ProviderConfig


class ProviderRegistry:
    """Discover and instantiate LLM providers."""

    def get_provider(
        self,
        config: Config,
        *,
        provider_name: str | None = None,
    ) -> LLMProvider:
        name = provider_name or config.default_provider
        pcfg = config.providers.get(name)

        if name == "anthropic":
            return self._make_anthropic(pcfg, name)
        if name == "openai":
            return self._make_openai(pcfg, name)
        if name == "ollama":
            return self._make_ollama(pcfg)
        if name == "llamacpp":
            return self._make_llamacpp(pcfg)

        # Fallback: try openai_compat for any configured provider
        if pcfg and pcfg.type == "openai_compat":
            return self._make_openai_compat(pcfg, name)

        # Last resort: treat as openai_compat
        if pcfg:
            return self._make_openai_compat(pcfg, name)

        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Available: {', '.join(config.providers.keys())}"
        )

    def get_model(
        self,
        config: Config,
        *,
        provider_name: str | None = None,
        model: str | None = None,
    ) -> str:
        if model:
            return model
        name = provider_name or config.default_provider
        pcfg = config.providers.get(name)
        if pcfg and pcfg.default_model:
            return pcfg.default_model
        return config.default_model

    # -- Factory methods ---------------------------------------------------

    @staticmethod
    def _resolve_key(pcfg: ProviderConfig | None, provider_name: str) -> str | None:
        """Resolve API key: config -> credentials file -> env var."""
        # 1. From config (env var or direct)
        if pcfg:
            key = pcfg.resolve_api_key()
            if key:
                return key
        # 2. From stored credentials
        try:
            from ccos.auth import load_credentials
            creds = load_credentials()
            key = creds.get_api_key(provider_name)
            if key:
                return key
        except Exception:
            pass
        return None

    @staticmethod
    def _make_anthropic(pcfg: ProviderConfig | None, name: str) -> LLMProvider:
        from ccos.providers.anthropic import AnthropicProvider
        kwargs: dict = {}

        # Check for OAuth token first
        try:
            from ccos.auth import load_credentials, get_valid_oauth_token
            creds = load_credentials()
            oauth_token = get_valid_oauth_token(creds)
            if oauth_token:
                kwargs["oauth_token"] = oauth_token
                if pcfg and pcfg.base_url:
                    kwargs["base_url"] = pcfg.base_url
                return AnthropicProvider(**kwargs)
        except Exception:
            pass

        # Fall back to API key
        key = ProviderRegistry._resolve_key(pcfg, name)
        if key:
            kwargs["api_key"] = key
        if pcfg and pcfg.base_url:
            kwargs["base_url"] = pcfg.base_url
        return AnthropicProvider(**kwargs)

    @staticmethod
    def _make_openai(pcfg: ProviderConfig | None, name: str) -> LLMProvider:
        from ccos.providers.openai import OpenAIProvider
        kwargs: dict = {}
        key = ProviderRegistry._resolve_key(pcfg, name)
        if key:
            kwargs["api_key"] = key
        if pcfg and pcfg.base_url:
            kwargs["base_url"] = pcfg.base_url
        return OpenAIProvider(**kwargs)

    @staticmethod
    def _make_ollama(pcfg: ProviderConfig | None) -> LLMProvider:
        from ccos.providers.ollama import OllamaProvider
        kwargs: dict = {}
        if pcfg and pcfg.base_url:
            kwargs["base_url"] = pcfg.base_url
        return OllamaProvider(**kwargs)

    @staticmethod
    def _make_llamacpp(pcfg: ProviderConfig | None) -> LLMProvider:
        from ccos.providers.llamacpp import LlamaCppProvider
        kwargs: dict = {}
        if pcfg and pcfg.base_url:
            kwargs["base_url"] = pcfg.base_url
        key = ProviderRegistry._resolve_key(pcfg, "llamacpp")
        if key:
            kwargs["api_key"] = key
        return LlamaCppProvider(**kwargs)

    @staticmethod
    def _make_openai_compat(pcfg: ProviderConfig, name: str) -> LLMProvider:
        from ccos.providers.openai_compat import OpenAICompatProvider
        key = ProviderRegistry._resolve_key(pcfg, name)
        return OpenAICompatProvider(
            api_key=key or "",
            base_url=pcfg.base_url,
            provider_name=name,
        )
