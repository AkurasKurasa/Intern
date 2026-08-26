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
const navSettings = document.getElementById("navSettings");
const emptyState = document.getElementById("emptyState");
const recordingView = document.getElementById("recordingView");
const workflowsWrap = document.getElementById("workflowsWrap");
const settingsWrap = document.getElementById("settingsWrap");
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

// The two mutually-exclusive views inside Home -- the empty-state hero, or
// the big-pane recording feed (mirrors showTasksSubview()'s running view --
// same "the activity belongs in the big pane, not a cramped sidebar log"
// treatment, direct user request).
function showHomeSubview(name) {
  emptyState.hidden = name !== "empty";
  recordingView.hidden = name !== "recording";
}

function showMain(name) {
  const toHome = name === "home";
  const toSettings = name === "settings";
  const toWorkflows = name === "workflows";
  settingsWrap.hidden = !toSettings;
  recorderPanel.hidden = !toHome;
  playPanel.hidden = !toWorkflows;
  navRecorder.classList.toggle("active", toHome);
  navWorkflows.classList.toggle("active", toWorkflows);
  navSettings.classList.toggle("active", toSettings);
  navRecorder.setAttribute("aria-current", toHome ? "page" : "false");
  navWorkflows.setAttribute("aria-current", toWorkflows ? "page" : "false");
  navSettings.setAttribute("aria-current", toSettings ? "page" : "false");
  if (toHome) {
    // Recording already in progress (e.g. switched away and back) should
    // still show the recording feed, not reset to the empty state --
    // btnStop's disabled state is the existing source of truth for "is a
    // recording actually running right now."
    showHomeSubview(!btnStop.disabled ? "recording" : "empty");
    showTasksSubview("");
    refreshTaskCount();
  } else if (toSettings) {
    showHomeSubview("");
    showTasksSubview("");
    updateLlmProviderView();
  } else {
    showHomeSubview("");
    // A capsule already running (e.g. the user switched to Home mid-run
    // and is coming back) should still show the running view, not reset
    // to the list -- btnStopCapsule's disabled state is the single
    // existing source of truth for "is a capsule running right now."
    showTasksSubview(!btnStopCapsule.disabled ? "running" : "list");
    loadWorkflows();
  }
  // Tells main.js which mini overlay to show if the window gets
  // minimized from here -- recorder Start/Stop from Recorder, the round
  // Play/Stop widget from Workflows. Settings has no mini overlay of its
  // own, so it falls into the same plain-recorder-widget branch
  // "workflows" used to be the only alternative to -- main.js's minimize
  // handler already treats anything other than "workflows" that way.
  window.recorderAPI.setActiveSection(toWorkflows ? "workflows" : "home");
}
navRecorder.addEventListener("click", () => showMain("home"));
navWorkflows.addEventListener("click", () => showMain("workflows"));
navSettings.addEventListener("click", () => showMain("settings"));
btnHomeStartRecording.addEventListener("click", () => btnStart.click());
btnHomeBrowseTasks.addEventListener("click", () => navWorkflows.click());

/* ── Recorder panel (always visible, right column) ────────────────────── */
const statusBar   = document.getElementById("statusBar");
const statusDot   = document.getElementById("statusDot");
const statStatus  = document.getElementById("statStatus");
const statFrames  = document.getElementById("statFrames");
const statPending = document.getElementById("statPending");
const statSessions= document.getElementById("statSessions");
const outDirInput = document.getElementById("outDir");
const btnStart    = document.getElementById("btnStart");
const btnStop     = document.getElementById("btnStop");
const recordingFeedEl = document.getElementById("recordingFeed");
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

// Big-pane treatment (same pattern as the Play panel's stepFeed/
// addStepFeedRaw below) instead of the old cramped sidebar log -- direct
// user request ("implement it the same way you implemented the activity
// when in the Workflow page, it gets viewed to the bigger pane").
function log(message, level = "dim") {
  const row = document.createElement("div");
  row.className = "step-feed-item";
  const label = document.createElement("span");
  label.className = `sf-label log-${level}`;
  label.textContent = message;
  const time = document.createElement("span");
  time.className = "sf-time";
  time.textContent = new Date().toLocaleTimeString();
  row.appendChild(label);
  row.appendChild(time);
  recordingFeedEl.appendChild(row);
  recordingFeedEl.scrollTop = recordingFeedEl.scrollHeight;
}

function setRecording(isRecording) {
  btnStart.disabled = isRecording;
  btnStop.disabled = !isRecording;
  statusDot.className = "dot" + (isRecording ? " recording" : "");
  statStatus.textContent = isRecording ? "Recording" : "Idle";
  statusBar.classList.toggle("is-recording", isRecording);
  // Swaps Home's big pane the moment recording actually starts/stops, not
  // just on next navigation -- harmless to call even while Home isn't the
  // currently-visible page, since showMain() re-derives this same state
  // from btnStop.disabled on every visit anyway.
  showHomeSubview(isRecording ? "recording" : "empty");
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
    // Inbox Router (Scope #3) events still arrive on this same stream from
    // the backend (untouched -- this pass only removed the UI, not
    // components/inbox_router/ or its bridge commands) but there's no
    // longer any UI element for them to update, so they're silently
    // ignored here rather than crashing on a null element lookup.
    case "inbox_poll_started":
    case "inbox_routed":
    case "inbox_confirm_applied":
    case "inbox_override_applied":
    case "inbox_stopped":
    case "inbox_log":
    case "inbox_error":
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
const ppDetails         = document.getElementById("ppDetails");
const ppDetailView      = document.getElementById("ppDetailView");
const ppDetailDesc      = document.getElementById("ppDetailDesc");
const ppDetailStats     = document.getElementById("ppDetailStats");
const ppDetailTags      = document.getElementById("ppDetailTags");
const ppDetailEdit      = document.getElementById("ppDetailEdit");
const ppDetailClose     = document.getElementById("ppDetailClose");
const ppDetailEditForm  = document.getElementById("ppDetailEditForm");
const ppDetailDescInput = document.getElementById("ppDetailDescInput");
const ppDetailEmojiGrid = document.getElementById("ppDetailEmojiGrid");
const ppDetailTriggerFields = document.getElementById("ppDetailTriggerFields");
const ppDetailKeywords  = document.getElementById("ppDetailKeywords");
const ppDetailApps      = document.getElementById("ppDetailApps");
const ppDetailDelete    = document.getElementById("ppDetailDelete");
const ppDetailCancel    = document.getElementById("ppDetailCancel");
const ppDetailSave      = document.getElementById("ppDetailSave");
const ppCheckpointGroup = document.getElementById("ppCheckpointGroup");
const ppCheckpoint   = document.getElementById("ppCheckpoint");
const ppTestGroup    = document.getElementById("ppTestGroup");
const btnLaunchMockups = document.getElementById("btnLaunchMockups");
const btnPlay        = document.getElementById("btnPlay");
const btnStopCapsule = document.getElementById("btnStopCapsule");
const btnDeploy      = document.getElementById("btnDeploy");
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
// Direct report: "the count of the workflow in the Workflow tab isn't
// the same of the actual workflow." Real, reproducible cause -- this
// used to count every registered capsule PLUS any recorded data/demos/
// group with no matching capsule yet (e.g. a real "eight_Tabs" folder on
// disk with no registry entry), but loadWorkflows()'s chip grid below
// only ever renders capsulesCache -- registered capsules, never those
// unregistered groups. The header could say "3 TASKS" while only 2 chips
// were ever on screen to count. Fixed by counting exactly what's
// rendered, not a broader definition the grid never actually showed --
// if surfacing unregistered recordings turns out to matter, that's a
// real, separate feature (showing them as their own chips), not a
// silent number that doesn't match anything visible.
async function computeTaskCount() {
  try {
    return (await window.capsulesAPI.list()).length;
  } catch (e) {
    return 0;
  }
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

// The Play panel's own "Activity" section is gone (direct request) -- its
// messages now feed the same big-pane step-feed the Running/Finished
// views already show, instead of a second, redundant scrolling log in
// the narrow panel. Same shape as recordingFeedEl's log() above.
function capsuleLog(message, level = "dim") {
  const row = document.createElement("div");
  row.className = "step-feed-item";
  const label = document.createElement("span");
  label.className = `sf-label log-${level}`;
  label.textContent = message;
  const time = document.createElement("span");
  time.className = "sf-time";
  time.textContent = new Date().toLocaleTimeString();
  row.appendChild(label);
  row.appendChild(time);
  stepFeedEl.appendChild(row);
  stepFeedEl.scrollTop = stepFeedEl.scrollHeight;
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
  // Disabled while a run is live -- popping more windows while the agent
  // is actively driving the mouse/keyboard (Scope #1) would be disruptive.
  btnLaunchMockups.disabled = isRunning;
  // The mini Play/Stop widget has no capsule-picker UI of its own, so it
  // needs to know which capsule name "Play" should mean -- this is the one
  // place that's called both right after a capsule loads/deploys AND on
  // every run-state change, so it's the single spot that keeps main.js's
  // copy in sync rather than duplicating this call at every currentCapsule
  // assignment site.
  window.capsulesAPI.setCurrent(currentCapsule ? currentCapsule.name : null);
}

/* Loads a capsule into the play panel for real -- called once the fly
   animation (below) lands, or immediately if animation is skipped. */
async function loadCapsuleIntoSlot(capsule) {
  // Details are capsule-specific -- closing here means loading a
  // DIFFERENT task never leaves stale details open for the wrong
  // capsule. openTaskDetails() (called right after this by the info
  // button's own handler) just reopens fresh, so this doesn't fight it.
  closeTaskDetails();
  currentCapsule = capsule;
  ppSlotHint.hidden = true;
  ppCapsule.hidden = false;
  applyCapsuleEmojiDisplay(ppCapsuleEmoji, capsule.emoji);
  ppCapsuleName.textContent = capsule.name;
  ppSlot.classList.add("filled");
  // "Test" shows for any loaded task with a real target app to open,
  // unlike Checkpoint -- it's not tied to having a swappable model, just
  // to a task being selected at all. A kind="url" capsule (e.g. Inbox
  // Dispatch) has no separate target app to open -- Play itself already
  // opens the one thing there is to look at -- so "Launch mockups" would
  // just fail with "no test mockups defined" every time. Hidden for that
  // kind instead of shown-but-guaranteed-to-fail.
  ppTestGroup.hidden = capsule.kind === "url";

  // A script-kind capsule (e.g. Scope #2) may or may not have a real,
  // swappable checkpoint -- Scope #2's matcher.pt is a genuine trained
  // artifact with a load path (automate.py --matcher), so it gets the
  // Checkpoint control just like an agent-kind capsule does. Whether the
  // control shows is decided by "does this capsule have a model_path at
  // all," not by kind -- a script-kind capsule with no checkpoint (nothing
  // to swap) still hides it, same as before.
  const runsSummary = capsule.kind === "script"
    ? `runs ${capsule.entrypoint} ${(capsule.args || []).join(" ")}`.trim()
    : capsule.kind === "url"
    ? `opens ${capsule.url} in your browser`
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

// Resets the Play panel back to its empty default -- used when the task
// currently loaded there gets deleted out from under it, so Play never
// keeps pointing at a task that no longer exists.
function clearPlaySlot() {
  closeTaskDetails();
  currentCapsule = null;
  ppSlotHint.hidden = false;
  ppCapsule.hidden = true;
  ppSlot.classList.remove("filled");
  ppCheckpointGroup.hidden = true;
  ppTestGroup.hidden = true;
  workflowsListEl.querySelectorAll(".task-chip.capsule-selected")
    .forEach((el) => el.classList.remove("capsule-selected"));
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

btnLaunchMockups.addEventListener("click", async () => {
  if (!currentCapsule) return;
  btnLaunchMockups.disabled = true;
  try {
    const result = await window.capsulesAPI.launchTestMockups(currentCapsule.name);
    if (result.ok) {
      capsuleLog(`Opened: ${result.opened.join(", ")}`, "ok");
    } else {
      capsuleLog(result.error, "err");
    }
  } catch (e) {
    capsuleLog(`Couldn't launch mockups: ${e.message || e}`, "err");
  } finally {
    btnLaunchMockups.disabled = false;
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
    refreshChipEmojis();
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

/* Re-reads each visible task chip's own capsule and patches just its emoji
   span -- cheaper than a full loadWorkflows() and doesn't collapse
   whatever the user has open. */
function refreshChipEmojis() {
  workflowsListEl.querySelectorAll(".task-chip").forEach((chipEl) => {
    const capsule = capsulesCache.find((c) => c.name === chipEl.dataset.capsuleName);
    const emojiEl = chipEl.querySelector(".task-chip-emoji");
    if (emojiEl) applyCapsuleEmojiDisplay(emojiEl, capsule ? capsule.emoji : "");
  });
}

btnPlay.addEventListener("click", async () => {
  if (!currentCapsule) return;
  // kind="url" never enters the running state (no subprocess, no
  // capsule_started event will ever arrive for it) -- log it directly
  // instead of leaving the button looking like it did nothing.
  const result = await window.capsulesAPI.run(currentCapsule.name);
  if (result && result.opened) {
    capsuleLog(`Opened ${currentCapsule.name} in your browser.`, "ok");
  }
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

/* ── Recorder panel's "Save to" dropdown -- lists real tasks (capsules),
   not a raw scan of data/demos/. Direct correction: "edit Save to to the
   specific workflows not some random data/demos/x" -- a folder that
   doesn't belong to any actual task is exactly what this used to allow.
   Script-kind tasks (e.g. Scope #2) don't record via the Recorder at all,
   so they're excluded here the same way findCapsuleForGroup() already
   excludes them elsewhere. ─────────────────────────────────────────────── */
async function populateOutDirOptions() {
  let capsules = [];
  try {
    capsules = await window.capsulesAPI.list();
  } catch (e) {
    return;
  }
  const recordable = capsules.filter((c) => c.kind === "agent");
  if (!recordable.length) return;
  const current = outDirInput.value;
  outDirInput.innerHTML = "";
  recordable.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = `data/demos/${c.name}`;
    opt.textContent = `data/demos/${c.name}`;
    outDirInput.appendChild(opt);
  });
  if (Array.from(outDirInput.options).some((o) => o.value === current)) {
    outDirInput.value = current;
  }
}
populateOutDirOptions();

// Tasks are shown as a grid of compact chips -- one per registered
// capsule (agent-kind and script-kind alike), each a direct, clickable
// tile into the Play panel. No nested "Takes" (recorded session) list
// here anymore -- direct request to remove it; browsing individual
// recordings isn't part of the Tasks page's job.
async function loadWorkflows() {
  workflowsListEl.innerHTML = '<p class="muted">Loading…</p>';
  try {
    capsulesCache = await window.capsulesAPI.list();
  } catch (e) {
    capsulesCache = [];
  }

  workflowsListEl.innerHTML = "";

  if (!capsulesCache.length) {
    workflowsListEl.innerHTML =
      '<p class="muted">No tasks yet — register a capsule or start a recording from the Recorder tab.</p>';
    workflowsLoaded = true;
    refreshTaskCount();
    return;
  }

  const grid = document.createElement("div");
  grid.className = "task-chip-grid";
  capsulesCache.forEach((capsule) => grid.appendChild(buildTaskChip(capsule)));
  workflowsListEl.appendChild(grid);

  workflowsLoaded = true;
  refreshTaskCount();
}

// Spec-sheet kicker text -- real data (the capsule's actual kind), not
// decorative filler, mirroring how the reference's own kickers ("Data
// Bus Width 64-bits") are real specs, not placeholder labels.
function taskChipKicker(capsule) {
  if (capsule.kind === "script") return "TASK · SCRIPT";
  if (capsule.kind === "url") return "TASK · LINK";
  return "TASK · AGENT";
}

// Shared by the chip body click and the info button below -- both need
// "make this the selected/loaded task," the info button just also opens
// details on top of that instead of stopping there.
function selectTaskChip(chip, capsule) {
  workflowsListEl.querySelectorAll(".task-chip.capsule-selected")
    .forEach((el) => el.classList.remove("capsule-selected"));
  chip.classList.add("capsule-selected");
  loadCapsuleIntoSlot(capsule);
  flashPlaySlot();
}

function buildTaskChip(capsule) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "task-chip";
  chip.title = "Click to load this task into Play";
  chip.dataset.capsuleName = capsule.name;
  const emojiValue = capsule.emoji || "";
  const emojiText = emojiValue || PLACEHOLDER_EMOJI;
  const emojiClass = "task-chip-emoji" + (emojiValue ? "" : " is-placeholder");
  chip.innerHTML =
    `<span class="task-chip-kicker">${escapeHtml(taskChipKicker(capsule))}</span>` +
    `<span class="task-chip-bottom">` +
      `<span class="${emojiClass}">${emojiText}</span>` +
      `<span class="task-chip-name">${escapeHtml(capsule.name)}</span>` +
    `</span>` +
    // Repurposed 2026-08-19 (direct request) from "open a centered edit
    // modal" to "open task details in the Play panel" -- pencil swapped
    // for an info glyph to match what it now does first (show info,
    // Edit is one click further in), same button so there's one obvious
    // place to find task details, not two competing entry points.
    `<button type="button" class="task-chip-edit" title="Task details">` +
      `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="8" r="0.5" fill="currentColor" stroke-width="1.2"/></svg>` +
    `</button>`;
  chip.addEventListener("click", () => selectTaskChip(chip, capsule));
  chip.querySelector(".task-chip-edit").addEventListener("click", (e) => {
    e.stopPropagation();
    selectTaskChip(chip, capsule);
    openTaskDetails(capsule);
  });
  return chip;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ── Task details (view + Update/Delete) -- moved 2026-08-19 (direct
   request) from a centered modal into the Play panel itself, opened via
   a chip's small info button. Renaming is deliberately not offered here
   -- a task's name ties together its registry entry, its
   data/demos/<name> folder, and (once trained) its model path, so a
   rename would mean moving files, a separate, harder feature not asked
   for. ─────────────────────────────────────────────────────────────── */
let detailsCapsule = null;   // which capsule ppDetails is currently showing

function closeTaskDetails() {
  ppDetails.hidden = true;
  detailsCapsule = null;
}

async function openTaskDetails(capsule) {
  detailsCapsule = capsule;
  ppDetails.hidden = false;
  ppDetailEditForm.hidden = true;
  ppDetailView.hidden = false;
  await renderTaskDetailsView(capsule);
}

// Real stats (session count, frame count, last-recorded date) pulled
// from the same data/demos/ scan the Recorder tab's own session counter
// already reads -- workflowsAPI.list() -- not a fabricated number.
async function renderTaskDetailsView(capsule) {
  ppDetailDesc.textContent = capsule.description || "No description yet.";

  let groups = [];
  try { groups = await window.workflowsAPI.list(); } catch (e) { /* stats just won't show */ }
  const group = groups.find((g) => g.name === capsule.name);
  ppDetailStats.textContent = group
    ? `${group.sessionCount} session${group.sessionCount === 1 ? "" : "s"} · ` +
      `${group.totalSteps} frame${group.totalSteps === 1 ? "" : "s"} · ` +
      `recorded ${new Date(group.mtime).toLocaleDateString()}`
    : "No recordings yet.";

  ppDetailTags.innerHTML = "";
  if (capsule.kind !== "script") {
    const tags = [...(capsule.trigger_keywords || []), ...(capsule.trigger_apps || [])];
    if (tags.length) {
      tags.forEach((t) => {
        const el = document.createElement("span");
        el.className = "pp-detail-tag";
        el.textContent = t;
        ppDetailTags.appendChild(el);
      });
    }
  }
}

let detailSelectedEmoji = "";
function renderDetailEmojiGrid() {
  ppDetailEmojiGrid.innerHTML = "";
  const makeTile = (value, isClear) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const selected = isClear ? detailSelectedEmoji === "" : detailSelectedEmoji === value;
    btn.className = "emoji-picker-tile" + (isClear ? " is-placeholder" : "") + (selected ? " is-selected" : "");
    btn.textContent = isClear ? PLACEHOLDER_EMOJI : value;
    btn.title = isClear ? "Clear (use placeholder)" : value;
    btn.addEventListener("click", () => { detailSelectedEmoji = isClear ? "" : value; renderDetailEmojiGrid(); });
    return btn;
  };
  ppDetailEmojiGrid.appendChild(makeTile(PLACEHOLDER_EMOJI, true));
  EMOJI_CHOICES.forEach((em) => ppDetailEmojiGrid.appendChild(makeTile(em, false)));
}

ppDetailEdit.addEventListener("click", () => {
  const capsule = detailsCapsule;
  if (!capsule) return;
  const isAgent = capsule.kind === "agent";
  ppDetailDescInput.value = capsule.description || "";
  detailSelectedEmoji = capsule.emoji || "";
  renderDetailEmojiGrid();
  ppDetailTriggerFields.hidden = !isAgent;
  if (isAgent) {
    ppDetailKeywords.value = (capsule.trigger_keywords || []).join(", ");
    ppDetailApps.value = (capsule.trigger_apps || []).join(", ");
  }
  ppDetailView.hidden = true;
  ppDetailEditForm.hidden = false;
});

ppDetailCancel.addEventListener("click", () => {
  ppDetailEditForm.hidden = true;
  ppDetailView.hidden = false;
});

ppDetailClose.addEventListener("click", closeTaskDetails);

ppDetailSave.addEventListener("click", async () => {
  const capsule = detailsCapsule;
  if (!capsule) return;
  const isAgent = capsule.kind === "agent";
  const updates = {
    description: ppDetailDescInput.value,
    emoji: detailSelectedEmoji,
  };
  if (isAgent) {
    updates.trigger_keywords = ppDetailKeywords.value.split(",").map((s) => s.trim()).filter(Boolean);
    updates.trigger_apps = ppDetailApps.value.split(",").map((s) => s.trim()).filter(Boolean);
  }
  try {
    const updated = await window.capsulesAPI.update(capsule.name, updates);
    detailsCapsule = updated;
    if (currentCapsule && currentCapsule.name === updated.name) {
      currentCapsule = updated;
      applyCapsuleEmojiDisplay(ppCapsuleEmoji, updated.emoji);
    }
    ppDetailEditForm.hidden = true;
    ppDetailView.hidden = false;
    await renderTaskDetailsView(updated);
    await loadWorkflows();
    await populateOutDirOptions();
  } catch (e) {
    capsuleLog(`Couldn't save task: ${e.message || e}`, "err");
  }
});

// Registry-entry only, confirmed directly -- recorded sessions and any
// trained checkpoint stay on disk. window.confirm() (not a custom
// in-app step) since Electron's renderer is a real Chromium context and
// this is a one-off, infrequent action, not worth a bespoke dialog.
ppDetailDelete.addEventListener("click", async () => {
  const capsule = detailsCapsule;
  if (!capsule) return;
  const ok = window.confirm(
    `Delete task '${capsule.name}'?\n\nThis only removes it from the task list. ` +
    `Any recorded sessions in data/demos/${capsule.name} and any trained checkpoint stay on disk.`
  );
  if (!ok) return;
  try {
    await window.capsulesAPI.delete(capsule.name);
    if (currentCapsule && currentCapsule.name === capsule.name) clearPlaySlot();
    closeTaskDetails();
    await loadWorkflows();
    await populateOutDirOptions();
  } catch (e) {
    capsuleLog(`Couldn't delete task: ${e.message || e}`, "err");
  }
});

btnRefreshWorkflows.addEventListener("click", () => { loadWorkflows(); populateOutDirOptions(); });

/* ── Create task — registers a real capsule entry (so it shows up as a
   chip immediately) AND reserves its matching data/demos/<name>/ recording
   folder in one step, via capsulesAPI.create(). Previously this only made
   the folder (workflowsAPI.create()) with no capsule to go with it -- a
   "workflow" that never actually became a task. Description/emoji/trigger
   fields are filled in afterward through each chip's own edit panel. ──── */
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
    await window.capsulesAPI.create(name, "");
    wfCreateForm.hidden = true;
    await loadWorkflows();
    await populateOutDirOptions();
  } catch (e) {
    capsuleLog(`Couldn't create task: ${e.message || e}`, "err");
  }
});

/* ── Settings tab -- LM Studio server/model control ───────────────────── */
const btnSettingsRefresh      = document.getElementById("btnSettingsRefresh");
const lmStudioServerDot       = document.getElementById("lmStudioServerDot");
const lmStudioServerLabel     = document.getElementById("lmStudioServerLabel");
const btnStartLmStudioServer  = document.getElementById("btnStartLmStudioServer");
const lmStudioModelSelect     = document.getElementById("lmStudioModelSelect");
const btnLoadLmStudioModel    = document.getElementById("btnLoadLmStudioModel");
const lmStudioLoadedLabel     = document.getElementById("lmStudioLoadedLabel");

function renderLmStudioStatus(status) {
  lmStudioServerDot.className = "dot";
  lmStudioServerDot.classList.add(status.serverRunning ? "ok" : "error");
  lmStudioServerLabel.textContent = status.serverRunning
    ? "Server running"
    : "Server not running";
  btnStartLmStudioServer.hidden = status.serverRunning;

  lmStudioModelSelect.innerHTML = "";
  if (!status.models.length) {
    lmStudioModelSelect.innerHTML = '<option value="">No models downloaded</option>';
    lmStudioModelSelect.disabled = true;
    btnLoadLmStudioModel.disabled = true;
  } else {
    status.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.modelKey;
      const isLoaded = status.loadedModelKeys.includes(m.modelKey);
      opt.textContent = m.displayName + (isLoaded ? "  (loaded)" : "");
      lmStudioModelSelect.appendChild(opt);
    });
    // Prefer whatever's already loaded as the selected option, so the
    // dropdown reflects real state rather than always defaulting to the
    // first entry.
    const loadedOpt = Array.from(lmStudioModelSelect.options)
      .find((o) => status.loadedModelKeys.includes(o.value));
    if (loadedOpt) lmStudioModelSelect.value = loadedOpt.value;
    lmStudioModelSelect.disabled = false;
    btnLoadLmStudioModel.disabled = false;
  }

  lmStudioLoadedLabel.textContent = status.loadedModelKeys.length
    ? `Loaded: ${status.loadedModelKeys.join(", ")}`
    : "No model loaded";
}

// Real lag traced to a real cause, direct report ("There is a lag from
// the Settings as it loads, I don't want that"): every navigation to the
// Settings tab -- not just an explicit Refresh click -- called
// window.settingsAPI.refreshLmStudio(), which shells out to LM Studio's
// own CLI (execFile with up to a 15s timeout, see runLmsCli() in
// main.js) to check the server and list models. Simply clicking the
// Settings nav button paid that cost every single time, whether or not
// anything had changed since the last check.
//
// Fixed by caching the last real status and rendering it INSTANTLY (no
// await, no lag) on plain navigation; a fresh CLI round-trip only
// happens on the very first check this session, or when the user
// explicitly asks for one (Refresh button, Start server, Load model --
// all pass forceRefresh=true since those actions genuinely need fresh
// state). The status shown is never fabricated -- it's always either a
// real previous result or a real fresh one, just not re-fetched on
// every idle tab switch.
let lastLmStudioStatus = null;

async function loadSettingsPanel(forceRefresh = false) {
  if (!forceRefresh && lastLmStudioStatus) {
    renderLmStudioStatus(lastLmStudioStatus);
    return;
  }

  lmStudioServerLabel.textContent = "Checking…";
  lmStudioServerDot.className = "dot";
  lmStudioModelSelect.disabled = true;
  lmStudioModelSelect.innerHTML = "<option>Loading…</option>";
  btnLoadLmStudioModel.disabled = true;

  let status;
  try {
    status = await window.settingsAPI.refreshLmStudio();
  } catch (e) {
    lmStudioServerLabel.textContent = `Couldn't reach LM Studio: ${e.message || e}`;
    lmStudioServerDot.classList.add("error");
    lmStudioModelSelect.innerHTML = "<option>—</option>";
    return;
  }

  lastLmStudioStatus = status;
  renderLmStudioStatus(status);
}

// ── LLM provider dropdown -- LM Studio keeps its full working panel above;
// the other providers (agent.py already supports anthropic/groq/gemini)
// get a real API-key field, saved to the repo's .env file -- the same
// file run_task.py's own loader already reads, so a key saved here is
// immediately usable by a real run, not just a UI-only preference.
const llmProviderSelect  = document.getElementById("llmProviderSelect");
const llmProviderLmStudio = document.getElementById("llmProviderLmStudio");
const llmProviderOther   = document.getElementById("llmProviderOther");
const llmApiKeyInput     = document.getElementById("llmApiKeyInput");
const btnSaveApiKey      = document.getElementById("btnSaveApiKey");
const llmApiKeyStatus    = document.getElementById("llmApiKeyStatus");

async function refreshApiKeyStatus() {
  const provider = llmProviderSelect.value;
  llmApiKeyInput.value = "";
  llmApiKeyStatus.textContent = "Checking…";
  try {
    const status = await window.settingsAPI.getApiKeyStatus(provider);
    if (!status.ok) {
      llmApiKeyStatus.textContent = status.error;
      return;
    }
    llmApiKeyStatus.textContent = status.isSet
      ? `Key saved (${status.masked}). Paste a new one to replace it.`
      : "No key saved yet.";
  } catch (e) {
    llmApiKeyStatus.textContent = `Couldn't check: ${e.message || e}`;
  }
}

function updateLlmProviderView(forceRefresh = false) {
  const isLmStudio = llmProviderSelect.value === "lmstudio";
  llmProviderLmStudio.hidden = !isLmStudio;
  llmProviderOther.hidden = isLmStudio;
  if (isLmStudio) loadSettingsPanel(forceRefresh);
  else refreshApiKeyStatus();
}
llmProviderSelect.addEventListener("change", () => updateLlmProviderView());

btnSaveApiKey.addEventListener("click", async () => {
  const provider = llmProviderSelect.value;
  const value = llmApiKeyInput.value;
  btnSaveApiKey.disabled = true;
  try {
    const result = await window.settingsAPI.saveApiKey(provider, value);
    if (result.ok) {
      llmApiKeyInput.value = "";
      await refreshApiKeyStatus();
    } else {
      llmApiKeyStatus.textContent = result.error;
    }
  } catch (e) {
    llmApiKeyStatus.textContent = `Couldn't save: ${e.message || e}`;
  } finally {
    btnSaveApiKey.disabled = false;
  }
});
llmApiKeyInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") btnSaveApiKey.click();
});

btnSettingsRefresh.addEventListener("click", () => updateLlmProviderView(true));

btnStartLmStudioServer.addEventListener("click", async () => {
  btnStartLmStudioServer.disabled = true;
  lmStudioServerLabel.textContent = "Starting…";
  try {
    const result = await window.settingsAPI.startLmStudioServer();
    if (!result.ok) lmStudioServerLabel.textContent = result.error;
  } catch (e) {
    lmStudioServerLabel.textContent = `Couldn't start server: ${e.message || e}`;
  } finally {
    btnStartLmStudioServer.disabled = false;
    loadSettingsPanel(true);
  }
});

btnLoadLmStudioModel.addEventListener("click", async () => {
  const modelKey = lmStudioModelSelect.value;
  if (!modelKey) return;
  btnLoadLmStudioModel.disabled = true;
  lmStudioLoadedLabel.textContent = "Loading…";
  try {
    const result = await window.settingsAPI.loadLmStudioModel(modelKey);
    lmStudioLoadedLabel.textContent = result.ok ? "Loaded." : result.error;
  } catch (e) {
    lmStudioLoadedLabel.textContent = `Couldn't load model: ${e.message || e}`;
  } finally {
    loadSettingsPanel(true);
  }
});

// ── Vision section -- perception backend preference, multi-select
// (direct request -- more than one backend can be relevant to a task at
// once, e.g. UIA + a VLM fallback). Real, working control (persists via
// localStorage as an array), but honestly scoped: run_task.py's own
// --perception uia|vision flag exists, but nothing in the bridge/IPC
// chain passes it through for a live Play run yet -- that's a real,
// separate backend change, not part of this UI pass. Excel is included
// as a 4th option alongside the three requested (UIA Tree/VLM/OCR)
// because it's a real, already-built perception backend in this project
// (ExcelObserver, proven for Scope #2), not an invented one.
const visionOptions = document.getElementById("visionOptions");
const visionSavedHint = document.getElementById("visionSavedHint");
const VISION_STORAGE_KEY = "intern.visionBackends";
const VISION_LABELS = { uia: "UIA Tree", vlm: "VLM", ocr: "OCR", excel: "Excel" };

function checkedVisionValues() {
  return Array.from(visionOptions.querySelectorAll('input[name="vision"]:checked'))
    .map((el) => el.value);
}

function describeVisionSelection(values) {
  if (!values.length) return "Nothing selected — no perception backend chosen.";
  const labels = values.map((v) => VISION_LABELS[v] || v).join(", ");
  return `Saved: ${labels} (preference only — not yet wired into a live run).`;
}

function loadVisionPreference() {
  let saved = ["uia"];
  try {
    const raw = localStorage.getItem(VISION_STORAGE_KEY);
    if (raw) saved = JSON.parse(raw);
  } catch (e) { /* localStorage unavailable or corrupt -- fall back to the default */ }
  visionOptions.querySelectorAll('input[name="vision"]').forEach((el) => {
    el.checked = saved.includes(el.value);
  });
  visionSavedHint.textContent = describeVisionSelection(checkedVisionValues());
}
visionOptions.addEventListener("change", (e) => {
  if (e.target.name !== "vision") return;
  const values = checkedVisionValues();
  try {
    localStorage.setItem(VISION_STORAGE_KEY, JSON.stringify(values));
  } catch (err) { /* localStorage unavailable -- selection still applies visually */ }
  visionSavedHint.textContent = describeVisionSelection(values);
});
loadVisionPreference();
