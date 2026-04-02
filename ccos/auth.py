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
import time
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
    oauth_refresh_token: str = ""
    oauth_expires_at: float = 0.0  # Unix timestamp
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

    def is_oauth_token_valid(self) -> bool:
        """Return True if oauth_token exists and hasn't expired (with 60s buffer)."""
        if not self.oauth_token:
            return False
        if self.oauth_expires_at and time.time() >= self.oauth_expires_at - 60:
            return False
        return True

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
        creds.oauth_refresh_token = data.get("oauth_refresh_token", "")
        creds.oauth_expires_at = data.get("oauth_expires_at", 0.0)
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
        "oauth_refresh_token": creds.oauth_refresh_token,
        "oauth_expires_at": creds.oauth_expires_at,
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


# ── Anthropic OAuth (PKCE) ─────────────────────────────────────────────────

_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"


def _generate_pkce_pair() -> tuple[str, str, str]:
    """Return (code_verifier, code_challenge, state) using S256.

    Per the Anthropic Claude Code OAuth spec, state must equal code_verifier.
    """
    import base64
    import hashlib
    import secrets

    verifier_bytes = secrets.token_bytes(32)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    # state must equal code_verifier in the Claude Code OAuth flow
    state = code_verifier
    return code_verifier, code_challenge, state


def build_oauth_url() -> tuple[str, str, str]:
    """Build the OAuth authorization URL.

    Returns (url, code_verifier, state).
    """
    import urllib.parse

    code_verifier, code_challenge, state = _generate_pkce_pair()

    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "scope": _OAUTH_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = _OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    return url, code_verifier, state


async def exchange_oauth_code(
    code: str,
    code_verifier: str,
    state: str,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens.

    Returns the parsed JSON response dict on success, raises on error.
    The ``code`` should already have the ``#{state}`` suffix stripped.
    """
    import urllib.request

    payload = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _OAUTH_CLIENT_ID,
        "redirect_uri": _OAUTH_REDIRECT_URI,
        "code_verifier": code_verifier,
        "state": state,
    }).encode()

    req = urllib.request.Request(
        _OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "anthropic",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


async def refresh_oauth_token(creds: Credentials) -> tuple[bool, str]:
    """Use the stored refresh token to get a new access token.

    Updates *creds* in-place on success (caller must call save_credentials).
    Returns (success, message).
    """
    if not creds.oauth_refresh_token:
        return False, "No refresh token stored."

    import urllib.request
    import urllib.error

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": creds.oauth_refresh_token,
        "client_id": _OAUTH_CLIENT_ID,
    }).encode()

    try:
        req = urllib.request.Request(
            _OAUTH_TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "anthropic",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return False, f"Token refresh failed ({e.code}): {body}"
    except Exception as e:
        return False, f"Token refresh error: {e}"

    creds.oauth_token = data.get("access_token", "")
    creds.oauth_refresh_token = data.get("refresh_token", creds.oauth_refresh_token)
    expires_in = data.get("expires_in", 28800)
    creds.oauth_expires_at = time.time() + expires_in
    return True, "Token refreshed successfully."


def get_valid_oauth_token(creds: Credentials) -> str | None:
    """Return a valid OAuth access token, refreshing synchronously if needed.

    Returns None if no token is available or refresh fails.
    """
    if not creds.oauth_token:
        return None
    if creds.is_oauth_token_valid():
        return creds.oauth_token
    # Try to refresh
    import asyncio
    try:
        ok, _ = asyncio.get_event_loop().run_until_complete(refresh_oauth_token(creds))
    except RuntimeError:
        # No running loop; create a temporary one
        ok, _ = asyncio.run(refresh_oauth_token(creds))
    if ok:
        save_credentials(creds)
        return creds.oauth_token
    return None
