"""Application configuration management.

Config is stored at ``~/.ccos/config.json`` (or ``$CCOS_CONFIG_DIR``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def get_config_dir() -> Path:
    d = Path(os.environ.get("CCOS_CONFIG_DIR", "~/.ccos")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_sessions_dir() -> Path:
    d = get_config_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ProviderConfig:
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    # For openai_compat providers
    type: str | None = None

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


@dataclass
class PermissionsConfig:
    mode: str = "default"
    always_allow: dict[str, list[str]] = field(default_factory=dict)
    always_deny: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class UIConfig:
    theme: str = "auto"
    vim_mode: bool = False


@dataclass
class Config:
    default_provider: str = "anthropic"
    default_model: str = "claude-sonnet-4-6"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        path = get_config_dir() / "config.json"
        if not path.exists():
            cfg = cls._default()
            cfg.save()
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls._from_dict(raw)
        except Exception:
            return cls._default()

    def save(self) -> None:
        path = get_config_dir() / "config.json"
        path.write_text(
            json.dumps(self._to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- Serialisation helpers -------------------------------------------------

    @classmethod
    def _default(cls) -> Config:
        return cls(
            providers={
                "anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
                "openai": ProviderConfig(
                    api_key_env="OPENAI_API_KEY",
                    default_model="gpt-4o",
                ),
                "ollama": ProviderConfig(
                    base_url="http://localhost:11434/v1",
                    default_model="llama3.1",
                ),
                "grok": ProviderConfig(
                    type="openai_compat",
                    base_url="https://api.x.ai/v1",
                    api_key_env="XAI_API_KEY",
                    default_model="grok-3",
                ),
            },
        )

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Config:
        providers: dict[str, ProviderConfig] = {}
        for k, v in d.get("providers", {}).items():
            providers[k] = ProviderConfig(**{
                f: v[f] for f in ProviderConfig.__dataclass_fields__ if f in v
            })

        perms_raw = d.get("permissions", {})
        perms = PermissionsConfig(
            mode=perms_raw.get("mode", "default"),
            always_allow=perms_raw.get("always_allow", {}),
            always_deny=perms_raw.get("always_deny", {}),
        )

        ui_raw = d.get("ui", {})
        ui = UIConfig(
            theme=ui_raw.get("theme", "auto"),
            vim_mode=ui_raw.get("vim_mode", False),
        )

        return cls(
            default_provider=d.get("default_provider", "anthropic"),
            default_model=d.get("default_model", "claude-sonnet-4-6"),
            providers=providers,
            permissions=perms,
            ui=ui,
            hooks=d.get("hooks", {}),
            mcp_servers=d.get("mcp_servers", d.get("mcpServers", {})),
        )

    def _to_dict(self) -> dict[str, Any]:
        return {
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "providers": {
                k: {f: getattr(v, f) for f in ProviderConfig.__dataclass_fields__ if getattr(v, f) is not None}
                for k, v in self.providers.items()
            },
            "permissions": {
                "mode": self.permissions.mode,
                "always_allow": self.permissions.always_allow,
                "always_deny": self.permissions.always_deny,
            },
            "ui": {
                "theme": self.ui.theme,
                "vim_mode": self.ui.vim_mode,
            },
            "hooks": self.hooks,
            "mcp_servers": self.mcp_servers,
        }
