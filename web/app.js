// EasyAgent chat UI controller for the standalone demo. Vanilla, no build step.
// Talks to the FastAPI backend at /api/chat (SSE), /api/models, and /api/profile.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);
const API_BASE = window.EASYAGENT_API_BASE
  || window.STRAUSS_API_BASE
  || (LOCAL_HOSTS.has(window.location.hostname) ? "http://127.0.0.1:8001" : "");

const SESSION_KEY = "strauss-session";
const FIXED_MODEL = "deepseek-v4-flash";

const state = {
  profile: null,
  currentModel: FIXED_MODEL,
  sessionId: null,
  inflight: false,
  // FIFO of unsettled tool indicators. The backend emits all tool_use_starts
  // for a hop, then all tool_results in matching order.
  pendingTools: [],
};

const els = {
  feed: () => document.getElementById("chat-feed"),
  form: () => document.getElementById("chat-form"),
  input: () => document.getElementById("message-input"),
  send: () => document.getElementById("send"),
  newChat: () => document.getElementById("new-chat"),
};

async function init() {
  state.sessionId = sessionStorage.getItem(SESSION_KEY) || crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, state.sessionId);

  setBusy(true);

  await loadProfile();
  await verifyDeepSeek();

  els.form().addEventListener("submit", onSubmit);
  els.newChat().addEventListener("click", onNewChat);

  if (state.currentModel) {
    setBusy(false);
    els.input().focus();
  }
}

async function loadProfile() {
  try {
    const res = await fetch(`${API_BASE}/api/profile`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.profile = await res.json();
    renderProfileIntro();
  } catch {
    // Keep the static intro if the backend is older or temporarily unavailable.
  }
}

async function verifyDeepSeek() {
  try {
    const res = await fetch(`${API_BASE}/api/models`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const models = data.models || [];
    if (!models.some(m => m.id === FIXED_MODEL)) {
      state.currentModel = null;
      appendError("DeepSeek V4 Flash is not available on the server right now.");
    }
  } catch (err) {
    state.currentModel = null;
    appendError(`Failed to load model availability: ${err.message}`);
  }
}

function onNewChat() {
  resetSession("new conversation");
}

function resetSession(reason) {
  state.sessionId = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, state.sessionId);
  state.pendingTools = [];
  const feed = els.feed();
  while (feed.children.length > 1) feed.removeChild(feed.lastChild);
  appendSystem(`(${reason})`);
}

async function onSubmit(e) {
  e.preventDefault();
  if (state.inflight) return;
  if (!state.currentModel) {
    appendError("DeepSeek V4 Flash is not loaded. Refresh the page or check the server.");
    return;
  }
  const text = els.input().value.trim();
  if (!text) return;

  els.input().value = "";
  setBusy(true);

  appendUser(text);
  const ctx = {
    thinkingNode: null,
    thinkingBuffer: "",
    agentNode: null,
    agentBuffer: "",
  };

  try {
    await streamChat(text, ctx);
  } catch (err) {
    appendError(err.message || String(err));
  } finally {
    setBusy(false);
    els.input().focus();
  }
}

function setBusy(busy) {
  state.inflight = busy;
  els.input().disabled = busy;
  els.send().disabled = busy;
}

async function streamChat(message, ctx) {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      session_id: state.sessionId,
      message,
      model: state.currentModel,
      profile: state.profile?.id || "strauss",
    }),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buf += decoder.decode(value, {stream: true});
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = frame.match(/^event: (.+)$/m)?.[1];
      const data = frame.match(/^data: (.+)$/m)?.[1];
      if (ev && data) {
        let payload;
        try { payload = JSON.parse(data); } catch { continue; }
        handleEvent(ev, payload, ctx);
      }
    }
  }
}

function handleEvent(type, payload, ctx) {
  switch (type) {
    case "thinking_delta":
      if (!ctx.thinkingNode) {
        ctx.thinkingNode = appendThinking();
        ctx.thinkingBuffer = "";
      }
      ctx.thinkingBuffer += payload.text || "";
      ctx.thinkingNode.querySelector(".thinking-content").innerHTML =
        renderMarkdown(ctx.thinkingBuffer);
      scrollToBottom();
      break;
    case "delta":
      if (!ctx.agentNode) {
        ctx.agentNode = appendAgent();
        ctx.agentBuffer = "";
      }
      ctx.agentBuffer += payload.text || "";
      ctx.agentNode.innerHTML = renderMarkdown(ctx.agentBuffer);
      scrollToBottom();
      break;
    case "tool_use_start":
      ctx.agentNode = null;
      ctx.agentBuffer = "";
      ctx.thinkingNode = null;
      ctx.thinkingBuffer = "";
      showToolIndicator(payload.name);
      break;
    case "tool_result":
      settleToolIndicator(payload.is_error);
      break;
    case "done":
      settleAllTools(false);
      break;
    case "usage":
      break;
    case "error":
      settleAllTools(true);
      appendError(payload.message || "stream error");
      break;
  }
}

function renderMarkdown(text) {
  if (!window.marked || !window.DOMPurify) return escapeHtml(text);
  const html = window.marked.parse(text, { breaks: true, gfm: true });
  return window.DOMPurify.sanitize(html);
}

function appendUser(text) {
  const div = document.createElement("div");
  div.className = "msg msg-user";
  div.textContent = text;
  els.feed().appendChild(div);
  scrollToBottom();
}

function appendAgent() {
  const div = document.createElement("div");
  div.className = "msg msg-agent";
  els.feed().appendChild(div);
  scrollToBottom();
  return div;
}

function appendThinking() {
  const det = document.createElement("details");
  det.className = "msg msg-thinking";
  det.open = true;
  const sum = document.createElement("summary");
  sum.textContent = "thinking";
  det.appendChild(sum);
  const body = document.createElement("div");
  body.className = "thinking-content";
  det.appendChild(body);
  els.feed().appendChild(det);
  scrollToBottom();
  return det;
}

function appendSystem(text) {
  const div = document.createElement("div");
  div.className = "msg msg-system";
  const p = document.createElement("p");
  p.textContent = text;
  div.appendChild(p);
  els.feed().appendChild(div);
}

function renderProfileIntro() {
  const first = els.feed().querySelector(".msg-system");
  if (!first || !state.profile) return;
  first.innerHTML = "";

  const p = document.createElement("p");
  p.textContent = state.profile.welcome || state.profile.description || "";
  first.appendChild(p);

  if (Array.isArray(state.profile.suggestions) && state.profile.suggestions.length) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.append("Try: ");
    state.profile.suggestions.forEach((s, i) => {
      if (i) hint.append(" · ");
      const em = document.createElement("em");
      em.textContent = s;
      hint.appendChild(em);
    });
    first.appendChild(hint);
  }
}

function appendError(text) {
  const div = document.createElement("div");
  div.className = "msg msg-error";
  div.textContent = text;
  els.feed().appendChild(div);
}

function showToolIndicator(toolName) {
  const div = document.createElement("div");
  div.className = "msg msg-tool";
  div.innerHTML = `running <strong>${escapeHtml(toolName)}</strong><span class="dots"></span>`;
  els.feed().appendChild(div);
  state.pendingTools.push(div);
  scrollToBottom();
}

function settleToolIndicator(isError) {
  const node = state.pendingTools.shift();
  if (!node) return;
  const dots = node.querySelector(".dots");
  if (dots) {
    const tail = document.createElement("span");
    tail.className = "tool-tail";
    tail.textContent = isError ? " · error" : " · done";
    dots.replaceWith(tail);
  }
  node.classList.add(isError ? "is-error" : "is-done");
}

function settleAllTools(isError) {
  while (state.pendingTools.length) settleToolIndicator(isError);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function scrollToBottom() {
  window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"});
}

init();
