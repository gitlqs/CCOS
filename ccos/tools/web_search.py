"""WebSearch tool -- search the web for information."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


def _build_web_search_description() -> str:
    # Mirror cc's getLocalMonthYear() (e.g. "June 2026").
    current_month_year = datetime.now().strftime("%B %Y")
    return (
        "\n"
        "- Allows Claude to search the web and use the results to inform responses\n"
        "- Provides up-to-date information for current events and recent data\n"
        "- Returns search result information formatted as search result blocks, "
        "including links as markdown hyperlinks\n"
        "- Use this tool for accessing information beyond Claude's knowledge cutoff\n"
        "- Searches are performed automatically within a single API call\n"
        "\n"
        "CRITICAL REQUIREMENT - You MUST follow this:\n"
        "  - After answering the user's question, you MUST include a \"Sources:\" "
        "section at the end of your response\n"
        "  - In the Sources section, list all relevant URLs from the search results "
        "as markdown hyperlinks: [Title](URL)\n"
        "  - This is MANDATORY - never skip including sources in your response\n"
        "  - Example format:\n"
        "\n"
        "    [Your answer here]\n"
        "\n"
        "    Sources:\n"
        "    - [Source Title 1](https://example.com/1)\n"
        "    - [Source Title 2](https://example.com/2)\n"
        "\n"
        "Usage notes:\n"
        "  - Domain filtering is supported to include or block specific websites\n"
        "  - Web search is only available in the US\n"
        "\n"
        "IMPORTANT - Use the correct year in search queries:\n"
        f"  - The current month is {current_month_year}. You MUST use this year when "
        "searching for recent information, documentation, or current events.\n"
        "  - Example: If the user asks for \"latest React docs\", search for "
        "\"React documentation\" with the current year, NOT last year\n"
    )


class WebSearchTool(Tool):
    name = "WebSearch"
    description = _build_web_search_description()
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to use",
                "minLength": 2,
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include search results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never include search results from these domains",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        query = params["query"]
        allowed_domains = params.get("allowed_domains") or []
        blocked_domains = params.get("blocked_domains") or []

        if allowed_domains and blocked_domains:
            return ToolOutput(
                content=(
                    "Error: Cannot specify both allowed_domains and "
                    "blocked_domains in the same request"
                ),
                is_error=True,
            )

        # Build a domain-filtered query: allowed_domains as `site:` OR include,
        # blocked_domains as `-site:` exclude.
        if allowed_domains:
            include_parts = " OR ".join(f"site:{d}" for d in allowed_domains)
            query = f"{query} ({include_parts})"
        elif blocked_domains:
            exclude_parts = " ".join(f"-site:{d}" for d in blocked_domains)
            query = f"{query} {exclude_parts}"

        # Try using the Brave Search API or fallback
        import os
        brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if brave_key:
            return await self._brave_search(query, brave_key)

        # Fallback: use DuckDuckGo HTML (no API key needed)
        return await self._ddg_search(query)

    async def _brave_search(self, query: str, api_key: str) -> ToolOutput:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 10},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("web", {}).get("results", [])
            if not results:
                return ToolOutput(content=f"No results found for: {query}")

            lines = [f"Search results for: {query}\n"]
            for i, r in enumerate(results[:10], 1):
                lines.append(f"{i}. [{r.get('title', '')}]({r.get('url', '')})")
                desc = r.get("description", "")
                if desc:
                    lines.append(f"   {desc}")
                lines.append("")
            return ToolOutput(content="\n".join(lines))

        except Exception as e:
            return ToolOutput(content=f"Search error: {e}", is_error=True)

    async def _ddg_search(self, query: str) -> ToolOutput:
        try:
            import httpx
            encoded = urllib.parse.quote_plus(query)
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={"User-Agent": "CCOS/0.1"},
                follow_redirects=True,
            ) as client:
                resp = await client.get(f"https://html.duckduckgo.com/html/?q={encoded}")
                resp.raise_for_status()

            # Parse results from HTML
            import re
            results = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]*)">(.*?)</a>.*?'
                r'<a class="result__snippet"[^>]*>(.*?)</a>',
                resp.text, re.DOTALL,
            )

            if not results:
                # Simpler pattern
                results = re.findall(
                    r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    resp.text, re.DOTALL,
                )
                results = [(url, title, "") for url, title in results]

            if not results:
                return ToolOutput(content=f"No results found for: {query}")

            lines = [f"Search results for: {query}\n"]
            for i, (url, title, snippet) in enumerate(results[:10], 1):
                title = re.sub(r"<[^>]+>", "", title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                # Decode DuckDuckGo redirect URL
                if "uddg=" in url:
                    url = urllib.parse.unquote(url.split("uddg=")[-1].split("&")[0])
                lines.append(f"{i}. {title}")
                lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet}")
                lines.append("")
            return ToolOutput(content="\n".join(lines))

        except Exception as e:
            return ToolOutput(content=f"Search error: {e}", is_error=True)
