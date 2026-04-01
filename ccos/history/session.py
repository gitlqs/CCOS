"""Session management -- create, save, list, and resume conversation sessions."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def transcript_path(self) -> str:
        return self._transcript_path

    def start_session(self, cwd: str, model: str = "") -> str:
        """Start a new session. Returns the session ID."""
        self._session_id = uuid.uuid4().hex[:16]
        self._project_dir = self._get_project_dir(cwd)
        os.makedirs(self._project_dir, exist_ok=True)
        self._transcript_path = os.path.join(
            self._project_dir, f"{self._session_id}.jsonl"
        )
        self._message_count = 0

        # Write session header
        header = {
            "type": "session_start",
            "session_id": self._session_id,
            "timestamp": time.time(),
            "cwd": cwd,
            "model": model,
            "version": "0.1.0",
        }
        self._append_entry(header)
        return self._session_id

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
                            messages.append(entry)
                            self._message_count += 1
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return None

        return messages if messages else None

    def save_user_message(self, content: str) -> None:
        """Persist a user message."""
        entry = {
            "type": "user",
            "uuid": uuid.uuid4().hex,
            "timestamp": time.time(),
            "content": content,
        }
        self._append_entry(entry)
        self._message_count += 1

    def save_assistant_message(self, content: list[dict[str, Any]], model: str = "") -> None:
        """Persist an assistant response (text + tool_use blocks)."""
        entry = {
            "type": "assistant",
            "uuid": uuid.uuid4().hex,
            "timestamp": time.time(),
            "content": content,
            "model": model,
        }
        self._append_entry(entry)
        self._message_count += 1

    def save_tool_result(self, tool_use_id: str, tool_name: str, content: str, is_error: bool = False) -> None:
        """Persist a tool result."""
        entry = {
            "type": "tool_result",
            "uuid": uuid.uuid4().hex,
            "timestamp": time.time(),
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "content": content,
            "is_error": is_error,
        }
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

            # Read first few lines for header + first prompt
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

                    if entry.get("type") == "session_start":
                        info.model = entry.get("model", "")
                        info.cwd = entry.get("cwd", "")
                        info.created_at = entry.get("timestamp", info.created_at)
                    elif entry.get("type") == "user" and not info.first_prompt:
                        content = entry.get("content", "")
                        # Truncate long prompts
                        if len(content) > 120:
                            content = content[:117] + "..."
                        info.first_prompt = content
                    elif entry.get("type") == "tombstone":
                        info.is_tombstoned = True
                        return info

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
        """Get the project-specific storage directory."""
        # Sanitize path: replace path separators with dashes
        sanitized = re.sub(r'[/\\:]+', '-', cwd.strip('/\\'))
        sanitized = re.sub(r'[^a-zA-Z0-9._-]', '_', sanitized)
        return os.path.join(self._projects_dir, sanitized)

    def _append_entry(self, entry: dict[str, Any]) -> None:
        """Append a JSON entry to the transcript file."""
        if not self._transcript_path:
            return
        try:
            with open(self._transcript_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass  # Best-effort persistence
