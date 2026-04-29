"""Anthropic provider — streaming tool-use loop, mirroring notebook 003/009."""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from backend.profiles import AgentProfile
from backend.providers.base import Event
from backend.tools import ToolResult, schemas_for_tools


class AnthropicProvider:
    def __init__(self, *, thinking_budget: int | None = None) -> None:
        self.client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        # When set, enable extended thinking with this budget. The API requires
        # max_tokens > thinking.budget_tokens, so the stream call also bumps
        # max_tokens upward if the caller's value would underflow.
        self.thinking_budget = thinking_budget

    def format_user(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def append_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )

    def tools_for_provider(self, profile: AgentProfile) -> list[dict]:
        return schemas_for_tools(profile.tools)

    def system_for_provider(self, profile: AgentProfile) -> Any:
        return [{"type": "text", "text": profile.system_prompt}]

    async def stream(
        self,
        *,
        model: str,
        messages: list,
        system: Any,
        tools: list,
        max_tokens: int,
    ) -> AsyncIterator[Event]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if self.thinking_budget:
            # Anthropic requires max_tokens strictly greater than budget_tokens; reserve
            # at least 1024 tokens for the visible response so a tight MAX_TOKENS env
            # doesn't starve it.
            kwargs["max_tokens"] = max(max_tokens, self.thinking_budget + 1024)
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}

        async with self.client.messages.stream(**kwargs) as stream:
            async for chunk in stream:
                if chunk.type == "text":
                    yield {"type": "text_delta", "text": chunk.text}
                elif chunk.type == "thinking":
                    yield {"type": "thinking_delta", "text": chunk.thinking}
                elif chunk.type == "content_block_start":
                    cb = getattr(chunk, "content_block", None)
                    if cb is not None and getattr(cb, "type", None) == "tool_use":
                        yield {"type": "tool_use_start", "name": cb.name}
            response = await stream.get_final_message()

        # The assistant turn (including tool_use blocks) must land in the message log
        # before the next turn's tool_result blocks. Otherwise the API rejects the next call.
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "tool_use":
                yield {
                    "type": "tool_use_complete",
                    "tool_use_id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                }

        yield {"type": "usage", "usage": _norm_usage(response.usage)}
        yield {
            "type": "message_done",
            "stop_reason": "tool_use" if response.stop_reason == "tool_use" else "end_turn",
        }


def _norm_usage(u: Any) -> dict:
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
