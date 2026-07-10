const feed = document.querySelector("#feed");
const status = document.querySelector("#status");
const summary = document.querySelector("#summary");
const template = document.querySelector("#entry-template");
const seen = new Set();
let newestId = 0;
let polling = false;

function setStatus(kind, text) {
  status.className = `status ${kind}`;
  status.lastChild.textContent = ` ${text}`;
}

function render(row, prepend = true) {
  if (seen.has(row.id)) return;
  seen.add(row.id);
  newestId = Math.max(newestId, Number(row.id));
  const node = template.content.firstElementChild.cloneNode(true);
  const created = new Date(row.created_at);
  const time = node.querySelector("time");
  time.dateTime = row.created_at;
  time.textContent = created.toLocaleString();
  node.querySelector(".command").textContent = row.command;
  node.querySelector(".device").textContent = row.device_id;
  node.querySelector(".transcript").textContent = row.transcript;
  node.querySelector(".reply").textContent = row.reply || "No spoken reply";
  if (prepend) feed.prepend(node); else feed.append(node);
}

async function update() {
  if (polling || document.hidden) return;
  polling = true;
  const query = newestId ? `?after_id=${newestId}&limit=100` : "?limit=100";
  try {
    const response = await fetch(`/api/transcripts${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const rows = Array.isArray(payload.transcripts) ? payload.transcripts : [];
    rows.slice().reverse().forEach((row) => render(row, true));
    summary.textContent = seen.size
      ? `${seen.size} interaction${seen.size === 1 ? "" : "s"} on screen`
      : "Waiting for the first transcript…";
    setStatus("online", "Live");
  } catch (error) {
    console.error(error);
    setStatus("offline", "Retrying");
  } finally {
    polling = false;
  }
}

document.querySelector("#clear").addEventListener("click", () => {
  feed.replaceChildren();
  seen.clear();
  newestId = 0;
  summary.textContent = "Screen cleared; reloading…";
  update();
});
document.addEventListener("visibilitychange", update);
update();
setInterval(update, 1500);
