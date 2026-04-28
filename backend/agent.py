"""Provider-agnostic agent loop. Mirrors the run_conversation pattern from
notebook 001_tools_009.ipynb — bounded loop on stop_reason, run tools, append
results, repeat. Differences: yields normalized SSE events, calls into a
LLMProvider so the same loop covers Anthropic + OpenAI/Moonshot.
"""
from __future__ import annotations

from typing import AsyncIterator

from backend.config import MAX_TOKENS, MAX_TOOL_HOPS
from backend.profiles import AgentProfile, load_profile
from backend.providers.base import LLMProvider
from backend.tools import run_tool


async def run_conversation_stream(
    user_message: str,
    session: dict,
    provider: LLMProvider,
    model: str,
    profile: AgentProfile | None = None,
) -> AsyncIterator[dict]:
    """Run one user turn through the agent. Yields SSE-shaped event dicts.

    Mutates session["messages"] across the conversation. Hard-bounded by MAX_TOOL_HOPS.
    """
    profile = profile or load_profile()
    session["messages"].append(provider.format_user(user_message))

    for _ in range(MAX_TOOL_HOPS):
        tool_calls_pending: list[dict] = []
        stop_reason: str = "end_turn"

        async for ev in provider.stream(
            model=model,
            messages=session["messages"],
            system=provider.system_for_provider(profile),
            tools=provider.tools_for_provider(profile),
            max_tokens=MAX_TOKENS,
        ):
            t = ev.get("type")
            if t == "text_delta":
                yield {"event": "delta", "text": ev["text"]}
            elif t == "tool_use_start":
                yield {"event": "tool_use_start", "name": ev["name"]}
            elif t == "tool_use_complete":
                tool_calls_pending.append(ev)
            elif t == "usage":
                yield {"event": "usage", **ev["usage"]}
            elif t == "message_done":
                stop_reason = ev["stop_reason"]
            elif t == "error":
                yield {"event": "error", "message": ev.get("text", "provider error")}
                return

        if stop_reason != "tool_use":
            yield {"event": "done", "stop_reason": stop_reason}
            return

        results = [
            run_tool(
                tc["name"],
                tc["arguments"],
                tc["tool_use_id"],
                root=profile.kb_root,
                allowed_tools=profile.tools,
            )
            for tc in tool_calls_pending
        ]
        for r in results:
            yield {"event": "tool_result", "tool_use_id": r.tool_use_id, "is_error": r.is_error}
        provider.append_tool_results(session["messages"], results)

    yield {"event": "error", "message": f"hit MAX_TOOL_HOPS={MAX_TOOL_HOPS}"}
