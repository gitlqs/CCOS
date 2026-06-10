"""Background memory extractor — auto-extracts memories from conversation.

After each turn where the model gives a final reply (no tool_use), a background
sub-agent analyzes the messages since the last extraction and persists any
memories worth keeping.

Faithful to cc/src/services/extractMemories:
- Triggers at end-of-turn whenever the model returned a final response with no
  tool_use, auto-memory is enabled, and this is the main agent (not a subagent).
  No message/tool-call thresholds (cc's extractMemories.ts).
- Skips the turn when the main agent already wrote memory (hasMemoryWritesSince).
- Maintains a per-turn cursor so each run only analyzes new model-visible
  messages since the last run, and threads that count into the prompt.
- The prompt is buildExtractAutoOnlyPrompt (prompts.ts) ported verbatim.
- The extraction engine is capped at a small turn budget (cc uses maxTurns: 5).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, TYPE_CHECKING

from ccos.memory.store import MemoryStore

if TYPE_CHECKING:
    from ccos.engine.query_engine import QueryEngine


# cc extractMemories.ts runs the forked extraction engine with maxTurns: 5.
MAX_EXTRACTION_TURNS = 5


# ---------------------------------------------------------------------------
# Verbatim prompt fragments from cc/src/memdir/memoryTypes.ts. Embedded here so
# the extraction prompt stays faithful without reaching into the (paraphrased)
# system-prompt section module. CCOS is individual-only → TYPES_SECTION_INDIVIDUAL.
# ---------------------------------------------------------------------------

_TYPES_SECTION_INDIVIDUAL: list[str] = [
    "## Types of memory",
    "",
    "There are several discrete types of memory that you can store in your memory system:",
    "",
    "<types>",
    "<type>",
    "    <name>user</name>",
    "    <description>Contain information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective. Your goal in reading and writing these memories is to build up an understanding of who the user is and how you can be most helpful to them specifically. For example, you should collaborate with a senior software engineer differently than a student who is coding for the very first time. Keep in mind, that the aim here is to be helpful to the user. Avoid writing memories about the user that could be viewed as a negative judgement or that are not relevant to the work you're trying to accomplish together.</description>",
    "    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>",
    "    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>",
    "    <examples>",
    "    user: I'm a data scientist investigating what logging we have in place",
    "    assistant: [saves user memory: user is a data scientist, currently focused on observability/logging]",
    "",
    "    user: I've been writing Go for ten years but this is my first time touching the React side of this repo",
    "    assistant: [saves user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>feedback</name>",
    "    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious.</description>",
    "    <when_to_save>Any time the user corrects your approach (\"no not that\", \"don't\", \"stop doing X\") OR confirms a non-obvious approach worked (\"yes exactly\", \"perfect, keep doing that\", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>",
    "    <how_to_use>Let these memories guide your behavior so that the user does not need to offer the same guidance twice.</how_to_use>",
    "    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>",
    "    <examples>",
    "    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed",
    "    assistant: [saves feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration]",
    "",
    "    user: stop summarizing what you just did at the end of every response, I can read the diff",
    "    assistant: [saves feedback memory: this user wants terse responses with no trailing summaries]",
    "",
    "    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn",
    "    assistant: [saves feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>project</name>",
    "    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work the user is doing within this working directory.</description>",
    "    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., \"Thursday\" → \"2026-03-05\"), so the memory remains interpretable after time passes.</when_to_save>",
    "    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request and make better informed suggestions.</how_to_use>",
    "    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>",
    "    <examples>",
    "    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch",
    "    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]",
    "",
    "    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements",
    "    assistant: [saves project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]",
    "    </examples>",
    "</type>",
    "<type>",
    "    <name>reference</name>",
    "    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>",
    "    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>",
    "    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>",
    "    <examples>",
    "    user: check the Linear project \"INGEST\" if you want context on these tickets, that's where we track all pipeline bugs",
    "    assistant: [saves reference memory: pipeline bugs are tracked in Linear project \"INGEST\"]",
    "",
    "    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone",
    "    assistant: [saves reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]",
    "    </examples>",
    "</type>",
    "</types>",
    "",
]

_WHAT_NOT_TO_SAVE_SECTION: list[str] = [
    "## What NOT to save in memory",
    "",
    "- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.",
    "- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.",
    "- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.",
    "- Anything already documented in CLAUDE.md files.",
    "- Ephemeral task details: in-progress work, temporary state, current conversation context.",
    "",
    "These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping.",
]

_MEMORY_FRONTMATTER_EXAMPLE: list[str] = [
    "```markdown",
    "---",
    "name: {{memory name}}",
    "description: {{one-line description — used to decide relevance in future conversations, so be specific}}",
    "type: {{user, feedback, project, reference}}",
    "---",
    "",
    "{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}",
    "```",
]


class MemoryExtractor:
    """Manages automatic memory extraction from conversations."""

    def __init__(
        self,
        store: MemoryStore,
        engine_factory: Callable[..., QueryEngine] | None = None,
    ):
        self._store = store
        self._engine_factory = engine_factory
        self._extraction_count = 0
        # Per-turn cursor: index into the conversation up to which we've already
        # considered messages (cc lastMemoryMessageUuid). Each run analyzes only
        # messages added since the previous run.
        self._cursor = 0

    def should_extract(
        self,
        messages: list[Any],
        has_memory_writes_since_last: bool,
        is_subagent: bool = False,
    ) -> bool:
        """Decide whether to trigger background extraction at end-of-turn.

        Triggers whenever the model returned a final response with no tool_use,
        this is the main agent, and the main agent did not already write memory
        this turn. No message-count or tool-call thresholds (cc extractMemories).

        Args:
            messages: Current conversation messages.
            has_memory_writes_since_last: Whether the main agent wrote memories
                in the most recent turn. If so, skip extraction (cc
                hasMemoryWritesSince).
            is_subagent: True when running inside a subagent; extraction only
                runs for the main agent.
        """
        if is_subagent:
            return False
        if has_memory_writes_since_last:
            return False
        if not messages:
            return False
        # End-of-turn = the model produced a final response with no tool_use.
        if not self._is_final_response(messages[-1]):
            return False
        # Nothing new since the last run → nothing to extract.
        if self._new_message_count(messages) <= 0:
            return False
        return True

    @staticmethod
    def _is_final_response(msg: Any) -> bool:
        """True if ``msg`` is an assistant message containing no tool_use block."""
        role = getattr(msg, "role", None)
        if role is None and isinstance(msg, dict):
            role = msg.get("role")
        if role != "assistant":
            return False
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                btype = getattr(block, "type", None)
                if btype is None and isinstance(block, dict):
                    btype = block.get("type")
                if btype == "tool_use":
                    return False
        return True

    def _new_message_count(self, messages: list[Any]) -> int:
        """Number of messages added since the last extraction run."""
        return max(0, len(messages) - self._cursor)

    async def extract(self, messages: list[Any]) -> None:
        """Run background extraction using a forked sub-agent.

        This creates a lightweight QueryEngine that analyzes the new messages
        since the last run and persists any relevant memories to the store.
        """
        if not self._engine_factory:
            return

        new_message_count = self._new_message_count(messages)
        if new_message_count <= 0:
            return

        # Scan existing memories so the extraction agent doesn't spend a turn
        # on `ls` and can update-rather-than-duplicate.
        existing = self._store.scan_all()
        existing_lines: list[str] = []
        for e in existing:
            import os

            filename = (
                os.path.basename(e.file_path) if e.file_path else e.filename
            )
            tag = f"[{e.type.value}] " if e.type else ""
            if e.description:
                existing_lines.append(f"- {tag}{filename}: {e.description}")
            else:
                existing_lines.append(f"- {tag}{filename}")
        existing_summary = "\n".join(existing_lines)

        prompt = self._build_extraction_prompt(
            new_message_count=new_message_count,
            existing_memories=existing_summary,
        )

        try:
            engine = self._create_engine()
            await engine.run_turn(prompt)
            self._extraction_count += 1
        except Exception:
            # Background extraction should never crash the main app.
            pass
        finally:
            # Advance the cursor regardless so the next run only sees fresh
            # messages (a failed run does not re-process the same window).
            self._cursor = len(messages)

    def _create_engine(self) -> QueryEngine:
        """Create the forked extraction engine, capped at MAX_EXTRACTION_TURNS."""
        assert self._engine_factory is not None
        engine = self._engine_factory()
        # Cap the turn budget if the engine exposes a max-turns knob (cc uses
        # maxTurns: 5). Best-effort — engines without the attribute are unchanged.
        for attr in ("max_turns", "maxTurns", "_max_turns"):
            if hasattr(engine, attr):
                try:
                    setattr(engine, attr, MAX_EXTRACTION_TURNS)
                except Exception:
                    pass
                break
        return engine

    def _build_extraction_prompt(
        self,
        new_message_count: int,
        existing_memories: str,
    ) -> str:
        """Port of cc buildExtractAutoOnlyPrompt (prompts.ts lines 50-94)."""
        parts: list[str] = [
            self._opener(new_message_count, existing_memories),
            "",
            "If the user explicitly asks you to remember something, save it immediately as "
            "whichever type fits best. If they ask you to forget something, find and remove the "
            "relevant entry.",
            "",
            *_TYPES_SECTION_INDIVIDUAL,
            *_WHAT_NOT_TO_SAVE_SECTION,
            "",
            *self._how_to_save(),
        ]
        return "\n".join(parts)

    @staticmethod
    def _opener(new_message_count: int, existing_memories: str) -> str:
        """Port of cc opener() (prompts.ts lines 29-44)."""
        manifest = (
            f"\n\n## Existing memory files\n\n{existing_memories}\n\n"
            "Check this list before writing — update an existing file rather than creating a "
            "duplicate."
            if existing_memories
            else ""
        )
        return "\n".join(
            [
                f"You are now acting as the memory extraction subagent. Analyze the most recent "
                f"~{new_message_count} messages above and use them to update your persistent "
                f"memory systems.",
                "",
                "Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail "
                "and similar), and Edit/Write for paths inside the memory directory only. Bash rm "
                "is not permitted. All other tools — MCP, Agent, write-capable Bash, etc — will be "
                "denied.",
                "",
                "You have a limited turn budget. Edit requires a prior Read of the same file, so "
                "the efficient strategy is: turn 1 — issue all Read calls in parallel for every "
                "file you might update; turn 2 — issue all Write/Edit calls in parallel. Do not "
                "interleave reads and writes across multiple turns.",
                "",
                f"You MUST only use content from the last ~{new_message_count} messages to update "
                "your persistent memories. Do not waste any turns attempting to investigate or "
                "verify that content further — no grepping source files, no reading code to "
                "confirm a pattern exists, no git commands." + manifest,
            ]
        )

    @staticmethod
    def _how_to_save() -> list[str]:
        """Port of cc buildExtractAutoOnlyPrompt non-skipIndex howToSave."""
        return [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            "**Step 1** — write the memory to its own file (e.g., `user_role.md`, "
            "`feedback_testing.md`) using this frontmatter format:",
            "",
            *_MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "**Step 2** — add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not "
            "a memory — each entry should be one line, under ~150 characters: "
            "`- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory "
            "content directly into `MEMORY.md`.",
            "",
            "- `MEMORY.md` is always loaded into your system prompt — lines after 200 will be "
            "truncated, so keep the index concise",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can "
            "update before writing a new one.",
        ]

    def run_background(self, messages: list[Any]) -> None:
        """Fire-and-forget extraction in a background thread.

        This is safe to call from the main event loop — it spawns a new thread
        with its own event loop. A snapshot of ``messages`` is taken so the
        cursor math is stable even if the main loop mutates the list.
        """
        import threading

        snapshot = list(messages)

        def _run() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.extract(snapshot))
                loop.close()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True)
        t.start()
