"""WebFetch tool -- fetch and extract content from URLs."""

from __future__ import annotations

import re
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

_MAX_CONTENT_LENGTH = 50_000


class WebFetchTool(Tool):
    name = "WebFetch"
    description = (
        "Fetches content from a URL and returns it as text.\n\n"
        "Use this tool to:\n"
        "- Read documentation pages\n"
        "- Fetch API responses\n"
        "- Download and read web content\n\n"
        "The tool extracts readable text from HTML pages automatically."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from",
            },
            "prompt": {
                "type": "string",
                "description": "Optional prompt to apply to the fetched content (e.g., 'summarize this page')",
            },
        },
        "required": ["url"],
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
