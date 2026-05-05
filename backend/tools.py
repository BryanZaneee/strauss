"""Tool schemas (Anthropic shape) + the dispatcher.

SCHEMAS are authored once in Anthropic's `input_schema` shape. The OpenAICompatProvider
translates these via tool_translator.to_openai() at startup.
"""
from __future__ import annotations

import json
import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any

import httpx

from backend.kb_loader import (
    KBError,
    get_project_context,
    get_resume_summary,
    list_kb,
    read_file,
    search_kb,
)
from backend.web_search import WebSearchError, web_search

SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_kb",
        "description": (
            "List files under the knowledge base, optionally filtered by a subdirectory. "
            "Returns relative paths so they can be passed to read_file. "
            "Call this only when you don't already know what's available — the system context "
            "already includes a manifest of the KB. Walk depth limited to 2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subdir": {
                    "type": "string",
                    "description": (
                        "Relative subdirectory under the KB root (e.g. 'projects', 'codebases'). "
                        "Empty string lists from the root."
                    ),
                }
            },
            "required": ["subdir"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a single file from the knowledge base by relative path. "
            "Files are markdown (resume, project summaries, FAQs) or repomix XML codebase dumps. "
            "Repomix dumps are LARGE — for them, pass start_line/end_line to slice. "
            "Returns {path, lines: 'X-Y of Z', content} so you know exactly what you didn't see."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path under the KB root, e.g. 'resume/resume.md' or 'codebases/shuttrr.xml'."
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional 1-indexed start line (default 1).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional 1-indexed end line, inclusive. Default: start+1499. Hard cap: start+2999.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_kb",
        "description": (
            "Search the knowledge base for a substring or regex match. "
            "Returns matching file paths with up to 3 lines of context per match (240 char cap). "
            "Use to find which file mentions a topic before you read_file. "
            "Defaults to case-insensitive substring; pass regex=true for regex."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "regex": {"type": "boolean", "default": False},
                "subdir": {"type": "string", "default": ""},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_resume_summary",
        "description": (
            "Return Bryan's resume in markdown. Use for any question about employment history, "
            "education, certifications, or overall qualifications — faster than "
            "read_file('resume/resume.md')."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_project_context",
        "description": (
            "Return a curated PITCH summary for one of Bryan's projects (the 'why it matters' angle). "
            "Use when a recruiter asks about a specific project — answers like 'tell me about X' or "
            "'is X relevant to Y role'. For technical detail (stack, key files, architecture), the "
            "system context already includes a quick_info cheat sheet — no tool call needed for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": (
                        "Project name (case-insensitive). Examples: 'shuttrr', 'bryanzane', "
                        "'infinichat', 'anywhere', 'papo', 'esme', 'physiq', 'ftrmsg'."
                    ),
                }
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the public web for up-to-date information not in the knowledge base. "
            "Use for recent news, current events, company/person/technology lookups, or to "
            "verify a fact that may have changed since the KB was last updated. "
            "Do NOT use for questions about Bryan's resume or projects — those live in the KB. "
            "Returns a synthesized answer plus ranked results with title, url, and a short "
            "content snippet. Cite the URL when you use a result in your reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "How many results to return (1-10, default 5).",
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": (
                        "'basic' is faster and cheaper; 'advanced' returns deeper snippets "
                        "for harder queries. Default 'basic'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url_text",
        "description": (
            "Fetch the readable text from one public HTTP(S) page. Use this after web_search "
            "when a research profile needs to inspect a result more closely. The tool rejects "
            "localhost, private-network hosts, file URLs, non-text responses, redirects to unsafe "
            "hosts, and oversized pages. Returns title and excerpt, not raw browser DOM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public http:// or https:// URL to fetch.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum excerpt characters to return. Default 6000, hard cap 20000.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "calculator",
        "description": (
            "Run deterministic numeric calculations. No arbitrary expression evaluation. "
            "Supported operations: sum, average, difference, ratio, percent_change, and cagr. "
            "Pass values as numbers; difference/ratio/percent_change use the first two values, "
            "and cagr uses [start_value, end_value, periods]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["sum", "average", "difference", "ratio", "percent_change", "cagr"],
                },
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Numeric inputs for the selected operation.",
                },
            },
            "required": ["operation", "values"],
        },
    },
    {
        "name": "catalog_lookup",
        "description": (
            "Search the active profile's structured product or service catalog. For Sales "
            "Concierge this reads EasyAgent packages from catalog.json under the profile data root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the visitor is looking for."},
                "category": {"type": "string", "description": "Optional catalog category filter."},
                "max_results": {"type": "integer", "description": "Maximum matches to return. Default 5."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "qualify_lead",
        "description": (
            "Classify a prospective EasyAgent lead using demo qualification rules. Returns a "
            "lead tier, recommended catalog package id, missing questions, and reasoning labels. "
            "Preview-only; does not write to a CRM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "use_case": {"type": "string"},
                "urgency": {"type": "string"},
                "team_size": {"type": "string"},
                "budget_range": {"type": "string"},
                "integrations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["use_case"],
        },
    },
    {
        "name": "lead_capture_preview",
        "description": (
            "Normalize lead contact details and return a mock lead id. Preview-only; does not "
            "persist data, send email, or call a CRM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "company": {"type": "string"},
                "use_case": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name", "email", "use_case"],
        },
    },
    {
        "name": "checkout_link_preview",
        "description": (
            "Return a fake checkout link and line-item summary for a Sales Concierge package. "
            "Preview-only; does not import Stripe or create a live Checkout Session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "package_id": {"type": "string"},
                "billing_cadence": {
                    "type": "string",
                    "enum": ["one_time", "monthly", "annual"],
                    "description": "Default one_time.",
                },
                "quantity": {"type": "integer", "description": "Default 1, clamped to 1-99."},
            },
            "required": ["package_id"],
        },
    },
]

SCHEMAS_BY_NAME: dict[str, dict[str, Any]] = {s["name"]: s for s in SCHEMAS}
DEFAULT_TOOL_NAMES: tuple[str, ...] = tuple(SCHEMAS_BY_NAME)
MAX_PUBLIC_SOURCE_ITEMS = 5
FETCH_TIMEOUT_SECONDS = 10.0
FETCH_DEFAULT_CHARS = 6_000
FETCH_MAX_CHARS = 20_000
FETCH_MAX_BYTES = 250_000
FETCH_MAX_REDIRECTS = 3
PUBLIC_PROJECT_LABELS: dict[str, str] = {
    "anywhere": "Anywhere",
    "ayopapo": "Ayopapo",
    "bryanzane-com": "Bryanzane.com",
    "esme": "Esme",
    "ftrmsg": "Ftrmsg",
    "infinichat": "Infinichat",
    "llmbench": "LLM Bench",
    "physiq": "Physiq",
    "shuttrr": "Shuttrr",
    # Public fixture project used by tests.
    "widget": "Widget",
}


class ToolExecutionError(Exception):
    """Raised when a non-KB tool rejects input or cannot complete safely."""


def schemas_for_tools(tool_names: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    """Return shared tool schemas for a profile's allowed tool names."""
    names = tuple(tool_names or DEFAULT_TOOL_NAMES)
    return [SCHEMAS_BY_NAME[name] for name in names if name in SCHEMAS_BY_NAME]


@dataclass
class ToolResult:
    """Normalized tool result. Providers convert this to their wire format."""

    tool_use_id: str
    name: str
    content: str  # JSON string passed back to the model
    is_error: bool = False
    source_summary: str = ""
    source_items: list[dict[str, str]] = field(default_factory=list)
    source_count: int = 0
    hidden_count: int = 0


def _clean_label(label: str, fallback: str = "Knowledge base source") -> str:
    """Return a browser-safe source label with no path separators or control chars."""
    label = re.sub(r"[/\\]+", " ", str(label or ""))
    label = "".join(ch for ch in label if ch.isprintable())
    label = re.sub(r"\s+", " ", label).strip()
    if not label:
        return fallback
    return label[:80]


def _label_from_slug(slug: str) -> str:
    """Convert a known public slug into display text."""
    slug = str(slug or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", slug):
        return "Project knowledge base"
    if slug not in PUBLIC_PROJECT_LABELS:
        return "Project knowledge base"
    return f"Project: {PUBLIC_PROJECT_LABELS[slug]}"


def _label_from_kb_path(path: str) -> str:
    """Map an internal KB path to a sanitized public category label."""
    path = str(path or "")
    if path == "resume/resume.md":
        return "Resume"
    if path == "INDEX.md":
        return "Knowledge base index"
    if path.startswith("projects/"):
        stem = Path(path).stem
        return _label_from_slug(stem)
    if path.startswith("codebases/"):
        return "Codebase reference"
    if path.startswith("meta/"):
        return "Portfolio knowledge base"
    return "Knowledge base document"


def _source_item(label: str, kind: str) -> dict[str, str]:
    return {"label": _clean_label(label), "kind": _clean_label(kind, "kb_read")}


def _unique_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = (item["label"], item["kind"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cap_items(items: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    return items[:MAX_PUBLIC_SOURCE_ITEMS], max(0, len(items) - MAX_PUBLIC_SOURCE_ITEMS)


def _public_domain(url: str) -> str | None:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "/" in host or "\\" in host:
        return None
    return host[:80]


def _assert_public_http_url(url: str) -> str:
    """Validate a URL before fetching so tools cannot reach local infrastructure."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ToolExecutionError("url must use http or https")
    if not parsed.hostname:
        raise ToolExecutionError("url must include a hostname")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "ip6-localhost", "ip6-loopback"}:
        raise ToolExecutionError("local hosts are not allowed")

    addresses: set[str] = set()
    try:
        ip = ipaddress.ip_address(host)
        addresses.add(str(ip))
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise ToolExecutionError(f"could not resolve hostname: {host}") from e
        addresses.update(info[4][0] for info in infos)

    if not addresses:
        raise ToolExecutionError(f"could not resolve hostname: {host}")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as e:
            raise ToolExecutionError(f"invalid resolved address for {host}") from e
        if not ip.is_global:
            raise ToolExecutionError("private, local, or reserved network hosts are not allowed")

    return parsed.geturl()


def _text_content_type(content_type: str) -> bool:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    return (
        ctype.startswith("text/")
        or ctype in {
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/xhtml+xml",
        }
    )


def _html_title(raw: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()[:160]


def _html_to_text(raw: str) -> str:
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|section|article|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_url_text(url: str, *, max_chars: int = FETCH_DEFAULT_CHARS) -> dict[str, Any]:
    """Fetch one public text page and return a compact excerpt."""
    if not isinstance(url, str) or not url.strip():
        raise ToolExecutionError("url must be a non-empty string")
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError) as e:
        raise ToolExecutionError("max_chars must be an integer") from e
    max_chars = max(1, min(max_chars, FETCH_MAX_CHARS))

    current_url = url.strip()
    response: httpx.Response | None = None
    for _ in range(FETCH_MAX_REDIRECTS + 1):
        current_url = _assert_public_http_url(current_url)
        try:
            response = httpx.get(
                current_url,
                follow_redirects=False,
                timeout=FETCH_TIMEOUT_SECONDS,
                headers={"User-Agent": "EasyAgent research fetch/0.1"},
            )
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"network error: {type(e).__name__}: {e}") from e

        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise ToolExecutionError("redirect response did not include a Location header")
            current_url = urljoin(current_url, location)
            continue
        break
    else:
        raise ToolExecutionError("too many redirects")

    assert response is not None
    if response.status_code >= 400:
        raise ToolExecutionError(f"HTTP {response.status_code} while fetching URL")

    content_type = response.headers.get("content-type", "")
    if not _text_content_type(content_type):
        raise ToolExecutionError(f"non-text content type: {content_type or 'unknown'}")
    if len(response.content) > FETCH_MAX_BYTES:
        raise ToolExecutionError(f"response too large: {len(response.content)} bytes")

    raw = response.text
    title = _html_title(raw) if "html" in content_type.lower() else ""
    readable = _html_to_text(raw) if "html" in content_type.lower() else raw.strip()
    readable = re.sub(r"\s+", " ", readable).strip()
    excerpt = readable[:max_chars]
    if len(readable) > max_chars:
        excerpt = excerpt.rstrip() + "..."

    return {
        "url": str(response.url),
        "status_code": response.status_code,
        "content_type": content_type.split(";", 1)[0].strip().lower(),
        "title": title,
        "excerpt": excerpt,
    }


def calculator(operation: str, values: list[int | float]) -> dict[str, Any]:
    """Run a bounded deterministic calculation."""
    if not isinstance(operation, str) or not operation:
        raise ToolExecutionError("operation must be a non-empty string")
    if not isinstance(values, list):
        raise ToolExecutionError("values must be a list of numbers")
    nums: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolExecutionError("values must contain only numbers")
        nums.append(float(value))

    op = operation.strip().lower()
    if op == "sum":
        if not nums:
            raise ToolExecutionError("sum requires at least one value")
        result = sum(nums)
    elif op == "average":
        if not nums:
            raise ToolExecutionError("average requires at least one value")
        result = sum(nums) / len(nums)
    elif op == "difference":
        if len(nums) < 2:
            raise ToolExecutionError("difference requires two values")
        result = nums[1] - nums[0]
    elif op == "ratio":
        if len(nums) < 2:
            raise ToolExecutionError("ratio requires two values")
        if nums[0] == 0:
            raise ToolExecutionError("ratio denominator must not be zero")
        result = nums[1] / nums[0]
    elif op == "percent_change":
        if len(nums) < 2:
            raise ToolExecutionError("percent_change requires two values")
        if nums[0] == 0:
            raise ToolExecutionError("percent_change starting value must not be zero")
        result = ((nums[1] - nums[0]) / nums[0]) * 100
    elif op == "cagr":
        if len(nums) < 3:
            raise ToolExecutionError("cagr requires [start_value, end_value, periods]")
        start, end, periods = nums[:3]
        if start <= 0 or end < 0 or periods <= 0:
            raise ToolExecutionError("cagr requires start > 0, end >= 0, and periods > 0")
        result = ((end / start) ** (1 / periods) - 1) * 100
    else:
        raise ToolExecutionError(f"unsupported operation: {operation}")

    return {
        "operation": op,
        "values": nums,
        "result": result,
    }


def _require_data_root(data_root: Path | None) -> Path:
    if data_root is None:
        raise ToolExecutionError("active profile does not define data_root")
    return data_root.resolve()


def _load_catalog(data_root: Path | None) -> dict[str, Any]:
    root = _require_data_root(data_root)
    path = (root / "catalog.json").resolve()
    try:
        path.relative_to(root)
    except ValueError as e:
        raise ToolExecutionError("catalog path escapes data root") from e
    if not path.exists() or not path.is_file():
        raise ToolExecutionError("catalog.json not found for active profile")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolExecutionError(f"catalog.json is invalid JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
        raise ToolExecutionError("catalog.json must contain a packages array")
    return data


def _package_text(package: dict[str, Any]) -> str:
    fields = [
        package.get("id", ""),
        package.get("name", ""),
        package.get("category", ""),
        package.get("description", ""),
        package.get("best_for", ""),
        " ".join(str(v) for v in package.get("keywords", [])),
        " ".join(str(v) for v in package.get("features", [])),
    ]
    return " ".join(str(v).lower() for v in fields)


def catalog_lookup(
    query: str,
    *,
    category: str = "",
    max_results: int = 5,
    data_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ToolExecutionError("query must be a non-empty string")
    try:
        max_results = max(1, min(int(max_results), 10))
    except (TypeError, ValueError) as e:
        raise ToolExecutionError("max_results must be an integer") from e

    catalog = _load_catalog(data_root)
    q_terms = [term for term in re.split(r"\W+", query.lower()) if term]
    category_norm = str(category or "").strip().lower()
    matches: list[tuple[int, dict[str, Any]]] = []
    for package in catalog["packages"]:
        if not isinstance(package, dict):
            continue
        if category_norm and str(package.get("category", "")).lower() != category_norm:
            continue
        text = _package_text(package)
        score = sum(1 for term in q_terms if term in text)
        if score or not q_terms:
            matches.append((score, package))

    matches.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    packages = [
        {
            "id": pkg.get("id", ""),
            "name": pkg.get("name", ""),
            "category": pkg.get("category", ""),
            "description": pkg.get("description", ""),
            "best_for": pkg.get("best_for", ""),
            "price_display": pkg.get("price_display", ""),
            "timeline": pkg.get("timeline", ""),
            "features": pkg.get("features", []),
            "next_step": pkg.get("next_step", ""),
        }
        for _, pkg in matches[:max_results]
    ]
    return {
        "query": query.strip(),
        "category": category_norm,
        "matches": packages,
        "catalog": catalog.get("name", "Product catalog"),
    }


def _catalog_package_by_id(data_root: Path | None, package_id: str) -> dict[str, Any]:
    catalog = _load_catalog(data_root)
    wanted = str(package_id or "").strip()
    for package in catalog["packages"]:
        if isinstance(package, dict) and package.get("id") == wanted:
            return package
    raise ToolExecutionError(f"unknown package_id: {package_id}")


def _recommend_package_id(use_case: str, integrations: list[str]) -> tuple[str, list[str]]:
    text = f"{use_case} {' '.join(integrations)}".lower()
    reasons: list[str] = []
    if any(term in text for term in ("research", "analyst", "market", "competitor", "brief")):
        reasons.append("research-oriented use case")
        return "research-profile", reasons
    if any(term in text for term in ("sales", "lead", "revenue", "pricing", "checkout")):
        reasons.append("sales or qualification workflow")
        return "sales-profile", reasons
    if any(term in text for term in ("tool", "api", "integration", "stripe", "calendar", "crm")):
        reasons.append("custom tool or integration need")
        return "custom-tools", reasons
    if any(term in text for term in ("production", "deploy", "security", "rate", "budget", "logging")):
        reasons.append("production readiness concern")
        return "production-hardening", reasons
    reasons.append("starter website widget fit")
    return "starter-widget", reasons


def qualify_lead(
    use_case: str,
    *,
    urgency: str = "",
    team_size: str = "",
    budget_range: str = "",
    integrations: list[str] | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(use_case, str) or not use_case.strip():
        raise ToolExecutionError("use_case must be a non-empty string")
    integrations = integrations or []
    if not isinstance(integrations, list) or any(not isinstance(v, str) for v in integrations):
        raise ToolExecutionError("integrations must be a list of strings")

    missing: list[str] = []
    if not str(urgency or "").strip():
        missing.append("urgency")
    if not str(team_size or "").strip():
        missing.append("team_size")
    if not str(budget_range or "").strip():
        missing.append("budget_range")

    package_id, reasons = _recommend_package_id(use_case, integrations)
    package = _catalog_package_by_id(data_root, package_id)

    urgency_text = str(urgency or "").lower()
    budget_text = str(budget_range or "").lower()
    high_urgency = any(term in urgency_text for term in ("urgent", "now", "this week", "asap"))
    meaningful_budget = any(term in budget_text for term in ("5k", "10k", "5000", "10000", "approved"))
    has_integrations = bool(integrations)
    if high_urgency and (meaningful_budget or has_integrations):
        tier = "high"
    elif missing:
        tier = "needs_info"
    else:
        tier = "medium"

    return {
        "tier": tier,
        "recommended_package_id": package_id,
        "recommended_package_name": package.get("name", ""),
        "missing_questions": missing,
        "reasoning_labels": reasons
        + (["urgent timeline"] if high_urgency else [])
        + (["integration surface present"] if has_integrations else []),
        "preview_only": True,
    }


def lead_capture_preview(
    *,
    name: str,
    email: str,
    company: str = "",
    use_case: str,
    notes: str = "",
) -> dict[str, Any]:
    clean = {
        "name": str(name or "").strip(),
        "email": str(email or "").strip().lower(),
        "company": str(company or "").strip(),
        "use_case": str(use_case or "").strip(),
        "notes": str(notes or "").strip(),
    }
    if not clean["name"]:
        raise ToolExecutionError("name must be provided")
    if "@" not in clean["email"] or clean["email"].startswith("@") or clean["email"].endswith("@"):
        raise ToolExecutionError("email must look like an email address")
    if not clean["use_case"]:
        raise ToolExecutionError("use_case must be provided")

    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "mock_lead_id": f"lead_preview_{digest[:10]}",
        "payload": clean,
        "persisted": False,
        "preview_only": True,
    }


def checkout_link_preview(
    package_id: str,
    *,
    billing_cadence: str = "one_time",
    quantity: int = 1,
    data_root: Path | None = None,
) -> dict[str, Any]:
    package = _catalog_package_by_id(data_root, package_id)
    cadence = str(billing_cadence or "one_time").strip().lower()
    if cadence not in {"one_time", "monthly", "annual"}:
        raise ToolExecutionError("billing_cadence must be one_time, monthly, or annual")
    try:
        quantity = max(1, min(int(quantity), 99))
    except (TypeError, ValueError) as e:
        raise ToolExecutionError("quantity must be an integer") from e

    line_item = {
        "package_id": package.get("id", ""),
        "name": package.get("name", ""),
        "price_display": package.get("price_display", ""),
        "billing_cadence": cadence,
        "quantity": quantity,
    }
    return {
        "checkout_url": (
            "https://checkout.easyagent.example/preview/"
            f"{package.get('id', '')}?cadence={cadence}&quantity={quantity}"
        ),
        "line_items": [line_item],
        "stripe_session_created": False,
        "preview_only": True,
    }


def _source_metadata(name: str, arguments: dict, out: Any, *, is_error: bool) -> dict[str, Any]:
    """Build safe browser-facing source metadata from a tool result.

    Raw KB paths and snippets stay inside `content` for the model. This metadata is
    deliberately category-level so public clients can show evidence type without
    revealing private filesystem or KB layout details.
    """
    if is_error:
        return {
            "source_summary": "source unavailable",
            "source_items": [],
            "source_count": 0,
            "hidden_count": 0,
        }

    if name == "get_resume_summary":
        return {
            "source_summary": "read Resume",
            "source_items": [_source_item("Resume", "kb_read")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "get_project_context":
        label = _label_from_slug(str((out or {}).get("project", "")))
        return {
            "source_summary": f"read {label}",
            "source_items": [_source_item(label, "kb_read")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "read_file":
        label = _label_from_kb_path(str((out or {}).get("path", "")))
        return {
            "source_summary": f"read {label}",
            "source_items": [_source_item(label, "kb_read")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "search_kb":
        matches = out if isinstance(out, list) else []
        items = _unique_items([
            _source_item(_label_from_kb_path(str(item.get("path", ""))), "kb_search")
            for item in matches
            if isinstance(item, dict)
        ])
        visible, hidden = _cap_items(items)
        match_label = "match" if len(matches) == 1 else "matches"
        source_label = "source" if len(items) == 1 else "sources"
        return {
            "source_summary": f"searched {len(items)} {source_label}, {len(matches)} {match_label}",
            "source_items": visible,
            "source_count": len(items),
            "hidden_count": hidden,
        }

    if name == "list_kb":
        count = len(out) if isinstance(out, list) else 0
        entry_label = "entry" if count == 1 else "entries"
        return {
            "source_summary": f"listed {count} knowledge-base {entry_label}",
            "source_items": [_source_item("Knowledge base index", "kb_list")],
            "source_count": count,
            "hidden_count": 0,
        }

    if name == "web_search":
        results = (out or {}).get("results", []) if isinstance(out, dict) else []
        items = []
        for result in results:
            if not isinstance(result, dict):
                continue
            domain = _public_domain(result.get("url", ""))
            items.append(_source_item(f"Web result: {domain}" if domain else "Web result", "web"))
        items = _unique_items(items)
        visible, hidden = _cap_items(items)
        result_label = "result" if len(results) == 1 else "results"
        return {
            "source_summary": f"searched public web, {len(results)} {result_label}",
            "source_items": visible,
            "source_count": len(results),
            "hidden_count": hidden,
        }

    if name == "fetch_url_text":
        domain = _public_domain(str((out or {}).get("url", ""))) if isinstance(out, dict) else None
        label = f"Public web page: {domain}" if domain else "Public web page"
        return {
            "source_summary": "fetched public web page",
            "source_items": [_source_item(label, "web_fetch")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "calculator":
        return {
            "source_summary": "calculated result",
            "source_items": [_source_item("Calculation", "calculation")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "catalog_lookup":
        matches = (out or {}).get("matches", []) if isinstance(out, dict) else []
        count = len(matches)
        package_label = "package" if count == 1 else "packages"
        return {
            "source_summary": f"searched product catalog, {count} {package_label}",
            "source_items": [_source_item("Product catalog", "catalog")],
            "source_count": count,
            "hidden_count": 0,
        }

    if name == "qualify_lead":
        return {
            "source_summary": "qualified lead",
            "source_items": [_source_item("Lead qualification rules", "lead_qualification")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "lead_capture_preview":
        return {
            "source_summary": "prepared lead capture preview",
            "source_items": [_source_item("Lead capture preview", "lead_preview")],
            "source_count": 1,
            "hidden_count": 0,
        }

    if name == "checkout_link_preview":
        return {
            "source_summary": "prepared checkout preview",
            "source_items": [_source_item("Checkout preview", "checkout_preview")],
            "source_count": 1,
            "hidden_count": 0,
        }

    return {
        "source_summary": "used tool",
        "source_items": [],
        "source_count": 0,
        "hidden_count": 0,
    }


def _tool_result(
    *,
    tool_use_id: str,
    name: str,
    content: str,
    is_error: bool,
    arguments: dict | None = None,
    output: Any = None,
) -> ToolResult:
    meta = _source_metadata(name, arguments or {}, output, is_error=is_error)
    return ToolResult(
        tool_use_id=tool_use_id,
        name=name,
        content=content,
        is_error=is_error,
        **meta,
    )


def run_tool(
    name: str,
    arguments: dict,
    tool_use_id: str,
    *,
    root: Path | None = None,
    data_root: Path | None = None,
    allowed_tools: tuple[str, ...] | list[str] | set[str] | None = None,
) -> ToolResult:
    """Dispatch a tool call. Catches KBError + unexpected errors → is_error=True."""
    try:
        if allowed_tools is not None and name not in set(allowed_tools):
            return _tool_result(
                tool_use_id=tool_use_id,
                name=name,
                content=json.dumps({"error": f"tool not enabled for this profile: {name}"}),
                is_error=True,
                arguments=arguments,
            )
        if name == "list_kb":
            out: Any = list_kb(arguments.get("subdir", ""), root=root)
        elif name == "read_file":
            out = read_file(
                arguments["path"],
                arguments.get("start_line", 1),
                arguments.get("end_line"),
                root=root,
            )
        elif name == "search_kb":
            out = search_kb(
                arguments["query"],
                regex=arguments.get("regex", False),
                subdir=arguments.get("subdir", ""),
                max_results=arguments.get("max_results", 20),
                root=root,
            )
        elif name == "get_resume_summary":
            out = get_resume_summary(root=root)
        elif name == "get_project_context":
            out = get_project_context(arguments["project_name"], root=root)
        elif name == "web_search":
            out = web_search(
                arguments["query"],
                max_results=arguments.get("max_results", 5),
                search_depth=arguments.get("search_depth", "basic"),
                include_answer=arguments.get("include_answer", True),
            )
        elif name == "fetch_url_text":
            out = fetch_url_text(
                arguments["url"],
                max_chars=arguments.get("max_chars", FETCH_DEFAULT_CHARS),
            )
        elif name == "calculator":
            out = calculator(arguments["operation"], arguments["values"])
        elif name == "catalog_lookup":
            out = catalog_lookup(
                arguments["query"],
                category=arguments.get("category", ""),
                max_results=arguments.get("max_results", 5),
                data_root=data_root,
            )
        elif name == "qualify_lead":
            out = qualify_lead(
                arguments["use_case"],
                urgency=arguments.get("urgency", ""),
                team_size=arguments.get("team_size", ""),
                budget_range=arguments.get("budget_range", ""),
                integrations=arguments.get("integrations", []),
                data_root=data_root,
            )
        elif name == "lead_capture_preview":
            out = lead_capture_preview(
                name=arguments["name"],
                email=arguments["email"],
                company=arguments.get("company", ""),
                use_case=arguments["use_case"],
                notes=arguments.get("notes", ""),
            )
        elif name == "checkout_link_preview":
            out = checkout_link_preview(
                arguments["package_id"],
                billing_cadence=arguments.get("billing_cadence", "one_time"),
                quantity=arguments.get("quantity", 1),
                data_root=data_root,
            )
        else:
            return _tool_result(
                tool_use_id=tool_use_id,
                name=name,
                content=json.dumps({"error": f"unknown tool: {name}"}),
                is_error=True,
                arguments=arguments,
            )
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps(out, ensure_ascii=False),
            is_error=False,
            arguments=arguments,
            output=out,
        )
    except KBError as e:
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": str(e)}),
            is_error=True,
            arguments=arguments,
        )
    except WebSearchError as e:
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": str(e)}),
            is_error=True,
            arguments=arguments,
        )
    except ToolExecutionError as e:
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": str(e)}),
            is_error=True,
            arguments=arguments,
        )
    except KeyError as e:
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": f"missing required argument: {e.args[0]}"}),
            is_error=True,
            arguments=arguments,
        )
    except Exception as e:
        return _tool_result(
            tool_use_id=tool_use_id,
            name=name,
            content=json.dumps({"error": f"{type(e).__name__}: {e}"}),
            is_error=True,
            arguments=arguments,
        )
