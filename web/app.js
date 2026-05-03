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
  profiles: [],
  currentModel: FIXED_MODEL,
  sessionId: null,
  inflight: false,
  // FIFO of unsettled tool indicators. The backend emits all tool_use_starts
  // for a hop, then all tool_results in matching order.
  pendingTools: [],
  // Per-turn token classifier output, cleared on each user message.
  lastTurnUsage: [],
  // Cumulative across the active session.
  sessionUsage: {input: 0, output: 0, reasoning: 0, cache_read: 0, tool_hops: 0},
  // Per-turn timing for TTFT (first token latency) and TPS (output tok/s).
  turnTiming: {startedAt: null, firstDeltaAt: null, doneAt: null},
};

const els = {
  feed: () => document.getElementById("chat-feed"),
  form: () => document.getElementById("chat-form"),
  input: () => document.getElementById("message-input"),
  send: () => document.getElementById("send"),
  newChat: () => document.getElementById("new-chat"),
  agentSelect: () => document.getElementById("agent-select"),
  agentDesc: () => document.getElementById("agent-info-desc"),
  agentTools: () => document.getElementById("agent-info-tools"),
  agentMcp: () => document.getElementById("agent-info-mcp"),
  agentLast: () => document.getElementById("agent-info-last"),
  agentSpeed: () => document.getElementById("agent-info-speed"),
  agentSession: () => document.getElementById("agent-info-session"),
};

async function init() {
  state.sessionId = sessionStorage.getItem(SESSION_KEY) || crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, state.sessionId);

  setBusy(true);

  await loadProfile();
  await loadProfiles();
  await verifyDeepSeek();

  els.form().addEventListener("submit", onSubmit);
  els.newChat().addEventListener("click", onNewChat);
  els.agentSelect().addEventListener("change", onAgentChange);

  renderAgentInfo();

  if (state.currentModel) {
    setBusy(false);
    els.input().focus();
  }
}

async function loadProfile(profileId) {
  try {
    const url = profileId
      ? `${API_BASE}/api/profile?profile_id=${encodeURIComponent(profileId)}`
      : `${API_BASE}/api/profile`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.profile = await res.json();
    renderProfileIntro();
  } catch {
    // Keep the static intro if the backend is older or temporarily unavailable.
  }
}

async function loadProfiles() {
  try {
    const res = await fetch(`${API_BASE}/api/profiles`);
    if (res.ok) {
      const data = await res.json();
      state.profiles = data.profiles || [];
    }
  } catch {
    // Older backend without /api/profiles — fall through to the single-profile fallback.
  }
  // Always show the dropdown. If listing failed, populate it with just the active
  // profile so the control is discoverable; a backend restart will then surface the rest.
  if (!state.profiles.length && state.profile) {
    state.profiles = [{
      id: state.profile.id,
      label: state.profile.label,
      description: state.profile.description,
      tools: state.profile.tools,
      mcp_servers: state.profile.mcp_servers || [],
    }];
  }
  populateAgentSelect();
}

function populateAgentSelect() {
  const sel = els.agentSelect();
  if (!sel || !state.profiles.length) return;
  sel.innerHTML = "";
  for (const p of state.profiles) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label;
    if (state.profile && state.profile.id === p.id) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function onAgentChange(e) {
  const newId = e.target.value;
  if (!newId || (state.profile && state.profile.id === newId)) return;
  setBusy(true);
  try {
    await loadProfile(newId);
    state.sessionUsage = {input: 0, output: 0, reasoning: 0, cache_read: 0, tool_hops: 0};
    state.lastTurnUsage = [];
    state.turnTiming = {startedAt: null, firstDeltaAt: null, doneAt: null};
    resetSession(`switched to ${state.profile?.label || newId}`);
    renderAgentInfo();
  } finally {
    setBusy(false);
    els.input().focus();
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
  state.lastTurnUsage = [];
  state.turnTiming = {startedAt: performance.now(), firstDeltaAt: null, doneAt: null};
  renderAgentInfo();

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
      if (state.turnTiming.startedAt && !state.turnTiming.firstDeltaAt) {
        state.turnTiming.firstDeltaAt = performance.now();
      }
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
      if (state.turnTiming.startedAt && !state.turnTiming.firstDeltaAt) {
        state.turnTiming.firstDeltaAt = performance.now();
      }
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
      state.turnTiming.doneAt = performance.now();
      renderAgentInfo();
      break;
    case "usage":
      accumulateUsage(payload);
      break;
    case "error":
      settleAllTools(true);
      state.turnTiming.doneAt = performance.now();
      appendError(payload.message || "stream error");
      renderAgentInfo();
      break;
  }
}

function renderMarkdown(text) {
  return (window.EasyAgentMarkdown?.renderMarkdown ?? escapeHtml)(text);
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

function accumulateUsage(payload) {
  state.lastTurnUsage.push(payload);
  state.sessionUsage.input += Number(payload.input_tokens) || 0;
  state.sessionUsage.output += Number(payload.output_tokens) || 0;
  state.sessionUsage.reasoning += Number(payload.reasoning_tokens) || 0;
  state.sessionUsage.cache_read += Number(payload.cache_read_input_tokens) || 0;
  state.sessionUsage.tool_hops += 1;
  renderAgentInfo();
}

function renderAgentInfo() {
  const p = state.profile;
  if (els.agentDesc()) {
    els.agentDesc().textContent = (p && p.description) || "—";
  }
  if (els.agentTools()) {
    const tools = (p && Array.isArray(p.tools)) ? p.tools : [];
    els.agentTools().textContent = tools.length ? tools.join(", ") : "—";
  }
  if (els.agentMcp()) {
    const mcp = (p && Array.isArray(p.mcp_servers)) ? p.mcp_servers : [];
    els.agentMcp().textContent = mcp.length ? mcp.join(", ") : "none configured";
  }
  if (els.agentLast()) {
    if (!state.lastTurnUsage.length) {
      els.agentLast().textContent = "—";
    } else {
      els.agentLast().textContent = state.lastTurnUsage
        .map(u => {
          const inT = Number(u.input_tokens) || 0;
          const outT = Number(u.output_tokens) || 0;
          return `hop ${u.hop} (${u.category || "?"}) ${inT}/${outT}`;
        })
        .join(" · ");
    }
  }
  if (els.agentSpeed()) {
    const t = state.turnTiming;
    if (!t.startedAt) {
      els.agentSpeed().textContent = "—";
    } else {
      const parts = [];
      if (t.firstDeltaAt) {
        parts.push(`TTFT ${Math.round(t.firstDeltaAt - t.startedAt)} ms`);
      }
      if (t.doneAt && t.firstDeltaAt) {
        const totalOut = state.lastTurnUsage.reduce(
          (sum, u) => sum + (Number(u.output_tokens) || 0), 0,
        );
        const elapsedSec = (t.doneAt - t.firstDeltaAt) / 1000;
        if (totalOut > 0 && elapsedSec > 0) {
          parts.push(`TPS ${Math.round(totalOut / elapsedSec)} t/s`);
        }
      }
      els.agentSpeed().textContent = parts.length ? parts.join(" · ") : "measuring…";
    }
  }
  if (els.agentSession()) {
    const s = state.sessionUsage;
    const reasoningPart = s.reasoning > 0 ? ` (reasoning ${s.reasoning})` : "";
    const hopLabel = s.tool_hops === 1 ? "hop" : "hops";
    els.agentSession().textContent =
      `in ${s.input} · out ${s.output}${reasoningPart} · cache ${s.cache_read} · ${s.tool_hops} ${hopLabel}`;
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
