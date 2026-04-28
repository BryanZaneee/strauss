# Strauss

Strauss is a portable framework for building reusable agentic AI apps across different model providers.

The goal is simple: define an agent profile, give it a focused local knowledge base and toolset, then run it through Claude, OpenAI, Gemini, Kimi, or DeepSeek without rewriting the workflow each time.

## Why I Made It

Strauss is built around portability and usability. Profiles, knowledge bases, model providers, and tools are separated so the same engine can be reused for different professional workflows, like a social media video manager, customer support bot, sales assistant, internal operations agent, or personal site agent.

I will use it personally on my site, but the framework is meant to move anywhere.

## What It Does

- Runs a streaming chat UI with FastAPI and vanilla HTML/CSS/JS
- Supports Claude, OpenAI, Gemini, Kimi, and DeepSeek through one provider interface
- Keeps API keys on the server, never in the browser
- Uses profile-specific prompts, KB roots, and tool allowlists
- Reads/searches local knowledge bases instead of relying on model memory
- Preserves provider-specific protocol details, such as DeepSeek thinking-mode `reasoning_content`, inside the server only
- Adds production guardrails: per-IP chat rate limits, daily token budget tracking, active-session caps, and structured JSON logs
- Keeps the core agent loop small enough to understand and change

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

## Tests

```bash
.venv/bin/python -m pytest
```

## Shape

```text
backend/    agent loop, API, providers, tools, KB access
profiles/   agent personas and tool allowlists
kb/         local/private knowledge bases (ignored by git)
web/        static chat UI
tests/      pytest suite
docs/       architecture notes and agent practices
```

## License

MIT
