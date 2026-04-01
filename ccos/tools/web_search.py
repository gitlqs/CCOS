"""WebSearch tool -- search the web for information."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput


class WebSearchTool(Tool):
    name = "WebSearch"
    description = (
        "Search the web for real-time information.\n\n"
        "Use this when you need current information that may not be in your training data.\n"
        "Returns search results with titles, URLs, and snippets."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of domains to restrict search to",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        query = params["query"]
        domains = params.get("domains", [])

        # Build domain-restricted query if needed
        if domains:
            domain_parts = " OR ".join(f"site:{d}" for d in domains)
            query = f"{query} ({domain_parts})"

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
