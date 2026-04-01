"""Memory type taxonomy — four types matching Claude Code's memory system."""

from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    """The four memory types in the auto-memory system."""
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


MEMORY_TYPE_INFO: dict[str, dict[str, str]] = {
    "user": {
        "description": (
            "Information about the user's role, goals, responsibilities, and knowledge. "
            "Helps tailor behavior to the user's preferences and perspective."
        ),
        "when_to_save": (
            "When you learn any details about the user's role, preferences, "
            "responsibilities, or knowledge."
        ),
        "how_to_use": (
            "When your work should be informed by the user's profile or perspective."
        ),
    },
    "feedback": {
        "description": (
            "Guidance the user has given about how to approach work — both what to "
            "avoid and what to keep doing. Record from failure AND success."
        ),
        "when_to_save": (
            "Any time the user corrects your approach OR confirms a non-obvious "
            "approach worked. Include *why* so you can judge edge cases later."
        ),
        "how_to_use": (
            "Let these memories guide your behavior so the user does not need to "
            "offer the same guidance twice."
        ),
        "body_structure": (
            "Lead with the rule itself, then a **Why:** line and a **How to apply:** line."
        ),
    },
    "project": {
        "description": (
            "Information about ongoing work, goals, initiatives, bugs, or incidents "
            "that is not derivable from the code or git history."
        ),
        "when_to_save": (
            "When you learn who is doing what, why, or by when. Always convert "
            "relative dates to absolute dates."
        ),
        "how_to_use": (
            "Use these memories to understand the broader context and motivation "
            "behind the user's request."
        ),
        "body_structure": (
            "Lead with the fact or decision, then a **Why:** line and a **How to apply:** line."
        ),
    },
    "reference": {
        "description": (
            "Pointers to where information can be found in external systems "
            "(Linear, Grafana, Slack, etc.)."
        ),
        "when_to_save": (
            "When you learn about resources in external systems and their purpose."
        ),
        "how_to_use": (
            "When the user references an external system or information that may "
            "be in an external system."
        ),
    },
}


# Things that should NOT be saved to memory — they can be derived from code/git.
NOT_TO_SAVE = [
    "Code patterns, conventions, architecture, file paths, or project structure",
    "Git history, recent changes, or who-changed-what",
    "Debugging solutions or fix recipes",
    "Anything already documented in CLAUDE.md files",
    "Ephemeral task details: in-progress work, temporary state, current conversation context",
]
