"""Slash command registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class SlashCommand:
    name: str
    description: str
    handler: Callable[..., Any]
    aliases: list[str] | None = None


class CommandRegistry:
    """Register and look up slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases or []:
            self._commands[alias] = cmd

    def get(self, name: str) -> SlashCommand | None:
        return self._commands.get(name)

    def get_all_unique(self) -> list[SlashCommand]:
        seen: set[str] = set()
        result: list[SlashCommand] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: c.name)

    def names(self) -> list[str]:
        return [f"/{c.name}" for c in self.get_all_unique()]
