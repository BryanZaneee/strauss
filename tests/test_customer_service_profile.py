"""Tests for the bundled customer-service profile and the /api/profiles endpoint."""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from backend.kb_loader import search_kb
from backend.profiles import load_profile


def test_customer_service_profile_loads():
    p = load_profile("customer-service")
    assert p.id == "customer-service"
    assert p.label == "Customer Service"
    assert p.tools == ("list_kb", "read_file", "search_kb")
    assert p.mcp_servers == ()
    assert "Lantern Lane" in p.welcome
    assert len(p.suggestions) == 3


def test_customer_service_kb_root_resolves_to_profile_dir():
    p = load_profile("customer-service")
    assert p.kb_root.parts[-2:] == ("customer-service", "kb")
    assert p.kb_root.exists()
    assert p.kb_root.is_dir()


def test_customer_service_system_prompt_loaded():
    p = load_profile("customer-service")
    assert p.system_prompt
    assert "Lantern Lane" in p.system_prompt
    assert "I don't have that info" in p.system_prompt


def test_customer_service_kb_searchable():
    p = load_profile("customer-service")
    # Pass root= explicitly to bypass the use_mini_kb autouse monkeypatch.
    results = search_kb("hours", root=p.kb_root, max_results=5)
    assert results, "search_kb should return at least one match for 'hours'"
    paths = [r["path"] for r in results]
    assert any(path.endswith("hours.md") for path in paths), paths


def test_mcp_servers_field_round_trips(tmp_path, monkeypatch):
    """Write an ad-hoc profile with mcp_servers and verify it round-trips."""
    fake_root = tmp_path / "profiles"
    fake_root.mkdir()
    profile_dir = fake_root / "fixture-cs"
    profile_dir.mkdir()

    (profile_dir / "system.md").write_text("test prompt")
    (profile_dir / "profile.json").write_text(json.dumps({
        "id": "fixture-cs",
        "label": "Fixture CS",
        "description": "MCP round-trip test profile",
        "kb_root": "kb",
        "system_prompt_path": str(profile_dir / "system.md"),
        "tools": ["search_kb"],
        "mcp_servers": [
            {"name": "calendar", "command": "npx", "args": ["-y", "@x/cal"], "env": {}},
            {"name": "crm", "command": "/usr/bin/foo", "args": [], "env": {"K": "v"}},
        ],
    }))

    from backend import profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "PROFILE_ROOT", fake_root)
    p = load_profile("fixture-cs")

    assert len(p.mcp_servers) == 2
    assert p.mcp_servers[0]["name"] == "calendar"
    assert p.mcp_servers[1]["env"] == {"K": "v"}


# --------------------------------------------------------------------------- #
# /api/profiles endpoint
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from backend import config

    importlib.reload(config)
    from backend import app as app_module

    importlib.reload(app_module)
    app_module.limiter.enabled = False
    return TestClient(app_module.app)


def test_api_profiles_lists_both_bundled_profiles(client):
    r = client.get("/api/profiles")
    assert r.status_code == 200
    body = r.json()
    assert "default" in body
    ids = {p["id"] for p in body["profiles"]}
    assert {"strauss", "customer-service"} <= ids


def test_api_profiles_includes_tools_and_mcp_servers(client):
    r = client.get("/api/profiles")
    body = r.json()
    cs = next(p for p in body["profiles"] if p["id"] == "customer-service")
    assert cs["label"] == "Customer Service"
    assert cs["tools"] == ["list_kb", "read_file", "search_kb"]
    assert cs["mcp_servers"] == []
