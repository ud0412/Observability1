"use strict";

// ---------- 상태 ----------
const state = {
  sessions: [],
  models: [],
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

// ---------- 모델 설정 (다중 모델 관리) ----------
let editingModelId = null; // null = 새 모델 작성 모드

async function loadModels() {
  state.models = await api("/api/models");
  return state.models;
}

async function updateModelBadge() {
  const models = await loadModels();
  const active = models.find((m) => m.is_active) || models[0];
  modelBadgeEl.textContent = active ? `${active.name} · ${active.model}` : "모델 미설정";
}

function fillForm(m) {
  $("set-name").value = m?.name ?? "";
  $("set-base-url").value = m?.base_url ?? "";
  $("set-api-key").value = m?.api_key ?? "";
  $("set-model").value = m?.model ?? "";
  $("set-temperature").value = m?.temperature ?? 0.7;
  $("set-max-tokens").value = m?.max_tokens ?? "";
  $("set-system-prompt").value = m?.system_prompt ?? "";
}

function renderModelSelect(selectId) {
  const sel = $("model-select");
  sel.innerHTML = "";
  if (state.models.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(저장된 모델 없음)";
    sel.appendChild(opt);
    return;
  }
  for (const m of state.models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.name;
    sel.appendChild(opt);
  }
  sel.value = selectId ?? "";
}

async function openSettings() {
  await loadModels();
  const active = state.models.find((m) => m.is_active);
  const target = active || state.models[0];
  renderModelSelect(target?.id ?? "");
  if (target) {
    editingModelId = target.id;
    fillForm(target);
  } else {
    editingModelId = null;
    fillForm(null);
  }
  settingsModal.classList.remove("hidden");
}

async function selectModelById(id) {
  const m = state.models.find((x) => x.id == id);
  if (!m) return;
  editingModelId = m.id;
  fillForm(m);
  // 선택 즉시 활성 모델로 전환
  await api("/api/active-model", { method: "PUT", body: JSON.stringify({ model_id: m.id }) });
  await updateModelBadge();
}

function startNewModel() {
  editingModelId = null;
  fillForm(null);
  renderModelSelect("");
  $("set-name").focus();
}

async function saveSettings() {
  const body = {
    name: $("set-name").value.trim(),
    base_url: $("set-base-url").value.trim(),
    api_key: $("set-api-key").value.trim(),
    model: $("set-model").value.trim(),
    temperature: parseFloat($("set-temperature").value) || 0.7,
    max_tokens: $("set-max-tokens").value ? parseInt($("set-max-tokens").value, 10) : null,
    system_prompt: $("set-system-prompt").value,
  };
  if (!body.name) body.name = body.model || "모델"; // 이름 미입력 시 model 값 사용
  if (editingModelId) {
    await api(`/api/models/${editingModelId}`, { method: "PUT", body: JSON.stringify(body) });
  } else {
    await api("/api/models", { method: "POST", body: JSON.stringify(body) }); // 생성 시 자동 활성화
  }
  settingsModal.classList.add("hidden");
  await updateModelBadge();
}

async function deleteSelectedModel() {
  if (editingModelId === null) return;
  const name = state.models.find((x) => x.id == editingModelId)?.name || "";
  if (!confirm(`'${name}' 모델을 삭제할까요?`)) return;
  await api(`/api/models/${editingModelId}`, { method: "DELETE" });
  await openSettings();
  await updateModelBadge();
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
$("model-add-btn").onclick = startNewModel;
$("model-del-btn").onclick = deleteSelectedModel;
$("model-select").onchange = (e) => {
  if (e.target.value !== "") selectModelById(e.target.value);
};
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