# profiles-advanced/ — multi-channel, multi-tenant agent profiles

This folder is reserved for **tier-2** EasyAgent profiles that go beyond the in-website chat widget.

## What lives here

Tier-2 profiles need infrastructure the current single-process FastAPI engine does not run:

- **Channel adapters** — WhatsApp Business, Instagram DMs, Gmail, Google Business reviews. Each is its own webhook, auth flow, and outbound sender.
- **Per-tenant config** — many businesses sharing one EasyAgent deployment, each with their own KB, voice, hours, and rate-limit budget.
- **Persistent state** — durable conversation history that survives a process restart and lets a human teammate take over a thread mid-conversation.
- **Routing logic** — escalation rules, language detection, business-hours-aware handoffs, "test mode" so a profile can be demoed without affecting real customers.
- **Heartbeat / monitoring** — scheduled checks that alert when the response queue backs up.

## Why a separate folder

The profile loader (`backend/profiles.py`) does a flat one-level lookup under `PROFILE_ROOT` (default `./profiles/`). Anything in `profiles-advanced/` is invisible to it — by design. Keeping tier-2 work out of `profiles/` makes the boundary explicit:

- `profiles/` — runs in the current engine, in-widget only.
- `profiles-advanced/` — needs a different runtime (channel adapters, queues, multi-tenancy). Likely lives in its own deployment, possibly its own repo.

## Where to start

Until the tier-2 runtime exists, this folder is empty. The in-widget reference for a customer-service agent is at [`profiles/customer-service/`](../profiles/customer-service/) — read its `TEMPLATE.md` to see the profile contract that tier-2 profiles will extend.
