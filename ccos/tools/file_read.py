"""FileRead tool — read files with line numbers, images, and PDF support."""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from typing import Any

from ccos.tools.base import Tool, ToolContext, ToolOutput

# Match cc: only these five extensions are rendered as images. SVG/bmp/ico
# fall through to text reading.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_DEFAULT_LIMIT = 2000
_MAX_PDF_PAGES_PER_READ = 20


class FileReadTool(Tool):
    name = "Read"
    description = (
        "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
        "Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.\n\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
        "- When you already know which part of the file you need, only read that part. This can be important for larger files.\n"
        "- Results are returned using cat -n format, with line numbers starting at 1\n"
        "- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.\n"
        '- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.\n'
        "- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.\n"
        "- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.\n"
        "- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.\n"
        "- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "The line number to start reading from. Only provide if the file is too large to read at once",
            },
            "limit": {
                "type": "integer",
                "description": "The number of lines to read. Only provide if the file is too large to read at once.",
            },
            "pages": {
                "type": "string",
                "description": 'Page range for PDF files (e.g., "1-5", "3", "10-20"). Only applicable to PDF files. Maximum 20 pages per request.',
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def is_read_only(self, params: dict[str, Any]) -> bool:
        return True

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        file_path = params["file_path"]
        # cc treats offset as 1-indexed (call() defaults offset=1).
        offset = params.get("offset", 1)
        limit = params.get("limit", _DEFAULT_LIMIT)
        pages = params.get("pages")

        # Validate the pages parameter (pure string parsing).
        if pages is not None and self._parse_pdf_page_range(pages) is None:
            return ToolOutput(
                content=(
                    f'Invalid pages parameter: "{pages}". '
                    'Use formats like "1-5", "3", or "10-20". Pages are 1-indexed.'
                ),
                is_error=True,
            )

        # Expand path
        file_path = os.path.expanduser(file_path)
        if not os.path.isabs(file_path):
            file_path = os.path.normpath(os.path.join(ctx.cwd, file_path))

        if not os.path.exists(file_path):
            return ToolOutput(content=f"Error: File not found: {file_path}", is_error=True)

        if os.path.isdir(file_path):
            return ToolOutput(
                content=f"Error: {file_path} is a directory, not a file. Use Bash with 'ls' to list directory contents.",
                is_error=True,
            )

        # Check for image
        ext = os.path.splitext(file_path)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            return await self._read_image(file_path, ctx)

        # Read text file
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="utf-16-le", errors="replace") as f:
                    text = f.read()
            except Exception as e:
                return ToolOutput(content=f"Error reading file: {e}", is_error=True)
        except PermissionError:
            return ToolOutput(content=f"Error: Permission denied: {file_path}", is_error=True)
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)

        # Empty file: cc returns a system-reminder in place of contents.
        if text == "":
            ctx.record_read(file_path)
            return ToolOutput(
                content="<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"
            )

        # Split on newlines only (matches cc's content.split(/\r?\n/)).
        # Trailing whitespace within a line is preserved — no rstrip — so
        # Edit's exact-match against Read output stays correct.
        all_lines = re.split(r"\r?\n", text)
        # A trailing newline produces a final empty element; cc keeps it the
        # same way (split keeps the empty tail). Mirror cc exactly.

        # cc: lineOffset = offset === 0 ? 0 : offset - 1 (1-indexed offset).
        start = 0 if offset == 0 else offset - 1
        end = start + limit
        selected = all_lines[start:end]

        ctx.record_read(file_path)

        # Offset beyond file length: cc returns a system-reminder.
        if not selected:
            return ToolOutput(
                content=(
                    f"<system-reminder>Warning: the file exists but is shorter than "
                    f"the provided offset ({offset}). The file has {len(all_lines)} lines."
                    f"</system-reminder>"
                )
            )

        # Format with line numbers (cat -n style, compact "N\t" prefix).
        # The displayed line number for the first line equals `offset`.
        lines_out = [f"{i}\t{line}" for i, line in enumerate(selected, start=start + 1)]
        result = "\n".join(lines_out)

        if end < len(all_lines):
            result += f"\n\n(File has {len(all_lines)} total lines. Showing lines {start + 1}-{end}.)"

        return ToolOutput(content=result)

    @staticmethod
    def _parse_pdf_page_range(spec: str) -> tuple[int, int] | None:
        """Parse a PDF page-range spec like '1-5', '3', '10-20'.

        Returns (first_page, last_page) 1-indexed, or None if invalid.
        Mirrors cc's parsePDFPageRange (validation only).
        """
        spec = spec.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", spec)
        if not m:
            return None
        first = int(m.group(1))
        if first < 1:
            return None
        last = int(m.group(2)) if m.group(2) is not None else first
        if last < first:
            return None
        return first, last

    async def _read_image(self, file_path: str, ctx: ToolContext) -> ToolOutput:
        try:
            with open(file_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            mime = mimetypes.guess_type(file_path)[0] or "image/png"
            ctx.record_read(file_path)
            return ToolOutput(
                content=f"[Image: {os.path.basename(file_path)}]",
                images=[{"media_type": mime, "data": data}],
            )
        except Exception as e:
            return ToolOutput(content=f"Error reading image: {e}", is_error=True)
