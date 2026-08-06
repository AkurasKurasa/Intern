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
      break;
    default:
      console.log("Unhandled event:", event);
  }
});
