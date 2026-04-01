"""Plan mode management — plan file storage and state tracking."""

from __future__ import annotations

import os
import random
from typing import Optional

# Word lists for slug generation (adjective-noun-scientist pattern)
_ADJECTIVES = [
    "brave", "calm", "dark", "eager", "fair", "gentle", "happy", "keen",
    "lively", "merry", "noble", "proud", "quiet", "rapid", "sharp",
    "tender", "vivid", "warm", "young", "zesty", "amber", "bold",
    "clear", "deep", "fresh", "golden", "hidden", "ivory", "jade",
    "kind", "lunar", "misty", "neon", "olive", "pearl", "rosy",
    "silver", "teal", "ultra", "velvet", "wild", "dreamy", "cosmic",
    "frozen", "rustic", "silent", "ancient", "bright", "crimson",
]

_NOUNS = [
    "river", "mountain", "forest", "ocean", "valley", "meadow", "canyon",
    "island", "desert", "glacier", "bridge", "castle", "garden", "harbor",
    "lantern", "mirror", "nebula", "oasis", "palace", "quartz", "shadow",
    "temple", "voyage", "willow", "zenith", "beacon", "crystal", "dusk",
    "ember", "flame", "grove", "horizon", "jewel", "kite", "lotus",
    "marble", "nexus", "orbit", "prism", "reef", "spark", "thunder",
    "painting", "compass", "fountain", "puzzle", "riddle", "feather",
]

_SCIENTISTS = [
    "darwin", "curie", "tesla", "euler", "gauss", "newton", "planck",
    "turing", "fermi", "bohr", "dirac", "faraday", "kepler", "laplace",
    "maxwell", "pascal", "volta", "ampere", "dalton", "hertz", "joule",
    "kelvin", "lorenz", "mendel", "ohm", "pavlov", "raman", "sagan",
    "russell", "hopper", "lovelace", "noether", "hawking", "feynman",
]


def generate_word_slug() -> str:
    """Generate a random 3-word slug like 'dreamy-painting-russell'."""
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    name = random.choice(_SCIENTISTS)
    return f"{adj}-{noun}-{name}"


class PlanManager:
    """Manages plan files and plan mode state."""

    def __init__(self, config_home: str | None = None):
        if config_home is None:
            config_home = os.path.join(os.path.expanduser("~"), ".ccos")
        self._plans_dir = os.path.join(config_home, "plans")
        os.makedirs(self._plans_dir, exist_ok=True)
        # Session -> slug mapping
        self._slug_cache: dict[str, str] = {}
        # Current plan mode state
        self.is_plan_mode: bool = False
        self._pre_plan_mode: str | None = None  # permission mode before plan

    @property
    def plans_dir(self) -> str:
        return self._plans_dir

    def get_slug(self, session_id: str) -> str:
        """Get or generate a slug for this session."""
        if session_id not in self._slug_cache:
            # Try to find a unique slug
            for _ in range(10):
                slug = generate_word_slug()
                path = os.path.join(self._plans_dir, f"{slug}.md")
                if not os.path.exists(path):
                    break
            self._slug_cache[session_id] = slug
        return self._slug_cache[session_id]

    def set_slug(self, session_id: str, slug: str) -> None:
        self._slug_cache[session_id] = slug

    def clear_slug(self, session_id: str) -> None:
        self._slug_cache.pop(session_id, None)

    def get_plan_file_path(self, session_id: str, agent_id: str | None = None) -> str:
        slug = self.get_slug(session_id)
        if agent_id:
            return os.path.join(self._plans_dir, f"{slug}-agent-{agent_id}.md")
        return os.path.join(self._plans_dir, f"{slug}.md")

    def get_plan(self, session_id: str, agent_id: str | None = None) -> str | None:
        """Read the plan file content."""
        path = self.get_plan_file_path(session_id, agent_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def save_plan(self, session_id: str, content: str, agent_id: str | None = None) -> str:
        """Write the plan to disk. Returns the file path."""
        path = self.get_plan_file_path(session_id, agent_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return path

    def enter_plan_mode(self, current_perm_mode: str) -> None:
        """Enter plan mode, saving the current permission mode."""
        self._pre_plan_mode = current_perm_mode
        self.is_plan_mode = True

    def exit_plan_mode(self) -> str:
        """Exit plan mode, returning the original permission mode."""
        mode = self._pre_plan_mode or "default"
        self._pre_plan_mode = None
        self.is_plan_mode = False
        return mode
