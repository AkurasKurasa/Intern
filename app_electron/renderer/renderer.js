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
    case "capsule_started":
      capsuleLog(`▶ Running capsule — model=${event.model_path.split(/[\\/]/).pop()}`, "ok");
      setCapsuleRunning(true);
      break;
    case "capsule_progress":
      capsuleLog(event.line, "dim");
      break;
    case "capsule_done":
      capsuleLog(`Run ended (exit code ${event.code}).`, event.code === 0 ? "ok" : "err");
      setCapsuleRunning(false);
      break;
    case "capsule_stopped":
      capsuleLog("Stopping — saving partial results…", "dim");
      break;
    case "log":
      log(event.message, event.level || "dim");
      break;
    case "error":
      setRecording(false);
      log(event.message, "err");
      sideStatusDot.classList.add("error");
      capsuleLog(event.message, "err");
      setCapsuleRunning(false);
      break;
    default:
      console.log("Unhandled event:", event);
  }
});

/* ── Workflows panel ──────────────────────────────────────────────────── */
const workflowsListEl = document.getElementById("workflowsList");
const btnRefreshWorkflows = document.getElementById("btnRefreshWorkflows");
let workflowsLoaded = false;

/* ── Play panel — one loaded capsule at a time ────────────────────────────
   A "capsule" (components/agent/capsule.py's WorkflowCapsule) is a named
   task + the model checkpoint currently deployed for it -- e.g.
   "form_filling". Playing a capsule runs the REAL trained agent live (same
   as run_task.py), not a replay of one recorded session. Clicking a
   workflow GROUP (not an individual session) loads/flies its capsule here
   -- there's no formal group->capsule mapping in the registry yet, so with
   exactly one capsule registered (the only case this project has right
   now) any group click loads it; with more than one, name-matching is
   attempted and the first capsule is used as a last resort. */
const ppSlot         = document.getElementById("ppSlot");
const ppSlotHint     = document.getElementById("ppSlotHint");
const ppCapsule      = document.getElementById("ppCapsule");
const ppCapsuleEmoji = document.getElementById("ppCapsuleEmoji");
const ppCapsuleName  = document.getElementById("ppCapsuleName");
const ppCapsuleMeta  = document.getElementById("ppCapsuleMeta");
const ppCheckpoint   = document.getElementById("ppCheckpoint");
const btnPlay        = document.getElementById("btnPlay");
const btnStopCapsule = document.getElementById("btnStopCapsule");
const btnDeploy      = document.getElementById("btnDeploy");
const capsuleLogEl   = document.getElementById("capsuleLog");

const PLACEHOLDER_EMOJI = "🧩";

let capsulesCache = [];      // last fetched capsule list, from capsulesAPI.list()
let currentCapsule = null;   // the one loaded in the play panel right now

function timeAgo(ms) {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function capsuleLog(message, level = "dim") {
  const row = document.createElement("div");
  row.className = "log-entry";
  const time = new Date().toLocaleTimeString();
  row.innerHTML = `<span class="log-time">[${time}]</span> <span class="log-${level}">${escapeHtml(message)}</span>`;
  capsuleLogEl.appendChild(row);
  capsuleLogEl.scrollTop = capsuleLogEl.scrollHeight;
}

function setCapsuleRunning(isRunning) {
  btnPlay.disabled = isRunning || !currentCapsule;
  btnStopCapsule.disabled = !isRunning;
}

function findCapsuleForGroup(groupName) {
  if (capsulesCache.length === 1) return capsulesCache[0];
  const byName = capsulesCache.find((c) => c.name === groupName);
  if (byName) return byName;
  return capsulesCache[0] || null;
}

/* Loads a capsule into the play panel for real -- called once the fly
   animation (below) lands, or immediately if animation is skipped. */
async function loadCapsuleIntoSlot(capsule) {
  currentCapsule = capsule;
  ppSlotHint.hidden = true;
  ppCapsule.hidden = false;
  ppCapsuleEmoji.textContent = capsule.emoji || PLACEHOLDER_EMOJI;
  ppCapsuleName.textContent = capsule.name;
  ppCapsuleMeta.textContent = capsule.description || capsule.model_path;
  ppSlot.classList.add("filled");

  ppCheckpoint.disabled = true;
  ppCheckpoint.innerHTML = '<option>Loading…</option>';
  btnDeploy.hidden = true;
  let checkpoints = [];
  try {
    checkpoints = await window.capsulesAPI.checkpoints(capsule.name);
  } catch (e) {
    capsuleLog(`Couldn't list checkpoints: ${e.message || e}`, "err");
  }
  ppCheckpoint.innerHTML = "";
  if (!checkpoints.length) {
    ppCheckpoint.innerHTML = `<option value="${escapeHtml(capsule.model_path)}">${escapeHtml(capsule.model_path)}</option>`;
  } else {
    checkpoints.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.path;
      const deployed = c.path === currentCapsule.model_path;
      opt.textContent = `${c.name}${deployed ? "  (deployed)" : ""}`;
      ppCheckpoint.appendChild(opt);
    });
    const deployedOpt = Array.from(ppCheckpoint.options).find((o) => o.value === capsule.model_path);
    if (deployedOpt) ppCheckpoint.value = capsule.model_path;
  }
  ppCheckpoint.disabled = false;
  setCapsuleRunning(false);
}

ppCheckpoint.addEventListener("change", () => {
  btnDeploy.hidden = !currentCapsule || ppCheckpoint.value === currentCapsule.model_path;
});

btnDeploy.addEventListener("click", async () => {
  if (!currentCapsule) return;
  try {
    const updated = await window.capsulesAPI.deploy(currentCapsule.name, ppCheckpoint.value);
    currentCapsule = updated;
    // Keep capsulesCache in sync too -- without this, re-selecting the same
    // group without an explicit Refresh would reload the stale pre-deploy
    // model_path from the cached list, silently undoing the deploy's
    // visible effect (the registry file itself was still updated fine).
    const idx = capsulesCache.findIndex((c) => c.name === updated.name);
    if (idx !== -1) capsulesCache[idx] = updated;
    ppCapsuleMeta.textContent = currentCapsule.description || currentCapsule.model_path;
    btnDeploy.hidden = true;
    capsuleLog(`Deployed ${ppCheckpoint.value} for '${currentCapsule.name}'.`, "ok");
    // refresh the (deployed) labels
    loadCapsuleIntoSlot(currentCapsule);
  } catch (e) {
    capsuleLog(`Deploy failed: ${e.message || e}`, "err");
  }
});

/* Loading a capsule into the (already-visible, fixed-position) panel is
   instant -- a brief border/background pulse on the slot itself is enough
   feedback that something changed, without an element flying across the
   window. Re-triggerable: force a reflow so clicking a second group right
   after the first restarts the animation instead of no-op'ing. */
function flashPlaySlot() {
  ppSlot.classList.remove("pp-slot-flash");
  void ppSlot.offsetWidth;
  ppSlot.classList.add("pp-slot-flash");
}

/* Click-to-edit: the capsule's emoji bubble in the Play panel swaps to a
   small text input, committed on blur/Enter, discarded on Escape. Updates
   both currentCapsule and its entry in capsulesCache (same staleness
   pitfall as the checkpoint deploy handler above), then patches every
   matching group header in the list so it doesn't need a full reload. */
ppCapsuleEmoji.addEventListener("click", () => {
  if (!currentCapsule) return;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "pp-capsule-emoji-input";
  input.value = currentCapsule.emoji || "";
  input.placeholder = PLACEHOLDER_EMOJI;
  input.maxLength = 8;
  ppCapsuleEmoji.replaceWith(input);
  input.focus();
  input.select();

  let cancelled = false;
  const commit = async () => {
    if (cancelled) { input.replaceWith(ppCapsuleEmoji); return; }
    const value = input.value.trim();
    input.replaceWith(ppCapsuleEmoji);
    try {
      const updated = await window.capsulesAPI.setEmoji(currentCapsule.name, value);
      currentCapsule = updated;
      const idx = capsulesCache.findIndex((c) => c.name === updated.name);
      if (idx !== -1) capsulesCache[idx] = updated;
      ppCapsuleEmoji.textContent = updated.emoji || PLACEHOLDER_EMOJI;
      refreshGroupEmojis();
    } catch (e) {
      capsuleLog(`Couldn't set emoji: ${e.message || e}`, "err");
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    else if (e.key === "Escape") { cancelled = true; input.blur(); }
  });
  input.addEventListener("blur", commit, { once: true });
});

/* Re-reads each visible group's mapped capsule and patches just its emoji
   span -- cheaper than a full loadWorkflows() and doesn't collapse
   whatever the user has open. */
function refreshGroupEmojis() {
  workflowsListEl.querySelectorAll(".wf-group-head").forEach((headEl) => {
    const capsule = findCapsuleForGroup(headEl.dataset.groupName);
    const emojiEl = headEl.querySelector(".wf-emoji");
    if (emojiEl) emojiEl.textContent = capsule ? (capsule.emoji || PLACEHOLDER_EMOJI) : PLACEHOLDER_EMOJI;
  });
}

btnPlay.addEventListener("click", () => {
  if (!currentCapsule) return;
  window.capsulesAPI.run(currentCapsule.model_path);
});

btnStopCapsule.addEventListener("click", () => {
  window.capsulesAPI.stop();
});

/* ── Recorder panel's "Save to" dropdown -- populated from existing
   workflow groups instead of free-typed text. ─────────────────────────── */
async function populateOutDirOptions() {
  let groups = [];
  try {
    groups = await window.workflowsAPI.list();
  } catch (e) {
    return;
  }
  if (!groups.length) return;
  const current = outDirInput.value;
  outDirInput.innerHTML = "";
  groups.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = `data/demos/${g.name}`;
    opt.textContent = `data/demos/${g.name}`;
    outDirInput.appendChild(opt);
  });
  if (Array.from(outDirInput.options).some((o) => o.value === current)) {
    outDirInput.value = current;
  }
}
populateOutDirOptions();

async function loadWorkflows() {
  workflowsListEl.innerHTML = '<p class="muted">Loading…</p>';
  let groups;
  try {
    groups = await window.workflowsAPI.list();
  } catch (e) {
    workflowsListEl.innerHTML = `<p class="muted">Couldn't read data/demos/ (${e.message || e}).</p>`;
    return;
  }
  try {
    capsulesCache = await window.capsulesAPI.list();
  } catch (e) {
    capsulesCache = [];
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
    head.title = "Click to expand and load this capsule into Play";
    head.dataset.groupName = g.name;
    const headCapsule = findCapsuleForGroup(g.name);
    const headEmoji = headCapsule ? (headCapsule.emoji || PLACEHOLDER_EMOJI) : PLACEHOLDER_EMOJI;
    head.innerHTML =
      `<span><span class="chev">▸</span><span class="wf-emoji">${headEmoji}</span><span class="name">${escapeHtml(g.name)}</span></span>` +
      `<span class="meta">${g.sessionCount} session${g.sessionCount===1?"":"s"} · ${g.totalSteps.toLocaleString()} steps</span>`;
    head.addEventListener("click", () => {
      card.classList.toggle("open");
      const capsule = findCapsuleForGroup(g.name);
      if (!capsule) {
        capsuleLog(`No capsule registered yet for '${g.name}'.`, "dim");
        return;
      }
      workflowsListEl.querySelectorAll(".wf-group.capsule-selected")
        .forEach((el) => el.classList.remove("capsule-selected"));
      card.classList.add("capsule-selected");
      loadCapsuleIntoSlot(capsule);
      flashPlaySlot();
    });
    card.appendChild(head);

    const body = document.createElement("div");
    body.className = "wf-sessions";
    if (!g.sessions.length) {
      const empty = document.createElement("p");
      empty.className = "muted wf-empty-hint";
      empty.textContent = "No sessions yet — record one from the Recorder tab.";
      body.appendChild(empty);
    }
    g.sessions.forEach((s) => {
      const row = document.createElement("div");
      row.className = "wf-session";
      const left = document.createElement("span");
      left.className = "sname";
      left.textContent = s.name;
      const right = document.createElement("span");
      right.className = "ssteps";
      right.textContent = `${s.steps.toLocaleString()} steps · ${timeAgo(s.mtime)}`;
      row.appendChild(left);
      row.appendChild(right);
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

btnRefreshWorkflows.addEventListener("click", () => { loadWorkflows(); populateOutDirOptions(); });

/* ── Create workflow — just reserves an empty data/demos/<name>/ folder.
   No capsule/model gets registered here; there's nothing trained for a
   brand-new workflow yet. It shows up immediately in this list (as an
   empty group) and in the Recorder's Save-to dropdown. ─────────────────── */
const btnCreateWorkflow = document.getElementById("btnCreateWorkflow");
const wfCreateForm   = document.getElementById("wfCreateForm");
const wfCreateName   = document.getElementById("wfCreateName");
const wfCreateSubmit = document.getElementById("wfCreateSubmit");
const wfCreateCancel = document.getElementById("wfCreateCancel");

btnCreateWorkflow.addEventListener("click", () => {
  wfCreateForm.hidden = !wfCreateForm.hidden;
  if (!wfCreateForm.hidden) { wfCreateName.value = ""; wfCreateName.focus(); }
});
wfCreateCancel.addEventListener("click", () => { wfCreateForm.hidden = true; });
wfCreateName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") wfCreateSubmit.click();
  else if (e.key === "Escape") wfCreateForm.hidden = true;
});
wfCreateSubmit.addEventListener("click", async () => {
  const name = wfCreateName.value.trim();
  if (!name) return;
  try {
    await window.workflowsAPI.create(name);
    wfCreateForm.hidden = true;
    await loadWorkflows();
    await populateOutDirOptions();
  } catch (e) {
    capsuleLog(`Couldn't create workflow: ${e.message || e}`, "err");
  }
});
