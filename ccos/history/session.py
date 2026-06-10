"""Session management -- create, save, list, and resume conversation sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Mirror cc sessionStoragePortable MAX_SANITIZED_LENGTH.
MAX_SANITIZED_LENGTH = 200

# cc transcript record metadata defaults.
_VERSION = "0.1.0"
_USER_TYPE = "external"
_ENTRYPOINT = "cli"


def _iso_now() -> str:
    """Current UTC time as an ISO-8601 string (mirror cc toISOString)."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionInfo:
    """Metadata about a saved session."""
    session_id: str
    project_dir: str
    created_at: float
    updated_at: float
    first_prompt: str = ""
    message_count: int = 0
    model: str = ""
    cwd: str = ""
    plan_slug: str = ""
    is_tombstoned: bool = False


class SessionManager:
    """Manages conversation session persistence via JSONL files."""

    def __init__(self, config_home: str | None = None):
        if config_home is None:
            config_home = os.path.join(os.path.expanduser("~"), ".ccos")
        self._config_home = config_home
        self._projects_dir = os.path.join(config_home, "projects")
        self._session_id: str = ""
        self._project_dir: str = ""
        self._transcript_path: str = ""
        self._message_count: int = 0
        # Per-record metadata carried on every transcript line (mirror cc).
        self._cwd: str = ""
        self._model: str = ""
        self._git_branch: str = ""
        self._slug: str = ""
        # Running parentUuid chain — each new entry links to the previous one.
        self._parent_uuid: str | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transcript_path(self) -> str:
        return self._transcript_path

    def start_session(self, cwd: str, model: str = "", slug: str = "") -> str:
        """Start a new session. Returns the session ID."""
        self._session_id = uuid.uuid4().hex[:16]
        self._project_dir = self._get_project_dir(cwd)
        os.makedirs(self._project_dir, exist_ok=True)
        self._transcript_path = os.path.join(
            self._project_dir, f"{self._session_id}.jsonl"
        )
        self._message_count = 0
        self._cwd = cwd
        self._model = model
        self._slug = slug
        self._git_branch = _get_git_branch(cwd)
        self._parent_uuid = None
        # cc carries cwd/version/etc. on each record — no separate header line.
        return self._session_id

    def _base_record(self, entry_type: str) -> dict[str, Any]:
        """Build the cc-compatible common fields for a transcript record."""
        entry_uuid = uuid.uuid4().hex
        record: dict[str, Any] = {
            "parentUuid": self._parent_uuid,
            "isSidechain": False,
            "type": entry_type,
            "uuid": entry_uuid,
            "timestamp": _iso_now(),
            "cwd": self._cwd,
            "sessionId": self._session_id,
            "version": _VERSION,
            "gitBranch": self._git_branch,
            "slug": self._slug,
            "userType": _USER_TYPE,
            "entrypoint": _ENTRYPOINT,
        }
        # Advance the chain.
        self._parent_uuid = entry_uuid
        return record

    def resume_session(self, session_id: str, cwd: str) -> list[dict[str, Any]] | None:
        """Resume a previous session. Returns the messages or None if not found."""
        project_dir = self._get_project_dir(cwd)
        path = os.path.join(project_dir, f"{session_id}.jsonl")
        if not os.path.exists(path):
            # Search across all project dirs
            path = self._find_session_file(session_id)
            if path is None:
                return None

        self._session_id = session_id
        self._project_dir = os.path.dirname(path)
        self._transcript_path = path

        # Parse JSONL
        messages = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") in ("user", "assistant", "tool_result"):
                            # Expose a top-level `content` derived from the
                            # embedded cc message object so existing consumers
                            # (app._restore_messages) keep working with both the
                            # new and legacy on-disk shapes.
                            if "content" not in entry:
                                msg = entry.get("message")
                                if isinstance(msg, dict):
                                    entry["content"] = msg.get("content", "")
                            messages.append(entry)
                            self._message_count += 1
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return None

        return messages if messages else None

    def save_user_message(self, content: str) -> None:
        """Persist a user message (embedded Anthropic message object)."""
        entry = self._base_record("user")
        entry["message"] = {"role": "user", "content": content}
        self._append_entry(entry)
        self._message_count += 1

    def save_assistant_message(self, content: list[dict[str, Any]], model: str = "") -> None:
        """Persist an assistant response (text + tool_use blocks)."""
        entry = self._base_record("assistant")
        entry["message"] = {
            "role": "assistant",
            "content": content,
            "model": model or self._model,
        }
        self._append_entry(entry)
        self._message_count += 1

    def save_tool_result(self, tool_use_id: str, tool_name: str, content: str, is_error: bool = False) -> None:
        """Persist a tool result.

        cc represents tool results inside a user-role message as a
        tool_result content block (not a top-level 'tool_result' type).
        """
        entry = self._base_record("user")
        entry["message"] = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        }
        # Retain the tool name for CCOS display without polluting the message.
        entry["toolUseResult"] = {"tool_name": tool_name}
        self._append_entry(entry)

    def list_sessions(self, cwd: str, limit: int = 20) -> list[SessionInfo]:
        """List recent sessions for a project directory."""
        project_dir = self._get_project_dir(cwd)
        if not os.path.isdir(project_dir):
            return []

        sessions: list[SessionInfo] = []
        try:
            for fname in os.listdir(project_dir):
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(project_dir, fname)
                session_id = fname[:-6]  # strip .jsonl
                info = self._read_session_info(fpath, session_id, project_dir)
                if info and not info.is_tombstoned:
                    sessions.append(info)
        except Exception:
            pass

        # Sort by most recent first
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions[:limit]

    def _read_session_info(self, path: str, session_id: str, project_dir: str) -> SessionInfo | None:
        """Read session metadata from the first and last lines of a JSONL."""
        try:
            stat = os.stat(path)
            info = SessionInfo(
                session_id=session_id,
                project_dir=project_dir,
                created_at=stat.st_ctime,
                updated_at=stat.st_mtime,
            )

            # Read first few lines for metadata + first prompt. cc carries
            # cwd/model/version/slug on each record (no separate header line).
            with open(path, "r", encoding="utf-8") as f:
                count = 0
                for line in f:
                    if count > 20:
                        break
                    count += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    etype = entry.get("type")
                    if etype == "tombstone":
                        info.is_tombstoned = True
                        return info

                    # Pick up per-record metadata from the first record seen.
                    if not info.cwd and entry.get("cwd"):
                        info.cwd = entry.get("cwd", "")
                    if not info.plan_slug and entry.get("slug"):
                        info.plan_slug = entry.get("slug", "")

                    message = entry.get("message")
                    if etype == "assistant" and not info.model and isinstance(message, dict):
                        info.model = message.get("model", "") or info.model

                    if etype == "user" and not info.first_prompt:
                        content = _extract_text_content(entry)
                        if content:
                            if len(content) > 120:
                                content = content[:117] + "..."
                            info.first_prompt = content

            # Estimate message count from file size
            info.message_count = max(1, int(stat.st_size / 500))
            return info
        except Exception:
            return None

    def _find_session_file(self, session_id: str) -> str | None:
        """Search all project dirs for a session file."""
        if not os.path.isdir(self._projects_dir):
            return None
        for dirpath, _, filenames in os.walk(self._projects_dir):
            for fname in filenames:
                if fname == f"{session_id}.jsonl":
                    return os.path.join(dirpath, fname)
        return None

    def _get_project_dir(self, cwd: str) -> str:
        """Get the project-specific storage directory.

        Mirrors cc sanitizePath: replace EVERY non-alphanumeric char with '-'
        (including '.', '_', '/', ':', and the leading separator), and append a
        hash suffix when the result exceeds MAX_SANITIZED_LENGTH.
        """
        return os.path.join(self._projects_dir, _sanitize_path(cwd))

    def _append_entry(self, entry: dict[str, Any]) -> None:
        """Append a JSON entry to the transcript file."""
        if not self._transcript_path:
            return
        try:
            with open(self._transcript_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass  # Best-effort persistence


def _sanitize_path(name: str) -> str:
    """Make a string safe for use as a directory name (mirror cc sanitizePath).

    Replaces every non-alphanumeric character with '-'. For paths that would
    exceed MAX_SANITIZED_LENGTH, truncates and appends a hash suffix.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{digest}"


def _extract_text_content(entry: dict[str, Any]) -> str:
    """Extract displayable text from a cc-style transcript entry."""
    message = entry.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        # Backward-compat with the legacy flat shape.
        content = entry.get("content")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return " ".join(parts)
    return ""


def _get_git_branch(cwd: str) -> str:
    """Return the current git branch for *cwd*, or '' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""
