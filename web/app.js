// EasyAgent — chat UI controller for the standalone demo. Vanilla, no build step.
// Talks to the FastAPI backend at /api/chat (SSE) and /api/models.

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);
const API_BASE = window.EASYAGENT_API_BASE
  || window.STRAUSS_API_BASE
  || (LOCAL_HOSTS.has(window.location.hostname) ? "http://127.0.0.1:8001" : "");

const SESSION_KEY = "strauss-session";
const MODEL_KEY = "strauss-model";

const state = {
  models: [],
  profile: null,
  currentModel: null,
  sessionId: null,
  inflight: false,
  toolNode: null,
};

const els = {
  feed: () => document.getElementById("chat-feed"),
  form: () => document.getElementById("chat-form"),
  input: () => document.getElementById("message-input"),
  send: () => document.getElementById("send"),
  modelSelect: () => document.getElementById("model-selector"),
  newChat: () => document.getElementById("new-chat"),
};

// ─── Init ─────────────────────────────────────────────────────────────────

async function init() {
  state.sessionId = sessionStorage.getItem(SESSION_KEY) || crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, state.sessionId);

  await loadProfile();
  await loadModels();

  els.form().addEventListener("submit", onSubmit);
  els.modelSelect().addEventListener("change", onModelChange);
  els.newChat().addEventListener("click", onNewChat);
  els.input().focus();
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

async function loadModels() {
  try {
    const res = await fetch(`${API_BASE}/api/models`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.models = data.models || [];
    if (state.models.length === 0) {
      appendError("No models available — set ANTHROPIC_API_KEY in the server's .env");
      return;
    }
    const stored = sessionStorage.getItem(MODEL_KEY);
    state.currentModel = state.models.some(m => m.id === stored) ? stored : data.default;
    renderModelSelect();
  } catch (err) {
    appendError(`Failed to load models: ${err.message}`);
  }
}

function renderModelSelect() {
  const sel = els.modelSelect();
  sel.innerHTML = "";
  for (const m of state.models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = `${m.vendor} · ${m.label}`;
    if (m.id === state.currentModel) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ─── Event handlers ───────────────────────────────────────────────────────

function onModelChange(e) {
  const newModel = e.target.value;
  if (newModel === state.currentModel) return;
  // Switching models resets the conversation (different message-log shapes).
  state.currentModel = newModel;
  sessionStorage.setItem(MODEL_KEY, newModel);
  resetSession(`switched to ${state.models.find(m => m.id === newModel)?.label || newModel}`);
}

function onNewChat() {
  resetSession("new conversation");
}

function resetSession(reason) {
  state.sessionId = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, state.sessionId);
  // Clear feed but keep the system intro.
  const feed = els.feed();
  while (feed.children.length > 1) feed.removeChild(feed.lastChild);
  appendSystem(`(${reason})`);
}

async function onSubmit(e) {
  e.preventDefault();
  if (state.inflight) return;
  const text = els.input().value.trim();
  if (!text) return;

  els.input().value = "";
  setBusy(true);

  appendUser(text);
  const agentNode = appendAgent();

  try {
    await streamChat(text, agentNode);
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

// ─── SSE stream consumer ──────────────────────────────────────────────────

async function streamChat(message, agentNode) {
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
        handleEvent(ev, payload, agentNode);
      }
    }
  }
}

function handleEvent(type, payload, agentNode) {
  switch (type) {
    case "delta":
      agentNode.textContent += payload.text || "";
      scrollToBottom();
      break;
    case "tool_use_start":
      showToolIndicator(payload.name);
      break;
    case "tool_result":
      clearToolIndicator(payload.is_error);
      break;
    case "done":
      clearToolIndicator(false);
      break;
    case "usage":
      // Reserved for the dev overlay (Phase E). No-op for now.
      break;
    case "error":
      clearToolIndicator(true);
      appendError(payload.message || "stream error");
      break;
  }
}

// ─── DOM helpers ──────────────────────────────────────────────────────────

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

function appendSystem(text) {
  const div = document.createElement("div");
  div.className = "msg msg-system";
  const p = document.createElement("p");
  p.textContent = text;
  div.appendChild(p);
  els.feed().appendChild(div);
  scrollToBottom();
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
  scrollToBottom();
}

function showToolIndicator(toolName) {
  if (state.toolNode) state.toolNode.remove();
  const div = document.createElement("div");
  div.className = "msg msg-tool";
  div.innerHTML = `running <strong>${escapeHtml(toolName)}</strong><span class="dots"></span>`;
  els.feed().appendChild(div);
  state.toolNode = div;
  scrollToBottom();
}

function clearToolIndicator(isError) {
  if (!state.toolNode) return;
  if (isError) state.toolNode.style.color = "#a23636";
  state.toolNode = null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function scrollToBottom() {
  window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"});
}

// ─── Go ───────────────────────────────────────────────────────────────────

init();
