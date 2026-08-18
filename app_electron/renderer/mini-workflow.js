const stack     = document.getElementById("stack");
const btnHandle = document.getElementById("btnHandle");
const btnSwitch = document.getElementById("btnSwitchWidget");
const btnPlay   = document.getElementById("btnPlay");
const btnStop   = document.getElementById("btnStop");
const infoPill  = document.getElementById("infoPill");

let expanded    = false;
let hasCapsule  = false;
let isRunning   = false;
let capsuleName = null;
let lastStep    = null;   // reset on start/done/stop -- "Step N" while running

function setExpanded(next) {
  expanded = next;
  stack.classList.toggle("expanded", expanded);
  btnHandle.setAttribute("aria-expanded", String(expanded));
}

function refreshButtons() {
  btnPlay.disabled = isRunning || !hasCapsule;
  btnStop.disabled = !isRunning;
  btnHandle.classList.toggle("running", isRunning);
}

// Only ever shown while expanded (see the .info-pill CSS) -- the collapsed
// circle stays exactly as minimal as it's always been, matching the
// existing "the pulse alone is the honest signal" decision above it.
// Running always wins over the idle capsule-name line, since it's the
// more useful thing to know once a run is actually in progress.
function refreshInfoPill() {
  if (isRunning) {
    infoPill.textContent = lastStep != null ? `Step ${lastStep}` : "Starting…";
  } else if (hasCapsule && capsuleName) {
    infoPill.textContent = capsuleName;
  } else {
    infoPill.textContent = "";
  }
}

// Same regex components/agent/agent.py's "── Step N/MAX (K elements) ──"
// line already gets matched with in renderer.js's prettifyProgressLine() --
// MAX is run_task.py's ceiling, not a real total, so only the numerator is
// ever shown here, same honest-progress rule as the main window.
const STEP_LINE_RE = /Step (\d+)\/\d+\s+\((\d+) elements\)/;

btnHandle.addEventListener("click", () => setExpanded(!expanded));
btnSwitch.addEventListener("click", () => window.recorderAPI.switchMiniWidget());

btnPlay.addEventListener("click", () => {
  if (btnPlay.disabled) return;
  window.capsulesAPI.runCurrent();
  setExpanded(false);
});

btnStop.addEventListener("click", () => {
  if (btnStop.disabled) return;
  window.capsulesAPI.stop();
});

// Collapses "when you press outside" -- the window losing OS focus (a
// real click landing anywhere else on the desktop) is exactly that
// signal, no extra IPC/click-tracking needed.
window.addEventListener("blur", () => setExpanded(false));

// main.js pushes this once right after the window finishes loading
// (covers "a capsule was already loaded/running before this widget ever
// opened"), and it's also kept live via the same recorder-event stream
// the main window's own Play panel listens to below.
window.recorderAPI.onEvent((event) => {
  switch (event.event) {
    case "capsule_started":
      isRunning = true;
      lastStep = null;
      refreshButtons();
      refreshInfoPill();
      break;
    case "capsule_done":
    case "capsule_stopped":
      isRunning = false;
      lastStep = null;
      refreshButtons();
      refreshInfoPill();
      break;
    case "capsule_progress": {
      const match = STEP_LINE_RE.exec(event.line || "");
      if (match) {
        lastStep = match[1];
        refreshInfoPill();
      }
      break;
    }
    default:
      break;
  }
});

window.recorderAPI.onMiniWorkflowState((state) => {
  hasCapsule = !!state.hasCapsule;
  isRunning = !!state.isRunning;
  capsuleName = state.capsuleName || null;
  refreshButtons();
  refreshInfoPill();
});
