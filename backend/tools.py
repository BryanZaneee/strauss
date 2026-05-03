"""Tool schemas (Anthropic shape) + the dispatcher.

SCHEMAS are authored once in Anthropic's `input_schema` shape. The OpenAICompatProvider
translates these via tool_translator.to_openai() at startup.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.kb_loader import (
    KBError,
    get_project_context,
    get_resume_summary,
    list_kb,
    read_file,
    search_kb,
)
from backend.web_search import WebSearchError, web_search

SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_kb",
        "description": (
            "List files under the knowledge base, optionally filtered by a subdirectory. "
            "Returns relative paths so they can be passed to read_file. "
            "Call this only when you don't already know what's available — the system context "
            "already includes a manifest of the KB. Walk depth limited to 2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subdir": {
                    "type": "string",
                    "description": (
                        "Relative subdirectory under the KB root (e.g. 'projects', 'codebases'). "
                        "Empty string lists from the root."
                    ),
                }
            },
            "required": ["subdir"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a single file from the knowledge base by relative path. "
            "Files are markdown (resume, project summaries, FAQs) or repomix XML codebase dumps. "
            "Repomix dumps are LARGE — for them, pass start_line/end_line to slice. "
            "Returns {path, lines: 'X-Y of Z', content} so you know exactly what you didn't see."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path under the KB root, e.g. 'resume/resume.md' or 'codebases/shuttrr.xml'."
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional 1-indexed start line (default 1).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional 1-indexed end line, inclusive. Default: start+1499. Hard cap: start+2999.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_kb",
        "description": (
            "Search the knowledge base for a substring or regex match. "
            "Returns matching file paths with up to 3 lines of context per match (240 char cap). "
            "Use to find which file mentions a topic before you read_file. "
            "Defaults to case-insensitive substring; pass regex=true for regex."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "regex": {"type": "boolean", "default": False},
                "subdir": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_resume_summary",
        "description": (
            "Return Bryan's resume in markdown. Use for any question about employment history, "
            "education, certifications, or overall qualifications — faster than "
            "read_file('resume/resume.md')."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_project_context",
        "description": (
            "Return a curated PITCH summary for one of Bryan's projects (the 'why it matters' angle). "
            "Use when a recruiter asks about a specific project — answers like 'tell me about X' or "
            "'is X relevant to Y role'. For technical detail (stack, key files, architecture), the "
            "system context already includes a quick_info cheat sheet — no tool call needed for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": (
                        "Project name (case-insensitive). Examples: 'shuttrr', 'bryanzane', "
                        "'infinichat', 'anywhere', 'papo', 'esme', 'physiq', 'ftrmsg'."
                    ),
                }
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the public web for up-to-date information not in the knowledge base. "
            "Use for recent news, current events, company/person/technology lookups, or to "
            "verify a fact that may have changed since the KB was last updated. "
            "Do NOT use for questions about Bryan's resume or projects — those live in the KB. "
            "Returns a synthesized answer plus ranked results with title, url, and a short "
            "content snippet. Cite the URL when you use a result in your reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10, default 5).",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": (
                        "'basic' is faster and cheaper; 'advanced' returns deeper snippets "
                        "for harder queries. Default 'basic'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]

SCHEMAS_BY_NAME: dict[str, dict[str, Any]] = {s["name"]: s for s in SCHEMAS}
DEFAULT_TOOL_NAMES: tuple[str, ...] = tuple(SCHEMAS_BY_NAME)


def schemas_for_tools(tool_names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    """Return shared tool schemas for a profile's allowed tool names."""
    names = tuple(tool_names or DEFAULT_TOOL_NAMES)
    return [SCHEMAS_BY_NAME[name] for name in names]


@dataclass
class ToolResult:
    """Normalized tool result. Providers convert this to their wire format."""

    tool_use_id: str
    name: str
    content: str  # JSON string passed back to the model
    is_error: bool = False


def run_tool(
    name: str,
    arguments: dict,
    tool_use_id: str,
    *,
    root: Path | None = None,
    allowed_tools: tuple[str, ...] | list[str] | set[str] | None = None,
) -> ToolResult:
    """Dispatch a tool call. Catches KBError + unexpected errors → is_error=True."""
    try:
        if allowed_tools is not None and name not in set(allowed_tools):
            return ToolResult(
                tool_use_id=tool_use_id,
                name=name,
                content=json.dumps({"error": f"tool not enabled for this profile: {name}"}),
                is_error=True,
            )
        if name == "list_kb":
            out: Any = list_kb(arguments.get("subdir", ""), root=root)
        elif name == "read_file":
            out = read_file(
                arguments["path"],
                arguments.get("start_line", 1),
                arguments.get("end_line"),
                root=root,
            )
        elif name == "search_kb":
            out = search_kb(
                arguments["query"],
                regex=arguments.get("regex", False),
                subdir=arguments.get("subdir", ""),
                max_results=arguments.get("max_results", 20),
                root=root,
            )
        elif name == "get_resume_summary":
            out = get_resume_summary(root=root)
        elif name == "get_project_context":
            out = get_project_context(arguments["project_name"], root=root)
        elif name == "web_search":
            out = web_search(
                arguments["query"],
                max_results=arguments.get("max_results", 5),
                search_depth=arguments.get("search_depth", "basic"),
                include_answer=arguments.get("include_answer", True),
            )
        else:
            return ToolResult(
                tool_use_id=tool_use_id,
                name=name,
                content=json.dumps({"error": f"unknown tool: {name}"}),
                is_error=True,
            )
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps(out, ensure_ascii=False),
            is_error=False,
        )
    except KBError as e:
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": str(e)}),
            is_error=True,
        )
    except WebSearchError as e:
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": str(e)}),
            is_error=True,
        )
    except KeyError as e:
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": f"missing required argument: {e.args[0]}"}),
            is_error=True,
        )
    except Exception as e:
        return ToolResult(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": f"{type(e).__name__}: {e}"}),
            is_error=True,
        )
