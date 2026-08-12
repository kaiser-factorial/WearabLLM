const els = {
  status: document.querySelector("#status"),
  deviceList: document.querySelector("#device-list"),
  expressionBanner: document.querySelector("#expression-banner"),
  expressionGlow: document.querySelector("#expression-glow"),
  expressionMeta: document.querySelector("#expression-meta"),
  expressionText: document.querySelector("#expression-text"),
  conversationList: document.querySelector("#conversation-list"),
  newConversation: document.querySelector("#new-conversation"),
  archiveToggle: document.querySelector("#archive-toggle"),
  archiveCount: document.querySelector("#archive-count"),
  thread: document.querySelector("#thread"),
  threadTitle: document.querySelector("#thread-title"),
  viewEyebrow: document.querySelector("#view-eyebrow"),
  deliverWaveshare: document.querySelector("#deliver-waveshare"),
  replyForm: document.querySelector("#reply-form"),
  replyInput: document.querySelector("#reply-input"),
  send: document.querySelector("#send"),
  composerStatus: document.querySelector("#composer-status"),
  composerHint: document.querySelector("#composer-hint"),
  actionDebug: document.querySelector("#action-debug"),
  chatView: document.querySelector("#chat-view"),
  commandView: document.querySelector("#command-view"),
  sensorView: document.querySelector("#sensor-view"),
  sensorConnect: document.querySelector("#sensor-connect"),
  sensorTakeReading: document.querySelector("#sensor-take-reading"),
  sensorConnectionBadge: document.querySelector("#sensor-connection-badge"),
  sensorStatus: document.querySelector("#sensor-status"),
  sensorCelsius: document.querySelector("#sensor-celsius"),
  sensorFahrenheit: document.querySelector("#sensor-fahrenheit"),
  sensorReadingMeta: document.querySelector("#sensor-reading-meta"),
  sensorHistory: document.querySelector("#sensor-history"),
  sensorClear: document.querySelector("#sensor-clear"),
  settingsModal: document.querySelector("#settings-modal"),
  settingsClose: document.querySelector("#settings-close"),
  configForm: document.querySelector("#config-form"),
  configSource: document.querySelector("#config-source"),
  configStatus: document.querySelector("#config-status"),
  configReload: document.querySelector("#config-reload"),
  catalogRefresh: document.querySelector("#catalog-refresh"),
  catalogStatus: document.querySelector("#catalog-status"),
  cfgApiKey: document.querySelector("#cfg-api-key"),
  apiKeySave: document.querySelector("#api-key-save"),
  cfgSystemPrompt: document.querySelector("#cfg-system-prompt"),
  cfgLlmModel: document.querySelector("#cfg-llm-model"),
  cfgTtsModel: document.querySelector("#cfg-tts-model"),
  cfgTtsVoice: document.querySelector("#cfg-tts-voice"),
  cfgTtsInstructions: document.querySelector("#cfg-tts-instructions"),
  allowBrowserSpeech: document.querySelector("#allow-browser-speech"),
  cfgUpdated: document.querySelector("#cfg-updated"),
  deployRepo: document.querySelector("#deploy-repo"),
  deployBridge: document.querySelector("#deploy-bridge"),
  deployAvailability: document.querySelector("#deploy-availability"),
  deployDry: document.querySelector("#deploy-dry"),
  deployLive: document.querySelector("#deploy-live"),
  deployLog: document.querySelector("#deploy-log"),
};

const state = {
  devices: [],
  sessions: [],
  selectedSessionId: "",
  activeSessionId: "",
  deliverToWaveshare: false,
  latestActionId: "",
  turns: [],
  session: null,
  view: "chat",
  polling: false,
  sending: false,
  thinking: false,
  pausePolling: false,
  lastStatus: "",
  devicesSignature: "",
  sessionsSignature: "",
  turnsSignature: "",
  bootstrap: null,
  agentConfig: null,
  modelCatalog: null,
  showArchived: false,
  receivingBodyAction: false,
  expressionTimer: null,
  sensorDevice: null,
  sensorCharacteristic: null,
  sensorCommandCharacteristic: null,
  sensorReadings: [],
  sensorSeenActionIds: new Set(),
  sensorLastSequence: null,
};

const SENSOR_SERVICE_UUID = "7b8f2b10-3a42-4d4e-9fd4-8b5b86d8a101";
const SENSOR_READING_UUID = "7b8f2b11-3a42-4d4e-9fd4-8b5b86d8a101";
const SENSOR_COMMAND_UUID = "7b8f2b12-3a42-4d4e-9fd4-8b5b86d8a101";

const EXPRESSION_COLORS = {
  G: "#22c55e",
  R: "#ef4444",
  Y: "#eab308",
  B: "#3b82f6",
  P: "#a855f7",
};

function setStatus(kind, text) {
  const key = `${kind}:${text}`;
  if (state.lastStatus === key) return;
  state.lastStatus = key;
  els.status.className = `status ${kind}`;
  const dot = document.createElement("span");
  els.status.replaceChildren(dot, document.createTextNode(` ${text}`));
}

function kindFor(deviceId) {
  const found = state.devices.find((item) => item.id === deviceId);
  return found?.kind || "custom";
}

function labelFor(deviceId) {
  const found = state.devices.find((item) => item.id === deviceId);
  return found?.label || deviceId;
}

function shortId(value) {
  if (!value) return "—";
  const text = String(value);
  return text.length > 12 ? `${text.slice(0, 8)}…` : text;
}

function formatTime(value, { relative = false } = {}) {
  if (!value) return "—";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    if (relative) {
      const seconds = Math.round((Date.now() - date.getTime()) / 1000);
      if (seconds < 45) return "just now";
      if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
      if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
      if (seconds < 86400 * 7) return `${Math.round(seconds / 86400)}d ago`;
    }
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function formatTimeTitle(value) {
  if (!value) return "";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return String(value);
  }
}

function mergeDevices(known = [], remote = []) {
  const byId = new Map();
  for (const item of [...known, ...remote]) {
    if (!item?.id) continue;
    if (item.id === "local-bridge") continue;
    const previous = byId.get(item.id) || {};
    byId.set(item.id, {
      ...previous,
      ...item,
      seen: item.seen == null ? Boolean(previous.seen) : Boolean(item.seen),
    });
  }
  const order = {
    "wearabllm-esp32": 0,
    "wearabllm-android": 1,
    "web-console": 2,
    "ducati-temp-sensor": 3,
    "wearabllm-wearable": 4,
  };
  return Array.from(byId.values()).sort((a, b) =>
    (order[a.id] ?? 100).toString().localeCompare((order[b.id] ?? 100).toString())
    || a.label.localeCompare(b.label)
  );
}

function devicesSignature(devices) {
  return JSON.stringify(devices.map((d) => [d.id, d.label, d.kind, d.status, Boolean(d.seen)]));
}

function turnsSignature(turns) {
  return `${turns.map((t) => `${t.id}|${t.role}|${t.device_id}|${t.content}|${JSON.stringify(t.metadata || {})}|${t.created_at}`).join("\n")}|thinking:${state.thinking}`;
}

function nearBottom(el, px = 96) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < px;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderDevices({ force = false } = {}) {
  const signature = devicesSignature(state.devices);
  if (!force && signature === state.devicesSignature) return;
  state.devicesSignature = signature;

  els.deviceList.replaceChildren(
    ...state.devices.map((device) => {
      const card = document.createElement("article");
      card.className = [
        "body-status",
        device.kind || "custom",
        device.status === "planned" ? "planned" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const stateLabel = device.status === "planned"
        ? "Planned"
        : device.seen ? "Live" : "Idle";
      card.innerHTML = `
        <span class="device-dot" aria-hidden="true"></span>
        <strong>${escapeHtml(device.label)}</strong>
        <span class="device-badge">${stateLabel}</span>
      `;
      return card;
    })
  );
}

function conversationTitle(session) {
  if (!session) return "Conversation";
  if (session.title) return String(session.title);
  if (session.id === state.activeSessionId) return "Current conversation";
  if (session.summary) {
    const summary = String(session.summary).replace(/\s+/g, " ").trim();
    if (summary) return summary.length > 48 ? `${summary.slice(0, 47)}…` : summary;
  }
  return formatTime(session.started_at || session.last_turn_at);
}

function renderConversations({ force = false } = {}) {
  const archived = state.sessions.filter((item) => Boolean(item.archived_at));
  const visibleSessions = state.sessions.filter(
    (item) => Boolean(item.archived_at) === state.showArchived
  );
  els.archiveCount.textContent = String(archived.length);
  els.archiveToggle.firstChild.textContent = state.showArchived ? "← Conversations " : "Archive ";
  els.archiveToggle.classList.toggle("active", state.showArchived);
  const signature = JSON.stringify({
    selected: state.selectedSessionId,
    active: state.activeSessionId,
    showArchived: state.showArchived,
    sessions: visibleSessions.map((item) => [
      item.id, item.started_at, item.last_turn_at, item.ended_at, item.archived_at, item.summary, item.title,
    ]),
  });
  if (!force && signature === state.sessionsSignature) return;
  state.sessionsSignature = signature;

  if (!visibleSessions.length) {
    els.conversationList.innerHTML = `<p class="conversation-empty">${
      state.showArchived ? "No archived conversations yet." : "No conversations yet. Use + to begin one."
    }</p>`;
    return;
  }
  els.conversationList.replaceChildren(
    ...visibleSessions.map((session) => {
      const row = document.createElement("div");
      row.className = "conversation-row";
      const button = document.createElement("button");
      button.type = "button";
      button.className = `conversation-item${session.id === state.selectedSessionId ? " active" : ""}`;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", session.id === state.selectedSessionId ? "true" : "false");
      const current = session.id === state.activeSessionId;
      button.innerHTML = `
        <span class="conversation-copy">
          <strong>${escapeHtml(conversationTitle(session))}</strong>
          <small>${escapeHtml(formatTime(session.last_turn_at || session.started_at, { relative: true }))}</small>
        </span>
        ${current ? '<span class="current-badge">Live</span>' : session.archived_at ? '<span class="archive-badge">Archived</span>' : ""}
      `;
      button.addEventListener("click", () => {
        if (state.selectedSessionId === session.id) return;
        state.selectedSessionId = session.id;
        state.turnsSignature = "";
        renderConversations({ force: true });
        refreshConversation({ forceRender: true });
      });
      row.append(button);
      const actions = document.createElement("details");
      actions.className = "conversation-menu";
      const summary = document.createElement("summary");
      summary.textContent = "…";
      summary.title = `Actions for ${conversationTitle(session)}`;
      summary.setAttribute("aria-label", summary.title);
      const menuItems = document.createElement("div");
      menuItems.className = "conversation-menu-items";
      actions.append(summary, menuItems);
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "conversation-action rename-conversation";
      rename.textContent = "Rename";
      rename.title = `Rename ${conversationTitle(session)}`;
      rename.setAttribute("aria-label", rename.title);
      rename.addEventListener("click", () => void renameConversation(session));
      menuItems.append(rename);
      if (!session.archived_at) {
        const archive = document.createElement("button");
        archive.type = "button";
        archive.className = "conversation-action archive-conversation";
        archive.textContent = "Archive";
        archive.title = `Archive ${conversationTitle(session)}`;
        archive.setAttribute("aria-label", archive.title);
        archive.addEventListener("click", () => void archiveConversation(session));
        menuItems.append(archive);
      }
      row.append(actions);
      return row;
    })
  );
}

async function renameConversation(session) {
  const title = window.prompt("Conversation name", session.title || conversationTitle(session));
  if (title == null) return;
  const cleanTitle = title.replace(/\s+/g, " ").trim();
  if (!cleanTitle) {
    els.composerStatus.textContent = "Conversation name cannot be empty";
    return;
  }
  try {
    await fetchJson(`/api/sessions/${encodeURIComponent(session.id)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: cleanTitle }),
    });
    state.sessionsSignature = "";
    await refreshConversation({ forceRender: true });
    els.composerStatus.textContent = "Conversation renamed";
  } catch (error) {
    els.composerStatus.textContent = `Rename failed: ${error.message || "unknown error"}`;
  }
}

function toggleArchiveView() {
  state.showArchived = !state.showArchived;
  const candidates = state.sessions.filter(
    (item) => Boolean(item.archived_at) === state.showArchived
  );
  state.selectedSessionId = state.showArchived
    ? candidates[0]?.id || ""
    : state.activeSessionId || candidates[0]?.id || "";
  state.sessionsSignature = "";
  state.turnsSignature = "";
  renderConversations({ force: true });
  refreshConversation({ forceRender: true });
}

async function archiveConversation(session) {
  const isCurrent = session.id === state.activeSessionId;
  const prompt = isCurrent
    ? "Archive the current conversation and start a new one? The transcript will remain available here."
    : "Archive this conversation? The transcript will remain available here.";
  if (!window.confirm(prompt)) return;
  els.composerStatus.textContent = "Archiving conversation…";
  try {
    const payload = await fetchJson(`/api/sessions/${encodeURIComponent(session.id)}/archive`, {
      method: "POST",
    });
    if (state.selectedSessionId === session.id && payload.active_session_id) {
      state.selectedSessionId = payload.active_session_id;
    }
    state.showArchived = false;
    state.sessionsSignature = "";
    state.turnsSignature = "";
    await refreshConversation({ forceRender: true });
    els.composerStatus.textContent = "Conversation archived";
  } catch (error) {
    els.composerStatus.textContent = `Archive failed: ${error.message || "unknown error"}`;
  }
}

function renderThread({ force = false } = {}) {
  const signature = turnsSignature(state.turns);
  if (!force && signature === state.turnsSignature) return;

  const stick = force || nearBottom(els.thread) || state.turnsSignature === "";
  const previousScroll = els.thread.scrollTop;
  state.turnsSignature = signature;

  if (!state.turns.length) {
    els.thread.innerHTML = `
      <div class="empty">
        <strong>Quiet sphere</strong>
        <p>No turns yet for this view. Speak through Waveshare, or reply from this console to start the shared thread.</p>
      </div>
    `;
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const turn of state.turns) {
    const role = turn.role === "assistant" ? "assistant" : "user";
    const deviceId = turn.device_id || "unknown";
    const kind = kindFor(deviceId);
    const article = document.createElement("article");
    article.className = `bubble ${role}`;
    article.dataset.turnId = String(turn.id ?? "");
    const when = formatTime(turn.created_at);
    const whenTitle = formatTimeTitle(turn.created_at);
    article.innerHTML = `
      <div class="bubble-meta">
        <span class="device-pill ${kind}">${escapeHtml(labelFor(deviceId))}</span>
        <span>${role === "assistant" ? "WearabLLM" : "You"}</span>
        <time class="timestamp" datetime="${escapeHtml(turn.created_at || "")}" title="${escapeHtml(whenTitle)}">${escapeHtml(when)}</time>
      </div>
      <p></p>
    `;
    article.querySelector("p").textContent = turn.content || "";
    const toolResults = Array.isArray(turn.metadata?.tool_results) ? turn.metadata.tool_results : [];
    if (toolResults.length) {
      const activity = document.createElement("div");
      activity.className = "bubble-tools";
      for (const result of toolResults) {
        if (!result || typeof result !== "object" || !result.summary) continue;
        const row = document.createElement("div");
        row.className = `bubble-tool ${result.ok ? "ok" : "failed"}`;
        row.textContent = String(result.summary);
        activity.append(row);
      }
      if (activity.children.length) article.append(activity);
    }
    const sources = Array.isArray(turn.metadata?.sources) ? turn.metadata.sources : [];
    if (sources.length) {
      const list = document.createElement("div");
      list.className = "bubble-sources";
      const label = document.createElement("strong");
      label.textContent = "Sources";
      list.append(label);
      for (const [index, source] of sources.entries()) {
        if (!source || typeof source !== "object" || !source.url) continue;
        const link = document.createElement("a");
        link.href = String(source.url);
        link.target = "_blank";
        link.rel = "noreferrer noopener";
        link.textContent = `${index + 1}. ${source.title || source.url}`;
        list.append(link);
      }
      if (list.querySelector("a")) article.append(list);
    }
    fragment.append(article);
  }
  if (state.thinking) {
    const article = document.createElement("article");
    article.className = "bubble assistant thinking-bubble";
    article.innerHTML = `
      <div class="bubble-meta">
        <span class="device-pill custom">Sphere</span>
        <span>WearabLLM</span>
      </div>
      <p>Thinking<span class="thinking-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></p>
    `;
    fragment.append(article);
  }
  els.thread.replaceChildren(fragment);
  els.thread.scrollTop = stick ? els.thread.scrollHeight : previousScroll;
}

function renderEvents(rows) {
  let added = 0;
  for (const row of rows.slice().reverse()) {
    if (state.seenEvents.has(row.id)) continue;
    state.seenEvents.add(row.id);
    added += 1;
    const when = formatTime(row.created_at, { relative: true });
    const whenTitle = formatTimeTitle(row.created_at);
    const item = document.createElement("article");
    item.className = "event-item";
    item.dataset.createdAt = row.created_at || "";
    item.innerHTML = `
      <div class="meta">
        <span class="cmd">${escapeHtml(row.command || "—")}</span>
        <time class="timestamp" datetime="${escapeHtml(row.created_at || "")}" title="${escapeHtml(whenTitle)}">${escapeHtml(when)}</time>
      </div>
      <div class="event-device">${escapeHtml(labelFor(row.device_id || "device"))}</div>
      <div class="event-text">${escapeHtml(row.transcript || "")}</div>
    `;
    els.eventFeed.prepend(item);
  }
  while (els.eventFeed.children.length > 30) {
    els.eventFeed.lastElementChild.remove();
  }
  if (added > 0) refreshEventTimestamps();
}

function refreshEventTimestamps() {
  for (const item of els.eventFeed.querySelectorAll(".event-item")) {
    const createdAt = item.dataset.createdAt;
    const stamp = item.querySelector("time.timestamp");
    if (!stamp || !createdAt) continue;
    const next = formatTime(createdAt, { relative: true });
    if (stamp.textContent !== next) stamp.textContent = next;
    stamp.title = formatTimeTitle(createdAt);
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function sendHeartbeat() {
  try {
    await fetchJson("/api/heartbeat", { method: "POST" });
  } catch (error) {
    console.warn("presence heartbeat", error);
  }
}

function normalizedExpression(action) {
  const expression = action?.expression && typeof action.expression === "object" ? action.expression : {};
  return {
    command: String(expression.command || action?.command || "BS").toUpperCase(),
    text: String(expression.text || action?.reply || ""),
    channels: Array.isArray(expression.channels) ? expression.channels.map(String) : ["visual", "display", "audio"],
  };
}

function renderExpression(action) {
  const expression = normalizedExpression(action);
  const color = EXPRESSION_COLORS[expression.command[0]] || EXPRESSION_COLORS.B;
  els.expressionBanner.style.setProperty("--expression-color", color);
  els.expressionMeta.textContent = `Sphere · ${expression.command} · ${expression.channels.join(" + ")}`;
  els.expressionText.textContent = expression.text;
  els.expressionBanner.hidden = false;
  if (state.expressionTimer) clearTimeout(state.expressionTimer);
  state.expressionTimer = setTimeout(() => { els.expressionBanner.hidden = true; }, 8000);
}

async function acknowledgeBodyAction(actionId, status, error = "") {
  return fetchJson(`/api/body-actions/${encodeURIComponent(actionId)}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, error: error || undefined }),
  });
}

async function receiveBodyAction() {
  if (state.receivingBodyAction || document.hidden) return;
  state.receivingBodyAction = true;
  try {
    const payload = await fetchJson("/api/body-actions/next");
    const action = payload.action;
    if (!action) return;
    const expression = normalizedExpression(action);
    renderExpression(action);
    await acknowledgeBodyAction(action.id, "delivered");
    await acknowledgeBodyAction(action.id, "rendered");
    const speechEnabled = els.allowBrowserSpeech.checked && expression.channels.includes("audio");
    if (!speechEnabled || !("speechSynthesis" in window)) {
      await acknowledgeBodyAction(action.id, "completed");
      return;
    }
    await new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(expression.text);
      utterance.onstart = () => { void acknowledgeBodyAction(action.id, "tts_started"); };
      utterance.onend = () => { void acknowledgeBodyAction(action.id, "played").finally(resolve); };
      utterance.onerror = (event) => {
        void acknowledgeBodyAction(action.id, "failed", event.error || "Browser speech failed").finally(resolve);
      };
      window.speechSynthesis.speak(utterance);
    });
  } catch (error) {
    console.warn("body action", error);
  } finally {
    state.receivingBodyAction = false;
  }
}

async function refreshConversation({ forceRender = false } = {}) {
  if (state.polling) return;
  if (state.pausePolling && !forceRender) return;
  state.polling = true;
  try {
    const params = new URLSearchParams({ limit: "300" });
    if (state.selectedSessionId) params.set("session_id", state.selectedSessionId);
    const payload = await fetchJson(`/api/conversation?${params.toString()}`);
    state.turns = Array.isArray(payload.turns) ? payload.turns : [];
    state.session = payload.session || null;
    state.activeSessionId = payload.active_session_id || "";
    state.sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    if (!state.selectedSessionId && state.activeSessionId) {
      state.selectedSessionId = state.activeSessionId;
    }
    state.devices = mergeDevices(state.devices, payload.devices || []);
    renderDevices();
    renderConversations();
    renderThread({ force: forceRender });
    const selected = state.sessions.find((item) => item.id === state.selectedSessionId) || state.session;
    const isCurrent = !state.selectedSessionId || state.selectedSessionId === state.activeSessionId;
    if (state.view === "chat") {
      els.threadTitle.textContent = conversationTitle(selected);
    }
    els.replyInput.disabled = !isCurrent;
    els.send.disabled = !isCurrent || state.sending;
    els.deliverWaveshare.disabled = !isCurrent;
    els.replyInput.placeholder = isCurrent
      ? "Type to continue the shared conversation…"
      : "This conversation is archived. Start a new one to reply.";
    if (!isCurrent) els.composerStatus.textContent = "Archived conversation · read only";
    setStatus("online", "Live");
    if (state.view === "sensor") void refreshTemperatureActions();
  } catch (error) {
    console.error(error);
    setStatus("offline", error.message || "Retrying");
  } finally {
    state.polling = false;
  }
}

async function refreshTemperatureActions() {
  try {
    const payload = await fetchJson(
      "/api/interactions?target_device_id=ducati-temp-sensor&limit=50",
    );
    const actions = Array.isArray(payload.actions) ? [...payload.actions].reverse() : [];
    let latest = null;
    for (const action of actions) {
      if (!["temperature_measurement", "sensor_read"].includes(action?.action_type) || action?.status !== "completed") continue;
      if (!action.result || state.sensorSeenActionIds.has(action.id)) continue;
      state.sensorSeenActionIds.add(action.id);
      const genericTemperature = Array.isArray(action.result.readings)
        ? action.result.readings.find((item) => item?.sensor_id === "ambient_temperature")
        : null;
      const celsius = Number(genericTemperature?.value ?? action.result.celsius);
      if (!Number.isFinite(celsius)) continue;
      const reading = {
        sequence: Number(action.result.sequence),
        celsius,
        fahrenheit: Number(action.result.fahrenheit ?? ((celsius * 9) / 5 + 32)),
        rawAdc: Number(action.result.raw_adc),
        uptimeMs: Number(action.result.uptime_ms),
        time: new Date(action.result.measured_at || action.completed_at || Date.now()),
        source: "Wi-Fi",
      };
      const alreadyReceivedOverBle = state.sensorReadings.some(
        (item) => item.sequence === reading.sequence && item.uptimeMs === reading.uptimeMs,
      );
      if (!alreadyReceivedOverBle) {
        state.sensorReadings = [reading, ...state.sensorReadings].slice(0, 20);
      }
      latest = reading;
    }
    if (latest) {
      els.sensorCelsius.textContent = latest.celsius.toFixed(2);
      els.sensorFahrenheit.textContent = `${latest.fahrenheit.toFixed(2)} °F`;
      els.sensorReadingMeta.textContent = `Wi-Fi reading ${latest.sequence} · raw ADC ${latest.rawAdc} · board up ${Math.round(latest.uptimeMs / 1000)}s`;
      renderSensorHistory();
    }
  } catch (error) {
    console.warn("temperature actions", error);
  }
}

async function refreshEvents() {
  if (state.pausePolling) return;
  try {
    const payload = await fetchJson("/api/transcripts?limit=20");
    renderEvents(Array.isArray(payload.transcripts) ? payload.transcripts : []);
  } catch (error) {
    if (!String(error.message).includes("transcripts_not_configured")) {
      console.warn("event feed", error);
    }
  }
}

async function refreshLatestAction() {
  if (!state.latestActionId) return;
  try {
    const payload = await fetchJson(`/api/interactions/${encodeURIComponent(state.latestActionId)}`);
    if (!payload.action) return;
    const action = payload.action;
    renderActionDelivery(action);
    if (["completed", "played", "failed", "expired"].includes(action.status)) state.latestActionId = "";
  } catch (error) {
    els.composerStatus.textContent = `Could not check Waveshare delivery: ${error.message || "unknown error"}`;
  }
}

function renderActionDelivery(action) {
  const messages = {
    queued: "Bridge queued this response. Waveshare has not claimed it yet.",
    dispatched: "Waveshare claimed this response. Render and playback are not confirmed yet.",
    delivered: "Waveshare acknowledged receipt. Render and playback are unverified by that firmware.",
    rendered: "Waveshare reported its display/LED update. Audio completion is not confirmed yet.",
    tts_started: "Waveshare reported TTS playback started.",
    completed: "The target body completed its requested non-audio expression.",
    played: "Waveshare reported TTS playback completed.",
    failed: `Waveshare reported delivery failure${action.error ? `: ${action.error}` : "."}`,
    expired: "This expression expired before the target body could render it.",
  };
  els.composerStatus.textContent = messages[action.status] || `Waveshare action state: ${action.status || "unknown"}`;
  const target = action.target_device_id || "unknown";
  const details = [
    `action ${action.id || "unknown"} → ${target}`,
    `created ${formatTimeTitle(action.created_at)} · updated ${formatTimeTitle(action.updated_at)}`,
    `attempts ${action.attempts ?? 0}${action.error ? ` · error ${action.error}` : ""}`,
  ];
  els.actionDebug.textContent = details.join("\n");
  els.actionDebug.hidden = false;
}

function setView(view) {
  state.view = view;
  const views = {
    chat: els.chatView,
    command: els.commandView,
    sensor: els.sensorView,
  };
  for (const [name, element] of Object.entries(views)) {
    const active = name === view;
    element.classList.toggle("active", active);
    element.hidden = !active;
  }
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (view === "chat") {
    els.viewEyebrow.textContent = "Conversation";
    const selected = state.sessions.find((item) => item.id === state.selectedSessionId) || state.session;
    els.threadTitle.textContent = conversationTitle(selected);
  } else if (view === "sensor") {
    els.viewEyebrow.textContent = "Local Bluetooth";
    els.threadTitle.textContent = "Temperature sensor";
  } else {
    els.viewEyebrow.textContent = "Deployment";
    els.threadTitle.textContent = "Hugging Face Space";
  }
}

function setSensorConnectionStatus(kind, label, detail) {
  els.sensorConnectionBadge.className = `sensor-badge ${kind}`;
  const dot = document.createElement("span");
  dot.setAttribute("aria-hidden", "true");
  els.sensorConnectionBadge.replaceChildren(dot, document.createTextNode(` ${label}`));
  els.sensorStatus.textContent = detail;
}

function renderSensorHistory() {
  els.sensorClear.disabled = state.sensorReadings.length === 0;
  if (!state.sensorReadings.length) {
    const row = document.createElement("tr");
    row.className = "sensor-history-empty";
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.textContent = "No readings yet.";
    row.append(cell);
    els.sensorHistory.replaceChildren(row);
    return;
  }

  els.sensorHistory.replaceChildren(
    ...state.sensorReadings.map((reading) => {
      const row = document.createElement("tr");
      const values = [
        reading.time.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }),
        `${reading.celsius.toFixed(2)} °C`,
        `${reading.fahrenheit.toFixed(2)} °F`,
        String(reading.rawAdc),
      ];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      return row;
    })
  );
}

function handleSensorPacket(dataView) {
  const packet = window.WearabLLMSensorProtocol.decode(dataView);
  if (packet.kind === "packet-error") {
    setSensorConnectionStatus("error", "Packet error", "The board sent an incomplete reading. Press the button again.");
    return;
  }
  if (packet.kind === "version-error") {
    setSensorConnectionStatus("error", "Version error", `Unsupported sensor packet version ${packet.version}.`);
    return;
  }
  if (packet.kind === "waiting") {
    setSensorConnectionStatus("connected", "Connected", "Connected. Press the physical button to take a reading.");
    return;
  }
  if (state.sensorLastSequence === packet.sequence) return;
  state.sensorLastSequence = packet.sequence;

  if (packet.kind === "sensor-error") {
    setSensorConnectionStatus(
      "error",
      "Sensor error",
      `Reading ${packet.sequence} was outside the valid range (raw ADC ${packet.rawAdc}). Check the thermistor connection.`,
    );
    els.sensorReadingMeta.textContent = `Invalid reading · raw ADC ${packet.rawAdc}`;
    return;
  }

  const reading = { ...packet, time: new Date() };
  state.sensorReadings = [reading, ...state.sensorReadings].slice(0, 20);
  els.sensorCelsius.textContent = packet.celsius.toFixed(2);
  els.sensorFahrenheit.textContent = `${packet.fahrenheit.toFixed(2)} °F`;
  els.sensorReadingMeta.textContent = `Reading ${packet.sequence} · raw ADC ${packet.rawAdc} · board up ${Math.round(packet.uptimeMs / 1000)}s`;
  setSensorConnectionStatus("connected", "Connected", "Reading received. Press the physical button whenever you want another.");
  renderSensorHistory();
}

function onSensorValueChanged(event) {
  handleSensorPacket(event.target.value);
}

function onSensorDisconnected(event) {
  if (state.sensorCharacteristic) {
    state.sensorCharacteristic.removeEventListener("characteristicvaluechanged", onSensorValueChanged);
  }
  event?.target?.removeEventListener("gattserverdisconnected", onSensorDisconnected);
  state.sensorDevice = null;
  state.sensorCharacteristic = null;
  state.sensorCommandCharacteristic = null;
  state.sensorLastSequence = null;
  els.sensorConnect.textContent = "Connect sensor";
  els.sensorTakeReading.disabled = true;
  setSensorConnectionStatus("disconnected", "Disconnected", "Sensor disconnected. Reconnect when the board is powered and nearby.");
}

async function requestSensorReading() {
  const characteristic = state.sensorCommandCharacteristic;
  if (!characteristic || !state.sensorDevice?.gatt?.connected) {
    setSensorConnectionStatus("disconnected", "Disconnected", "Connect the sensor before requesting a reading.");
    return;
  }

  els.sensorTakeReading.disabled = true;
  setSensorConnectionStatus("connected", "Connected", "Measurement requested. Waiting for the ESP32-S3…");
  try {
    const command = new Uint8Array([0x01]);
    if (typeof characteristic.writeValueWithResponse === "function") {
      await characteristic.writeValueWithResponse(command);
    } else {
      await characteristic.writeValue(command);
    }
  } catch (error) {
    setSensorConnectionStatus("error", "Command error", `Could not request a reading: ${error?.message || "unknown Bluetooth error"}`);
  } finally {
    els.sensorTakeReading.disabled = !state.sensorDevice?.gatt?.connected;
  }
}

async function toggleSensorConnection() {
  if (state.sensorDevice?.gatt?.connected) {
    state.sensorDevice.gatt.disconnect();
    return;
  }
  if (!("bluetooth" in navigator)) {
    setSensorConnectionStatus("error", "Unavailable", "Web Bluetooth is not available here. Open this page in desktop Chrome or Edge.");
    return;
  }

  els.sensorConnect.disabled = true;
  setSensorConnectionStatus("connecting", "Connecting", "Choose Ducati Temperature Sensor in the Bluetooth window.");
  try {
    const device = await navigator.bluetooth.requestDevice({
      filters: [{ services: [SENSOR_SERVICE_UUID] }],
    });
    state.sensorDevice = device;
    device.addEventListener("gattserverdisconnected", onSensorDisconnected);
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(SENSOR_SERVICE_UUID);
    const characteristic = await service.getCharacteristic(SENSOR_READING_UUID);
    const commandCharacteristic = await service.getCharacteristic(SENSOR_COMMAND_UUID);
    state.sensorCharacteristic = characteristic;
    state.sensorCommandCharacteristic = commandCharacteristic;
    state.sensorLastSequence = null;
    characteristic.addEventListener("characteristicvaluechanged", onSensorValueChanged);
    await characteristic.startNotifications();
    els.sensorConnect.textContent = "Disconnect";
    els.sensorTakeReading.disabled = false;
    setSensorConnectionStatus("connected", "Connected", "Connected. Take a reading here or with the physical button.");
    handleSensorPacket(await characteristic.readValue());
  } catch (error) {
    if (state.sensorDevice?.gatt?.connected) state.sensorDevice.gatt.disconnect();
    state.sensorDevice = null;
    state.sensorCharacteristic = null;
    state.sensorCommandCharacteristic = null;
    els.sensorTakeReading.disabled = true;
    const cancelled = error?.name === "NotFoundError";
    setSensorConnectionStatus(
      "disconnected",
      "Disconnected",
      cancelled ? "Connection cancelled." : `Could not connect: ${error?.message || "unknown Bluetooth error"}`,
    );
  } finally {
    els.sensorConnect.disabled = false;
  }
}

function openSettings() {
  if (!els.settingsModal) return;
  if (!els.settingsModal.open) els.settingsModal.showModal();
  els.settingsModal.scrollTop = 0;
  loadAgentConfig().then(() => loadModelCatalog()).catch(() => {});
  window.requestAnimationFrame(() => {
    els.settingsModal.scrollTop = 0;
    els.settingsClose?.focus({ preventScroll: true });
  });
}

function closeSettings() {
  if (els.settingsModal?.open) els.settingsModal.close();
}

function fillConfigForm(config) {
  state.agentConfig = config;
  els.cfgSystemPrompt.value = config.system_prompt || "";
  fillSelect(els.cfgLlmModel, state.modelCatalog?.assistant_models || [], config.llm_model || "");
  fillSelect(els.cfgTtsModel, state.modelCatalog?.tts_models || [], config.tts_model || "");
  fillSelect(els.cfgTtsVoice, voicesForTtsModel(config.tts_model), config.tts_voice || "");
  els.cfgTtsInstructions.value = config.tts_instructions || "";
  els.cfgUpdated.value = config.updated_at
    ? formatTimeTitle(config.updated_at)
    : "not saved yet";
  if (els.configSource) els.configSource.textContent = `source: ${config.source || "unknown"}`;
}

function voicesForTtsModel(model) {
  return state.modelCatalog?.tts_voices_by_model?.[model]
    || state.modelCatalog?.tts_voices
    || [];
}

function fillSelect(select, values, selected) {
  const unique = [...new Set((values || []).filter(Boolean))];
  if (selected && !unique.includes(selected)) unique.unshift(selected);
  if (!unique.length) {
    const option = document.createElement("option");
    option.value = selected || "";
    option.textContent = selected || "Load models first";
    select.replaceChildren(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  select.replaceChildren(
    ...unique.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === selected;
      return option;
    })
  );
}

function refreshModelSelects() {
  if (!state.agentConfig) return;
  fillSelect(els.cfgLlmModel, state.modelCatalog?.assistant_models || [], state.agentConfig.llm_model || "");
  fillSelect(els.cfgTtsModel, state.modelCatalog?.tts_models || [], state.agentConfig.tts_model || "");
  fillSelect(els.cfgTtsVoice, voicesForTtsModel(state.agentConfig.tts_model), state.agentConfig.tts_voice || "");
}

async function loadAgentConfig() {
  els.configStatus.textContent = "Loading config…";
  try {
    const payload = await fetchJson("/api/admin/config");
    fillConfigForm(payload.config || {});
    els.configStatus.textContent = "Loaded live agent config";
  } catch (error) {
    els.configStatus.textContent = error.message || "Config load failed";
  }
}

async function loadModelCatalog({ quiet = false } = {}) {
  if (!quiet) els.catalogStatus.textContent = "Fetching models from OpenAI…";
  try {
    const payload = await fetchJson("/api/admin/catalog");
    state.modelCatalog = payload.catalog || null;
    refreshModelSelects();
    const assistants = state.modelCatalog?.assistant_models?.length || 0;
    const tts = state.modelCatalog?.tts_models?.length || 0;
    els.catalogStatus.textContent = `Live from OpenAI: ${assistants} assistant models · ${tts} TTS models`;
  } catch (error) {
    if (!quiet) els.catalogStatus.textContent = error.message || "Model fetch failed";
    throw error;
  }
}

async function saveApiKey() {
  const apiKey = els.cfgApiKey.value.trim();
  if (!apiKey) {
    els.catalogStatus.textContent = "Paste a new API key first.";
    els.cfgApiKey.focus();
    return;
  }
  els.apiKeySave.disabled = true;
  els.catalogStatus.textContent = "Validating key with OpenAI…";
  try {
    const payload = await fetchJson("/api/admin/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: apiKey }),
    });
    state.modelCatalog = payload.catalog || null;
    refreshModelSelects();
    const assistants = state.modelCatalog?.assistant_models?.length || 0;
    const tts = state.modelCatalog?.tts_models?.length || 0;
    els.catalogStatus.textContent = `Key saved in Keychain. Live models: ${assistants} assistant · ${tts} TTS`;
  } catch (error) {
    els.catalogStatus.textContent = error.message || "Key update failed";
  } finally {
    els.cfgApiKey.value = "";
    els.apiKeySave.disabled = false;
  }
}

async function saveAgentConfig(event) {
  event.preventDefault();
  els.configStatus.textContent = "Saving…";
  try {
    const payload = await fetchJson("/api/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        system_prompt: els.cfgSystemPrompt.value,
        llm_model: els.cfgLlmModel.value,
        tts_model: els.cfgTtsModel.value,
        tts_voice: els.cfgTtsVoice.value,
        tts_instructions: els.cfgTtsInstructions.value,
      }),
    });
    fillConfigForm(payload.config || {});
    els.configStatus.textContent = "Saved to live agent";
  } catch (error) {
    els.configStatus.textContent = error.message || "Save failed";
  }
}

async function runDeploy({ dryRun }) {
  if (!dryRun) {
    const ok = window.confirm(
      `Deploy bridge code to Hugging Face Space ${state.bootstrap?.hf_space_repo || ""}?\n\nThis uploads source from this laptop. Secrets stay local.`
    );
    if (!ok) return;
  }
  els.deployLog.textContent = dryRun ? "Packing dry-run…" : "Deploying…";
  try {
    const payload = await fetchJson("/api/admin/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run: Boolean(dryRun),
        repo_id: state.bootstrap?.hf_space_repo || undefined,
      }),
    });
    els.deployLog.textContent = payload.output || JSON.stringify(payload, null, 2);
    if (!payload.ok) {
      els.configStatus.textContent = "Deploy failed — see log";
    }
  } catch (error) {
    els.deployLog.textContent = error.message || "Deploy failed";
  }
}

async function bootstrap() {
  const payload = await fetchJson("/api/bootstrap");
  state.bootstrap = payload;
  state.devices = mergeDevices(payload.known_devices || [], []);
  renderDevices({ force: true });
  els.deployRepo.textContent = payload.hf_space_repo || "—";
  els.deployBridge.textContent = payload.bridge_configured ? "configured" : "missing";
  els.deployAvailability.textContent = payload.deploy_available ? "available" : "disabled";
  els.allowBrowserSpeech.checked = localStorage.getItem("wearabllm.allowBrowserSpeech") === "true";
  if (!payload.bridge_configured) {
    setStatus("offline", "Bridge missing");
    els.composerStatus.textContent = "Configure WEARABLLM_BRIDGE_URL in firmware sdkconfig.";
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => setView(tab.dataset.view || "chat"));
});
document.querySelectorAll("[data-open-settings]").forEach((control) => {
  control.addEventListener("click", openSettings);
});
els.settingsClose?.addEventListener("click", closeSettings);

els.configForm?.addEventListener("submit", saveAgentConfig);
els.configReload?.addEventListener("click", () => loadAgentConfig());
els.catalogRefresh?.addEventListener("click", () => loadModelCatalog());
els.apiKeySave?.addEventListener("click", saveApiKey);
els.cfgTtsModel?.addEventListener("change", () => {
  const voices = voicesForTtsModel(els.cfgTtsModel.value);
  const selected = voices.includes(els.cfgTtsVoice.value) ? els.cfgTtsVoice.value : voices[0] || "";
  fillSelect(els.cfgTtsVoice, voices, selected);
});
els.allowBrowserSpeech?.addEventListener("change", () => {
  localStorage.setItem("wearabllm.allowBrowserSpeech", String(els.allowBrowserSpeech.checked));
  if (!els.allowBrowserSpeech.checked && "speechSynthesis" in window) window.speechSynthesis.cancel();
});
els.deployDry?.addEventListener("click", () => runDeploy({ dryRun: true }));
els.deployLive?.addEventListener("click", () => runDeploy({ dryRun: false }));
els.sensorConnect?.addEventListener("click", toggleSensorConnection);
els.sensorTakeReading?.addEventListener("click", requestSensorReading);
els.sensorClear?.addEventListener("click", () => {
  state.sensorReadings = [];
  renderSensorHistory();
});

els.deliverWaveshare.addEventListener("change", () => {
  state.deliverToWaveshare = els.deliverWaveshare.checked;
  els.send.textContent = state.deliverToWaveshare ? "Send + play" : "Send";
  els.composerHint.textContent = state.deliverToWaveshare
    ? "The shared reply will also be queued for Waveshare display and speech."
    : "The conversation is shared with Android and this dashboard automatically.";
});

els.replyInput.addEventListener("focus", () => {
  state.pausePolling = true;
});
els.replyInput.addEventListener("blur", () => {
  state.pausePolling = false;
  refreshConversation();
});
els.replyInput.addEventListener("input", () => {
  // Keep polling paused while the user is composing.
  state.pausePolling = true;
});

els.replyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.sending) return;
  const transcript = els.replyInput.value.trim();
  if (!transcript) return;

  state.sending = true;
  state.thinking = true;
  state.pausePolling = true;
  els.send.disabled = true;
  const optimisticTurn = {
    id: `optimistic-${Date.now()}`,
    device_id: "web-console",
    role: "user",
    content: transcript,
    created_at: new Date().toISOString(),
  };
  state.turns = [...state.turns, optimisticTurn];
  els.replyInput.value = "";
  renderThread({ force: true });
  els.composerStatus.textContent = "";
  try {
    const payload = await fetchJson("/api/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transcript,
        device_id: "web-console",
        target_device_id: state.deliverToWaveshare ? "wearabllm-esp32" : "",
        idempotency_key: state.deliverToWaveshare ? `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}` : "",
      }),
    });
    state.latestActionId = payload.action?.id || "";
    if (payload.action) renderActionDelivery(payload.action);
    else els.composerStatus.textContent = payload.command ? `Got ${payload.command}` : "Reply sent";
    state.thinking = false;
    await refreshConversation({ forceRender: true });
  } catch (error) {
    console.error(error);
    els.composerStatus.textContent = error.message || "Send failed";
    setStatus("offline", "Send failed");
    state.thinking = false;
    state.turns = [
      ...state.turns,
      {
        id: `send-error-${Date.now()}`,
        device_id: "web-console",
        role: "assistant",
        content:
          "I couldn’t reach Sphere. Your message is still shown here, but it may not have reached the shared conversation. Please try again.",
        created_at: new Date().toISOString(),
      },
    ];
    renderThread({ force: true });
  } finally {
    state.sending = false;
    state.pausePolling = document.activeElement === els.replyInput;
    els.send.disabled = false;
    els.replyInput.focus();
  }
});

els.newConversation.addEventListener("click", async () => {
  els.newConversation.disabled = true;
  try {
    const payload = await fetchJson("/api/session/reset", { method: "POST" });
    state.selectedSessionId = payload.active_session_id || "";
    state.turnsSignature = "";
    state.sessionsSignature = "";
    els.composerStatus.textContent = "New conversation started";
    await refreshConversation({ forceRender: true });
    els.replyInput.focus();
  } catch (error) {
    els.composerStatus.textContent = error.message || "Could not start a new conversation";
  } finally {
    els.newConversation.disabled = false;
  }
});

els.archiveToggle.addEventListener("click", toggleArchiveView);

els.replyInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    els.replyForm.requestSubmit();
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !state.pausePolling) {
    refreshConversation();
  }
  if (!document.hidden) refreshLatestAction();
});

bootstrap()
  .then(async () => {
    await sendHeartbeat();
    await refreshConversation({ forceRender: true });
    await receiveBodyAction();
  })
  .catch((error) => {
    console.error(error);
    setStatus("offline", "Boot failed");
  });

setInterval(() => {
  if (document.hidden) return;
  refreshLatestAction();
  receiveBodyAction();
  if (state.pausePolling) return;
  refreshConversation();
}, 4000);

setInterval(() => {
  if (!document.hidden) sendHeartbeat();
}, 8000);
