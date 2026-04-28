"""OpenAI-compatible chat-completions provider.

This covers OpenAI proper and Moonshot/Kimi because Kimi exposes an
OpenAI-compatible /chat/completions API for function tools.
"""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from backend.profiles import AgentProfile
from backend.providers.base import Event
from backend.providers.tool_translator import to_openai
from backend.tools import ToolResult, schemas_for_tools


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        api_key_env: str,
        base_url: str | None = None,
        token_param: str = "max_completion_tokens",
        stream_options: bool = True,
        include_tool_result_name: bool = False,
        extra_body: dict | None = None,
        reasoning_effort: str | None = None,
        preserve_reasoning_content: bool = False,
        client: Any | None = None,
    ) -> None:
        self.token_param = token_param
        self.stream_options = stream_options
        self.include_tool_result_name = include_tool_result_name
        self.extra_body = extra_body
        self.reasoning_effort = reasoning_effort
        self.preserve_reasoning_content = preserve_reasoning_content
        self.client = client or AsyncOpenAI(
            api_key=os.environ[api_key_env],
            base_url=base_url,
        )

    def format_user(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def append_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        for r in results:
            msg = {
                "role": "tool",
                "tool_call_id": r.tool_use_id,
                "content": r.content,
            }
            if self.include_tool_result_name:
                msg["name"] = r.name
            messages.append(msg)

    def tools_for_provider(self, profile: AgentProfile) -> list[dict]:
        return to_openai(schemas_for_tools(profile.tools))

    def system_for_provider(self, profile: AgentProfile) -> dict:
        return {"role": "system", "content": profile.system_prompt}

    async def stream(
        self,
        *,
        model: str,
        messages: list,
        system: Any,
        tools: list,
        max_tokens: int,
    ) -> AsyncIterator[Event]:
        request: dict[str, Any] = {
            "model": model,
            "messages": [system, *messages],
            "tools": tools,
            "stream": True,
            self.token_param: max_tokens,
        }
        if self.stream_options:
            request["stream_options"] = {"include_usage": True}
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            request["extra_body"] = self.extra_body

        stream = await self.client.chat.completions.create(**request)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        announced_tool_names: set[str] = set()
        stop_reason = "end_turn"
        usage: dict[str, int] | None = None

        async for chunk in stream:
            usage_obj = getattr(chunk, "usage", None)
            if usage_obj is not None:
                usage = _norm_usage(usage_obj)

            for choice in getattr(chunk, "choices", []) or []:
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"

                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    continue

                text = getattr(delta, "content", None)
                if text:
                    content_parts.append(text)
                    yield {"type": "text_delta", "text": text}

                for tc in getattr(delta, "tool_calls", None) or []:
                    index = getattr(tc, "index", None)
                    if index is None:
                        index = len(tool_calls)
                    acc = tool_calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )

                    tc_id = getattr(tc, "id", None)
                    if tc_id:
                        acc["id"] = tc_id
                    tc_type = getattr(tc, "type", None)
                    if tc_type:
                        acc["type"] = tc_type

                    fn = getattr(tc, "function", None)
                    if fn is None:
                        continue
                    name = getattr(fn, "name", None)
                    if name:
                        acc["function"]["name"] = name
                        if name not in announced_tool_names:
                            announced_tool_names.add(name)
                            yield {"type": "tool_use_start", "name": name}
                    args = getattr(fn, "arguments", None)
                    if args:
                        acc["function"]["arguments"] += args

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        completed_tool_calls = [tool_calls[i] for i in sorted(tool_calls)]
        if completed_tool_calls:
            assistant_msg["tool_calls"] = completed_tool_calls
            stop_reason = "tool_use"
        # DeepSeek thinking mode requires reasoning_content to round-trip on tool-call turns
        # or the next request 400s. Other providers don't accept the field, so this is gated.
        if self.preserve_reasoning_content and completed_tool_calls and reasoning_parts:
            assistant_msg["reasoning_content"] = "".join(reasoning_parts)
        messages.append(assistant_msg)

        for i, tc in enumerate(completed_tool_calls):
            fn = tc.get("function", {})
            name = fn.get("name") or ""
            tool_use_id = tc.get("id") or f"call_{i}"
            yield {
                "type": "tool_use_complete",
                "tool_use_id": tool_use_id,
                "name": name,
                "arguments": _parse_arguments(fn.get("arguments", "")),
            }

        if usage is not None:
            yield {"type": "usage", "usage": usage}
        yield {"type": "message_done", "stop_reason": stop_reason}


def _parse_arguments(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _norm_usage(u: Any) -> dict:
    input_tokens = getattr(u, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(u, "input_tokens", 0) or 0
    output_tokens = getattr(u, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(u, "output_tokens", 0) or 0
    # DeepSeek surfaces KV-cache hits as prompt_cache_hit_tokens; absent on other providers.
    cache_hit = getattr(u, "prompt_cache_hit_tokens", 0) or 0
    return {
        "input_tokens": input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "cache_read_input_tokens": cache_hit,
        "cache_creation_input_tokens": 0,
    }
