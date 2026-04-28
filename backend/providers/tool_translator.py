"""Translate shared Anthropic-shaped tool schemas to provider-specific shapes."""
from __future__ import annotations

from typing import Any


def to_openai(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert {name, description, input_schema} tools to OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["input_schema"],
            },
        }
        for schema in schemas
    ]


def to_gemini_declarations(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert shared tool schemas to Gemini FunctionDeclaration dictionaries."""
    return [
        {
            "name": schema["name"],
            "description": schema["description"],
            "parameters_json_schema": schema["input_schema"],
        }
        for schema in schemas
    ]
