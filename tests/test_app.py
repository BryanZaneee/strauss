"""Phase C tests: FastAPI endpoints with a monkeypatched provider.

Hits /api/health, /api/models, and /api/chat. Verifies the SSE stream is well-formed
when the provider is replaced with a FakeProvider that yields canned events.
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

from backend.profiles import AgentProfile
from backend.providers.base import Event
from backend.tools import SCHEMAS, ToolResult


# --------------------------------------------------------------------------- #
# A minimal FakeProvider duplicated from test_agent_loop.py so this file can
# be read in isolation.
# --------------------------------------------------------------------------- #


class FakeProvider:
    def __init__(self, scripted_turns: list[list[Event]]) -> None:
        self.scripted_turns = scripted_turns
        self.turn_index = 0

    async def stream(self, **kwargs: Any) -> AsyncIterator[Event]:
        events = self.scripted_turns[self.turn_index]
        self.turn_index += 1
        kwargs["messages"].append({"role": "assistant", "content": "<scripted>"})
        for ev in events:
            yield ev

    def format_user(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def append_tool_results(self, messages: list, results: list[ToolResult]) -> None:
        messages.append({"role": "user", "content": [r.tool_use_id for r in results]})

    def tools_for_provider(self, profile: AgentProfile) -> list[dict]:
        return SCHEMAS

    def system_for_provider(self, profile: AgentProfile) -> Any:
        return "test-system"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch):
    # Pretend Anthropic key is set so /api/models returns at least one model.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Reload config so available_models() sees the patched env.
    import importlib
    from backend import config

    importlib.reload(config)
    from backend import app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


# --------------------------------------------------------------------------- #
# /api/health
# --------------------------------------------------------------------------- #


class TestHealth:
    def test_health_ok(self, client):
        c, _ = client
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "sessions" in body


# --------------------------------------------------------------------------- #
# /api/models
# --------------------------------------------------------------------------- #


class TestModels:
    def test_lists_all_configured_providers_with_keys(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setenv("MOONSHOT_API_KEY", "would-be-key")
        monkeypatch.setenv("OPENAI_API_KEY", "would-be-key")
        monkeypatch.setenv("GEMINI_API_KEY", "would-be-key")
        r = c.get("/api/models")
        assert r.status_code == 200
        body = r.json()
        assert body["default"] is not None
        ids = {m["id"] for m in body["models"]}
        providers = {m["provider"] for m in body["models"]}
        assert {"anthropic", "openai_compat", "gemini"} <= providers
        assert "claude-sonnet-4-5" in ids
        assert "gpt-5" in ids
        assert "kimi-k2.6" in ids
        assert "gemini-2.5-flash" in ids


# --------------------------------------------------------------------------- #
# /api/chat
# --------------------------------------------------------------------------- #


class TestChat:
    def test_unknown_model_400(self, client):
        c, _ = client
        r = c.post(
            "/api/chat",
            json={"session_id": "s1", "message": "hi", "model": "definitely-not-a-model"},
        )
        assert r.status_code == 400

    def test_streams_well_formed_sse(self, client, monkeypatch):
        c, app_module = client

        fake = FakeProvider(
            [
                [
                    {"type": "text_delta", "text": "Hello"},
                    {"type": "text_delta", "text": " there."},
                    {"type": "usage", "usage": {
                        "input_tokens": 10, "output_tokens": 3,
                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    }},
                    {"type": "message_done", "stop_reason": "end_turn"},
                ]
            ]
        )
        monkeypatch.setattr(app_module, "get_provider", lambda mid: fake)

        with c.stream(
            "POST",
            "/api/chat",
            json={"session_id": "s2", "message": "hi", "model": "claude-sonnet-4-5"},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            body = b"".join(r.iter_bytes()).decode("utf-8")

        # SSE frames are separated by blank lines and contain `event:` + `data:` pairs.
        frames = [f for f in body.split("\n\n") if f.strip()]
        assert frames, "no SSE frames received"
        kinds = [f.split("\n", 1)[0] for f in frames]
        assert "event: delta" in kinds
        assert "event: usage" in kinds
        assert kinds[-1] == "event: done"

    def test_session_state_grows(self, client, monkeypatch):
        c, app_module = client

        fake = FakeProvider(
            [
                [
                    {"type": "text_delta", "text": "ok"},
                    {"type": "message_done", "stop_reason": "end_turn"},
                ]
            ]
        )
        monkeypatch.setattr(app_module, "get_provider", lambda mid: fake)

        with c.stream(
            "POST",
            "/api/chat",
            json={"session_id": "session-grow", "message": "hi", "model": "claude-sonnet-4-5"},
        ) as r:
            list(r.iter_bytes())  # drain stream

        # Session was created and accumulated user + assistant turns.
        sess = app_module.SESSIONS["session-grow"]
        assert len(sess["messages"]) == 2
        assert sess["messages"][0]["role"] == "user"
