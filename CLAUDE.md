# CLAUDE.md

This file is for Claude Code (and other agents) working in this repo. Read it before making changes.

## Project Overview

Strauss is a portable framework for reusable agentic AI across multiple model providers. Bryan's personal site can run one profile on top of it, but the engine is meant to support many professional agent profiles with different knowledge bases and tools.

## Tech Stack

- Python 3.11+, FastAPI, uvicorn, Server-Sent Events
- `anthropic` SDK for Claude models
- `openai` SDK with configurable `base_url` for OpenAI proper *and* Moonshot Kimi K2.6
- `google-genai` SDK for Gemini models
- Vanilla HTML / CSS / JS frontend — no bundler, no build step
- pytest with `monkeypatch`-based provider injection

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env  # set ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, and/or MOONSHOT_API_KEY

.venv/bin/python -m pytest -v                                     # tests
.venv/bin/python -m uvicorn backend.app:app --reload --port 8001  # backend
.venv/bin/python -m http.server 8000 --directory web              # frontend
```

## Architecture (one-screen tour)

The agent loop in [`backend/agent.py`](backend/agent.py) mirrors the `run_conversation()` pattern from `Anthropic-course/001_tools_009.ipynb`. It iterates normalized events from a `LLMProvider` and calls `run_tool` for each `tool_use_complete`.

```
agent.run_conversation_stream(user_msg, session, provider, model, profile)
       │
       │  async for ev in provider.stream(...)
       ▼
   { text_delta, tool_use_complete, message_done, ... }   ← normalized Event
       │
       ▼
   run_tool(name, args, id, root=profile.kb_root) → ToolResult
       │
       ▼
   provider.append_tool_results(messages, results)
       │
       ▼  loop or done (bounded by MAX_TOOL_HOPS)
```

Providers under [`backend/providers/`](backend/providers/):

- `anthropic_provider.py` — uses `anthropic.AsyncAnthropic.messages.stream(...)`.
- `openai_compat_provider.py` — uses `openai.AsyncOpenAI` with configurable `base_url`. **One class** covers both OpenAI and Moonshot Kimi K2.6.
- `gemini_provider.py` — uses `google-genai` async streaming with manual function-call handling.

`MODEL_REGISTRY` in [`backend/config.py`](backend/config.py) maps `model_id` → `{provider, model, base_url, ...}`. `REGISTERED_PROVIDERS` in [`backend/app.py`](backend/app.py) gates which models the dropdown shows based on what's actually wired up.
Agent persona and KB root are loaded from [`profiles/`](profiles/) through [`backend/profiles.py`](backend/profiles.py), so the engine can be reused for another agent by adding a profile package instead of forking the loop.

## File Map

- [`backend/agent.py`](backend/agent.py) — the bounded tool-use loop
- [`backend/providers/base.py`](backend/providers/base.py) — `LLMProvider` Protocol + normalized `Event` shape
- [`backend/providers/anthropic_provider.py`](backend/providers/anthropic_provider.py) — Claude path
- [`backend/providers/openai_compat_provider.py`](backend/providers/openai_compat_provider.py) — OpenAI + Kimi path
- [`backend/providers/gemini_provider.py`](backend/providers/gemini_provider.py) — Gemini path
- [`backend/profiles.py`](backend/profiles.py) — profile loader for persona + KB root
- [`backend/tools.py`](backend/tools.py) — `SCHEMAS` (Anthropic shape) + `run_tool` dispatcher + `ToolResult`
- [`backend/kb_loader.py`](backend/kb_loader.py) — `_safe_resolve()` is the trust boundary; everything else uses it
- [`backend/app.py`](backend/app.py) — FastAPI: POST `/api/chat` (SSE), GET `/api/models`, GET `/api/health`
- [`backend/config.py`](backend/config.py) — env loading, `MODEL_REGISTRY`, limits
- `web/` — vanilla chat UI, palette/fonts borrowed from `bryanzane_v3`
- `profiles/` — reusable agent profiles; `profiles/strauss/` is the default
- `kb/` — content: `resume/`, `projects/`, `codebases/` (XMLs), `meta/`
- [`tests/conftest.py`](tests/conftest.py) — `use_mini_kb` autouse fixture monkeypatches `KB_ROOT` to `tests/fixtures/mini_kb/`

## Conventions

- **Tool schemas are authored in Anthropic shape.** When adding a tool, edit `SCHEMAS` and `run_tool` in [`backend/tools.py`](backend/tools.py). Provider adapters translate via `tool_translator.py`. Don't author the same tool twice.
- **Agent identity belongs in profiles, not providers.** Add/edit `profiles/<id>/profile.json` and `system.md` for persona, welcome copy, suggestions, and KB root.
- **Follow the agent practices checklist.** [`docs/agent_best_practices.md`](docs/agent_best_practices.md) captures the standing rules for API boundaries, model selection, prompts, tools, streaming, retrieval, evals, and portability.
- **All KB filesystem ops go through `_safe_resolve()`.** It rejects `..`, absolute paths, and symlink escapes. Never bypass it.
- **The provider mutates `messages` inside `stream()`.** After the API call completes, append the assistant turn to `messages` *before* yielding `tool_use_complete` events. Mirror this contract in any new provider — otherwise the next API call rejects with "tool_result without preceding tool_use."
- **Loop bound: `MAX_TOOL_HOPS = 8`.** A normal profile-specific question should rarely need more than 3 hops. The cap is a runaway-loop safety net.
- **No `Co-Authored-By: Claude` in commits.** Per repo owner's preference.

## Reading order for a new contributor / agent

1. [`README.md`](README.md) — what + how to run
2. [`history.md`](history.md) — why each decision was made (and what was rejected)
3. [`backend/agent.py`](backend/agent.py) — the loop (~50 lines)
4. [`backend/providers/base.py`](backend/providers/base.py) + [`anthropic_provider.py`](backend/providers/anthropic_provider.py) — the Protocol + an implementation

## Build phases (tracking)

- ✅ **Phase 0**: scaffold
- ✅ **Phase A**: `kb_loader.py` + `tools.py` + 32 unit tests
- ✅ **Phase B**: `AnthropicProvider` + provider-agnostic loop + 4 mocked-provider tests
- ✅ **Phase C**: FastAPI SSE + chat UI + 5 endpoint tests
- ✅ **Phase D**: `OpenAICompatProvider` + `tool_translator.py` + Kimi K2.6 / GPT-5 wiring
- ✅ **Profile split**: reusable engine + `profiles/strauss/` persona and KB root
- ⏳ **Phase E**: prompt caching / usage overlay across providers
- ⏳ **Phase F**: populate `kb/` (resume, quick_info, project pitches, meta) + smoke prompts
- ⏳ **Phase G**: production hardening (rate limits, daily token budget, logging)
