"""Shared pytest fixtures. Points the KB at tests/fixtures/mini_kb/ for all tests."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_KB = (Path(__file__).parent / "fixtures" / "mini_kb").resolve()


@pytest.fixture(autouse=True)
def use_mini_kb(monkeypatch):
    """Override KB_ROOT in both modules that import it. Autouse so every test is isolated."""
    from backend import config, kb_loader

    monkeypatch.setattr(config, "KB_ROOT", FIXTURE_KB)
    monkeypatch.setattr(kb_loader, "KB_ROOT", FIXTURE_KB)
    return FIXTURE_KB


@pytest.fixture(autouse=True)
def reset_budget():
    """Reset the daily token budget between tests so they don't leak state."""
    from backend.budget import TOKEN_BUDGET

    TOKEN_BUDGET.reset()
    yield
    TOKEN_BUDGET.reset()


@pytest.fixture
def kb_root(use_mini_kb):
    """Convenience alias when a test wants to reference the path explicitly."""
    return use_mini_kb
