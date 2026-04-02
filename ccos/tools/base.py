"""Tool base class and registry."""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(slots=True)
class PermissionCheck:
    decision: PermissionDecision
    reason: str = ""


@dataclass
class ToolContext:
    """Shared context passed to every tool invocation."""
    cwd: str = ""
    # Files that have been read this session (path -> mtime at read)
    read_files: dict[str, float] = field(default_factory=dict)
    # Background tasks
    background_tasks: dict[str, Any] = field(default_factory=dict)
    next_task_id: int = 1

    def record_read(self, path: str) -> None:
        try:
            self.read_files[os.path.normpath(path)] = os.path.getmtime(path)
        except OSError:
            pass

    def was_read(self, path: str) -> bool:
        return os.path.normpath(path) in self.read_files

    def was_modified_since_read(self, path: str) -> bool:
        npath = os.path.normpath(path)
        if npath not in self.read_files:
            return True
        try:
            return os.path.getmtime(path) > self.read_files[npath]
        except OSError:
            return True


@dataclass(slots=True)
class ToolOutput:
    """Result of tool execution."""
    content: str
    is_error: bool = False
    # For image results (base64)
    images: list[dict[str, str]] | None = None


class Tool(ABC):
    """Abstract base class for all tools."""

    name: str
    description: str
    input_schema: dict[str, Any]
    max_result_chars: int = 30_000

    @abstractmethod
    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        """Execute the tool and return output."""

    def is_read_only(self, params: dict[str, Any]) -> bool:
        """Return True if this invocation is read-only (safe to auto-approve)."""
        return False

    def check_permissions(self, params: dict[str, Any], ctx: ToolContext) -> PermissionCheck:
        """Default permission check — read-only tools auto-allow, others ask."""
        if self.is_read_only(params):
            return PermissionCheck(PermissionDecision.ALLOW)
        return PermissionCheck(PermissionDecision.ASK)

    def to_schema(self) -> dict[str, Any]:
        """Convert to API tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_all(self) -> list[Tool]:
        return list(self._tools.values())

    def get_all_schemas(self) -> list[dict[str, Any]]:
        return [t.to_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())


def create_default_registry(cwd: str) -> ToolRegistry:
    """Create a registry with all built-in tools."""
    from ccos.tools.agent import AgentTool
    from ccos.tools.ask_user import AskUserQuestionTool
    from ccos.tools.bash import BashTool
    from ccos.tools.file_edit import FileEditTool
    from ccos.tools.file_read import FileReadTool
    from ccos.tools.file_write import FileWriteTool
    from ccos.tools.glob_tool import GlobTool
    from ccos.tools.grep_tool import GrepTool
    from ccos.tools.notebook_edit import NotebookEditTool
    from ccos.tools.task_tools import TaskOutputTool, TaskStopTool
    from ccos.tools.todo import TodoWriteTool
    from ccos.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool
    from ccos.tools.tool_search import ToolSearchTool
    from ccos.tools.web_fetch import WebFetchTool
    from ccos.tools.web_search import WebSearchTool

    registry = ToolRegistry()

    # Core tools
    registry.register(BashTool())
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileEditTool())
    registry.register(GlobTool())
    registry.register(GrepTool())

    # Agent + task management
    registry.register(AgentTool())
    registry.register(TaskOutputTool())
    registry.register(TaskStopTool())

    # User interaction
    registry.register(AskUserQuestionTool())
    registry.register(TodoWriteTool())

    # Plan mode
    registry.register(EnterPlanModeTool())
    registry.register(ExitPlanModeTool())

    # Web tools
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())

    # Notebook
    registry.register(NotebookEditTool())

    # Tool search (deferred tool loading)
    registry.register(ToolSearchTool())

    return registry
