"""Authentication and credential management.

Credentials are stored at ~/.ccos/.credentials.json (plaintext, chmod 600).
Supports:
- Direct API key entry for any provider
- OAuth token storage (for Anthropic Console flow)
- Multiple provider credentials in one file
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OAuthAccount:
    """OAuth account metadata (Anthropic Console)."""
    email: str = ""
    display_name: str = ""
    organization_id: str = ""


@dataclass
class Credentials:
    """All stored credentials."""
    # Provider -> API key
    api_keys: dict[str, str] = field(default_factory=dict)
    # OAuth (Anthropic Console)
    oauth_token: str = ""
    oauth_account: OAuthAccount | None = None

    def get_api_key(self, provider: str) -> str | None:
        """Get API key for a provider. Checks stored creds then env vars."""
        # 1. Stored credentials
        key = self.api_keys.get(provider)
        if key:
            return key
        # 2. Environment variable fallback
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "grok": "XAI_API_KEY",
            "xai": "XAI_API_KEY",
        }
        env_var = env_map.get(provider)
        if env_var:
            return os.environ.get(env_var)
        return None

    def has_any_key(self) -> bool:
        """Check if any credentials are available."""
        if self.api_keys:
            return True
        if self.oauth_token:
            return True
        # Check env vars
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
            if os.environ.get(var):
                return True
        return False


def _get_credentials_path() -> str:
    config_dir = os.environ.get("CCOS_CONFIG_DIR", os.path.expanduser("~/.ccos"))
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, ".credentials.json")


def load_credentials() -> Credentials:
    """Load credentials from disk."""
    path = _get_credentials_path()
    if not os.path.exists(path):
        return Credentials()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        creds = Credentials()
        creds.api_keys = data.get("api_keys", {})
        creds.oauth_token = data.get("oauth_token", "")
        acct = data.get("oauth_account")
        if acct:
            creds.oauth_account = OAuthAccount(
                email=acct.get("email", ""),
                display_name=acct.get("display_name", ""),
                organization_id=acct.get("organization_id", ""),
            )
        return creds
    except Exception:
        return Credentials()


def save_credentials(creds: Credentials) -> None:
    """Save credentials to disk with restricted permissions."""
    path = _get_credentials_path()
    data: dict[str, Any] = {
        "api_keys": creds.api_keys,
        "oauth_token": creds.oauth_token,
    }
    if creds.oauth_account:
        data["oauth_account"] = {
            "email": creds.oauth_account.email,
            "display_name": creds.oauth_account.display_name,
            "organization_id": creds.oauth_account.organization_id,
        }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # Restrict permissions (owner only) on Unix
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


async def verify_api_key(provider: str, api_key: str) -> tuple[bool, str]:
    """Verify an API key by making a minimal API call.

    Returns (success, message).
    """
    if provider == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            # Minimal call - count tokens for a tiny message
            resp = client.messages.count_tokens(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": "hi"}],
            )
            return True, "Anthropic API key verified successfully."
        except anthropic.AuthenticationError:
            return False, "Invalid Anthropic API key."
        except Exception as e:
            # Key format looks right, assume valid if we can't verify
            if api_key.startswith("sk-ant-"):
                return True, f"Key format looks valid (could not verify: {e})"
            return False, f"Verification failed: {e}"

    elif provider == "openai":
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            client.models.list()
            return True, "OpenAI API key verified successfully."
        except openai.AuthenticationError:
            return False, "Invalid OpenAI API key."
        except Exception as e:
            if api_key.startswith("sk-"):
                return True, f"Key format looks valid (could not verify: {e})"
            return False, f"Verification failed: {e}"

    elif provider in ("grok", "xai"):
        # xAI/Grok uses OpenAI-compatible API
        try:
            import openai
            client = openai.OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
            client.models.list()
            return True, "Grok API key verified successfully."
        except Exception as e:
            if api_key.startswith("xai-"):
                return True, f"Key format looks valid (could not verify: {e})"
            return False, f"Verification failed: {e}"

    elif provider == "ollama":
        return True, "Ollama runs locally, no API key needed."

    else:
        return True, f"Key saved for {provider} (no verification available)."
