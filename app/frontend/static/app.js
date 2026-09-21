"use strict";

// ---------- 상태 ----------
const state = {
  sessions: [],
  activeSessionId: null,
  streaming: false,
};

// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const sessionListEl = $("session-list");
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("send-btn");
const inputForm = $("input-form");
const activeTitleEl = $("active-title");
const modelBadgeEl = $("model-badge");
const settingsModal = $("settings-modal");

// ---------- API 헬퍼 ----------
async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

function esc(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ---------- 세션 ----------
async function loadSessions() {
  state.sessions = await api("/api/sessions");
  renderSessions();
}

function renderSessions() {
  sessionListEl.innerHTML = "";
  for (const s of state.sessions) {
    const li = document.createElement("li");
    li.classList.toggle("active", s.id === state.activeSessionId);
    const title = s.title || "(제목 없음)";
    const count = s.message_count || 0;
    li.innerHTML = `<div class="s-title">${esc(title)}</div>
                    <div class="s-meta">메시지 ${count}개</div>`;
    li.onclick = () => openSession(s.id);
    sessionListEl.appendChild(li);
  }
}

async function newSession() {
  const s = await api("/api/sessions", { method: "POST", body: "{}" });
  await loadSessions();
  await openSession(s.id);
}

async function openSession(id) {
  state.activeSessionId = id;
  renderSessions();
  const s = state.sessions.find((x) => x.id === id);
  activeTitleEl.textContent = s ? s.title : "대화";
  await loadMessages(id);
}

async function loadMessages(id) {
  messagesEl.innerHTML = "";
  const msgs = await api(`/api/sessions/${id}/messages`);
  for (const m of msgs) appendStoredMessage(m.role, m.content);
  scrollToBottom();
}

function appendStoredMessage(role, content) {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "ai"}`;
  div.innerHTML = `${esc(content) || "(빈 응답)"}`;
  messagesEl.appendChild(div);
}

// ---------- 모델 설정 ----------
async function openSettings() {
  const s = await api("/api/settings");
  $("set-base-url").value = s.base_url || "";
  $("set-api-key").value = s.api_key || "";
  $("set-model").value = s.model || "";
  $("set-temperature").value = s.temperature ?? 0.7;
  $("set-max-tokens").value = s.max_tokens ?? "";
  $("set-system-prompt").value = s.system_prompt || "";
  settingsModal.classList.remove("hidden");
}

async function saveSettings() {
  const body = {
    base_url: $("set-base-url").value.trim(),
    api_key: $("set-api-key").value.trim(),
    model: $("set-model").value.trim(),
    temperature: parseFloat($("set-temperature").value) || 0.7,
    max_tokens: $("set-max-tokens").value ? parseInt($("set-max-tokens").value, 10) : null,
    system_prompt: $("set-system-prompt").value,
  };
  await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
  settingsModal.classList.add("hidden");
  await updateModelBadge();
}

async function updateModelBadge() {
  const s = await api("/api/settings");
  modelBadgeEl.textContent = `${s.model} @ ${s.base_url}`;
}

// ---------- SSE 파싱 (fetch ReadableStream) ----------
function parseBlock(block, handler) {
  let event = "";
  for (const rawLine of block.split("\n")) {
    const line = rawLine.trim();
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:") && event) {
      let data;
      try {
        data = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }
      handler(event, data);
    }
  }
}

// ---------- 메시지 전송 ----------
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || state.streaming) return;
  if (!state.activeSessionId) await newSession();

  appendStoredMessage("user", text);
  inputEl.value = "";
  autoGrow();
  state.streaming = true;
  sendBtn.disabled = true;
  inputEl.disabled = true;

  const aiBubble = document.createElement("div");
  aiBubble.className = "msg ai";
  aiBubble.innerHTML = '<span class="role-tag">assistant</span><span class="typing-dots">…</span>';
  messagesEl.appendChild(aiBubble);
  const statusEl = document.createElement("div");
  statusEl.className = "stream-status";
  messagesEl.appendChild(statusEl);
  scrollToBottom();

  let finished = false;

  function removeTypingDots() {
    const dots = aiBubble.querySelector(".typing-dots");
    if (dots) dots.remove();
  }

  function handleEvent(event, data) {
    if (event === "messages") {
      const content = data.content || "";
      if (content) {
        removeTypingDots();
        aiBubble.innerHTML += esc(content);
        statusEl.textContent = data.metadata?.langgraph_node
          ? `… ${data.metadata.langgraph_node} 응답 생성`
          : "";
        scrollToBottom();
      }
    } else if (event === "updates") {
      statusEl.textContent = `⚙ node=${data.node}, messages=${(data.messages || []).length}`;
    } else if (event === "error") {
      finished = true;
      statusEl.textContent = "";
      aiBubble.classList.add("error");
      aiBubble.innerHTML = "오류: " + esc(data.message || "알 수 없는 오류");
    } else if (event === "done") {
      finished = true;
      removeTypingDots();
      if (data.content) {
        aiBubble.innerHTML = '<span class="role-tag">assistant</span>' + esc(data.content);
      }
      statusEl.textContent = "";
      scrollToBottom();
    }
  }

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.activeSessionId, message: text }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        parseBlock(buf.slice(0, sep), handleEvent);
        buf = buf.slice(sep + 2);
      }
    }
  } catch (err) {
    finished = true;
    aiBubble.classList.add("error");
    aiBubble.innerHTML = "오류: " + esc(err.message);
    statusEl.textContent = "";
  } finally {
    if (!finished) {
      removeTypingDots();
      statusEl.textContent = "⚠ 연결 종료 (done 이벤트 없이 끝남)";
    }
    state.streaming = false;
    sendBtn.disabled = false;
    inputEl.disabled = false;
    inputEl.focus();
    await loadSessions();
  }
}

// ---------- 입력 텍스트영역 ----------
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
}

inputEl.addEventListener("input", autoGrow);
inputForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage();
});
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ---------- 이벤트 바인딩 ----------
$("new-session-btn").onclick = newSession;
$("settings-btn").onclick = openSettings;
$("settings-cancel").onclick = () => settingsModal.classList.add("hidden");
$("settings-save").onclick = saveSettings;
settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) settingsModal.classList.add("hidden");
});

// ---------- 초기화 ----------
(async function init() {
  try {
    await loadSessions();
    await updateModelBadge();
    if (state.sessions.length === 0) {
      await newSession();
    } else {
      await openSession(state.sessions[0].id);
    }
  } catch (err) {
    messagesEl.innerHTML = `<div class="msg error">초기화 오류: ${esc(err.message)}</div>`;
  }
})();