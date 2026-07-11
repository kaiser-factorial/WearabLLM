const els = {
  status: document.querySelector("#status"),
  deviceList: document.querySelector("#device-list"),
  thread: document.querySelector("#thread"),
  threadTitle: document.querySelector("#thread-title"),
  sessionActive: document.querySelector("#session-active"),
  sessionTurns: document.querySelector("#session-turns"),
  sessionFilter: document.querySelector("#session-filter"),
  replyAs: document.querySelector("#reply-as"),
  replyForm: document.querySelector("#reply-form"),
  replyInput: document.querySelector("#reply-input"),
  send: document.querySelector("#send"),
  composerStatus: document.querySelector("#composer-status"),
  eventFeed: document.querySelector("#event-feed"),
  refresh: document.querySelector("#refresh"),
  resetSession: document.querySelector("#reset-session"),
};

const state = {
  devices: [],
  selectedDeviceId: "all",
  replyAsDeviceId: "web-console",
  turns: [],
  session: null,
  polling: false,
  sending: false,
  seenEvents: new Set(),
};

function setStatus(kind, text) {
  els.status.className = `status ${kind}`;
  els.status.innerHTML = `<span></span> ${text}`;
}

function kindFor(deviceId, devices = state.devices) {
  const found = devices.find((item) => item.id === deviceId);
  return found?.kind || "custom";
}

function labelFor(deviceId, devices = state.devices) {
  const found = devices.find((item) => item.id === deviceId);
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
      const deltaMs = Date.now() - date.getTime();
      const seconds = Math.round(deltaMs / 1000);
      if (seconds < 45) return "just now";
      if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
      if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
      if (seconds < 86400 * 7) return `${Math.round(seconds / 86400)}d ago`;
    }
    // Compact local stamp: "Jul 10, 11:42 PM"
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
    const previous = byId.get(item.id) || {};
    byId.set(item.id, {
      ...previous,
      ...item,
      seen: Boolean(previous.seen || item.seen),
    });
  }
  // Always offer an "all bodies" virtual filter via UI, not as a device body.
  return Array.from(byId.values()).sort((a, b) => a.label.localeCompare(b.label));
}

function renderDevices() {
  const cards = [
    {
      id: "all",
      label: "All bodies",
      kind: "custom",
      status: "active",
      description: "Shared principal conversation across every device",
      seen: true,
    },
    ...state.devices,
  ];

  els.deviceList.replaceChildren(
    ...cards.map((device) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `device-card ${device.kind || "custom"}${device.id === state.selectedDeviceId ? " active" : ""}${device.status === "planned" ? " planned" : ""}`;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", device.id === state.selectedDeviceId ? "true" : "false");
      button.innerHTML = `
        <span class="device-dot" aria-hidden="true"></span>
        <span>
          <strong>${escapeHtml(device.label)}</strong>
          <small>${escapeHtml(device.description || device.id)}</small>
        </span>
        <span class="device-badge">${escapeHtml(device.status || "active")}</span>
      `;
      button.addEventListener("click", () => {
        state.selectedDeviceId = device.id;
        els.threadTitle.textContent = device.id === "all" ? "All bodies" : device.label;
        els.sessionFilter.textContent = device.id === "all" ? "All bodies" : device.label;
        renderDevices();
        refreshConversation();
      });
      return button;
    })
  );

  const replyOptions = state.devices.filter((device) => device.status !== "planned" || device.id === "web-console");
  const preferred = replyOptions.some((device) => device.id === state.replyAsDeviceId)
    ? state.replyAsDeviceId
    : "web-console";
  state.replyAsDeviceId = preferred;
  els.replyAs.replaceChildren(
    ...replyOptions.map((device) => {
      const option = document.createElement("option");
      option.value = device.id;
      option.textContent = device.label;
      option.selected = device.id === preferred;
      return option;
    })
  );
}

function renderThread() {
  if (!state.turns.length) {
    els.thread.innerHTML = `
      <div class="empty">
        <strong>Quiet sphere</strong>
        <p>No turns yet for this view. Speak through the home base, or reply from this console to start the shared thread.</p>
      </div>
    `;
    els.sessionTurns.textContent = "0";
    return;
  }

  els.sessionTurns.textContent = String(state.turns.length);
  els.thread.replaceChildren(
    ...state.turns.map((turn) => {
      const role = turn.role === "assistant" ? "assistant" : "user";
      const deviceId = turn.device_id || "unknown";
      const kind = kindFor(deviceId);
      const article = document.createElement("article");
      article.className = `bubble ${role}`;
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
      return article;
    })
  );
  els.thread.scrollTop = els.thread.scrollHeight;
}

function renderEvents(rows) {
  for (const row of rows.slice().reverse()) {
    if (state.seenEvents.has(row.id)) continue;
    state.seenEvents.add(row.id);
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
  refreshEventTimestamps();
}

function refreshEventTimestamps() {
  for (const item of els.eventFeed.querySelectorAll(".event-item")) {
    const createdAt = item.dataset.createdAt;
    const stamp = item.querySelector("time.timestamp");
    if (!stamp || !createdAt) continue;
    stamp.textContent = formatTime(createdAt, { relative: true });
    stamp.dateTime = createdAt;
    stamp.title = formatTimeTitle(createdAt);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.error || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

async function refreshConversation() {
  if (state.polling) return;
  state.polling = true;
  try {
    const params = new URLSearchParams({ limit: "300" });
    if (state.selectedDeviceId !== "all") {
      params.set("device_id", state.selectedDeviceId);
    }
    const payload = await fetchJson(`/api/conversation?${params.toString()}`);
    state.turns = Array.isArray(payload.turns) ? payload.turns : [];
    state.session = payload.session || null;
    state.devices = mergeDevices(state.devices, payload.devices || []);
    els.sessionActive.textContent = shortId(payload.active_session_id || state.session?.id);
    renderDevices();
    renderThread();
    setStatus("online", "Live");
  } catch (error) {
    console.error(error);
    setStatus("offline", error.message || "Retrying");
  } finally {
    state.polling = false;
  }
}

async function refreshEvents() {
  try {
    const payload = await fetchJson("/api/transcripts?limit=20");
    const rows = Array.isArray(payload.transcripts) ? payload.transcripts : [];
    renderEvents(rows);
  } catch (error) {
    // Optional side panel; conversation remains primary.
    if (!String(error.message).includes("transcripts_not_configured")) {
      console.warn("event feed", error);
    }
  }
}

async function bootstrap() {
  const payload = await fetchJson("/api/bootstrap");
  state.devices = mergeDevices(payload.known_devices || [], []);
  state.replyAsDeviceId = payload.default_device_id || "web-console";
  renderDevices();
  if (!payload.bridge_configured) {
    setStatus("offline", "Bridge missing");
    els.composerStatus.textContent = "Configure WEARABLLM_BRIDGE_URL in firmware sdkconfig.";
  }
}

els.replyAs.addEventListener("change", () => {
  state.replyAsDeviceId = els.replyAs.value;
});

els.replyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.sending) return;
  const transcript = els.replyInput.value.trim();
  if (!transcript) return;

  state.sending = true;
  els.send.disabled = true;
  els.composerStatus.textContent = "Thinking…";
  try {
    const payload = await fetchJson("/api/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        transcript,
        device_id: state.replyAsDeviceId || "web-console",
      }),
    });
    els.replyInput.value = "";
    els.composerStatus.textContent = payload.command
      ? `Got ${payload.command}`
      : "Reply sent";
    await refreshConversation();
  } catch (error) {
    console.error(error);
    els.composerStatus.textContent = error.message || "Send failed";
    setStatus("offline", "Send failed");
  } finally {
    state.sending = false;
    els.send.disabled = false;
  }
});

els.refresh.addEventListener("click", () => {
  refreshConversation();
  refreshEvents();
});

els.resetSession.addEventListener("click", async () => {
  if (!window.confirm("Archive the active shared session and start fresh? Durable memories are kept.")) {
    return;
  }
  try {
    await fetchJson("/api/session/reset", { method: "POST" });
    els.composerStatus.textContent = "Session archived";
    await refreshConversation();
  } catch (error) {
    els.composerStatus.textContent = error.message || "Reset failed";
  }
});

els.replyInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    els.replyForm.requestSubmit();
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshConversation();
    refreshEvents();
  }
});

bootstrap()
  .then(() => Promise.all([refreshConversation(), refreshEvents()]))
  .catch((error) => {
    console.error(error);
    setStatus("offline", "Boot failed");
  });

setInterval(() => {
  if (!document.hidden) {
    refreshConversation();
    refreshEvents();
    refreshEventTimestamps();
  }
}, 2500);
