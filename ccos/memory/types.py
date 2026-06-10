"""Memory type taxonomy — four types matching Claude Code's memory system.

Verbatim source: cc/src/memdir/memoryTypes.ts (TYPES_SECTION_INDIVIDUAL,
WHAT_NOT_TO_SAVE_SECTION). CCOS is individual-only (no team memory), so the
descriptions below mirror the INDIVIDUAL variant — no <scope> qualifiers.
"""

from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    """The four memory types in the auto-memory system."""
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


def parse_memory_type(raw: object) -> MemoryType | None:
    """Parse a raw frontmatter value into a MemoryType.

    Mirrors cc's parseMemoryType (memoryTypes.ts): invalid or missing values
    return None so legacy files without a ``type:`` field keep working and
    unknown types degrade gracefully (rather than coercing to PROJECT).
    """
    if not isinstance(raw, str):
        return None
    for t in MemoryType:
        if t.value == raw:
            return t
    return None


# Verbatim description / when_to_save / how_to_use / body_structure text from
# cc's TYPES_SECTION_INDIVIDUAL (memoryTypes.ts lines 113-178).
MEMORY_TYPE_INFO: dict[str, dict[str, str]] = {
    "user": {
        "description": (
            "Contain information about the user's role, goals, responsibilities, and "
            "knowledge. Great user memories help you tailor your future behavior to the "
            "user's preferences and perspective. Your goal in reading and writing these "
            "memories is to build up an understanding of who the user is and how you can be "
            "most helpful to them specifically. For example, you should collaborate with a "
            "senior software engineer differently than a student who is coding for the very "
            "first time. Keep in mind, that the aim here is to be helpful to the user. Avoid "
            "writing memories about the user that could be viewed as a negative judgement or "
            "that are not relevant to the work you're trying to accomplish together."
        ),
        "when_to_save": (
            "When you learn any details about the user's role, preferences, "
            "responsibilities, or knowledge"
        ),
        "how_to_use": (
            "When your work should be informed by the user's profile or perspective. For "
            "example, if the user is asking you to explain a part of the code, you should "
            "answer that question in a way that is tailored to the specific details that they "
            "will find most valuable or that helps them build their mental model in relation "
            "to domain knowledge they already have."
        ),
    },
    "feedback": {
        "description": (
            "Guidance the user has given you about how to approach work — both what to avoid "
            "and what to keep doing. These are a very important type of memory to read and "
            "write as they allow you to remain coherent and responsive to the way you should "
            "approach work in the project. Record from failure AND success: if you only save "
            "corrections, you will avoid past mistakes but drift away from approaches the user "
            "has already validated, and may grow overly cautious."
        ),
        "when_to_save": (
            "Any time the user corrects your approach (\"no not that\", \"don't\", \"stop "
            "doing X\") OR confirms a non-obvious approach worked (\"yes exactly\", \"perfect, "
            "keep doing that\", accepting an unusual choice without pushback). Corrections are "
            "easy to notice; confirmations are quieter — watch for them. In both cases, save "
            "what is applicable to future conversations, especially if surprising or not "
            "obvious from the code. Include *why* so you can judge edge cases later."
        ),
        "how_to_use": (
            "Let these memories guide your behavior so that the user does not need to offer "
            "the same guidance twice."
        ),
        "body_structure": (
            "Lead with the rule itself, then a **Why:** line (the reason the user gave — often "
            "a past incident or strong preference) and a **How to apply:** line (when/where "
            "this guidance kicks in). Knowing *why* lets you judge edge cases instead of "
            "blindly following the rule."
        ),
    },
    "project": {
        "description": (
            "Information that you learn about ongoing work, goals, initiatives, bugs, or "
            "incidents within the project that is not otherwise derivable from the code or git "
            "history. Project memories help you understand the broader context and motivation "
            "behind the work the user is doing within this working directory."
        ),
        "when_to_save": (
            "When you learn who is doing what, why, or by when. These states change relatively "
            "quickly so try to keep your understanding of this up to date. Always convert "
            "relative dates in user messages to absolute dates when saving (e.g., \"Thursday\" "
            "→ \"2026-03-05\"), so the memory remains interpretable after time passes."
        ),
        "how_to_use": (
            "Use these memories to more fully understand the details and nuance behind the "
            "user's request and make better informed suggestions."
        ),
        "body_structure": (
            "Lead with the fact or decision, then a **Why:** line (the motivation — often a "
            "constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this "
            "should shape your suggestions). Project memories decay fast, so the why helps "
            "future-you judge whether the memory is still load-bearing."
        ),
    },
    "reference": {
        "description": (
            "Stores pointers to where information can be found in external systems. These "
            "memories allow you to remember where to look to find up-to-date information "
            "outside of the project directory."
        ),
        "when_to_save": (
            "When you learn about resources in external systems and their purpose. For "
            "example, that bugs are tracked in a specific project in Linear or that feedback "
            "can be found in a specific Slack channel."
        ),
        "how_to_use": (
            "When the user references an external system or information that may be in an "
            "external system."
        ),
    },
}


# Things that should NOT be saved to memory — they can be derived from code/git.
# Verbatim from cc's WHAT_NOT_TO_SAVE_SECTION (memoryTypes.ts lines 183-195),
# including the trailing rationale clauses.
NOT_TO_SAVE = [
    "Code patterns, conventions, architecture, file paths, or project structure — these can "
    "be derived by reading the current project state.",
    "Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.",
    "Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.",
    "Anything already documented in CLAUDE.md files.",
    "Ephemeral task details: in-progress work, temporary state, current conversation context.",
]

# Explicit-save gate appended after the NOT_TO_SAVE bullets (memoryTypes.ts
# line 194). Eval-validated: prevents activity-log noise when the user asks to
# save a PR list or activity summary.
NOT_TO_SAVE_EXPLICIT_GATE = (
    "These exclusions apply even when the user explicitly asks you to save. If they ask you "
    "to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about "
    "it — that is the part worth keeping."
)
