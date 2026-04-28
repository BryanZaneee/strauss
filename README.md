# Strauss

Strauss is a portable framework for building agentic AI apps across different model providers.

The goal is simple: define an agent profile, give it a focused knowledge base and toolset, then run it through Claude, OpenAI, Gemini, or Kimi without rewriting the workflow each time.

## Why I Made It

Agentic AI can fall apart when context gets messy. A larger context window does not solve that by itself; it can turn into a massive stack of papers where the important details get missed.

Strauss is built around context management. Profiles, knowledge bases, model providers, and tools are separated so the same engine can be reused for different professional workflows, like a social media video manager, customer support bot, sales assistant, or internal operations agent.

I will use it personally on my site, but the framework is meant to move anywhere.

## What It Does

- Runs a streaming chat UI with FastAPI and vanilla HTML/CSS/JS
- Supports Claude, OpenAI, Gemini, and Kimi through one provider interface
- Keeps API keys on the server, never in the browser
- Uses profile-specific prompts, KB roots, and tool allowlists
- Reads/searches local knowledge bases instead of relying on model memory
- Keeps the core agent loop small enough to understand and change

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
```

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
kb/         local knowledge bases
web/        static chat UI
tests/      pytest suite
docs/       architecture notes and agent practices
```

## License

MIT
