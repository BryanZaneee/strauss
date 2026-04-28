# Agent Best Practices

This project treats each agent as a profile on top of a reusable engine. Keep these practices in place as the engine grows.

## API Boundary

- Client apps never call model providers directly. Web, iOS, or any future client sends user input to our FastAPI server.
- Provider API keys stay server-side in environment variables.
- The server owns model selection, tool execution, session state, and budget/rate controls.

## Model Selection

- Use a balanced model for the default user-facing chat path.
- Use higher-intelligence models for complex multi-step planning, deep codebase analysis, and evaluation.
- Use faster/cheaper models for lightweight routing, dataset generation, and high-volume background tasks.
- Prefer per-task routing over assuming one model is best for every workflow.

## Prompting

- Put stable identity and behavior in profile system prompts, not in provider code.
- Make prompts clear, direct, and specific. Lead with the task, then constraints.
- Structure long prompts with explicit sections such as XML-style tags.
- Use examples only when they clarify formatting, edge cases, or tone.
- Keep factual agents low-creativity by default; knowledge-base answers should be grounded and repeatable.

## Conversation State

- Providers are stateless. The server must preserve the full message history required by each provider.
- Assistant turns containing tool calls must be recorded before tool results are appended.
- Tool results must preserve the provider's required pairing identifier.
- Switching provider families or profiles resets the session history because message formats and tool surfaces may differ.

## Tools

- Tools should be named clearly and return compact, high-signal data.
- Tool descriptions should explain what the tool does, when to use it, what it returns, and limitations.
- Validate inputs at the tool boundary and return useful errors that the model can recover from.
- Keep tools generic where possible so agents can compose them flexibly.
- Profiles declare their allowed tools. The engine must not expose every tool to every profile by default.
- Use bounded tool loops. Strauss uses `MAX_TOOL_HOPS` as a runaway-loop guard.

## Streaming

- Stream responses to clients for good UX.
- Surface tool activity events so users know when the agent is gathering evidence.
- Preserve final assembled assistant messages for future turns, even when text arrived in chunks.

## Retrieval

- Start with simple KB listing, reading, and search for a small curated KB.
- As the KB grows, add chunking and retrieval indexes rather than stuffing whole documents into prompts.
- Prefer hybrid retrieval for larger corpora: semantic search plus lexical/BM25 search, then rerank when quality matters.
- Keep raw source documents available for citation or verification.

## Evaluation

- Do not rely on one or two manual prompts before production changes.
- Maintain smoke prompts per profile for core workflows.
- Add small eval datasets before changing system prompts, tool descriptions, or retrieval behavior.
- Use code-based graders for structured output and model/human graders for answer quality.

## Portability

- New agents should be created as `profiles/<id>/profile.json` plus `system.md`, with their own KB root and tool allowlist.
- Add new tools once in the engine tool registry, then opt profiles into them deliberately.
- Keep clients thin. Web, iOS, and other apps should consume the same HTTP/SSE API.
