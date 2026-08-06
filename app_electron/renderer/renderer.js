/* ── Splash → shell transition ────────────────────────────────────────── */
const splashEl    = document.getElementById("splash");
const splashStatus= document.getElementById("splashStatus");
const shellEl     = document.getElementById("shell");

const SPLASH_MIN_MS = 1200;
const splashStart = Date.now();
let bridgeIsReady = false;

function maybeDismissSplash() {
  const elapsed = Date.now() - splashStart;
  if (!bridgeIsReady || elapsed < SPLASH_MIN_MS) return;
  splashEl.classList.add("hide");
  shellEl.classList.add("show");
  setTimeout(() => { splashEl.style.display = "none"; }, 550);
}
// Fallback: don't hang forever on the splash if the backend never reports ready.
setTimeout(() => { bridgeIsReady = true; maybeDismissSplash(); }, 4000);

/* ── Sidebar navigation ───────────────────────────────────────────────── */
const navRecorder = document.getElementById("navRecorder");
const navWorkflows = document.getElementById("navWorkflows");
const panelRecorder = document.getElementById("panel-recorder");
const panelWorkflows = document.getElementById("panel-workflows");

function showPanel(name) {
  const toRecorder = name === "recorder";
  panelRecorder.classList.toggle("active", toRecorder);
  panelWorkflows.classList.toggle("active", !toRecorder);
  navRecorder.classList.toggle("active", toRecorder);
  navWorkflows.classList.toggle("active", !toRecorder);
  navRecorder.setAttribute("aria-current", toRecorder ? "page" : "false");
  navWorkflows.setAttribute("aria-current", !toRecorder ? "page" : "false");
  if (!toRecorder) loadWorkflows();
}
navRecorder.addEventListener("click", () => showPanel("recorder"));
navWorkflows.addEventListener("click", () => showPanel("workflows"));

/* ── Recorder panel ───────────────────────────────────────────────────── */
const statusDot   = document.getElementById("statusDot");
const statStatus  = document.getElementById("statStatus");
const statFrames  = document.getElementById("statFrames");
const statSessions= document.getElementById("statSessions");
const statusBar   = document.getElementById("statusBar");
const outDirInput = document.getElementById("outDir");
const btnStart    = document.getElementById("btnStart");
const btnStop     = document.getElementById("btnStop");
const btnReplay   = document.getElementById("btnReplay");
const logEl       = document.getElementById("log");
const clockEl     = document.getElementById("clock");
const sideStatusDot = document.getElementById("sideStatusDot");
const sideStatus     = document.getElementById("sideStatus");

let sessions = 0;

function tickClock() {
  clockEl.textContent = new Date().toLocaleString([], {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  setTimeout(tickClock, 1000);
}
tickClock();

function log(message, level = "dim") {
  const row = document.createElement("div");
  row.className = "log-entry";
  const time = new Date().toLocaleTimeString();
  row.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-${level}">${message}</span>`;
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

function setRecording(isRecording) {
  btnStart.disabled = isRecording;
  btnStop.disabled = !isRecording;
  btnReplay.disabled = isRecording;
  statusDot.className = "dot" + (isRecording ? " recording" : "");
  statStatus.innerHTML = `<i class="dot${isRecording ? " recording" : ""}" id="statusDot"></i>${isRecording ? "Recording" : "Idle"}`;
}

btnStart.addEventListener("click", () => {
  window.recorderAPI.start(outDirInput.value.trim() || null);
  statFrames.textContent = "0";
  statusBar.textContent = "Recording — fill the form, then click Stop & Save";
});

btnStop.addEventListener("click", () => {
  window.recorderAPI.stop();
});

btnReplay.addEventListener("click", () => {
  const n = prompt("Duplicate the newest session how many times?", "10");
  if (!n) return;
  const count = parseInt(n, 10);
  if (!count || count < 1) return;
  log(`Replaying newest session x${count}...`, "dim");
  window.recorderAPI.replay(count);
});

window.recorderAPI.onEvent((event) => {
  switch (event.event) {
    case "ready":
      log("Backend ready.", "dim");
      sideStatusDot.classList.remove("error");
      sideStatusDot.style.background = "";
      sideStatusDot.classList.add("recording");
      sideStatus.title = "Backend connected";
      bridgeIsReady = true;
      splashStatus.textContent = "Ready.";
      maybeDismissSplash();
      break;
    case "started":
      setRecording(true);
      log("Demo recorder started.", "ok");
      break;
    case "frame_count":
      statFrames.textContent = String(event.value);
      break;
    case "saved":
      setRecording(false);
      sessions += 1;
      statSessions.textContent = String(sessions);
      statFrames.textContent = String(event.steps);
      statusBar.textContent = `Saved — ${event.steps} frames · ${sessions} session(s) total`;
      log(`Session saved — ${event.steps} frames. Now click Replay ×N to repeat it.`, "ok");
      break;
    case "replay_progress":
      statusBar.textContent = `Replaying (${event.current}/${event.total})...`;
      break;
    case "replay_done":
      statusBar.textContent = `Copied ×${event.made} → data/demos/human (${event.steps_each} steps each)`;
      log(`Replay done — ${event.made} copies (${event.steps_each} steps each) -> ${event.dest}`, "ok");
      break;
    case "log":
      log(event.message, event.level || "dim");
      break;
    case "error":
      setRecording(false);
      statusBar.textContent = `Error: ${event.message}`;
      log(event.message, "err");
      sideStatusDot.classList.add("error");
      break;
    default:
      console.log("Unhandled event:", event);
  }
});

/* ── Workflows panel ──────────────────────────────────────────────────── */
const workflowsListEl = document.getElementById("workflowsList");
const btnRefreshWorkflows = document.getElementById("btnRefreshWorkflows");
let workflowsLoaded = false;

function timeAgo(ms) {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

async function loadWorkflows() {
  workflowsListEl.innerHTML = '<p class="muted">Loading…</p>';
  let groups;
  try {
    groups = await window.workflowsAPI.list();
  } catch (e) {
    workflowsListEl.innerHTML = `<p class="muted">Couldn't read data/demos/ (${e.message || e}).</p>`;
    return;
  }
  if (!groups || !groups.length) {
    workflowsListEl.innerHTML = '<p class="muted">No recorded workflows yet — start one from the Recorder tab.</p>';
    return;
  }

  workflowsListEl.innerHTML = "";
  groups.forEach((g, gi) => {
    const card = document.createElement("div");
    card.className = "wf-group" + (gi === 0 ? " open" : "");

    const head = document.createElement("div");
    head.className = "wf-group-head";
    head.innerHTML =
      `<span><span class="chev">▸</span><span class="name">${escapeHtml(g.name)}</span></span>` +
      `<span class="meta">${g.sessionCount} session${g.sessionCount===1?"":"s"} · ${g.totalSteps.toLocaleString()} steps</span>`;
    head.addEventListener("click", () => card.classList.toggle("open"));
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "wf-sessions";
    g.sessions.forEach((s) => {
      const row = document.createElement("div");
      row.className = "wf-session";
      row.innerHTML =
        `<span class="sname">${escapeHtml(s.name)}</span>` +
        `<span class="ssteps">${s.steps.toLocaleString()} steps · ${timeAgo(s.mtime)}</span>`;
      body.appendChild(row);
    });
    card.appendChild(body);
    workflowsListEl.appendChild(card);
  });
  workflowsLoaded = true;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

btnRefreshWorkflows.addEventListener("click", loadWorkflows);
