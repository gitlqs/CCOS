"""Interactive input using prompt_toolkit — CC-style ❯ prompt."""

from __future__ import annotations

import os
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from ccos.ui.figures import POINTER

PromptMode = Literal["default", "bash", "plan"]


def _make_prompt(mode: PromptMode = "default", loading: bool = False) -> FormattedText:
    """Build the prompt character matching CC's PromptInputModeIndicator.

    - default mode:  ❯  (POINTER character)
    - bash mode:     !  (magenta)
    - plan mode:     ❯  (blue)
    - loading:       dimmed version of any of the above
    """
    if mode == "bash":
        if loading:
            return FormattedText([("class:bash-dim", "! ")])
        return FormattedText([("class:bash", "! ")])
    elif mode == "plan":
        if loading:
            return FormattedText([("class:plan-dim", f"{POINTER} ")])
        return FormattedText([("class:plan", f"{POINTER} ")])
    else:
        if loading:
            return FormattedText([("class:prompt-dim", f"{POINTER} ")])
        return FormattedText([("class:prompt", f"{POINTER} ")])


def create_input_session(
    slash_commands: list[str] | None = None,
    vim_mode: bool = False,
) -> PromptSession:
    """Create a prompt_toolkit session with history and autocompletion."""

    commands = slash_commands or [
        "/help", "/exit", "/quit", "/clear", "/model", "/provider",
        "/cost", "/config", "/compact", "/diff", "/status", "/history",
        "/resume", "/plan", "/memory", "/doctor", "/login", "/logout",
        "/init", "/permissions", "/vim", "/theme", "/export", "/hooks",
        "/files", "/fast", "/branch",
    ]
    completer = WordCompleter(commands, sentence=True)

    # Key bindings: Enter submits, Alt+Enter / Ctrl+J for newline
    bindings = KeyBindings()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _alt_enter(event):
        event.current_buffer.insert_text("\n")

    @bindings.add(Keys.ControlJ)
    def _ctrl_j(event):
        event.current_buffer.insert_text("\n")

    # Persistent history
    history_dir = os.path.join(os.path.expanduser("~"), ".ccos")
    os.makedirs(history_dir, exist_ok=True)
    history_path = os.path.join(history_dir, "input_history")
    try:
        history = FileHistory(history_path)
    except Exception:
        history = InMemoryHistory()

    # Style: CC uses the default text color for ❯, magenta for !, blue for plan
    style_dict = {
        "prompt": "",                    # default text color
        "prompt-dim": "fg:ansigray",     # dimmed when loading
        "bash": "fg:ansimagenta bold",   # bash mode
        "bash-dim": "fg:ansimagenta",    # bash mode dimmed
        "plan": "fg:ansiblue",           # plan mode
        "plan-dim": "fg:ansiblue",       # plan mode dimmed
    }

    from prompt_toolkit.styles import Style as PTStyle
    pt_style = PTStyle.from_dict(style_dict)

    session: PromptSession = PromptSession(
        completer=completer,
        auto_suggest=AutoSuggestFromHistory(),
        history=history,
        multiline=False,
        key_bindings=bindings,
        vi_mode=vim_mode,
        enable_history_search=True,
        style=pt_style,
    )

    return session


def get_user_input(
    session: PromptSession,
    mode: PromptMode = "default",
) -> str | None:
    """Get input from user. Returns None on Ctrl+D (EOF).

    Uses CC's ❯ prompt character with mode-dependent coloring.
    """
    try:
        prompt = _make_prompt(mode)
        text = session.prompt(prompt, multiline=False)
        return text.strip() if text else None
    except EOFError:
        return None
    except KeyboardInterrupt:
        return ""  # Empty string = interrupted, not exit
