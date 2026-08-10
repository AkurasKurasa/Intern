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

/* ── Sidebar navigation — controls what shows in the middle main area ──── */
/* The recorder panel only makes sense while actually recording, so it's
   hidden on the Workflows tab -- that tab plays sessions instead. */
const navRecorder = document.getElementById("navRecorder");
const navWorkflows = document.getElementById("navWorkflows");
const emptyState = document.getElementById("emptyState");
const workflowsWrap = document.getElementById("workflowsWrap");
const recorderPanel = document.getElementById("recorderPanel");
const playPanel = document.getElementById("playPanel");

function showMain(name) {
  const toHome = name === "home";
  emptyState.hidden = !toHome;
  workflowsWrap.hidden = toHome;
  recorderPanel.hidden = !toHome;
  playPanel.hidden = toHome;
  navRecorder.classList.toggle("active", toHome);
  navWorkflows.classList.toggle("active", !toHome);
  navRecorder.setAttribute("aria-current", toHome ? "page" : "false");
  navWorkflows.setAttribute("aria-current", !toHome ? "page" : "false");
  if (!toHome) loadWorkflows();
}
navRecorder.addEventListener("click", () => showMain("home"));
navWorkflows.addEventListener("click", () => showMain("workflows"));

/* ── Recorder panel (always visible, right column) ────────────────────── */
const statusDot   = document.getElementById("statusDot");
const statStatus  = document.getElementById("statStatus");
const statFrames  = document.getElementById("statFrames");
const statPending = document.getElementById("statPending");
const statSessions= document.getElementById("statSessions");
const outDirInput = document.getElementById("outDir");
const btnStart    = document.getElementById("btnStart");
const btnStop     = document.getElementById("btnStop");
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
  statusDot.className = "dot" + (isRecording ? " recording" : "");
  statStatus.textContent = isRecording ? "Recording" : "Idle";
}

btnStart.addEventListener("click", () => {
  window.recorderAPI.start(outDirInput.value.trim() || null);
  statFrames.textContent = "0";
  statPending.hidden = true;
  log("Recording — fill the form, then click Stop & Save.", "dim");
});

btnStop.addEventListener("click", () => {
  window.recorderAPI.stop();
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
      statPending.hidden = !event.pending;
      break;
    case "saved":
      setRecording(false);
      sessions += 1;
      statSessions.textContent = String(sessions);
      statFrames.textContent = String(event.steps);
      statPending.hidden = true;
      log(`Saved — ${event.steps} frames. Now click Replay ×N to repeat it.`, "ok");
      break;
    case "replay_progress":
      statStatus.textContent = `Replaying ${event.current}/${event.total}`;
      break;
    case "replay_done":
      statStatus.textContent = "Idle";
      log(`Replay done — ${event.made} copies (${event.steps_each} steps each) -> ${event.dest}`, "ok");
      break;
    case "play_started":
      setPlayStatus(`▶ Playing '${event.session}'…`);
      btnPlay.disabled = true;
      break;
    case "play_progress":
      setPlayStatus(event.message);
      break;
    case "play_done":
      setPlayStatus(`Done — ${event.steps} steps replayed.`);
      setTimeout(() => setPlayStatus(""), 4000);
      btnPlay.disabled = !selectedSession;
      break;
    case "log":
      log(event.message, event.level || "dim");
      break;
    case "error":
      setRecording(false);
      log(event.message, "err");
      sideStatusDot.classList.add("error");
      setPlayStatus(event.message);
      btnPlay.disabled = !selectedSession;
      break;
    default:
      console.log("Unhandled event:", event);
  }
});

/* ── Workflows panel ──────────────────────────────────────────────────── */
const workflowsListEl = document.getElementById("workflowsList");
const btnRefreshWorkflows = document.getElementById("btnRefreshWorkflows");
const playStatusEl = document.getElementById("playStatus");
let workflowsLoaded = false;

/* ── Play panel — one loaded capsule at a time ────────────────────────── */
const ppSlot        = document.getElementById("ppSlot");
const ppSlotHint     = document.getElementById("ppSlotHint");
const ppCapsule      = document.getElementById("ppCapsule");
const ppCapsuleName  = document.getElementById("ppCapsuleName");
const ppCapsuleMeta  = document.getElementById("ppCapsuleMeta");
const ppCount        = document.getElementById("ppCount");
const btnPlay        = document.getElementById("btnPlay");
let selectedSession = null;   // relative path, e.g. "data/demos/eight_Tabs/session_..."

function timeAgo(ms) {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function setPlayStatus(message) {
  playStatusEl.textContent = message || "";
  playStatusEl.hidden = !message;
}

/* Loads a capsule into the play panel for real -- called once the fly
   animation (below) lands, or immediately if animation is skipped. */
function loadCapsuleIntoSlot(sessionPath, name, metaText) {
  selectedSession = sessionPath;
  ppSlotHint.hidden = true;
  ppCapsule.hidden = false;
  ppCapsuleName.textContent = name;
  ppCapsuleMeta.textContent = metaText;
  ppSlot.classList.add("filled");
  btnPlay.disabled = false;
}

/* Clones a small chip at the clicked row's position and animates it to the
   play panel's slot -- a lightweight FLIP animation (no library): read the
   two real rects, position the clone at the start rect, then transition it
   to the end rect on the next frame. */
function flyToPlayPanel(rowEl, sessionPath, name, metaText) {
  const fromRect = rowEl.getBoundingClientRect();
  const toRect = ppSlot.getBoundingClientRect();

  const clone = document.createElement("div");
  clone.className = "capsule-flying";
  clone.innerHTML = `<span class="pp-capsule-icon">▶</span><span>${escapeHtml(name)}</span>`;
  clone.style.left = `${fromRect.left}px`;
  clone.style.top = `${fromRect.top}px`;
  clone.style.width = `${fromRect.width}px`;
  document.body.appendChild(clone);

  const dx = (toRect.left + toRect.width / 2) - (fromRect.left + fromRect.width / 2);
  const dy = (toRect.top + toRect.height / 2) - (fromRect.top + fromRect.height / 2);

  requestAnimationFrame(() => {
    clone.style.transform = `translate(${dx}px, ${dy}px) scale(.7)`;
    clone.style.opacity = "0";
  });

  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    clone.remove();
    loadCapsuleIntoSlot(sessionPath, name, metaText);
  };
  clone.addEventListener("transitionend", finish, { once: true });
  setTimeout(finish, 500);   // fallback in case transitionend doesn't fire
}

btnPlay.addEventListener("click", () => {
  if (!selectedSession) return;
  const count = parseInt(ppCount.value, 10) || 1;
  window.workflowsAPI.play(selectedSession, count);
});

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
      const sessionPath = `data/demos/${g.name}/${s.name}`;
      const metaText = `${s.steps.toLocaleString()} steps · ${g.name}`;
      const row = document.createElement("div");
      row.className = "wf-session";
      row.title = "Click to load this session into the Play panel";
      const left = document.createElement("span");
      left.className = "sname";
      left.textContent = s.name;
      const right = document.createElement("span");
      right.className = "ssteps";
      right.textContent = `${s.steps.toLocaleString()} steps · ${timeAgo(s.mtime)}`;
      row.appendChild(left);
      row.appendChild(right);
      row.addEventListener("click", () => {
        workflowsListEl.querySelectorAll(".wf-session.selected")
          .forEach((el) => el.classList.remove("selected"));
        row.classList.add("selected");
        flyToPlayPanel(row, sessionPath, s.name, metaText);
      });
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
