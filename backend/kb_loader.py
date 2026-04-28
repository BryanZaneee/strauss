"""Knowledge-base primitives.

Single trust boundary: every path passes through `_safe_resolve()` before any
filesystem op. Read-only. No write operations exist by design.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.config import KB_ROOT

MAX_BYTES_PER_CALL = 80_000
MAX_LINES_PER_READ = 3000
DEFAULT_LINES_PER_READ = 1500
LIST_MAX_DEPTH = 2

# Aliases the model might use for project lookup. Maps lowercase input → file slug under kb/projects/.
# Files not in this map can still be looked up by their exact slug.
PROJECT_ALIASES: dict[str, str] = {
    "bryanzane.com": "bryanzane-com",
    "bryanzane": "bryanzane-com",
    "bryan zane": "bryanzane-com",
    "bryanzanecom": "bryanzane-com",
    "infinichat_rn": "infinichat",
    "infinichat-rn": "infinichat",
}


class KBError(Exception):
    """Raised for any KB violation (path escape, missing file, bad arg)."""


def _root(root: Path | None) -> Path:
    return (root or KB_ROOT).resolve()


def _safe_resolve(rel: str, *, root: Path | None = None) -> Path:
    """Resolve `rel` under root; reject absolute paths, traversal, and symlinks pointing outside."""
    if not isinstance(rel, str):
        raise KBError(f"path must be a string, got {type(rel).__name__}")
    if not rel:
        raise KBError("path must not be empty")
    if Path(rel).is_absolute():
        raise KBError(f"absolute paths not allowed: {rel}")
    base = _root(root)
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise KBError(f"path escapes kb root: {rel}")
    return candidate


def list_kb(subdir: str = "", *, root: Path | None = None) -> list[dict]:
    """List entries under `subdir`. Walk depth ≤ LIST_MAX_DEPTH. Hidden/junk paths skipped."""
    base_rel = subdir if subdir else "."
    base = _safe_resolve(base_rel, root=root)
    if not base.exists():
        return []
    if not base.is_dir():
        raise KBError(f"not a directory: {subdir}")

    root_resolved = _root(root)
    out: list[dict] = []
    for entry in sorted(base.rglob("*")):
        # Depth relative to the listed base, not the kb root.
        if len(entry.relative_to(base).parts) > LIST_MAX_DEPTH:
            continue
        if entry.name.startswith(".") or "__pycache__" in entry.parts:
            continue
        rel_to_root = entry.relative_to(root_resolved).as_posix()
        out.append(
            {
                "path": rel_to_root,
                "size_bytes": entry.stat().st_size if entry.is_file() else 0,
                "kind": "file" if entry.is_file() else "dir",
            }
        )
    return out


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    *,
    root: Path | None = None,
) -> dict:
    """Read a slice of a KB file. Returns {path, lines, content}.

    Always reports total lines so the caller knows what was omitted.
    """
    p = _safe_resolve(path, root=root)
    if not p.exists() or not p.is_file():
        raise KBError(f"not a file: {path}")

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    if start_line < 1:
        start_line = 1
    cap_end = start_line + MAX_LINES_PER_READ - 1
    if end_line is None:
        end_line = min(start_line + DEFAULT_LINES_PER_READ - 1, total, cap_end)
    else:
        end_line = min(end_line, cap_end, total)
    if end_line < start_line:
        end_line = start_line

    slice_text = "\n".join(lines[start_line - 1 : end_line])

    encoded = slice_text.encode("utf-8")
    if len(encoded) > MAX_BYTES_PER_CALL:
        truncated = encoded[:MAX_BYTES_PER_CALL].decode("utf-8", errors="ignore")
        slice_text = (
            truncated
            + f"\n[truncated; {len(encoded) - MAX_BYTES_PER_CALL} bytes omitted]"
        )

    return {
        "path": path,
        "lines": f"{start_line}-{end_line} of {total}",
        "content": slice_text,
    }


def search_kb(
    query: str,
    regex: bool = False,
    subdir: str = "",
    max_results: int = 20,
    *,
    root: Path | None = None,
) -> list[dict]:
    """Substring (case-insensitive) or regex search across KB files. Returns matches with context."""
    if not query:
        raise KBError("query must not be empty")
    base_rel = subdir if subdir else "."
    base = _safe_resolve(base_rel, root=root)
    root_resolved = _root(root)

    if regex:
        try:
            pat = re.compile(query, re.IGNORECASE)
        except re.error as e:
            raise KBError(f"invalid regex: {e}")
        matcher = lambda line: bool(pat.search(line))
    else:
        ql = query.lower()
        matcher = lambda line: ql in line.lower()

    results: list[dict] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root_resolved).as_posix()
        size = path.stat().st_size
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if matcher(line):
                lo = max(0, i - 2)
                hi = min(len(lines), i + 1)
                ctx = "\n".join(lines[lo:hi])
                if len(ctx) > 240:
                    ctx = ctx[:240] + "…"
                results.append(
                    {"path": rel, "line": i, "size_bytes": size, "context": ctx}
                )
                if len(results) >= max_results:
                    return results
    return results


def get_resume_summary(*, root: Path | None = None) -> dict:
    """Specialized: return resume.md as if read_file was called on it."""
    return read_file("resume/resume.md", root=root)


def get_project_context(project_name: str, *, root: Path | None = None) -> dict:
    """Specialized: return the curated pitch summary for a named project.

    Tries the literal slug first (e.g., 'shuttrr' → projects/shuttrr.md), falls back to
    PROJECT_ALIASES for friendly names ('bryanzane.com' → projects/bryanzane-com.md).
    """
    name = (project_name or "").lower().strip()
    if not name:
        raise KBError("project_name must not be empty")
    slug = PROJECT_ALIASES.get(name, name)
    rel = f"projects/{slug}.md"
    try:
        loaded = read_file(rel, root=root)
    except KBError:
        raise KBError(
            f"no project file for '{project_name}'. Try list_kb(subdir='projects') to see what's available."
        )
    return {"project": slug, "summary": loaded["content"]}
