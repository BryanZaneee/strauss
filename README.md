# EasyAgent

EasyAgent is a portable framework for building reusable agentic AI apps across different model providers.

The goal is simple: define an agent profile, give it a focused local knowledge base and toolset, then run it through Claude, OpenAI, Gemini, Kimi, or DeepSeek without rewriting the workflow each time.

`profiles/strauss/` is the bundled showcase profile (the personal-site agent for [bryanzane.com/strauss](https://bryanzane.com/strauss/)). `profiles/customer-service/` is a second bundled profile — a tier-1 in-widget customer-service agent for a fictional small business (Lantern Lane Coffee) that ships with realistic KB content and a `TEMPLATE.md` describing what to swap to adapt it for a real business. Use either as a template for your own profiles.

The web chat widget includes an **agent switcher** in the controls strip — pick a profile from the dropdown to see how its persona, tools, MCP servers, and per-turn token usage differ. Switching resets the conversation.

A sibling top-level folder, [`profiles-advanced/`](./profiles-advanced/), is reserved for tier-2 multi-channel/multi-tenant agents (WhatsApp, Instagram, Gmail, Google Business). It is intentionally outside `profiles/` so the in-widget loader does not pick it up — see its README for details.

## Why I Made It

EasyAgent is built around portability and usability. Profiles, knowledge bases, model providers, and tools are separated so the same engine can be reused for different professional workflows, like a social media video manager, customer support bot, sales assistant, internal operations agent, or personal site agent.

I use it personally on my site (Strauss profile), but the framework is meant to move anywhere.

## What It Does

- Runs a streaming chat UI with FastAPI and vanilla HTML/CSS/JS
- Supports Claude, OpenAI, Gemini, Kimi, and DeepSeek through one provider interface
- Keeps API keys on the server, never in the browser
- Uses profile-specific prompts, KB roots, and tool allowlists
- Reads/searches local knowledge bases instead of relying on model memory
- Preserves provider-specific protocol details, such as DeepSeek thinking-mode `reasoning_content`, inside the server only
- Adds production guardrails: per-IP chat rate limits, daily token budget tracking, active-session caps, and structured JSON logs
- Keeps the core agent loop small enough to understand and change

## Adding Agent Profiles

Adding a new agent profile is straightforward and does not require git branches. The framework is designed to swap agents easily by adding a new folder under `profiles/`:

1. **Create a directory:** `profiles/my-agent/`
2. **Add `profile.json`:** Define the agent's identity, KB root, and tools:
   ```json
   {
     "id": "my-agent",
     "label": "My Custom Agent",
     "kb_root": "kb/my-agent-kb",
     "system_prompt_path": "profiles/my-agent/system.md",
     "tools": ["list_kb", "read_file", "search_kb"]
   }
   ```
3. **Add `system.md`:** Write the system prompt defining the agent's persona.

To use the new profile, change `DEFAULT_PROFILE=my-agent` in your `.env` file and restart the server, or pick it from the agent switcher dropdown in the web UI without changing the default.

`profile.json` also accepts an `mcp_servers` array — each entry mirrors the standard MCP stdio config (`name`, `command`, `args`, `env`). The schema is parsed and shown in the agent-info panel today; full MCP client integration is a follow-up task.

## Privacy Boundary

This public repo intentionally does **not** include my personal knowledge base, resume files, private codebase XML dumps, API keys, or deployment secrets found for my personal site.

The `kb/` directory is ignored by git. To run a real profile, create your own local KB that matches the profile's `kb_root`:

```text
kb/
  resume/
  projects/
  codebases/
  meta/
```

Tests use `tests/fixtures/mini_kb/`, so the framework can be developed and verified without committing private data.

## Run It

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Set one or more API keys in `.env`:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
MOONSHOT_API_KEY=...
DEEPSEEK_API_KEY=...
```

All keys stay server-side. Browser clients call the FastAPI server over SSE; they never receive provider credentials.

Start the backend:

```bash
.venv/bin/python -m uvicorn backend.app:app --reload --port 8001
```

Start the frontend:

```bash
.venv/bin/python -m http.server 8000 --directory web
```

Open `http://localhost:8000`.


## License

MIT
