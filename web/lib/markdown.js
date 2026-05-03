// EasyAgent — reusable markdown rendering for chat surfaces.
//
// Renders markdown with syntax-highlighted fenced code (highlight.js) and
// LaTeX math (KaTeX), then sanitizes the result with DOMPurify. Designed
// for SSE streaming: renderMarkdown() is safe to call repeatedly on a
// growing buffer — initialization is idempotent and cheap.
//
// Required globals (load via CDN before this file):
//   - marked      v12+
//   - DOMPurify   v3+
//   - hljs        highlight.js v11+   (optional — code blocks fall back to plain)
//   - katex       v0.16+              (optional — math falls back to source text)
//
// KaTeX needs its CSS for fonts/layout. Load alongside katex.min.js:
//   <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">
//
// cdnjs hosts KaTeX as `KaTeX` (capitalized). Latest cdnjs build is 0.16.9.
//
// Usage:
//   <script src=".../marked.min.js" defer></script>
//   <script src=".../purify.min.js" defer></script>
//   <script src=".../highlight.min.js" defer></script>
//   <script src=".../katex.min.js" defer></script>
//   <script src=".../lib/markdown.js" defer></script>
//   <script src=".../app.js" defer></script>
//
//   const html = window.EasyAgentMarkdown.renderMarkdown(text);
//   element.innerHTML = html;

(function () {
  "use strict";

  let configured = false;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // Math via marked's extension API. Tokenizers run before paragraphs are
  // formed, so $...$ and $$...$$ are detected without escaping rules in the
  // markdown source. An unclosed delimiter mid-stream falls through as text
  // and snaps into rendered math once the closing delimiter arrives.
  const inlineMath = {
    name: "inlineMath",
    level: "inline",
    start(src) { return src.indexOf("$"); },
    tokenizer(src) {
      // Single $...$. Disallow newlines and require non-space adjacency
      // around the delimiters so prose like "$5 and $10" isn't matched.
      const m = src.match(/^\$(?!\s)([^\n$]+?)(?<!\s)\$(?!\d)/);
      if (m) return { type: "inlineMath", raw: m[0], text: m[1] };
    },
    renderer(token) { return renderKatex(token.text, false); },
  };

  const blockMath = {
    name: "blockMath",
    level: "block",
    start(src) { return src.indexOf("$$"); },
    tokenizer(src) {
      const m = src.match(/^\$\$\s*([\s\S]+?)\s*\$\$(?:\n|$)/);
      if (m) return { type: "blockMath", raw: m[0], text: m[1].trim() };
    },
    renderer(token) {
      return `<div class="ea-math-block">${renderKatex(token.text, true)}</div>\n`;
    },
  };

  function renderKatex(expr, displayMode) {
    if (!window.katex) return escapeHtml(displayMode ? `$$${expr}$$` : `$${expr}$`);
    try {
      return window.katex.renderToString(expr, {
        throwOnError: false,
        displayMode,
        output: "html",
      });
    } catch {
      return escapeHtml(displayMode ? `$$${expr}$$` : `$${expr}$`);
    }
  }

  // Custom code renderer: highlight.js when language is recognized, plain
  // escaped text otherwise. Wrapping with `hljs language-X` lets a host CSS
  // theme target tokens via .hljs-keyword, .hljs-string, etc.
  function highlightCode(code, lang) {
    if (!window.hljs) return escapeHtml(code);
    try {
      if (lang && window.hljs.getLanguage(lang)) {
        return window.hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
      }
      return window.hljs.highlightAuto(code).value;
    } catch {
      return escapeHtml(code);
    }
  }

  // marked v12 hands renderer methods a token object. Older versions pass
  // (code, lang, escaped) positionally — handle both so this module isn't
  // pinned to an exact marked release.
  function codeRenderer(codeOrToken, maybeLang) {
    let code, lang;
    if (typeof codeOrToken === "object" && codeOrToken !== null) {
      code = codeOrToken.text;
      lang = codeOrToken.lang;
    } else {
      code = codeOrToken;
      lang = maybeLang;
    }
    const langClass = lang ? ` language-${lang}` : "";
    return `<pre><code class="hljs${langClass}">${highlightCode(code, lang)}</code></pre>\n`;
  }

  function configure() {
    if (configured || !window.marked) return;
    window.marked.use({
      gfm: true,
      breaks: true,
      extensions: [blockMath, inlineMath],
      renderer: { code: codeRenderer },
    });
    configured = true;
  }

  // DOMPurify defaults already permit <span class="..." style="..."> and
  // aria-hidden, which is everything KaTeX's HTML output needs. Highlight.js
  // emits the same. Keep the call simple so the host page can still apply
  // its own DOMPurify hooks if needed.
  function sanitize(html) {
    return window.DOMPurify.sanitize(html);
  }

  function renderMarkdown(text) {
    if (!window.marked || !window.DOMPurify) return escapeHtml(text);
    configure();
    const html = window.marked.parse(text);
    return sanitize(html);
  }

  window.EasyAgentMarkdown = {
    renderMarkdown,
    escapeHtml,
    // Exposed for tests / advanced use; calling it is optional.
    configure,
  };
})();
