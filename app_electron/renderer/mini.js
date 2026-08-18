const stack       = document.getElementById("stack");
const btnHandle   = document.getElementById("btnHandle");
const btnStart    = document.getElementById("btnStart");
const btnStop     = document.getElementById("btnStop");
const infoFrames  = document.getElementById("infoFrames");
const infoSessions= document.getElementById("infoSessions");

let expanded    = false;
let isRecording = false;
let frames      = 0;
let sessions    = 0;   // matches renderer.js's own "count of saves seen by
                        // this window since it loaded" convention exactly --
                        // not read from disk, resets per window like the
                        // main window's own counter already does.

function setExpanded(next) {
  expanded = next;
  stack.classList.toggle("expanded", expanded);
  btnHandle.setAttribute("aria-expanded", String(expanded));
}

function refreshButtons() {
  btnStart.disabled = isRecording;
  btnStop.disabled = !isRecording;
  btnHandle.classList.toggle("recording", isRecording);
}

// Only ever shown while expanded, matching the Workflows-tab sibling's
// "collapsed stays minimal" rule.
function refreshInfoPill() {
  infoFrames.textContent = `${frames} frame${frames === 1 ? "" : "s"}`;
  infoSessions.textContent = `${sessions} session${sessions === 1 ? "" : "s"}`;
}
refreshInfoPill();

btnHandle.addEventListener("click", () => setExpanded(!expanded));

btnStart.addEventListener("click", () => {
  if (btnStart.disabled) return;
  window.recorderAPI.start(null);
  frames = 0;
  refreshInfoPill();
});
btnStop.addEventListener("click", () => {
  if (btnStop.disabled) return;
  window.recorderAPI.stop();
});

// Collapses "when you press outside" -- same real signal as the
// Workflows-tab sibling: the window losing OS focus.
window.addEventListener("blur", () => setExpanded(false));

window.recorderAPI.onEvent((event) => {
  switch (event.event) {
    case "started":
      isRecording = true;
      frames = 0;
      refreshButtons();
      refreshInfoPill();
      break;
    case "frame_count":
      frames = event.value;
      refreshInfoPill();
      break;
    case "saved":
      isRecording = false;
      sessions += 1;
      frames = event.steps;
      refreshButtons();
      refreshInfoPill();
      break;
    case "error":
      isRecording = false;
      refreshButtons();
      break;
    default:
      break;
  }
});

refreshButtons();
