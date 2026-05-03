"""Agent profile loading.

Profiles keep persona and knowledge-base choices out of the reusable EasyAgent
engine. The bundled `strauss` example profile lives under profiles/strauss/, but
the engine can run another profile by loading a different profile.json + system
prompt.

The `mcp_servers` field on a profile is parsed and stored, but no MCP client is
wired up yet — that integration is a follow-up task. See backend/agent.py for
the planned shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import DEFAULT_PROFILE, KB_ROOT, PROFILE_ROOT

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_TOOLS: tuple[str, ...] = (
    "list_kb",
    "read_file",
    "search_kb",
    "get_resume_summary",
    "get_project_context",
    "web_search",
)


@dataclass(frozen=True)
class AgentProfile:
    id: str
    label: str
    description: str
    kb_root: Path
    system_prompt: str
    welcome: str = ""
    suggestions: tuple[str, ...] = ()
    tools: tuple[str, ...] = DEFAULT_PROFILE_TOOLS
    mcp_servers: tuple[dict, ...] = ()


def _project_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback.resolve()
    p = Path(value)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def load_profile(profile_id: str | None = None) -> AgentProfile:
    """Load a profile by id. Falls back to the legacy KB root if no file exists."""
    pid = profile_id or DEFAULT_PROFILE
    profile_dir = (PROFILE_ROOT / pid).resolve()
    cfg_path = profile_dir / "profile.json"

    if not cfg_path.exists():
        if profile_id is not None and pid != DEFAULT_PROFILE:
            raise FileNotFoundError(cfg_path)
        return AgentProfile(
            id=pid,
            label=pid.title(),
            description="Default profile",
            kb_root=KB_ROOT,
            system_prompt=(
                "You are an AI agent. Answer using the available knowledge-base tools. "
                "If the knowledge base does not contain a fact, say so."
            ),
        )

    cfg: dict[str, Any] = json.loads(cfg_path.read_text(encoding="utf-8"))
    system_path = _project_path(cfg.get("system_prompt_path"), profile_dir / "system.md")
    system_prompt = system_path.read_text(encoding="utf-8").strip()

    return AgentProfile(
        id=cfg.get("id", pid),
        label=cfg.get("label", pid.title()),
        description=cfg.get("description", ""),
        kb_root=_project_path(cfg.get("kb_root"), KB_ROOT),
        system_prompt=system_prompt,
        welcome=cfg.get("welcome", ""),
        suggestions=tuple(cfg.get("suggestions", ())),
        tools=tuple(cfg.get("tools", DEFAULT_PROFILE_TOOLS)),
        mcp_servers=tuple(cfg.get("mcp_servers", ())),
    )
