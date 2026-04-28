# Strauss

Strauss is an AI agent for getting me a job.

It answers recruiter questions from a local knowledge base of my resume, projects, codebases, and notes. The goal is simple: give the model exactly the context it needs, let it use tools, and make it say when it does not know something instead of inventing a perfect-sounding answer.

## Why I Made It

Agentic AI can fall apart when context gets messy. A larger context window does not solve that by itself; it can turn into a massive stack of papers where the important details get missed.

Strauss is built around context management. The agent profile, knowledge base, model providers, and tools are separated so the engine can be reused later for other agents, like a game advisor or project assistant.

## What It Does

- Runs a streaming chat UI with FastAPI and vanilla HTML/CSS/JS
- Supports Claude, OpenAI, Gemini, and Kimi through one provider interface
- Keeps API keys on the server, never in the browser
- Uses profile-specific prompts, KB roots, and tool allowlists
- Reads/searches a local knowledge base instead of relying on model memory
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
kb/         local knowledge base
web/        static chat UI
tests/      pytest suite
docs/       architecture notes and agent practices
```

## License

MIT
