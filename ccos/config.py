"""Application configuration management.

CCOS reads cc-compatible ``settings.json`` files with a layered 5-source merge
(mirroring cc's ``SETTING_SOURCES``), while keeping CCOS's extra multi-provider
keys as passthrough. The user source lives at ``<config_dir>/settings.json``
(``~/.ccos/settings.json`` or ``$CCOS_CONFIG_DIR``); the legacy
``config.json`` is still read for backward compatibility.

Settings sources, low-to-high precedence (later overrides earlier):
  1. userSettings    -> <config_dir>/settings.json
  2. projectSettings -> <cwd>/.claude/settings.json
  3. localSettings   -> <cwd>/.claude/settings.local.json
  4. flagSettings    -> path from a --settings CLI flag, if present
  5. policySettings  -> platform managed-settings.json (first source wins)
"""

from __future__ import annotations

import json
import os
import sys
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


# ── Permission modes (mirror cc EXTERNAL_PERMISSION_MODES) ──────────────────

# cc/src/types/permissions.ts EXTERNAL_PERMISSION_MODES
PERMISSION_MODES = (
    "acceptEdits",
    "bypassPermissions",
    "default",
    "dontAsk",
    "plan",
)


# ── Layered settings merge (mirror settingsMergeCustomizer) ─────────────────

def _merge_arrays(a: list[Any], b: list[Any]) -> list[Any]:
    """Concatenate and de-duplicate two lists (mirror cc mergeArrays).

    Order is preserved (first occurrence wins). Non-hashable elements are
    de-duplicated by structural equality.
    """
    out: list[Any] = []
    for item in list(a) + list(b):
        if item not in out:
            out.append(item)
    return out


def _merge_settings(base: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *src* over *base* (mirror lodash mergeWith + customizer).

    Arrays are concatenated and de-duplicated; dicts are deep-merged; scalars
    from *src* override *base*.
    """
    out = dict(base)
    for key, src_val in src.items():
        base_val = out.get(key)
        if isinstance(base_val, dict) and isinstance(src_val, dict):
            out[key] = _merge_settings(base_val, src_val)
        elif isinstance(base_val, list) and isinstance(src_val, list):
            out[key] = _merge_arrays(base_val, src_val)
        else:
            out[key] = src_val
    return out


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _managed_settings_path() -> Path | None:
    """Platform managed-settings.json path (policySettings, first source wins)."""
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ClaudeCode" / "managed-settings.json"
    return Path("/etc/claude-code/managed-settings.json")


def load_settings(
    cwd: str | None = None,
    flag_settings_path: str | None = None,
) -> dict[str, Any]:
    """Load and merge cc-compatible settings from all sources.

    Sources are merged low-to-high precedence (later overrides earlier):
    userSettings < projectSettings < localSettings < flagSettings < policySettings.
    Arrays are concatenated and de-duplicated; dicts deep-merged.
    """
    cwd_path = Path(cwd) if cwd else Path.cwd()
    config_dir = get_config_dir()

    merged: dict[str, Any] = {}

    # 1. userSettings — prefer settings.json, fall back to legacy config.json
    user = _read_json_file(config_dir / "settings.json")
    if user is None:
        user = _read_json_file(config_dir / "config.json")
    if user:
        merged = _merge_settings(merged, user)

    # 2. projectSettings
    project = _read_json_file(cwd_path / ".claude" / "settings.json")
    if project:
        merged = _merge_settings(merged, project)

    # 3. localSettings
    local = _read_json_file(cwd_path / ".claude" / "settings.local.json")
    if local:
        merged = _merge_settings(merged, local)

    # 4. flagSettings (from a --settings CLI flag if present)
    if flag_settings_path:
        flag = _read_json_file(Path(flag_settings_path).expanduser())
        if flag:
            merged = _merge_settings(merged, flag)

    # 5. policySettings — managed-settings.json (first source wins; we only
    #    support the file-based source). Merged last = highest precedence.
    managed_path = _managed_settings_path()
    policy = _read_json_file(managed_path) if managed_path else None
    if policy:
        merged = _merge_settings(merged, policy)

    return merged


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
    """Permissions block mirroring cc's PermissionsSchema.

    Rules are flat ``Tool(pattern)`` strings (e.g. ``Bash(git *)``,
    ``Read(*.ts)``) — NOT a dict keyed by tool name. Serialized under
    allow/deny/ask/defaultMode/additionalDirectories (camelCase, cc JSON).

    Backward-compat: ``mode`` aliases ``default_mode``, and ``always_allow`` /
    ``always_deny`` expose the rule lists as ``{tool: [patterns]}`` dicts for
    existing CCOS consumers (app.py, commands/builtin.py).
    """
    default_mode: str = "default"
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)
    additional_directories: list[str] = field(default_factory=list)
    disable_bypass_permissions_mode: str | None = None
    # Preserve unknown keys (mirror .passthrough()).
    extra: dict[str, Any] = field(default_factory=dict)

    # -- Backward-compatible accessors -----------------------------------------

    @property
    def mode(self) -> str:
        return self.default_mode

    @mode.setter
    def mode(self, value: str) -> None:
        self.default_mode = value

    @property
    def always_allow(self) -> dict[str, list[str]]:
        return _rules_to_dict(self.allow)

    @property
    def always_deny(self) -> dict[str, list[str]]:
        return _rules_to_dict(self.deny)


@dataclass
class GitAttribution:
    """Attribution text for commits/PRs (mirror cc's attribution object)."""
    # Attribution text for git commits, including any trailers. Empty string
    # hides attribution.
    commit: str | None = None
    # Attribution text for pull request descriptions. Empty string hides
    # attribution.
    pr: str | None = None


# Standard cc commit trailer.
DEFAULT_COMMIT_ATTRIBUTION = "Co-Authored-By: Claude <noreply@anthropic.com>"


@dataclass
class GitConfig:
    """Git attribution config mirroring cc's includeCoAuthoredBy/attribution.

    Backward-compat: ``co_author`` resolves to the effective commit trailer
    (empty string disables it) for existing CCOS consumers.
    """
    # Deprecated: Use attribution instead. Whether to include Claude's
    # co-authored by attribution in commits and PRs (defaults to true).
    include_co_authored_by: bool = True
    attribution: GitAttribution = field(default_factory=GitAttribution)

    @property
    def co_author(self) -> str:
        """Effective commit attribution trailer ('' disables it)."""
        if self.attribution.commit is not None:
            return self.attribution.commit
        if not self.include_co_authored_by:
            return ""
        return DEFAULT_COMMIT_ATTRIBUTION

    @co_author.setter
    def co_author(self, value: str) -> None:
        self.attribution.commit = value
        self.include_co_authored_by = bool(value)


@dataclass
class UIConfig:
    theme: str = "auto"
    vim_mode: bool = False
    tool_display: str = "full"  # "full" or "header"


@dataclass
class Config:
    default_provider: str = "anthropic"
    default_model: str = "claude-sonnet-4-6"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    git: GitConfig = field(default_factory=GitConfig)
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    # cc settings: custom directory for plan files, relative to project root.
    # If not set, defaults to ~/.claude/plans/
    plans_directory: str | None = None
    # MCP gating keys (cc settings; passthrough).
    enable_all_project_mcp_servers: bool | None = None
    enabled_mcpjson_servers: list[str] = field(default_factory=list)
    disabled_mcpjson_servers: list[str] = field(default_factory=list)
    # Preserve any other unknown top-level settings keys (mirror .passthrough()).
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, cwd: str | None = None, flag_settings_path: str | None = None) -> Config:
        merged = load_settings(cwd=cwd, flag_settings_path=flag_settings_path)
        if not merged:
            cfg = cls._default()
            cfg.save()
            return cfg
        try:
            return cls._from_dict(merged)
        except Exception:
            return cls._default()

    def save(self) -> None:
        # Persist the user source as cc-compatible settings.json.
        path = get_config_dir() / "settings.json"
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
                "gemini": ProviderConfig(
                    api_key_env="GEMINI_API_KEY",
                    base_url="https://generativelanguage.googleapis.com/v1beta",
                    default_model="gemini-2.5-flash",
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
                "llamacpp": ProviderConfig(
                    base_url="http://localhost:8080/v1",
                    default_model="local-model",
                ),
            },
        )

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Config:
        known_top = {
            "default_provider", "default_model", "providers", "permissions",
            "ui", "git", "hooks", "mcp_servers", "mcpServers", "plansDirectory",
            "enableAllProjectMcpServers", "enabledMcpjsonServers",
            "disabledMcpjsonServers", "attribution", "includeCoAuthoredBy",
            "model",
        }

        # Start with defaults so newly added built-in providers are always present
        providers: dict[str, ProviderConfig] = dict(cls._default().providers)
        for k, v in d.get("providers", {}).items():
            providers[k] = ProviderConfig(**{
                f: v[f] for f in ProviderConfig.__dataclass_fields__ if f in v
            })

        perms = _permissions_from_dict(d.get("permissions", {}))

        ui_raw = d.get("ui", {})
        ui = UIConfig(
            theme=ui_raw.get("theme", "auto"),
            vim_mode=ui_raw.get("vim_mode", False),
            tool_display=ui_raw.get("tool_display", "full"),
        )

        git = _git_from_dict(d)

        # cc uses `model` at the top level; keep CCOS's default_model too.
        default_model = d.get("default_model", d.get("model", "claude-sonnet-4-6"))

        return cls(
            default_provider=d.get("default_provider", "anthropic"),
            default_model=default_model,
            providers=providers,
            permissions=perms,
            ui=ui,
            git=git,
            hooks=d.get("hooks", {}),
            mcp_servers=d.get("mcpServers", d.get("mcp_servers", {})),
            plans_directory=d.get("plansDirectory"),
            enable_all_project_mcp_servers=d.get("enableAllProjectMcpServers"),
            enabled_mcpjson_servers=list(d.get("enabledMcpjsonServers", []) or []),
            disabled_mcpjson_servers=list(d.get("disabledMcpjsonServers", []) or []),
            extra={k: v for k, v in d.items() if k not in known_top},
        )

    def _to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "providers": {
                k: {f: getattr(v, f) for f in ProviderConfig.__dataclass_fields__ if getattr(v, f) is not None}
                for k, v in self.providers.items()
            },
            "permissions": _permissions_to_dict(self.permissions),
            "ui": {
                "theme": self.ui.theme,
                "vim_mode": self.ui.vim_mode,
                "tool_display": self.ui.tool_display,
            },
            "includeCoAuthoredBy": self.git.include_co_authored_by,
            "hooks": self.hooks,
            # cc interop key (alongside the snake_case alias CCOS consumers use).
            "mcpServers": self.mcp_servers,
        }
        attribution: dict[str, Any] = {}
        if self.git.attribution.commit is not None:
            attribution["commit"] = self.git.attribution.commit
        if self.git.attribution.pr is not None:
            attribution["pr"] = self.git.attribution.pr
        if attribution:
            out["attribution"] = attribution
        if self.plans_directory is not None:
            out["plansDirectory"] = self.plans_directory
        if self.enable_all_project_mcp_servers is not None:
            out["enableAllProjectMcpServers"] = self.enable_all_project_mcp_servers
        if self.enabled_mcpjson_servers:
            out["enabledMcpjsonServers"] = self.enabled_mcpjson_servers
        if self.disabled_mcpjson_servers:
            out["disabledMcpjsonServers"] = self.disabled_mcpjson_servers
        # Preserve unknown passthrough keys.
        for k, v in self.extra.items():
            out.setdefault(k, v)
        return out


# ── Permission rule helpers ─────────────────────────────────────────────────

def _rules_to_dict(rules: list[str]) -> dict[str, list[str]]:
    """Group flat 'Tool(pattern)' rule strings into {tool: [patterns]}.

    A bare 'Tool' (no parens) maps to a '*' pattern.
    """
    out: dict[str, list[str]] = {}
    for rule in rules:
        tool, pattern = _parse_rule(rule)
        out.setdefault(tool, [])
        if pattern not in out[tool]:
            out[tool].append(pattern)
    return out


def _dict_to_rules(d: dict[str, list[str]]) -> list[str]:
    """Convert legacy {tool: [patterns]} into flat 'Tool(pattern)' strings."""
    rules: list[str] = []
    for tool, patterns in d.items():
        if not patterns:
            rules.append(tool)
            continue
        for pattern in patterns:
            if pattern in ("", "*"):
                rules.append(tool)
            else:
                rules.append(f"{tool}({pattern})")
    return rules


def _parse_rule(rule: str) -> tuple[str, str]:
    """Parse 'Tool(pattern)' -> (tool, pattern). Bare 'Tool' -> (tool, '*')."""
    rule = rule.strip()
    if "(" in rule and rule.endswith(")"):
        tool = rule[: rule.index("(")]
        pattern = rule[rule.index("(") + 1 : -1]
        return tool, (pattern or "*")
    return rule, "*"


def _permissions_from_dict(raw: dict[str, Any]) -> PermissionsConfig:
    known = {
        "allow", "deny", "ask", "defaultMode", "mode",
        "additionalDirectories", "disableBypassPermissionsMode",
        "always_allow", "always_deny",
    }

    allow = list(raw.get("allow", []) or [])
    deny = list(raw.get("deny", []) or [])
    ask = list(raw.get("ask", []) or [])

    # Migrate legacy {tool: [patterns]} dicts into flat rule strings.
    legacy_allow = raw.get("always_allow")
    if isinstance(legacy_allow, dict):
        allow = _merge_arrays(allow, _dict_to_rules(legacy_allow))
    legacy_deny = raw.get("always_deny")
    if isinstance(legacy_deny, dict):
        deny = _merge_arrays(deny, _dict_to_rules(legacy_deny))

    default_mode = raw.get("defaultMode", raw.get("mode", "default"))
    if default_mode not in PERMISSION_MODES:
        default_mode = "default"

    return PermissionsConfig(
        default_mode=default_mode,
        allow=allow,
        deny=deny,
        ask=ask,
        additional_directories=list(raw.get("additionalDirectories", []) or []),
        disable_bypass_permissions_mode=raw.get("disableBypassPermissionsMode"),
        extra={k: v for k, v in raw.items() if k not in known},
    )


def _permissions_to_dict(perms: PermissionsConfig) -> dict[str, Any]:
    out: dict[str, Any] = {
        "allow": perms.allow,
        "deny": perms.deny,
        "ask": perms.ask,
        "defaultMode": perms.default_mode,
    }
    if perms.additional_directories:
        out["additionalDirectories"] = perms.additional_directories
    if perms.disable_bypass_permissions_mode is not None:
        out["disableBypassPermissionsMode"] = perms.disable_bypass_permissions_mode
    # Preserve unknown passthrough keys.
    for k, v in perms.extra.items():
        out.setdefault(k, v)
    return out


def _git_from_dict(d: dict[str, Any]) -> GitConfig:
    include = d.get("includeCoAuthoredBy", True)
    attribution_raw = d.get("attribution", {}) or {}
    attribution = GitAttribution(
        commit=attribution_raw.get("commit"),
        pr=attribution_raw.get("pr"),
    )

    # Backward-compat: read a legacy git.co_author key if present.
    git_raw = d.get("git", {}) or {}
    if attribution.commit is None and "co_author" in git_raw:
        legacy = git_raw["co_author"]
        if legacy == "":
            attribution.commit = ""
            include = False
        elif legacy and legacy not in ("CCOS <noreply@ccos.dev>",):
            attribution.commit = legacy

    return GitConfig(include_co_authored_by=bool(include), attribution=attribution)
