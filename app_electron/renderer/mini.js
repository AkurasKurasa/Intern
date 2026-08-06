const statusDot  = document.getElementById("statusDot");
const statStatus = document.getElementById("statStatus");
const statFrames = document.getElementById("statFrames");
const btnStart   = document.getElementById("btnStart");
const btnStop    = document.getElementById("btnStop");
const btnRestore = document.getElementById("btnRestore");

function setRecording(isRecording) {
  btnStart.disabled = isRecording;
  btnStop.disabled = !isRecording;
  statusDot.className = "dot" + (isRecording ? " recording" : "");
  statStatus.textContent = isRecording ? "Recording" : "Idle";
}

btnStart.addEventListener("click", () => {
  window.recorderAPI.start(null);
  statFrames.textContent = "0";
});
btnStop.addEventListener("click", () => window.recorderAPI.stop());
btnRestore.addEventListener("click", () => window.recorderAPI.restoreMain());

window.recorderAPI.onEvent((event) => {
  switch (event.event) {
    case "started":
      setRecording(true);
      break;
    case "frame_count":
      statFrames.textContent = String(event.value);
      break;
    case "saved":
      setRecording(false);
      statFrames.textContent = String(event.steps);
      break;
    case "error":
      setRecording(false);
      statusDot.classList.add("error");
      break;
    default:
      break;
  }
});
