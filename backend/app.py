"""FastAPI app: POST /api/chat (SSE), GET /api/health, GET /api/models, GET /api/budget.

Sessions live in-memory; stale ones are swept lazily at the top of each chat request.
Provider lookup goes through `get_provider()` so tests can monkeypatch it.

Abuse protection: per-IP slowapi rate limit on /api/chat, daily token budget enforced
before each request and recorded after, hard cap on concurrent sessions, structured
JSON logs of every chat completion.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.agent import run_conversation_stream
from backend.budget import TOKEN_BUDGET
from backend.config import (
    ALLOWED_ORIGINS,
    DEFAULT_MODEL,
    DEFAULT_PROFILE,
    LOG_LEVEL,
    MAX_ACTIVE_SESSIONS,
    MAX_TURNS_PER_SESSION,
    MODEL_REGISTRY,
    RATE_LIMIT_CHAT,
    RATE_LIMIT_ENABLED,
    SESSION_TTL,
    available_models,
)
from backend.logging_config import configure_logging
from backend.providers.anthropic_provider import AnthropicProvider
from backend.providers.base import LLMProvider
from backend.profiles import AgentProfile, load_profile

configure_logging(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("strauss")

# Providers wired up behind the shared engine.
REGISTERED_PROVIDERS: set[str] = {"anthropic", "openai_compat", "gemini"}


SESSIONS: dict[str, dict] = {}


def _cleanup_stale_sessions() -> None:
    now = time.time()
    stale = [sid for sid, s in SESSIONS.items() if now - s["last_seen"] > SESSION_TTL]
    for sid in stale:
        del SESSIONS[sid]


def get_provider(model_id: str) -> LLMProvider:
    """Resolve model_id → provider instance. Tests monkeypatch this."""
    cfg = MODEL_REGISTRY[model_id]
    if cfg["provider"] == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(status_code=400, detail="missing ANTHROPIC_API_KEY")
        return AnthropicProvider()
    if cfg["provider"] == "openai_compat":
        api_key_env = cfg["api_key_env"]
        if not os.environ.get(api_key_env):
            raise HTTPException(status_code=400, detail=f"missing {api_key_env}")
        from backend.providers.openai_compat_provider import OpenAICompatProvider

        return OpenAICompatProvider(
            api_key_env=api_key_env,
            base_url=cfg.get("base_url"),
            token_param=cfg.get("token_param", "max_completion_tokens"),
            stream_options=cfg.get("stream_options", True),
            include_tool_result_name=bool(cfg.get("base_url")),
            extra_body=cfg.get("extra_body"),
            reasoning_effort=cfg.get("reasoning_effort"),
            preserve_reasoning_content=bool(cfg.get("preserve_reasoning_content")),
        )
    if cfg["provider"] == "gemini":
        api_key_env = cfg["api_key_env"]
        if not os.environ.get(api_key_env):
            raise HTTPException(status_code=400, detail=f"missing {api_key_env}")
        from backend.providers.gemini_provider import GeminiProvider

        return GeminiProvider(api_key_env=api_key_env)
    raise HTTPException(
        status_code=400, detail=f"provider not implemented yet: {cfg['provider']}"
    )


def get_profile(profile_id: str = DEFAULT_PROFILE) -> AgentProfile:
    """Resolve profile id → profile. Tests can monkeypatch this if needed."""
    try:
        return load_profile(profile_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=f"profile not found: {profile_id}") from e


app = FastAPI(title="Strauss", version="0.1.0")

limiter = Limiter(key_func=get_remote_address, enabled=RATE_LIMIT_ENABLED)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|\[::1\]):\d+$",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=4000)
    model: str = Field(..., min_length=1)
    profile: str = Field(default=DEFAULT_PROFILE, min_length=1, max_length=64)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "sessions": len(SESSIONS)}


@app.get("/api/budget")
async def budget() -> dict:
    return TOKEN_BUDGET.stats()


@app.get("/api/models")
async def list_models() -> dict:
    models = [m for m in available_models() if m["provider"] in REGISTERED_PROVIDERS]
    default = DEFAULT_MODEL if any(m["id"] == DEFAULT_MODEL for m in models) else (
        models[0]["id"] if models else None
    )
    return {"default": default, "models": models}


@app.get("/api/profile")
async def profile(profile_id: str = DEFAULT_PROFILE) -> dict:
    p = get_profile(profile_id)
    return {
        "id": p.id,
        "label": p.label,
        "description": p.description,
        "welcome": p.welcome,
        "suggestions": list(p.suggestions),
        "tools": list(p.tools),
    }


@app.post("/api/chat")
@limiter.limit(RATE_LIMIT_CHAT)
async def chat(request: Request, req: ChatRequest) -> StreamingResponse:
    if req.model not in MODEL_REGISTRY:
        raise HTTPException(status_code=400, detail=f"unknown model: {req.model}")
    if MODEL_REGISTRY[req.model]["provider"] not in REGISTERED_PROVIDERS:
        raise HTTPException(
            status_code=400, detail=f"provider not implemented for: {req.model}"
        )

    if not TOKEN_BUDGET.has_capacity():
        raise HTTPException(status_code=503, detail="daily token budget exhausted")

    _cleanup_stale_sessions()

    if req.session_id not in SESSIONS and len(SESSIONS) >= MAX_ACTIVE_SESSIONS:
        raise HTTPException(status_code=503, detail="server at session capacity")

    provider = get_provider(req.model)
    cfg = MODEL_REGISTRY[req.model]
    profile = get_profile(req.profile)

    session = SESSIONS.setdefault(
        req.session_id,
        {
            "messages": [],
            "last_seen": time.time(),
            "provider": cfg["provider"],
            "profile": profile.id,
        },
    )
    session["last_seen"] = time.time()
    if session.get("provider") != cfg["provider"] or session.get("profile") != profile.id:
        session["messages"] = []
        session["provider"] = cfg["provider"]
        session["profile"] = profile.id

    if len(session["messages"]) >= MAX_TURNS_PER_SESSION * 2:
        raise HTTPException(status_code=429, detail="session message cap reached")

    ip = request.client.host if request.client else "unknown"
    instrumented = _instrument(
        run_conversation_stream(req.message, session, provider, cfg["model"], profile),
        ip=ip,
        session_id=req.session_id,
        model_id=req.model,
        profile_id=profile.id,
    )

    return StreamingResponse(
        _sse_format(instrumented),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _instrument(
    events: AsyncIterator[dict],
    *,
    ip: str,
    session_id: str,
    model_id: str,
    profile_id: str,
) -> AsyncIterator[dict]:
    """Pass events through; tally usage for the budget and emit one structured log."""
    started = time.time()
    tokens_in = 0
    tokens_out = 0
    cache_read = 0
    tool_hops = 0
    status = "ok"
    try:
        async for ev in events:
            kind = ev.get("event")
            if kind == "usage":
                tokens_in += int(ev.get("input_tokens") or 0)
                tokens_out += int(ev.get("output_tokens") or 0)
                cache_read += int(ev.get("cache_read_input_tokens") or 0)
            elif kind == "tool_use_start":
                tool_hops += 1
            elif kind == "error":
                status = "error"
            yield ev
    except Exception:
        status = "exception"
        raise
    finally:
        total = tokens_in + tokens_out
        TOKEN_BUDGET.record(total)
        log.info(
            "chat_complete",
            extra={
                "ip": ip,
                "session": session_id[:8],
                "model": model_id,
                "profile": profile_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tokens_total": total,
                "cache_read": cache_read,
                "tool_hops": tool_hops,
                "duration_ms": int((time.time() - started) * 1000),
                "status": status,
            },
        )


async def _sse_format(events: AsyncIterator[dict]) -> AsyncIterator[bytes]:
    """Convert {event, ...} dicts to SSE wire frames."""
    try:
        async for ev in events:
            ev_type = ev.pop("event")
            payload = json.dumps(ev, ensure_ascii=False)
            yield f"event: {ev_type}\ndata: {payload}\n\n".encode("utf-8")
    except Exception as e:
        payload = json.dumps({"message": f"{type(e).__name__}: {e}"})
        yield f"event: error\ndata: {payload}\n\n".encode("utf-8")
