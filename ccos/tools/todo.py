"""TodoWrite tool -- session task list management."""

from __future__ import annotations

from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


# Session-level task store
_todos: list[dict[str, str]] = []


def get_todos() -> list[dict[str, str]]:
    return _todos


# cc TodoWriteTool DESCRIPTION followed by the full PROMPT body
# (TodoWriteTool/prompt.ts), verbatim. The substituted Edit-tool name is "Edit"
# (CCOS's edit tool is named Edit).
_DESCRIPTION = (
    "Update the todo list for the current session. To be used proactively and "
    "often to track progress and pending tasks. Make sure that at least one task "
    "is in_progress at all times. Always provide both content (imperative) and "
    "activeForm (present continuous) for each task.\n\n"
    "Use this tool to create and manage a structured task list for your current "
    "coding session. This helps you track progress, organize complex tasks, and "
    "demonstrate thoroughness to the user.\n"
    "It also helps the user understand the progress of the task and overall "
    "progress of their requests.\n\n"
    "## When to Use This Tool\n"
    "Use this tool proactively in these scenarios:\n\n"
    "1. Complex multi-step tasks - When a task requires 3 or more distinct steps "
    "or actions\n"
    "2. Non-trivial and complex tasks - Tasks that require careful planning or "
    "multiple operations\n"
    "3. User explicitly requests todo list - When the user directly asks you to "
    "use the todo list\n"
    "4. User provides multiple tasks - When users provide a list of things to be "
    "done (numbered or comma-separated)\n"
    "5. After receiving new instructions - Immediately capture user requirements "
    "as todos\n"
    "6. When you start working on a task - Mark it as in_progress BEFORE beginning "
    "work. Ideally you should only have one todo as in_progress at a time\n"
    "7. After completing a task - Mark it as completed and add any new follow-up "
    "tasks discovered during implementation\n\n"
    "## When NOT to Use This Tool\n\n"
    "Skip using this tool when:\n"
    "1. There is only a single, straightforward task\n"
    "2. The task is trivial and tracking it provides no organizational benefit\n"
    "3. The task can be completed in less than 3 trivial steps\n"
    "4. The task is purely conversational or informational\n\n"
    "NOTE that you should not use this tool if there is only one trivial task to "
    "do. In this case you are better off just doing the task directly.\n\n"
    "## Examples of When to Use the Todo List\n\n"
    "<example>\n"
    "User: I want to add a dark mode toggle to the application settings. Make sure "
    "you run the tests and build when you're done!\n"
    "Assistant: *Creates todo list with the following items:*\n"
    "1. Creating dark mode toggle component in Settings page\n"
    "2. Adding dark mode state management (context/store)\n"
    "3. Implementing CSS-in-JS styles for dark theme\n"
    "4. Updating existing components to support theme switching\n"
    "5. Running tests and build process, addressing any failures or errors that "
    "occur\n"
    "*Begins working on the first task*\n\n"
    "<reasoning>\n"
    "The assistant used the todo list because:\n"
    "1. Adding dark mode is a multi-step feature requiring UI, state management, "
    "and styling changes\n"
    "2. The user explicitly requested tests and build be run afterward\n"
    "3. The assistant inferred that tests and build need to pass by adding "
    '"Ensure tests and build succeed" as the final task\n'
    "</reasoning>\n"
    "</example>\n\n"
    "<example>\n"
    "User: Help me rename the function getCwd to getCurrentWorkingDirectory across "
    "my project\n"
    "Assistant: *Uses grep or search tools to locate all instances of getCwd in "
    "the codebase*\n"
    "I've found 15 instances of 'getCwd' across 8 different files.\n"
    "*Creates todo list with specific items for each file that needs updating*\n\n"
    "<reasoning>\n"
    "The assistant used the todo list because:\n"
    "1. First, the assistant searched to understand the scope of the task\n"
    "2. Upon finding multiple occurrences across different files, it determined "
    "this was a complex task with multiple steps\n"
    "3. The todo list helps ensure every instance is tracked and updated "
    "systematically\n"
    "4. This approach prevents missing any occurrences and maintains code "
    "consistency\n"
    "</reasoning>\n"
    "</example>\n\n\n"
    "<example>\n"
    "User: I need to implement these features for my e-commerce site: user "
    "registration, product catalog, shopping cart, and checkout flow.\n"
    "Assistant: *Creates a todo list breaking down each feature into specific "
    "tasks based on the project architecture*\n"
    "Let's start with user registration. This will involve creating database "
    "models, API endpoints, and frontend forms that integrate with your existing "
    "authentication system.\n\n"
    "<reasoning>\n"
    "The assistant used the todo list because:\n"
    "1. The user provided multiple complex features to implement in a comma "
    "separated list\n"
    "2. The todo list helps organize these large features into manageable tasks\n"
    "3. This approach allows for tracking progress across the entire "
    "implementation\n"
    "</reasoning>\n"
    "</example>\n\n"
    "<example>\n"
    "User: Can you help optimize my React application? It's rendering slowly and "
    "has performance issues.\n"
    "Assistant: *Reviews component structure, render patterns, state management, "
    "and data fetching*\n"
    "After analyzing your codebase, I've identified several performance issues.\n"
    "*Creates todo list with items like: 1) Implementing memoization for expensive "
    "calculations in ProductList, 2) Adding virtualization for long lists in "
    "Dashboard, 3) Optimizing image loading in Gallery component, 4) Fixing state "
    "update loops in ShoppingCart, 5) Reviewing bundle size and implementing code "
    "splitting*\n"
    "Let's start by implementing memoization for the expensive calculations in "
    "your ProductList component.\n\n"
    "<reasoning>\n"
    "The assistant used the todo list because:\n"
    "1. First, the assistant examined the codebase to identify specific "
    "performance issues\n"
    "2. Based on this analysis, it identified multiple optimization opportunities\n"
    "3. Performance optimization is a non-trivial task requiring multiple steps\n"
    "4. The todo list helps methodically track improvements across different "
    "components\n"
    "5. This systematic approach ensures all performance bottlenecks are "
    "addressed\n"
    "</reasoning>\n"
    "</example>\n\n"
    "## Examples of When NOT to Use the Todo List\n\n"
    "<example>\n"
    "User: How do I print 'Hello World' in Python?\n"
    'Assistant: In Python, you can print "Hello World" with this simple code:\n\n'
    "python\n"
    'print("Hello World")\n\n'
    'This will output the text "Hello World" to the console when executed.\n\n'
    "<reasoning>\n"
    "The assistant did not use the todo list because this is a single, trivial "
    "task that can be completed in one step. There's no need to track multiple "
    "tasks or steps for such a straightforward request.\n"
    "</reasoning>\n"
    "</example>\n\n"
    "<example>\n"
    "User: What does the git status command do?\n"
    "Assistant: The git status command shows the current state of your working "
    "directory and staging area. It displays which changes have been staged, "
    "which haven't, and which files aren't being tracked by Git. This is a "
    "fundamental command for understanding the current state of your repository "
    "before making commits.\n\n"
    "<reasoning>\n"
    "The assistant did not use the todo list because this is an informational "
    "request with no actual coding task to complete. The user is simply asking for "
    "an explanation, not for the assistant to perform multiple steps or tasks.\n"
    "</reasoning>\n"
    "</example>\n\n"
    "<example>\n"
    "User: Can you add a comment to the calculateTotal function to explain what it "
    "does?\n"
    "Assistant: Sure, let me add a comment to the calculateTotal function to "
    "explain what it does.\n"
    "* Uses the Edit tool to add a comment to the calculateTotal function *\n\n"
    "<reasoning>\n"
    "The assistant did not use the todo list because this is a single, "
    "straightforward task confined to one location in the code. Adding a comment "
    "doesn't require tracking multiple steps or systematic organization.\n"
    "</reasoning>\n"
    "</example>\n\n"
    "<example>\n"
    "User: Run npm install for me and tell me what happens.\n"
    "Assistant: I'll run the npm install command for you.\n\n"
    "*Executes: npm install*\n\n"
    "The command completed successfully. Here's the output:\n"
    "[Output of npm install command]\n\n"
    "All dependencies have been installed according to your package.json file.\n\n"
    "<reasoning>\n"
    "The assistant did not use the todo list because this is a single command "
    "execution with immediate results. There are no multiple steps to track or "
    "organize, making the todo list unnecessary for this straightforward task.\n"
    "</reasoning>\n"
    "</example>\n\n"
    "## Task States and Management\n\n"
    "1. **Task States**: Use these states to track progress:\n"
    "   - pending: Task not yet started\n"
    "   - in_progress: Currently working on (limit to ONE task at a time)\n"
    "   - completed: Task finished successfully\n\n"
    "   **IMPORTANT**: Task descriptions must have two forms:\n"
    "   - content: The imperative form describing what needs to be done (e.g., "
    '"Run tests", "Build the project")\n'
    "   - activeForm: The present continuous form shown during execution (e.g., "
    '"Running tests", "Building the project")\n\n'
    "2. **Task Management**:\n"
    "   - Update task status in real-time as you work\n"
    "   - Mark tasks complete IMMEDIATELY after finishing (don't batch "
    "completions)\n"
    "   - Exactly ONE task must be in_progress at any time (not less, not more)\n"
    "   - Complete current tasks before starting new ones\n"
    "   - Remove tasks that are no longer relevant from the list entirely\n\n"
    "3. **Task Completion Requirements**:\n"
    "   - ONLY mark a task as completed when you have FULLY accomplished it\n"
    "   - If you encounter errors, blockers, or cannot finish, keep the task as "
    "in_progress\n"
    "   - When blocked, create a new task describing what needs to be resolved\n"
    "   - Never mark a task as completed if:\n"
    "     - Tests are failing\n"
    "     - Implementation is partial\n"
    "     - You encountered unresolved errors\n"
    "     - You couldn't find necessary files or dependencies\n\n"
    "4. **Task Breakdown**:\n"
    "   - Create specific, actionable items\n"
    "   - Break complex tasks into smaller, manageable steps\n"
    "   - Use clear, descriptive task names\n"
    "   - Always provide both forms:\n"
    '     - content: "Fix authentication bug"\n'
    '     - activeForm: "Fixing authentication bug"\n\n'
    "When in doubt, use this tool. Being proactive with task management "
    "demonstrates attentiveness and ensures you complete all requirements "
    "successfully."
)


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = _DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task description (imperative form)",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {
                            "type": "string",
                            "description": "Present continuous form shown during execution",
                        },
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True  # Internal state only, no filesystem changes

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        todos = params.get("todos", [])

        # cc clears the stored list when every todo is completed
        # (newTodos = allDone ? [] : todos), while still echoing success.
        all_done = bool(todos) and all(
            t.get("status") == "completed" for t in todos
        )
        _todos.clear()
        if not all_done:
            _todos.extend(todos)

        # cc tool_result text. The Rich rendered checklist stays a UI concern in
        # the display layer; the tool_result sent to the model is cc's sentence.
        return ToolOutput(
            content=(
                "Todos have been modified successfully. Ensure that you continue "
                "to use the todo list to track your progress. Please proceed with "
                "the current tasks if applicable"
            )
        )
