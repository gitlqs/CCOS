"""AskUserQuestion tool -- ask the user multiple-choice questions during execution."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from ccos.tools.base import Tool, ToolContext, ToolOutput


# cc description() + ASK_USER_QUESTION_TOOL_PROMPT (AskUserQuestionTool/prompt.ts),
# verbatim. The Plan mode note references ExitPlanMode by name (EXIT_PLAN_MODE_TOOL_NAME).
_DESCRIPTION = (
    "Asks the user multiple choice questions to gather information, clarify "
    "ambiguity, understand preferences, make decisions or offer them choices.\n\n"
    "Use this tool when you need to ask the user questions during execution. "
    "This allows you to:\n"
    "1. Gather user preferences or requirements\n"
    "2. Clarify ambiguous instructions\n"
    "3. Get decisions on implementation choices as you work\n"
    "4. Offer choices to the user about what direction to take.\n\n"
    "Usage notes:\n"
    '- Users will always be able to select "Other" to provide custom text input\n'
    "- Use multiSelect: true to allow multiple answers to be selected for a question\n"
    "- If you recommend a specific option, make that the first option in the list "
    'and add "(Recommended)" at the end of the label\n\n'
    "Plan mode note: In plan mode, use this tool to clarify requirements or choose "
    "between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "
    '"Is my plan ready?" or "Should I proceed?" - use ExitPlanMode for plan '
    'approval. IMPORTANT: Do not reference "the plan" in your questions (e.g., '
    '"Do you have feedback about the plan?", "Does the plan look good?") because '
    "the user cannot see the plan in the UI until you call ExitPlanMode. If you "
    "need plan approval, use ExitPlanMode instead."
)


class AskUserQuestionTool(Tool):
    name = "AskUserQuestion"
    description = _DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "description": "Questions to ask the user (1-4 questions)",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": (
                                "The complete question to ask the user. Should be "
                                "clear, specific, and end with a question mark. "
                                'Example: "Which library should we use for date '
                                'formatting?" If multiSelect is true, phrase it '
                                'accordingly, e.g. "Which features do you want to '
                                'enable?"'
                            ),
                        },
                        "header": {
                            "type": "string",
                            "description": (
                                "Very short label displayed as a chip/tag (max 12 "
                                'chars). Examples: "Auth method", "Library", '
                                '"Approach".'
                            ),
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "description": (
                                "The available choices for this question. Must have "
                                "2-4 options. Each option should be a distinct, "
                                "mutually exclusive choice (unless multiSelect is "
                                "enabled). There should be no 'Other' option, that "
                                "will be provided automatically."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": (
                                            "The display text for this option that "
                                            "the user will see and select. Should be "
                                            "concise (1-5 words) and clearly describe "
                                            "the choice."
                                        ),
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": (
                                            "Explanation of what this option means or "
                                            "what will happen if chosen. Useful for "
                                            "providing context about trade-offs or "
                                            "implications."
                                        ),
                                    },
                                },
                                "required": ["label", "description"],
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Set to true to allow the user to select multiple "
                                "options instead of just one. Use when choices are "
                                "not mutually exclusive."
                            ),
                        },
                    },
                    "required": ["question", "header", "options"],
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        # Interactive user questioning is NOT read-only from a permission
        # perspective: it requires an interactive prompt and must be subject
        # to the PermissionManager's ASK policy (which will turn into DENY for
        # any non-main context via prompting_allowed=False).
        return False

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        questions = params.get("questions", [])
        console = Console()

        # answers: question text -> answer string (multi-select comma-separated)
        answers: dict[str, str] = {}

        for q in questions:
            question_text = q.get("question", "")
            header = q.get("header", "")
            options = q.get("options", [])
            multi = bool(q.get("multiSelect", False))

            answer = self._ask_one(console, question_text, header, options, multi)
            answers[question_text] = answer

        # Format the result so the model can read each answer keyed by question text.
        if not answers:
            return ToolOutput(content="(no questions asked)")
        lines = [f"{qt} -> {ans}" for qt, ans in answers.items()]
        return ToolOutput(content="\n".join(lines))

    def _ask_one(
        self,
        console: Console,
        question_text: str,
        header: str,
        options: list[dict[str, Any]],
        multi: bool,
    ) -> str:
        # Render the question + numbered options. "Other" is always offered as a
        # free-text choice, matching cc's automatic "Other" option.
        body_lines: list[str] = [question_text, ""]
        for i, opt in enumerate(options, 1):
            label = opt.get("label", "")
            desc = opt.get("description", "")
            body_lines.append(f"  {i}. {label}")
            if desc:
                body_lines.append(f"     {desc}")
        other_index = len(options) + 1
        body_lines.append(f"  {other_index}. Other (provide your own answer)")

        title = f"[yellow]{header}[/yellow]" if header else "[yellow]Question[/yellow]"
        console.print(Panel(
            "\n".join(body_lines),
            title=title,
            border_style="yellow",
        ))

        if multi:
            prompt = (
                "[yellow]Your selection(s) "
                "(comma-separated numbers): [/yellow]"
            )
        else:
            prompt = "[yellow]Your selection (number): [/yellow]"

        try:
            raw = console.input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return "(user did not respond)"

        if not raw:
            return "(no response)"

        selected: list[str] = []
        tokens = raw.split(",") if multi else [raw]
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            try:
                idx = int(tok)
            except ValueError:
                # Treat unparseable input as free text.
                selected.append(tok)
                continue
            if 1 <= idx <= len(options):
                selected.append(options[idx - 1].get("label", tok))
            elif idx == other_index:
                try:
                    custom = console.input(
                        "[yellow]Enter your answer: [/yellow]"
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    custom = ""
                selected.append(custom or "(no response)")
            else:
                selected.append(tok)
            if not multi:
                break

        return ", ".join(selected) if selected else "(no response)"
