"""Phase A tests: KB primitives + tool dispatcher.

Covers path safety, line-cap behavior, search semantics, and the run_tool error envelope.
"""
from __future__ import annotations

import json

import pytest

from backend.kb_loader import (
    KBError,
    get_project_context,
    get_resume_summary,
    list_kb,
    read_file,
    search_kb,
)
from backend.tools import SCHEMAS, run_tool


# --------------------------------------------------------------------------- #
# _safe_resolve / path safety
# --------------------------------------------------------------------------- #

class TestPathSafety:
    def test_rejects_traversal(self):
        with pytest.raises(KBError, match="escapes kb root"):
            read_file("../../../etc/passwd")

    def test_rejects_absolute_path(self):
        with pytest.raises(KBError, match="absolute paths not allowed"):
            read_file("/etc/passwd")

    def test_rejects_empty_path(self):
        with pytest.raises(KBError):
            read_file("")

    def test_rejects_non_string(self):
        with pytest.raises(KBError):
            read_file(None)  # type: ignore[arg-type]

    def test_traversal_via_subdir_in_search_blocked(self):
        with pytest.raises(KBError):
            search_kb("anything", subdir="..")


# --------------------------------------------------------------------------- #
# list_kb
# --------------------------------------------------------------------------- #

class TestListKB:
    def test_root_listing_includes_subdirs(self):
        entries = list_kb("")
        kinds = {e["path"]: e["kind"] for e in entries}
        # Subdirs are present at depth 1.
        assert "resume" in kinds
        assert kinds["resume"] == "dir"
        assert "projects" in kinds
        # Files are reported with size.
        sizes = {e["path"]: e["size_bytes"] for e in entries}
        assert "INDEX.md" in sizes
        assert sizes["INDEX.md"] > 0

    def test_subdir_listing(self):
        entries = list_kb("projects")
        assert any(e["path"] == "projects/widget.md" and e["kind"] == "file" for e in entries)

    def test_missing_subdir_returns_empty(self):
        assert list_kb("nonexistent-subdir") == []


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #

class TestReadFile:
    def test_basic_read(self):
        result = read_file("resume/resume.md")
        assert "Test User Resume" in result["content"]
        assert "of " in result["lines"]
        assert result["path"] == "resume/resume.md"

    def test_reports_total_lines(self, tmp_path, monkeypatch, kb_root):
        # Construct a file with known line count under the existing fixture KB.
        big = kb_root / "projects" / "big.md"
        big.write_text("\n".join(str(i) for i in range(1, 51)))  # 50 lines
        try:
            result = read_file("projects/big.md")
            assert result["lines"].endswith("of 50")
        finally:
            big.unlink()

    def test_line_slice(self):
        result = read_file("resume/resume.md", start_line=1, end_line=2)
        assert result["lines"].startswith("1-2")
        assert result["content"].count("\n") <= 1

    def test_caps_end_line_at_max(self, tmp_path, kb_root):
        # Write a 5000-line file and confirm end_line clamps to start + 2999.
        big = kb_root / "projects" / "huge.md"
        big.write_text("\n".join("line" for _ in range(5000)))
        try:
            result = read_file("projects/huge.md", start_line=1, end_line=4999)
            # MAX_LINES_PER_READ = 3000 → end clamps to start+2999 = 3000.
            assert result["lines"] == "1-3000 of 5000"
        finally:
            big.unlink()

    def test_missing_file_raises(self):
        with pytest.raises(KBError, match="not a file"):
            read_file("does/not/exist.md")


# --------------------------------------------------------------------------- #
# search_kb
# --------------------------------------------------------------------------- #

class TestSearchKB:
    def test_finds_substring(self):
        results = search_kb("zonkletcheese-42")
        assert any(r["path"] == "meta/faq.md" for r in results)
        # Each result includes context and size.
        first = results[0]
        assert "context" in first and "size_bytes" in first and "line" in first

    def test_case_insensitive(self):
        results = search_kb("CONVEX")
        assert any("convex" in r["context"].lower() or "Convex" in r["context"] for r in results)

    def test_regex_mode(self):
        results = search_kb(r"zonklet\w+", regex=True)
        assert any(r["path"] == "meta/faq.md" for r in results)

    def test_invalid_regex_raises(self):
        with pytest.raises(KBError, match="invalid regex"):
            search_kb("[unclosed", regex=True)

    def test_max_results(self):
        results = search_kb("e", max_results=3)
        assert len(results) <= 3


# --------------------------------------------------------------------------- #
# Specialized helpers
# --------------------------------------------------------------------------- #

class TestSpecialized:
    def test_get_resume_summary_matches_read_file(self):
        a = get_resume_summary()
        b = read_file("resume/resume.md")
        assert a["content"] == b["content"]

    def test_get_project_context_basic(self):
        result = get_project_context("widget")
        assert result["project"] == "widget"
        assert "test widget" in result["summary"].lower()

    def test_get_project_context_unknown_raises(self):
        with pytest.raises(KBError, match="no project file"):
            get_project_context("nonexistent")

    def test_get_project_context_empty_raises(self):
        with pytest.raises(KBError):
            get_project_context("")


# --------------------------------------------------------------------------- #
# run_tool — the dispatcher and error envelope
# --------------------------------------------------------------------------- #

class TestRunTool:
    def test_dispatch_list_kb(self):
        result = run_tool("list_kb", {"subdir": ""}, tool_use_id="t1")
        assert result.is_error is False
        payload = json.loads(result.content)
        assert isinstance(payload, list) and len(payload) > 0

    def test_dispatch_read_file(self):
        result = run_tool("read_file", {"path": "resume/resume.md"}, tool_use_id="t2")
        assert result.is_error is False
        assert "Test User Resume" in json.loads(result.content)["content"]

    def test_dispatch_get_resume_summary_no_args(self):
        result = run_tool("get_resume_summary", {}, tool_use_id="t3")
        assert result.is_error is False

    def test_path_traversal_returns_is_error(self):
        result = run_tool("read_file", {"path": "../../../etc/passwd"}, tool_use_id="t4")
        assert result.is_error is True
        payload = json.loads(result.content)
        assert "error" in payload

    def test_unknown_tool_returns_is_error(self):
        result = run_tool("hack_the_planet", {}, tool_use_id="t5")
        assert result.is_error is True

    def test_missing_required_arg_returns_is_error(self):
        result = run_tool("read_file", {}, tool_use_id="t6")
        assert result.is_error is True
        assert "path" in json.loads(result.content)["error"]

    def test_unknown_project_returns_is_error(self):
        result = run_tool(
            "get_project_context", {"project_name": "nope"}, tool_use_id="t7"
        )
        assert result.is_error is True

    def test_tool_use_id_round_trips(self):
        result = run_tool("list_kb", {"subdir": ""}, tool_use_id="my-unique-id")
        assert result.tool_use_id == "my-unique-id"


# --------------------------------------------------------------------------- #
# SCHEMAS sanity
# --------------------------------------------------------------------------- #

class TestSchemas:
    def test_all_schemas_have_required_fields(self):
        for s in SCHEMAS:
            assert s["name"]
            assert s["description"]
            assert "input_schema" in s
            assert s["input_schema"]["type"] == "object"
            assert "properties" in s["input_schema"]
            assert "required" in s["input_schema"]

    def test_schema_names_match_dispatcher(self):
        # Every declared schema must dispatch successfully (or fail on missing args, but not "unknown tool").
        for s in SCHEMAS:
            result = run_tool(s["name"], {}, tool_use_id="schema-check")
            payload = json.loads(result.content)
            if result.is_error:
                # Acceptable: missing required arg. NOT acceptable: "unknown tool".
                assert "unknown tool" not in payload.get("error", "")
