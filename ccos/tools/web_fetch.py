"""WebFetch tool -- fetch and extract content from URLs."""

from __future__ import annotations

import re
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

_MAX_CONTENT_LENGTH = 50_000


_DESCRIPTION = (
    "\n"
    "- Fetches content from a specified URL and processes it using an AI model\n"
    "- Takes a URL and a prompt as input\n"
    "- Fetches the URL content, converts HTML to markdown\n"
    "- Processes the content with the prompt using a small, fast model\n"
    "- Returns the model's response about the content\n"
    "- Use this tool when you need to retrieve and analyze web content\n"
    "\n"
    "Usage notes:\n"
    "  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using "
    "that tool instead of this one, as it may have fewer restrictions.\n"
    "  - The URL must be a fully-formed valid URL\n"
    "  - HTTP URLs will be automatically upgraded to HTTPS\n"
    "  - The prompt should describe what information you want to extract from the page\n"
    "  - This tool is read-only and does not modify any files\n"
    "  - Results may be summarized if the content is very large\n"
    "  - Includes a self-cleaning 15-minute cache for faster responses when "
    "repeatedly accessing the same URL\n"
    "  - When a URL redirects to a different host, the tool will inform you and "
    "provide the redirect URL in a special format. You should then make a new "
    "WebFetch request with the redirect URL to fetch the content.\n"
    "  - For GitHub URLs, prefer using the gh CLI via Bash instead "
    "(e.g., gh pr view, gh issue view, gh api).\n"
)

_WARNING = (
    "IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Before using "
    "this tool, check if the URL points to an authenticated service (e.g. Google "
    "Docs, Confluence, Jira, GitHub). If so, look for a specialized MCP tool that "
    "provides authenticated access."
)


class WebFetchTool(Tool):
    name = "WebFetch"
    # cc assembles prompt as `warning + '\n' + DESCRIPTION`, and DESCRIPTION
    # itself begins with a leading newline.
    description = _WARNING + "\n" + _DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to run on the fetched content",
            },
        },
        "required": ["url", "prompt"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        url = params["url"]

        try:
            import httpx
        except ImportError:
            return ToolOutput(content="Error: httpx not installed", is_error=True)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=30.0,
                headers={"User-Agent": "CCOS/0.1 (Agentic Coding CLI)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ToolOutput(content=f"HTTP error {e.response.status_code}: {url}", is_error=True)
        except httpx.RequestError as e:
            return ToolOutput(content=f"Request error: {e}", is_error=True)

        content_type = resp.headers.get("content-type", "")
        text = resp.text

        # Extract text from HTML
        if "html" in content_type:
            text = _extract_text_from_html(text)

        # Truncate
        if len(text) > _MAX_CONTENT_LENGTH:
            text = text[:_MAX_CONTENT_LENGTH] + f"\n\n... (truncated, {len(resp.text)} total chars)"

        if not text.strip():
            return ToolOutput(content=f"(No readable content from {url})")

        header = f"Content from {url}:\n\n"
        return ToolOutput(content=header + text)


def _extract_text_from_html(html: str) -> str:
    """Simple HTML to text extraction."""
    # Remove script and style
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav, header, footer
    text = re.sub(r"<(nav|header|footer)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace common blocks with newlines
    text = re.sub(r"<(br|hr|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    lines = [line.strip() for line in text.split("\n")]
    lines = [l for l in lines if l]
    # Remove duplicate blank lines
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    return "\n".join(result)
