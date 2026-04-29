# EasyAgent — Architectural History

A running log of decisions that future agents (or future-me) can't recover from reading
the source code alone. New decisions go at the top, dated. Each entry should answer
**why** the choice was made and **what was rejected**.

> **Naming note:** the framework is **EasyAgent**. The personal-site agent profile is **Strauss**, bundled under `profiles/strauss/` as the showcase example. Older entries below predate this rename and refer to the framework as "Strauss" — read them in that historical context.

---

## 2026-04-28 — Public repo hygiene and production guardrails

### Decision: Personal KB content stays out of the public repository
**Choice:** `kb/` is treated as local/private runtime data and ignored by git. The public repo documents the expected KB shape, but does not publish personal resume files, private project notes, or codebase XML dumps.

**Why:** Strauss is a reusable framework. The engine, profile loader, tools, provider adapters, tests, and docs are the reusable surface; Bryan's personal knowledge base is deployment data. Publishing the KB would mix private content into the framework's source history and make future profile reuse harder to reason about.

**Rejected:** Seeding the public repo with Bryan-specific KB markdown or XML dumps. That would make the demo feel richer on GitHub, but it creates an avoidable privacy and maintenance risk. Tests already use `tests/fixtures/mini_kb/`, which is the right public fixture boundary.

### Decision: Public project aliases can stay in code; private content stays in KB
**Choice:** `PROJECT_ALIASES` may include public-facing project names, domains, and common spellings so the default Strauss profile can resolve natural project questions to local KB slugs. The files those aliases point at still live under ignored `kb/` runtime data.

**Why:** Aliases are routing hints, not private source material. Keeping them in code improves tool reliability for site visitors without exposing resumes, codebase dumps, or private notes.

**Rejected:** Moving every alias into private KB metadata. That would keep the public framework slightly more generic, but it would also make a fresh deploy easier to misconfigure and weaken tests around the project lookup path.

### Decision: Production limits live in the app, not in the browser
**Choice:** The FastAPI app enforces a per-IP `/api/chat` rate limit, a process-local daily token budget with `/api/budget` introspection, an active-session cap, and structured JSON completion logs. The browser remains a thin SSE client.

**Why:** Abuse controls and usage accounting need to sit beside the provider keys and model calls. Keeping them server-side lets the same engine support a portfolio deployment today and other profiles later without asking every client to reimplement cost and safety rules.

**Rejected:** Client-only throttling. It is useful as polish, but it is not a security boundary and cannot protect server-side API keys or model spend.

---

## 2026-04-28 — DeepSeek thinking mode through OpenAICompatProvider

### Decision: DeepSeek slots in by extending OpenAICompatProvider, not adding a new provider class
**Choice:** `OpenAICompatProvider` gains three optional kwargs — `extra_body`, `reasoning_effort`, `preserve_reasoning_content` — plus a reasoning-accumulation branch in the streaming loop and a DeepSeek-specific KV-cache field in `_norm_usage`. Each piece is registry-driven and defaults to a no-op for OpenAI/Kimi/GPT-5.

**Why:** DeepSeek's `/chat/completions` is OpenAI-shape compatible for the parts that matter (streaming, function calls, role: tool results). The deltas it emits in thinking mode add `reasoning_content` chunks the standard OpenAI client surfaces via `getattr(delta, "reasoning_content", None)`, so we don't need a separate transport. Forking a `DeepSeekProvider` would have duplicated ~200 lines of streaming/tool-call accumulation logic for one new field.

**Rejected:** A dedicated `DeepSeekProvider` class. Tempting because thinking mode's quirks (round-tripping `reasoning_content` on tool-call turns or DeepSeek 400s the next request) feel provider-specific. But those quirks are gated by per-model registry flags (`preserve_reasoning_content`), not by the provider class — so the gating belongs at the registry level. One provider, multiple capability flags, beats N providers each repeating the same OpenAI-shape boilerplate.

### Decision: Reasoning content is preserved server-side on every thinking-mode turn, never crossed to the SSE stream
**Choice:** When a DeepSeek delta contains `reasoning_content`, the provider accumulates it into a per-call `reasoning_parts` list and `continue`s — it is *not* yielded as `text_delta`. When the assistant turn finishes, if (a) the registry opted in via `preserve_reasoning_content` and (b) reasoning was actually streamed, the concatenation is attached to the assistant message as `reasoning_content` so it round-trips to DeepSeek on the next request.

**Why:** DeepSeek requires `reasoning_content` to come back on **every** thinking-mode assistant turn that streamed reasoning, not just tool-call turns. The error surface confirms this empirically — a follow-up turn within the same session 400s with `"The reasoning_content in the thinking mode must be passed back to the API"` if any prior assistant turn (tool-call or plain-text) had reasoning streamed but didn't preserve it. At the same time, raw chain-of-thought is provider-protocol state, not user-facing answer text — surfacing it to the browser would leak working-out the model assumes is private. The split keeps the engine correct for multi-turn conversations *and* keeps the UI clean.

**Rejected (initially tried):** Gating preservation on `completed_tool_calls and reasoning_parts`. Tighter and seemed safer per the original DeepSeek docs framing ("when a thinking-mode response performs tool calls"), but multi-turn conversations 400 the moment a plain-text turn's reasoning is dropped. The smoke test that surfaced this was a recruiter-style chat: turn 1 "Tell me about Shuttrr" succeeded with a tool call → turn 2 "And what about Physiq?" 400'd because turn 1's *final answer* turn (plain-text after the tool result) had streamed reasoning that wasn't echoed back.

**Rejected:** Streaming reasoning to the browser as a separate `reasoning_delta` event so a "thinking…" UI could render it live. Cute, but it bakes provider-protocol leakage into the public API and would have to be sanitized at every UI layer. A `tool_use_start` event already exists for the "the agent is doing something" affordance — that's enough.

### Decision: Default sampling params are not sent for DeepSeek thinking mode
**Choice:** No `temperature`, `top_p`, `presence_penalty`, or `frequency_penalty` are passed for the `deepseek-v4-flash` model.

**Why:** DeepSeek docs state those parameters have no effect in thinking mode. Sending them is harmless wire bloat at best, confusing-to-debug at worst. The provider doesn't hardcode these today (they're only added if a caller passes them), so this is a documentation rule for future contributors rather than a code change.

---

## 2026-04-28 — Multi-provider wiring + reusable profiles

### Decision: Agent profile is separate from the engine
**Choice:** persona, welcome copy, suggestions, system prompt, and KB root now live under `profiles/<id>/`. The reusable engine receives an `AgentProfile` and passes `profile.kb_root` into tool dispatch.

**Why:** Strauss should be a reusable agent framework, not a one-off bot. A future social media video manager, customer support bot, or internal operations agent can add a profile package and point at a different KB without forking the provider loop.

### Decision: Profiles explicitly allow tools
**Choice:** `profile.json` declares the tools available to that agent. Providers translate only those schemas, and the tool dispatcher rejects calls outside the active profile's allowlist.

**Why:** exposing every future tool to every future agent would make behavior harder to evaluate and easier to misuse. Tool allowlists keep each profile's capabilities intentional while preserving the same engine.

### Decision: OpenAI and Kimi share one OpenAI-compatible provider
**Choice:** `OpenAICompatProvider` handles OpenAI and Moonshot/Kimi chat-completions streaming. Tool schemas are still authored once in Anthropic shape, then translated to OpenAI function-tool shape.

**Why:** Kimi documents OpenAI-compatible tool calls and `role: tool` results, including streamed argument accumulation by `tool_calls[index]`. Keeping OpenAI and Kimi in one provider makes their shared message shape explicit while still allowing per-model knobs like `base_url`, token parameter, and stream-usage support.

### Decision: Gemini gets a native provider
**Choice:** `GeminiProvider` uses Google's `google-genai` SDK, `GenerateContentConfig`, and manual function-call handling with automatic function calling disabled.

**Why:** Gemini's history and function response shape is different enough from OpenAI that pretending it is OpenAI-compatible would make the engine brittle. The provider preserves Gemini `Content`/`Part` history and returns function responses with the model's call id.

## 2026-04-26 — Phase 0/A/B foundation

### Decision: Two native SDKs + thin protocol layer (not LiteLLM, not OpenRouter)
**Choice:** `anthropic` SDK for Claude + `openai` SDK with configurable `base_url` for OpenAI/Moonshot. A small `LLMProvider` Protocol normalizes the wire formats into a common `Event` shape that the agent loop consumes.

**Rejected:**
- **LiteLLM** — would have collapsed the two providers into one `litellm.acompletion(...)` call. Mature, well-maintained, supports 100+ providers. Rejected because it hides the protocol differences entirely (you'd never see how Moonshot's tool_calls accumulate from JSON fragments vs Anthropic's typed events). Building this for educational value, so seeing the differences IS the value.
- **OpenRouter as a gateway** — single OpenAI-compatible endpoint that routes to all providers. Rejected for the same reason + adds a paid middleman + you lose Anthropic-native `cache_control` (OR converts to OpenAI shape).

If maintaining two providers ever becomes painful, swap both for a single `LiteLLMProvider` behind the same `LLMProvider` interface. ~1 day of work.

### Decision: Tool schemas authored in Anthropic shape, translated to OpenAI shape
**Choice:** [SCHEMAS](backend/tools.py) live in Anthropic's `{name, description, input_schema}` form. A trivial `tool_translator.to_openai()` (Phase D) emits the OpenAI/Moonshot `{type: "function", function: {parameters: ...}}` form on demand.

**Why:** the user's Anthropic-course notebooks (`001_tools_009.ipynb`) use `input_schema` natively. Authoring in that shape keeps a 1-to-1 correspondence with what was already studied. The JSON Schema body is identical between the two formats — only the wrapper differs — so translation is mechanical.

### Decision: Hybrid tool design (3 generic + 2 specialized), not all-generic
**Choice:** `list_kb`, `read_file`, `search_kb` cover the long tail; `get_resume_summary`, `get_project_context` are specialized shortcuts.

**Why:** the specialized tools signal to the model "this is the canonical answer for resume/project questions, don't go fishing." Without them, models tend to do 3-4 unnecessary `search_kb` → `read_file` round trips for canonical questions. Specialized tools earn their keep when the model's default behavior is wasteful.

### Decision: KB layering — four tiers, only two are tool-result layers
```
manifest (always loaded, cached system block)        ← navigable index
quick_info.md (always loaded, cached system block)   ← per-codebase technical cheat sheet
project pitch summaries (tool result)                ← why-it-matters narrative per project
raw repomix XMLs (tool result with line-slicing)     ← actual source for "show me the code"
```

**Why:** putting per-codebase summaries directly in the system prompt as a third cached block means 80%+ of "tell me about Bryan's projects" questions need zero tool calls. Cache reads cost 10% of input on Anthropic; auto-cache covers it on OpenAI/Moonshot. A model that has the cheat sheet in its context will naturally cite it instead of thrashing through XML.

### Decision: Provider mutates `messages` list inside `stream()`
**Choice:** [`AnthropicProvider.stream`](backend/providers/anthropic_provider.py) appends the assistant turn to the messages list internally, *before* yielding `tool_use_complete` events. The agent.py loop only mutates messages via `provider.append_tool_results()`.

**Why:** the assistant turn (containing tool_use blocks) MUST land in the message log before the next turn's tool_result blocks. Otherwise Anthropic's API rejects the next call with "tool_result without preceding tool_use." Putting this side effect inside the provider means there's only one ordering invariant to remember: provider writes assistant, loop writes user(tool_results). Clean, predictable.

**Trade-off:** the test `FakeProvider` has to mirror this contract too (it appends a placeholder assistant turn). Documented in [tests/test_agent_loop.py](tests/test_agent_loop.py).

### Decision: Mid-conversation model switch resets the session
**Choice:** When the user switches models in the dropdown, the frontend treats it as starting a fresh conversation (new `session_id`).

**Why:** `session["messages"]` is provider-specific (Anthropic uses content blocks; OpenAI uses `tool_calls` arrays + `role:"tool"` results). Switching mid-conversation would feed Anthropic-shaped messages into Kimi K2 (or vice versa) and the model wouldn't understand them.

**Rejected:** a normalized intermediate message-log shape with translation hooks. More code, more failure modes, and matches how ChatGPT/Claude/Cursor behave when you switch models. Defer to v2 if there's a real demand for "show recruiter both answers" UX.

### Decision: `MAX_TOOL_HOPS = 8` as a runaway-loop safety net
**Choice:** the agent loop is bounded by [`MAX_TOOL_HOPS`](backend/config.py).

**Why:** a normal profile-specific question should rarely need more than ~3 hops (one specialized tool call + at most one source read). 8 is generous for legitimate use, tight enough to catch a model that's stuck in a tool-thrashing loop. Loop terminates with an explicit `error` event so the frontend can surface it cleanly.

### Decision: In-memory sessions, swept lazily per request
**Choice:** `SESSIONS: dict[str, dict]` lives in process memory. Stale sessions (idle > `SESSION_TTL`) are pruned at the top of each `/api/chat` call.

**Why:** v1 is single-worker. Lazy sweeping avoids a background `asyncio.create_task` and the testing complications it creates with `TestClient` (which doesn't always run startup hooks the way uvicorn does). When a v2 multi-worker setup is needed, this graduates to sqlite or Redis.

**Rejected:** persistent sessions, cross-visit memory. Privacy implications are non-trivial and the use case doesn't demand it yet.

### Decision: Vanilla static frontend (no React, no Vite, no build step)
**Choice:** [`web/index.html`](web/index.html) + `styles.css` + `app.js`, served as static files.

**Why:** matches `bryanzane_v3`'s existing deployment philosophy (CDN Tailwind + GSAP + Formspree + zero build). The chat UI is small (~350 lines total) — a framework would add more weight than it saves. Same VPS, same nginx, same auto-deploy webhook flow.

### Decision: Streaming-first AnthropicProvider (skipping the non-streaming step)
**Choice:** `AnthropicProvider.stream()` is the only entry point. There is no non-streaming variant.

**Why:** the original phased plan (Phase B non-streaming, then Phase C streaming) would have required rewriting the provider's core method between phases. Going straight to streaming costs no clarity — the loop's `stop_reason` branching lives in `agent.py`, not the provider, so it's still visible.

### Decision: Provider DI via module-level factory + `monkeypatch`, not FastAPI `Depends()`
**Choice:** [`backend/app.py`](backend/app.py) exposes a `get_provider(model_id)` function. Tests `monkeypatch.setattr("backend.app.get_provider", ...)` to inject a `FakeProvider`.

**Why:** matches the existing `monkeypatch` pattern in [tests/conftest.py](tests/conftest.py) for `KB_ROOT`. One fewer FastAPI concept to learn while building. `Depends()` + `app.dependency_overrides` is the more "FastAPI-blessed" pattern and is worth knowing, but we don't need its features (composable dependencies, request-scoped caching) here.

### Decision: `MODEL_REGISTRY` declares all 6 models from day one; `REGISTERED_PROVIDERS` gates which are visible
**Choice:** [`config.py`](backend/config.py) declares Anthropic + OpenAI + Moonshot model entries simultaneously. A `REGISTERED_PROVIDERS = {"anthropic"}` constant in `app.py` filters `/api/models` to only return models whose provider is implemented. Phase D adds `"openai_compat"` to the set.

**Why:** keeps the registry stable across phases (no rework when adding providers). The frontend dropdown only ever sees what works. Footgun avoided: a Moonshot key set with no `OpenAICompatProvider` available wouldn't surface as a broken model in the dropdown.
