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
const navInbox = document.getElementById("navInbox");
const emptyState = document.getElementById("emptyState");
const workflowsWrap = document.getElementById("workflowsWrap");
const inboxWrap = document.getElementById("inboxWrap");
const recorderPanel = document.getElementById("recorderPanel");
const playPanel = document.getElementById("playPanel");
const runningView = document.getElementById("runningView");
const finishedView = document.getElementById("finishedView");
const btnHomeStartRecording = document.getElementById("btnHomeStartRecording");
const btnHomeBrowseTasks = document.getElementById("btnHomeBrowseTasks");
const homeTaskCountEl = document.getElementById("homeTaskCount");
const tasksCountLineEl = document.getElementById("tasksCountLine");

// The three mutually-exclusive views inside the Tasks section (list of
// tasks / a live run / the just-finished summary) -- separate from
// showMain()'s Home-vs-Tasks split, since Running/Finished only make sense
// once a capsule is loaded and playing. Passing "" hides all three, used
// when leaving the Tasks section entirely (see showMain() below).
function showTasksSubview(name) {
  workflowsWrap.hidden = name !== "list";
  runningView.hidden = name !== "running";
  finishedView.hidden = name !== "finished";
}

function showMain(name) {
  const toHome = name === "home";
  const toInbox = name === "inbox";
  const toWorkflows = name === "workflows";
  emptyState.hidden = !toHome;
  inboxWrap.hidden = !toInbox;
  recorderPanel.hidden = !toHome;
  playPanel.hidden = toHome || toInbox;
  navRecorder.classList.toggle("active", toHome);
  navWorkflows.classList.toggle("active", toWorkflows);
  navInbox.classList.toggle("active", toInbox);
  navRecorder.setAttribute("aria-current", toHome ? "page" : "false");
  navWorkflows.setAttribute("aria-current", toWorkflows ? "page" : "false");
  navInbox.setAttribute("aria-current", toInbox ? "page" : "false");
  if (toHome) {
    showTasksSubview("");
    refreshTaskCount();
  } else if (toInbox) {
    showTasksSubview("");
    loadInboxMessages();
  } else {
    // A capsule already running (e.g. the user switched to Home mid-run
    // and is coming back) should still show the running view, not reset
    // to the list -- btnStopCapsule's disabled state is the single
    // existing source of truth for "is a capsule running right now."
    showTasksSubview(!btnStopCapsule.disabled ? "running" : "list");
    loadWorkflows();
  }
  // Tells main.js which mini overlay to show if the window gets
  // minimized from here -- recorder Start/Stop from Recorder, the round
  // Play/Stop widget from Workflows. Inbox has no mini overlay of its own,
  // so it falls into the same plain-recorder-widget branch "workflows"
  // used to be the only alternative to -- main.js's minimize handler
  // already treats anything other than "workflows" that way.
  window.recorderAPI.setActiveSection(toWorkflows ? "workflows" : (toInbox ? "inbox" : "home"));
}
navRecorder.addEventListener("click", () => showMain("home"));
navWorkflows.addEventListener("click", () => showMain("workflows"));
navInbox.addEventListener("click", () => showMain("inbox"));
btnHomeStartRecording.addEventListener("click", () => btnStart.click());
btnHomeBrowseTasks.addEventListener("click", () => navWorkflows.click());

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
  // Drives the Start button's ink -> clay swap in style.css -- recording
  // shares the agent-control signal colour because the take being captured
  // is what will later drive the agent.
  recorderPanel.classList.toggle("is-recording", isRecording);
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
      hideCountdown();
      wasUserStopped = false;
      progressLineCount = 0;
      capsuleLog(`▶ Running — ${event.label}`, "ok");
      setCapsuleRunning(true);
      break;
    case "capsule_progress":
      handleCapsuleProgressLine(event.line);
      break;
    case "capsule_done":
      hideCountdown();
      stopElapsedTimer();
      tbAgentPill.hidden = true;
      capsuleLog(`Run ended (exit code ${event.code}).`, event.code === 0 ? "ok" : "err");
      setCapsuleRunning(false);
      showFinished(event.code);
      break;
    case "capsule_stopped":
      wasUserStopped = true;
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
      hideCountdown();
      stopElapsedTimer();
      tbAgentPill.hidden = true;
      break;
    // Inbox Router (Scope #3) events -- deliberately their own event names,
    // not "error"/"log", since those two already carry recorder/capsule-
    // specific side effects (setRecording, setCapsuleRunning, hideCountdown)
    // that must never fire for an unrelated inbox problem.
    case "inbox_poll_started":
      setInboxRunning(true);
      inboxProviderLabel.textContent = event.provider === "real"
        ? "LIVE GMAIL" : "MOCK GMAIL — no live credentials";
      break;
    case "inbox_routed":
      upsertInboxRow(event);
      break;
    case "inbox_confirm_applied":
      markInboxRowConfirmed(event.message_id, event.decision);
      break;
    case "inbox_override_applied":
      markInboxRowOverridden(event.message_id, event.new_decision);
      break;
    case "inbox_stopped":
      setInboxRunning(false);
      break;
    case "inbox_log":
      break; // dev-detail stream; the plain-language row is what the UI shows
    case "inbox_error":
      log(`Inbox Router: ${event.message}`, "err");
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
const ppCheckpointGroup = document.getElementById("ppCheckpointGroup");
const ppCheckpoint   = document.getElementById("ppCheckpoint");
const btnPlay        = document.getElementById("btnPlay");
const btnStopCapsule = document.getElementById("btnStopCapsule");
const btnDeploy      = document.getElementById("btnDeploy");
const capsuleLogEl   = document.getElementById("capsuleLog");
const ppCountdown       = document.getElementById("ppCountdown");
const ppCountdownNumber = document.getElementById("ppCountdownNumber");
const ppCountdownHint   = document.getElementById("ppCountdownHint");
const btnCopyLog        = document.getElementById("btnCopyLog");
const btnOpenLog        = document.getElementById("btnOpenLog");

// ── Running / Finished views + the full-window Handover overlay ─────────
// The overlay and the titlebar's "AGENT HAS CONTROL" pill both key off the
// exact same COUNTDOWN_BEGIN/COUNTDOWN N/COUNTDOWN_END lines that already
// drove #ppCountdown -- see handleCapsuleProgressLine() below -- so they
// can never show something that isn't actually happening on the real
// backend process.
const tbAgentPill        = document.getElementById("tbAgentPill");
const handoverOverlay    = document.getElementById("handoverOverlay");
const handoverCountdownEl= document.getElementById("handoverCountdown");
const handoverTaskNameEl = document.getElementById("handoverTaskName");
const btnHandoverCancel  = document.getElementById("btnHandoverCancel");
const btnTakeBackControl = document.getElementById("btnTakeBackControl");
const runningElapsedEl   = document.getElementById("runningElapsed");
const stepFeedEl         = document.getElementById("stepFeed");
const finishedHeadlineEl = document.getElementById("finishedHeadline");
const finishedBodyEl     = document.getElementById("finishedBody");
const finishedDurationEl = document.getElementById("finishedDuration");
const finishedLineCountEl= document.getElementById("finishedLineCount");
const btnRunAgain        = document.getElementById("btnRunAgain");
const btnBackToTasks     = document.getElementById("btnBackToTasks");

let runStartedAt = null;
let elapsedTimerId = null;
let progressLineCount = 0;
let wasUserStopped = false;

function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}
function startElapsedTimer() {
  runStartedAt = Date.now();
  runningElapsedEl.textContent = "00:00";
  elapsedTimerId = setInterval(() => {
    runningElapsedEl.textContent = formatElapsed(Date.now() - runStartedAt);
  }, 1000);
}
function stopElapsedTimer() {
  if (elapsedTimerId) { clearInterval(elapsedTimerId); elapsedTimerId = null; }
}

// Appends a raw progress line to the step feed, neutral styling -- used
// for anything prettifyProgressLine() below can't confidently classify.
// The full verbatim transcript still lives in #capsuleLog either way; this
// is an additive, friendlier rendering of the same stream, never a
// replacement for it.
function addStepFeedRaw(line) {
  const row = document.createElement("div");
  row.className = "step-feed-item";
  const label = document.createElement("span");
  label.className = "sf-label";
  label.textContent = line;
  const time = document.createElement("span");
  time.className = "sf-time";
  time.textContent = new Date().toLocaleTimeString();
  row.appendChild(label);
  row.appendChild(time);
  stepFeedEl.appendChild(row);
  stepFeedEl.scrollTop = stepFeedEl.scrollHeight;
}

// components/agent/agent.py logs "── Step N/MAX (K elements) ──" on every
// live iteration -- MAX is run_task.py's MAX_STEPS ceiling (currently
// 1000), not a real total (the actual step count is only known once a run
// ends), so only the numerator is ever shown here. Never render "N of
// MAX" -- it would read as a real progress fraction when it isn't one.
const STEP_LINE_RE = /Step (\d+)\/\d+\s+\((\d+) elements\)/;

function prettifyProgressLine(line) {
  const match = STEP_LINE_RE.exec(line);
  if (!match) {
    addStepFeedRaw(line);
    return;
  }
  const prevCurrent = stepFeedEl.querySelector(".step-feed-item.current");
  if (prevCurrent) {
    prevCurrent.classList.replace("current", "done");
    prevCurrent.querySelector(".sf-icon").innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 13l4 4L19 7"/></svg>';
  }
  const row = document.createElement("div");
  row.className = "step-feed-item current";
  const icon = document.createElement("span");
  icon.className = "sf-icon";
  const label = document.createElement("span");
  label.className = "sf-label";
  label.dataset.stepLabel = `STEP ${match[1]}`;
  label.textContent = `Looking at ${match[2]} elements on screen…`;
  row.appendChild(icon);
  row.appendChild(label);
  stepFeedEl.appendChild(row);
  stepFeedEl.scrollTop = stepFeedEl.scrollHeight;
}

function showFinished(code) {
  finishedDurationEl.textContent = runStartedAt ? formatElapsed(Date.now() - runStartedAt) : "—";
  finishedLineCountEl.textContent = String(progressLineCount);
  if (wasUserStopped) {
    finishedHeadlineEl.textContent = "Stopped — you have control again.";
    finishedBodyEl.textContent = "Intern stopped partway through. Anything already typed or clicked stays as it was.";
  } else if (code === 0) {
    finishedHeadlineEl.textContent = "Finished — you have control again.";
    finishedBodyEl.textContent = "Intern completed the task and handed control back to you.";
  } else {
    finishedHeadlineEl.textContent = "Stopped early — you have control again.";
    finishedBodyEl.textContent = `Intern's run ended unexpectedly (exit code ${code}). Check the activity log below for details.`;
  }
  showTasksSubview("finished");
}

// Esc, "Take back control" (on the running banner) and "Cancel" (on the
// handover overlay) all resolve to the exact same guarded path the
// existing Stop button already uses, rather than calling
// capsulesAPI.stop() directly -- one source of truth for "stop the run."
function stopIfRunning() {
  if (!btnStopCapsule.disabled) btnStopCapsule.click();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") stopIfRunning();
});
btnTakeBackControl.addEventListener("click", stopIfRunning);
btnHandoverCancel.addEventListener("click", stopIfRunning);
btnBackToTasks.addEventListener("click", () => showTasksSubview("list"));
btnRunAgain.addEventListener("click", () => {
  if (!currentCapsule) return;
  window.capsulesAPI.run(currentCapsule.name);
});

// ── Home hero / Tasks header live counts ─────────────────────────────────
// A "task" is anything the user can think of as one job Intern knows:
// every registered capsule, plus any recorded data/demos/ group that
// doesn't have a capsule yet (recorded but not yet trained/registered).
async function computeTaskCount() {
  let groups = [];
  let capsules = [];
  try { groups = await window.workflowsAPI.list(); } catch (e) { /* counted as 0 */ }
  try { capsules = await window.capsulesAPI.list(); } catch (e) { /* counted as 0 */ }
  const capsuleNames = new Set(capsules.map((c) => c.name));
  const groupOnly = groups.filter((g) => !capsuleNames.has(g.name)).length;
  return capsules.length + groupOnly;
}
async function refreshTaskCount() {
  const n = await computeTaskCount();
  homeTaskCountEl.textContent = `(${n})`;
  tasksCountLineEl.textContent = `${n} TASK${n === 1 ? "" : "S"}`;
}
refreshTaskCount();

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

/* Both run_task.py and components/scope2/automate.py print the same
   structured COUNTDOWN_BEGIN / COUNTDOWN N / COUNTDOWN_END sentinel lines
   (added to automate.py for consistency between the two Play workflows,
   even though it has no real window to click into) so this can render an
   actual countdown indicator instead of 5 seconds of scrolling log text
   -- everything else on capsule_progress still just logs normally.

   The one line immediately after COUNTDOWN_BEGIN is each script's own
   plain-text explanation of what's about to happen ("Click on the target
   window NOW." for run_task.py; a different, accurate line for
   automate.py, which has nothing to click) -- captured here and shown as
   the widget's hint text instead of a single hardcoded string that would
   only ever be true for one of the two workflows. Still also logged
   normally below, same as any other progress line. */
let countdownHintPending = false;

function handleCapsuleProgressLine(line) {
  if (line === "COUNTDOWN_BEGIN") {
    ppCountdown.hidden = false;
    countdownHintPending = true;
    // The real handover moment: the process is about to click into the
    // target window and start typing. Everything below is driven off this
    // exact same event, not a separate client-side timer.
    tbAgentPill.hidden = false;
    handoverTaskNameEl.textContent = currentCapsule ? currentCapsule.name : "—";
    handoverOverlay.hidden = false;
    stepFeedEl.innerHTML = "";
    showTasksSubview("running");
    startElapsedTimer();
    return;
  }
  if (line === "COUNTDOWN_END") {
    hideCountdown();
    capsuleLog("Starting…", "ok");
    return;
  }
  const tick = /^COUNTDOWN (\d+)$/.exec(line);
  if (tick) {
    ppCountdownNumber.textContent = tick[1];
    ppCountdownNumber.classList.remove("pp-countdown-tick");
    void ppCountdownNumber.offsetWidth; // restart the animation on every tick
    ppCountdownNumber.classList.add("pp-countdown-tick");
    handoverCountdownEl.textContent = tick[1];
    handoverCountdownEl.classList.remove("pp-countdown-tick");
    void handoverCountdownEl.offsetWidth;
    handoverCountdownEl.classList.add("pp-countdown-tick");
    return;
  }
  if (countdownHintPending) {
    ppCountdownHint.textContent = line;
    countdownHintPending = false;
  }
  capsuleLog(line, "dim");
  progressLineCount += 1;
  prettifyProgressLine(line);
}

function hideCountdown() {
  ppCountdown.hidden = true;
  handoverOverlay.hidden = true;
}

function setCapsuleRunning(isRunning) {
  btnPlay.disabled = isRunning || !currentCapsule;
  btnStopCapsule.disabled = !isRunning;
  // The mini Play/Stop widget has no capsule-picker UI of its own, so it
  // needs to know which capsule name "Play" should mean -- this is the one
  // place that's called both right after a capsule loads/deploys AND on
  // every run-state change, so it's the single spot that keeps main.js's
  // copy in sync rather than duplicating this call at every currentCapsule
  // assignment site.
  window.capsulesAPI.setCurrent(currentCapsule ? currentCapsule.name : null);
}

// Script-kind capsules (e.g. Scope #2) get their own dedicated card in the
// Workflow list (see loadWorkflows() below) and are loaded directly from
// that click -- they're excluded here so an unrelated demo group never
// falls back to guessing one of them.
function findCapsuleForGroup(groupName) {
  const agentCapsules = capsulesCache.filter((c) => c.kind !== "script");
  if (agentCapsules.length === 1) return agentCapsules[0];
  const byName = agentCapsules.find((c) => c.name === groupName);
  if (byName) return byName;
  return agentCapsules[0] || null;
}

/* Loads a capsule into the play panel for real -- called once the fly
   animation (below) lands, or immediately if animation is skipped. */
async function loadCapsuleIntoSlot(capsule) {
  currentCapsule = capsule;
  ppSlotHint.hidden = true;
  ppCapsule.hidden = false;
  applyCapsuleEmojiDisplay(ppCapsuleEmoji, capsule.emoji);
  ppCapsuleName.textContent = capsule.name;
  ppSlot.classList.add("filled");

  // A script-kind capsule (e.g. Scope #2) may or may not have a real,
  // swappable checkpoint -- Scope #2's matcher.pt is a genuine trained
  // artifact with a load path (automate.py --matcher), so it gets the
  // Checkpoint control just like an agent-kind capsule does. Whether the
  // control shows is decided by "does this capsule have a model_path at
  // all," not by kind -- a script-kind capsule with no checkpoint (nothing
  // to swap) still hides it, same as before.
  const runsSummary = capsule.kind === "script"
    ? `runs ${capsule.entrypoint} ${(capsule.args || []).join(" ")}`.trim()
    : "";

  if (!capsule.model_path) {
    ppCheckpointGroup.hidden = true;
    ppCapsuleMeta.textContent = [capsule.description, runsSummary].filter(Boolean).join(" — ");
    setCapsuleRunning(false);
    return;
  }

  ppCheckpointGroup.hidden = false;
  ppCapsuleMeta.textContent = [capsule.description || capsule.model_path, runsSummary]
    .filter(Boolean).join(" — ");
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

/* Brief border/background pulse on the panel, used as the "arrival" cue
   once a flown chip lands. Re-triggerable: force a reflow so clicking a
   second group right after the first restarts the animation instead of
   no-op'ing (removing+re-adding the same class name back-to-back is a
   no-op unless the browser is forced to notice the class was gone). */
function flashPlaySlot() {
  ppSlot.classList.remove("pp-slot-flash");
  void ppSlot.offsetWidth;
  ppSlot.classList.add("pp-slot-flash");
}

/* A fixed, curated set rather than free text -- "a section that displays
   all the available emojis" to click, not type into. The placeholder
   puzzle piece doubles as the first tile, so picking it is how you clear
   back to "unset." */
const EMOJI_CHOICES = [
  "📋", "🚗", "📊", "✅", "🔧", "⚙️", "📁", "💼",
  "🖱️", "⌨️", "📝", "🗂️", "🎯", "🔁", "🤖", "⚡",
  "🧠", "🗃️", "📌", "📇", "🧾", "🛠️", "🗺️", "💡", "🔍",
];

// A "template"/unset emoji shows in gray chrome (background + border) --
// note this can only ever be the CHROME around the glyph, not the glyph
// itself: real emoji render via the OS's own color emoji font, which
// ignores CSS `color` entirely, so there's no way to actually recolor the
// character glyph itself either way.
function applyCapsuleEmojiDisplay(el, emojiValue) {
  el.textContent = emojiValue || PLACEHOLDER_EMOJI;
  el.classList.toggle("is-placeholder", !emojiValue);
}

async function chooseEmojiForCurrentCapsule(value) {
  if (!currentCapsule) return;
  try {
    const updated = await window.capsulesAPI.setEmoji(currentCapsule.name, value);
    currentCapsule = updated;
    // Same staleness pitfall as the checkpoint deploy handler above.
    const idx = capsulesCache.findIndex((c) => c.name === updated.name);
    if (idx !== -1) capsulesCache[idx] = updated;
    applyCapsuleEmojiDisplay(ppCapsuleEmoji, updated.emoji);
    refreshGroupEmojis();
  } catch (e) {
    capsuleLog(`Couldn't set emoji: ${e.message || e}`, "err");
  }
}

let openEmojiPicker = null;

function closeEmojiPicker() {
  if (!openEmojiPicker) return;
  openEmojiPicker.remove();
  openEmojiPicker = null;
  document.removeEventListener("mousedown", handlePickerOutsideClick, true);
  document.removeEventListener("keydown", handlePickerEscape, true);
}
function handlePickerOutsideClick(e) {
  if (openEmojiPicker && !openEmojiPicker.contains(e.target) && e.target !== ppCapsuleEmoji) {
    closeEmojiPicker();
  }
}
function handlePickerEscape(e) {
  if (e.key === "Escape") closeEmojiPicker();
}

/* Click the capsule's emoji bubble in the Play panel to open a small grid
   of every available choice, anchored just below the bubble. Clicking a
   tile commits immediately and closes the picker -- no typing, no
   confirm step. Uses `mousedown` (not `click`) for the outside-close
   listener specifically so it can't catch the very click that opened the
   picker: mousedown for that click already finished before this `click`
   handler even runs, so a mousedown listener added here can only ever
   fire on a later, separate click. */
ppCapsuleEmoji.addEventListener("click", () => {
  if (!currentCapsule) return;
  if (openEmojiPicker) { closeEmojiPicker(); return; }

  const picker = document.createElement("div");
  picker.className = "emoji-picker";

  const makeTile = (value, isClear) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "emoji-picker-tile" + (isClear ? " is-placeholder" : "");
    btn.textContent = value;
    btn.title = isClear ? "Clear (use placeholder)" : value;
    btn.addEventListener("click", () => {
      chooseEmojiForCurrentCapsule(isClear ? "" : value);
      closeEmojiPicker();
    });
    return btn;
  };
  picker.appendChild(makeTile(PLACEHOLDER_EMOJI, true));
  EMOJI_CHOICES.forEach((em) => picker.appendChild(makeTile(em, false)));

  const rect = ppCapsuleEmoji.getBoundingClientRect();
  picker.style.left = `${rect.left}px`;
  picker.style.top = `${rect.bottom + 6}px`;
  document.body.appendChild(picker);
  openEmojiPicker = picker;

  document.addEventListener("mousedown", handlePickerOutsideClick, true);
  document.addEventListener("keydown", handlePickerEscape, true);
});

/* Re-reads each visible group's mapped capsule and patches just its emoji
   span -- cheaper than a full loadWorkflows() and doesn't collapse
   whatever the user has open. */
function refreshGroupEmojis() {
  // Script-kind capsule cards (no dataset.groupName -- they're not a
  // data/demos/ group) are skipped: their emoji is set directly from their
  // own capsule object at render time, not looked up via findCapsuleForGroup.
  workflowsListEl.querySelectorAll(".wf-group-head[data-group-name]").forEach((headEl) => {
    const capsule = findCapsuleForGroup(headEl.dataset.groupName);
    const emojiEl = headEl.querySelector(".wf-emoji");
    if (emojiEl) applyCapsuleEmojiDisplay(emojiEl, capsule ? capsule.emoji : "");
  });
}

btnPlay.addEventListener("click", () => {
  if (!currentCapsule) return;
  window.capsulesAPI.run(currentCapsule.name);
});

btnStopCapsule.addEventListener("click", () => {
  window.capsulesAPI.stop();
});

/* "So you could actually read them" -- the full, real transcript, not
   just whatever still fits in the small scrolling box above. Reads from
   logs/capsule_activity.log (recorder_bridge.py's persisted, truncated-
   fresh-per-run file) rather than just this window's in-memory DOM, so it
   still works even after a reload and always matches the actual file on
   disk that a human (or Claude, next session) would open directly. */
btnCopyLog.addEventListener("click", async () => {
  try {
    const text = await window.capsulesAPI.readLog();
    if (!text) {
      capsuleLog("No log yet — run a capsule first.", "dim");
      return;
    }
    await navigator.clipboard.writeText(text);
    capsuleLog("Full log copied to clipboard.", "ok");
  } catch (e) {
    capsuleLog(`Couldn't copy log: ${e.message || e}`, "err");
  }
});

btnOpenLog.addEventListener("click", async () => {
  try {
    const result = await window.capsulesAPI.openLog();
    if (!result.ok) capsuleLog(result.error || "Couldn't open log.", "dim");
  } catch (e) {
    capsuleLog(`Couldn't open log: ${e.message || e}`, "err");
  }
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
    groups = [];
    workflowsListEl.innerHTML = `<p class="muted">Couldn't read data/demos/ (${e.message || e}).</p>`;
  }
  try {
    capsulesCache = await window.capsulesAPI.list();
  } catch (e) {
    capsulesCache = [];
  }

  workflowsListEl.innerHTML = "";

  // Every registered capsule -- agent-kind (Scope #1) and script-kind
  // (Scope #2) alike -- gets its own direct, clickable card here, shown
  // unconditionally. Originally this only applied to script-kind capsules
  // (they have no recorded data/demos/ group to hang a card off of at
  // all), but an agent-kind capsule with zero recorded demo sessions was
  // just as invisible for the same reason -- the workflow is fully
  // runnable, it just never got its own card until a session existed.
  // Direct user report: "Workflow section only shows sheet-to-portal" with
  // an empty data/demos/ -- the car insurance capsule needs the same
  // unconditional treatment, not a second-class one.
  if (capsulesCache.length) {
    const capsulesHead = document.createElement("h3");
    capsulesHead.className = "wf-section-head";
    capsulesHead.textContent = "Tasks";
    workflowsListEl.appendChild(capsulesHead);
  }
  capsulesCache.forEach((capsule) => {
    const card = document.createElement("div");
    card.className = "wf-group wf-group-capsule";

    const head = document.createElement("div");
    head.className = "wf-group-head";
    head.title = "Click to load this workflow into Play";
    const emojiValue = capsule.emoji || "";
    const emojiText = emojiValue || PLACEHOLDER_EMOJI;
    const emojiClass = "wf-emoji" + (emojiValue ? "" : " is-placeholder");
    head.innerHTML =
      `<span><span class="${emojiClass}">${emojiText}</span><span class="name">${escapeHtml(capsule.name)}</span></span>` +
      `<span class="meta">${escapeHtml(capsule.description || "")}</span>`;
    head.addEventListener("click", () => {
      workflowsListEl.querySelectorAll(".wf-group.capsule-selected")
        .forEach((el) => el.classList.remove("capsule-selected"));
      card.classList.add("capsule-selected");
      loadCapsuleIntoSlot(capsule);
      flashPlaySlot();
    });
    card.appendChild(head);
    workflowsListEl.appendChild(card);
  });

  if (!capsulesCache.length && (!groups || !groups.length)) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No tasks yet — register a capsule or start a recording from the Recorder tab.";
    workflowsListEl.appendChild(empty);
    workflowsLoaded = true;
    refreshTaskCount();
    return;
  }
  if (!groups || !groups.length) {
    workflowsLoaded = true;
    refreshTaskCount();
    return;
  }

  const sessionsHead = document.createElement("h3");
  sessionsHead.className = "wf-section-head";
  sessionsHead.textContent = "Takes";
  workflowsListEl.appendChild(sessionsHead);

  groups.forEach((g, gi) => {
    const card = document.createElement("div");
    card.className = "wf-group" + (gi === 0 ? " open" : "");

    const head = document.createElement("div");
    head.className = "wf-group-head";
    head.title = "Click to expand and load this capsule into Play";
    head.dataset.groupName = g.name;
    const headCapsule = findCapsuleForGroup(g.name);
    const headEmojiValue = headCapsule ? headCapsule.emoji : "";
    const headEmojiText = headEmojiValue || PLACEHOLDER_EMOJI;
    const headEmojiClass = "wf-emoji" + (headEmojiValue ? "" : " is-placeholder");
    head.innerHTML =
      `<span><span class="chev">▸</span><span class="${headEmojiClass}">${headEmojiText}</span><span class="name">${escapeHtml(g.name)}</span></span>` +
      `<span class="meta">${g.sessionCount} session${g.sessionCount===1?"":"s"} · ${g.totalSteps.toLocaleString()} steps</span>`;
    head.addEventListener("click", () => {
      card.classList.toggle("open");
      const capsule = findCapsuleForGroup(g.name);
      if (!capsule) {
        capsuleLog(`No task registered yet for '${g.name}'.`, "dim");
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
  refreshTaskCount();
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

/* ── Inbox (Scope #3) ──────────────────────────────────────────────────── */
const inboxCountLineEl = document.getElementById("inboxCountLine");
const inboxProviderLabel = document.getElementById("inboxProviderLabel");
const btnInboxStart = document.getElementById("btnInboxStart");
const btnInboxStop = document.getElementById("btnInboxStop");
const btnInboxRefresh = document.getElementById("btnInboxRefresh");
const inboxListEl = document.getElementById("inboxList");

const DECISION_LABELS = {
  route_scope1: "Routed to form-filler", route_scope2: "Routed to sheet-matcher",
  reply: "Reply suggested", forward: "Forward suggested",
  flag: "Flagged for review", leave_alone: "Left alone",
};

function decisionLabel(decision) {
  return DECISION_LABELS[decision] || decision;
}

function setInboxRunning(isRunning) {
  btnInboxStart.disabled = isRunning;
  btnInboxStop.disabled = !isRunning;
}

async function loadInboxMessages() {
  let messages = [];
  try {
    messages = await window.inboxAPI.list();
  } catch (e) {
    inboxListEl.innerHTML = `<p class="muted">Couldn't read Inbox Router history (${e.message || e}).</p>`;
    return;
  }
  inboxListEl.innerHTML = "";
  if (!messages.length) {
    inboxListEl.innerHTML = '<p class="muted">Not started. Click Start to begin routing mail.</p>';
  } else {
    // Newest first.
    messages.slice().reverse().forEach((msg) => inboxListEl.appendChild(buildInboxRow(msg)));
  }
  inboxCountLineEl.textContent = `${messages.length} ROUTED`;
}

// Pure builder -- returns a detached row element, doesn't touch the DOM
// itself. loadInboxMessages() appends in order; upsertInboxRow() below
// prepends a single row live as inbox_routed events arrive.
function buildInboxRow(msg) {
  const row = document.createElement("div");
  row.className = "inbox-row";
  row.dataset.messageId = msg.message_id;

  const main = document.createElement("div");
  main.className = "inbox-row-main";
  const chipClass = "inbox-decision-chip" +
    (msg.status === "confirmed" ? " confirmed" : "") +
    (msg.decision === "flag" ? " flag" : "");
  main.innerHTML =
    `<span class="inbox-row-subject">${escapeHtml(msg.subject || "(no subject)")}</span>` +
    `<span class="inbox-row-sender">${escapeHtml(msg.sender || "")}</span>` +
    `<span class="${chipClass}">${escapeHtml(decisionLabel(msg.decision))}</span>` +
    `<span class="inbox-row-rationale">${escapeHtml(msg.rationale || "")}</span>`;
  row.appendChild(main);

  if (msg.status === "pending") {
    const actions = document.createElement("div");
    actions.className = "inbox-row-actions";
    const confirmBtn = document.createElement("button");
    confirmBtn.className = "btn btn-primary btn-sm";
    confirmBtn.type = "button";
    confirmBtn.textContent = "Confirm";
    confirmBtn.addEventListener("click", () => onInboxConfirmClick(msg));
    actions.appendChild(confirmBtn);
    row.appendChild(actions);
  }
  return row;
}

// The Scope #1/#2 handoff reuses the existing, unmodified capsule-run IPC
// call verbatim -- Inbox Router itself does nothing Gmail-side for these
// two decisions; router.py's confirm_suggestion() only records that this
// happened and feeds the pattern profile.
function onInboxConfirmClick(msg) {
  if ((msg.decision === "route_scope1" || msg.decision === "route_scope2") && msg.capsule_name) {
    window.capsulesAPI.run(msg.capsule_name);
  }
  window.inboxAPI.confirm(msg.message_id, msg.decision);
}

function upsertInboxRow(msg) {
  const existing = inboxListEl.querySelector(`[data-message-id="${CSS.escape(msg.message_id)}"]`);
  if (existing) existing.remove();
  const empty = inboxListEl.querySelector(".muted");
  if (empty) empty.remove();
  inboxListEl.insertBefore(buildInboxRow(msg), inboxListEl.firstChild);
  refreshInboxCount();
}

function refreshInboxCount() {
  inboxCountLineEl.textContent = `${inboxListEl.querySelectorAll(".inbox-row").length} ROUTED`;
}

function markInboxRowConfirmed(messageId, decision) {
  const row = inboxListEl.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (!row) return;
  const chip = row.querySelector(".inbox-decision-chip");
  if (chip) {
    chip.classList.add("confirmed");
    chip.textContent = decisionLabel(decision);
  }
  const actions = row.querySelector(".inbox-row-actions");
  if (actions) actions.remove();
}

function markInboxRowOverridden(messageId, newDecision) {
  const row = inboxListEl.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (!row) return;
  const chip = row.querySelector(".inbox-decision-chip");
  if (chip) chip.textContent = decisionLabel(newDecision);
}

btnInboxStart.addEventListener("click", () => window.inboxAPI.start());
btnInboxStop.addEventListener("click", () => window.inboxAPI.stop());
btnInboxRefresh.addEventListener("click", () => loadInboxMessages());
