const stack     = document.getElementById("stack");
const btnHandle = document.getElementById("btnHandle");
const btnPlay   = document.getElementById("btnPlay");
const btnStop   = document.getElementById("btnStop");

let expanded   = false;
let hasCapsule = false;
let isRunning  = false;

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

btnHandle.addEventListener("click", () => setExpanded(!expanded));

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
      refreshButtons();
      break;
    case "capsule_done":
    case "capsule_stopped":
      isRunning = false;
      refreshButtons();
      break;
    default:
      break;
  }
});

window.recorderAPI.onMiniWorkflowState((state) => {
  hasCapsule = !!state.hasCapsule;
  isRunning = !!state.isRunning;
  refreshButtons();
});
