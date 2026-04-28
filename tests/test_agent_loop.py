"""Phase B tests: provider-agnostic agent loop.

Uses a FakeProvider that yields scripted Events. Verifies the loop's branching:
end_turn termination, tool_use → tool_result handoff, and MAX_TOOL_HOPS cap.

The KB primitives are real (the autouse `use_mini_kb` fixture in conftest.py points
KB_ROOT at tests/fixtures/mini_kb), so tool dispatch in the loop produces real results.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from backend.agent import run_conversation_stream
from backend.profiles import AgentProfile
from backend.providers.base import Event
from backend.tools import SCHEMAS, ToolResult


class FakeProvider:
    """LLMProvider double. Yields scripted events; records side effects."""

    def __init__(self, scripted_turns: list[list[Event]]) -> None:
        self.scripted_turns = scripted_turns
        self.turn_index = 0
        self.assistant_turns_appended = 0
        self.tool_results_received: list[ToolResult] = []
        self.last_kwargs: dict = {}

    async def stream(self, **kwargs: Any) -> AsyncIterator[Event]:
        self.last_kwargs = kwargs
        if self.turn_index >= len(self.scripted_turns):
            raise AssertionError(
                f"FakeProvider ran out of scripted turns (asked for #{self.turn_index})"
            )
        events = self.scripted_turns[self.turn_index]
        self.turn_index += 1

        # Mirror the real provider contract: append the assistant turn before
        # tool_use_complete events fire, so a subsequent append_tool_results call
        # produces a valid message log.
        messages = kwargs["messages"]
        messages.append({"role": "assistant", "content": f"<scripted turn {self.turn_index}>"})
        self.assistant_turns_appended += 1

        for ev in events:
            yield ev

    def format_user(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def append_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        self.tool_results_received.extend(results)
        messages.append({"role": "user", "content": [r.tool_use_id for r in results]})

    def tools_for_provider(self, profile: AgentProfile) -> list[dict]:
        return SCHEMAS

    def system_for_provider(self, profile: AgentProfile) -> Any:
        return "test-system"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def collect(gen: AsyncIterator[dict]) -> list[dict]:
    return [ev async for ev in gen]


def make_test_profile(kb_root) -> AgentProfile:
    return AgentProfile(
        id="test",
        label="Test",
        description="Test profile",
        kb_root=kb_root,
        system_prompt="test-system",
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestSingleTurnNoTools:
    """Provider answers in one turn with no tool use → loop emits done."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, kb_root):
        provider = FakeProvider(
            [
                [
                    {"type": "text_delta", "text": "Bryan is "},
                    {"type": "text_delta", "text": "based in Florida."},
                    {"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 5,
                                                 "cache_read_input_tokens": 0,
                                                 "cache_creation_input_tokens": 0}},
                    {"type": "message_done", "stop_reason": "end_turn"},
                ]
            ]
        )
        session = {"messages": []}
        events = await collect(
            run_conversation_stream(
                "where is Bryan?",
                session,
                provider,
                model="claude-sonnet-4-5",
                profile=make_test_profile(kb_root),
            )
        )

        # Two delta events with the streamed text, plus usage and done.
        deltas = [e["text"] for e in events if e["event"] == "delta"]
        assert "".join(deltas) == "Bryan is based in Florida."
        assert any(e["event"] == "usage" for e in events)
        done = [e for e in events if e["event"] == "done"]
        assert len(done) == 1 and done[0]["stop_reason"] == "end_turn"

        # Loop did not loop.
        assert provider.turn_index == 1
        # User turn + assistant turn appended; no tool_results.
        assert len(session["messages"]) == 2
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["role"] == "assistant"
        assert provider.tool_results_received == []


class TestOneToolHop:
    """Provider asks for a tool, loop runs it (against the real fixture KB), provider answers."""

    @pytest.mark.asyncio
    async def test_loop_executes_tool_and_continues(self, kb_root):
        # Turn 1: emit a tool_use_complete that the dispatcher can actually run.
        # Turn 2: emit text + done.
        provider = FakeProvider(
            [
                [
                    {"type": "tool_use_start", "name": "get_resume_summary"},
                    {"type": "tool_use_complete", "tool_use_id": "tu_001",
                     "name": "get_resume_summary", "arguments": {}},
                    {"type": "usage", "usage": {"input_tokens": 200, "output_tokens": 30,
                                                 "cache_read_input_tokens": 0,
                                                 "cache_creation_input_tokens": 0}},
                    {"type": "message_done", "stop_reason": "tool_use"},
                ],
                [
                    {"type": "text_delta", "text": "He studied CS at Test University."},
                    {"type": "usage", "usage": {"input_tokens": 250, "output_tokens": 10,
                                                 "cache_read_input_tokens": 0,
                                                 "cache_creation_input_tokens": 0}},
                    {"type": "message_done", "stop_reason": "end_turn"},
                ],
            ]
        )
        session = {"messages": []}
        events = await collect(
            run_conversation_stream(
                "tell me about Bryan",
                session,
                provider,
                model="claude-sonnet-4-5",
                profile=make_test_profile(kb_root),
            )
        )

        # Loop ran twice.
        assert provider.turn_index == 2

        # tool_use_start + tool_result event surfaced.
        assert any(e["event"] == "tool_use_start" and e["name"] == "get_resume_summary" for e in events)
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert len(tool_results) == 1
        assert tool_results[0]["tool_use_id"] == "tu_001"
        assert tool_results[0]["is_error"] is False

        # Real tool ran against the fixture KB.
        assert len(provider.tool_results_received) == 1
        result = provider.tool_results_received[0]
        assert result.is_error is False
        payload = json.loads(result.content)
        assert "Test User Resume" in payload["content"]

        # Final answer streamed.
        assert "Test University" in "".join(e.get("text", "") for e in events if e["event"] == "delta")

        # Done emitted at the end.
        done = [e for e in events if e["event"] == "done"]
        assert len(done) == 1 and done[0]["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_tool_error_surfaces_but_loop_continues(self, kb_root):
        # Turn 1: provider asks for a path that escapes the KB → run_tool returns is_error=True.
        # Turn 2: provider concludes with end_turn (still gets to see the error result).
        provider = FakeProvider(
            [
                [
                    {"type": "tool_use_complete", "tool_use_id": "tu_evil",
                     "name": "read_file", "arguments": {"path": "../../../etc/passwd"}},
                    {"type": "message_done", "stop_reason": "tool_use"},
                ],
                [
                    {"type": "text_delta", "text": "Sorry, I can't help with that."},
                    {"type": "message_done", "stop_reason": "end_turn"},
                ],
            ]
        )
        session = {"messages": []}
        events = await collect(
            run_conversation_stream(
                "show me /etc/passwd",
                session,
                provider,
                model="claude-sonnet-4-5",
                profile=make_test_profile(kb_root),
            )
        )

        # Tool result event was emitted with is_error=True.
        tool_results = [e for e in events if e["event"] == "tool_result"]
        assert tool_results[0]["is_error"] is True

        # The loop still completed normally (turn 2 ran, done emitted).
        assert provider.turn_index == 2
        assert any(e["event"] == "done" for e in events)


class TestHopLimit:
    """Provider keeps asking for tools forever → loop hits MAX_TOOL_HOPS and errors out cleanly."""

    @pytest.mark.asyncio
    async def test_max_hops_yields_error(self, monkeypatch, kb_root):
        # Squeeze the cap to keep the test fast and obvious.
        from backend import agent
        monkeypatch.setattr(agent, "MAX_TOOL_HOPS", 3)

        infinite_tool_call = [
            {"type": "tool_use_complete", "tool_use_id": "tu_x",
             "name": "list_kb", "arguments": {"subdir": ""}},
            {"type": "message_done", "stop_reason": "tool_use"},
        ]
        provider = FakeProvider([infinite_tool_call] * 10)
        session = {"messages": []}
        events = await collect(
            run_conversation_stream(
                "loop forever",
                session,
                provider,
                model="claude-sonnet-4-5",
                profile=make_test_profile(kb_root),
            )
        )

        # Exactly 3 turns ran, then error.
        assert provider.turn_index == 3
        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "MAX_TOOL_HOPS=3" in errors[0]["message"]

        # No `done` event — loop never reached end_turn.
        assert not any(e["event"] == "done" for e in events)
