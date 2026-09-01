// components/inbox_router/cold_email/app.js
let targets = [];
let openEmail = null;
let snackbarTimer = null;

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");
const targetCount = document.getElementById("targetCount");
const snackbar = document.getElementById("snackbar");
const subjectInput = document.getElementById("subjectInput");
const bodyInput = document.getElementById("bodyInput");
const sendBtn = document.getElementById("sendBtn");

function setStatus(message) {
  detailStatus.textContent = message;
  detailStatus.classList.toggle("is-error", message.startsWith("Error"));
}

function showSnackbar(message) {
  clearTimeout(snackbarTimer);
  snackbar.textContent = message;
  snackbar.hidden = false;
  snackbarTimer = setTimeout(() => { snackbar.hidden = true; }, 4000);
}

async function loadTargets() {
  setStatus("");
  try {
    const resp = await fetch("/cold-email/api/targets");
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    targets = data.targets || [];
    renderList();
  } catch (e) {
    targets = [];
    rowList.innerHTML = "";
    targetCount.textContent = "";
    emptyState.hidden = false;
    emptyState.textContent = "Can't reach the local server -- is it running? Try refreshing in a few seconds.";
  }
}

function renderList() {
  rowList.innerHTML = "";
  targetCount.textContent = targets.length > 0 ? String(targets.length) : "";
  emptyState.hidden = targets.length > 0;
  if (targets.length === 0) {
    emptyState.textContent = "Nobody left on the task list. Add names to data/task_list.txt.";
  }
  targets.forEach((target) => {
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <span class="row-sender">${escapeHtml(target.name)}</span>
      <span class="row-snippet">
        <span class="row-subject">${escapeHtml(target.email)}</span>
        <span class="row-preview"> - ${escapeHtml(target.context_line || "")}</span>
      </span>
    `;
    li.addEventListener("click", () => openTarget(target.email));
    rowList.appendChild(li);
  });
}

function openTarget(email) {
  const target = targets.find((t) => t.email === email);
  if (!target) return;
  openEmail = email;
  document.getElementById("detailTargetName").textContent = target.name;
  document.getElementById("detailTargetEmail").textContent = target.email;
  subjectInput.value = target.context_line || "";
  bodyInput.value = "";
  setStatus("");
  listView.hidden = true;
  detailView.hidden = false;
}

function closeTarget() {
  openEmail = null;
  listView.hidden = false;
  detailView.hidden = true;
}

async function sendPending() {
  if (!openEmail) return;
  if (!subjectInput.value.trim()) {
    setStatus("Error: type a subject.");
    subjectInput.focus();
    return;
  }
  if (!bodyInput.value.trim()) {
    setStatus("Error: type a message.");
    bodyInput.focus();
    return;
  }
  sendBtn.disabled = true;
  try {
    const resp = await fetch("/cold-email/api/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: openEmail, subject: subjectInput.value, body: bodyInput.value }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      setStatus(`Error: ${err.error || "request failed"}`);
      return;
    }
    showSnackbar("Sent.");
    await loadTargets();
    closeTarget();
  } catch (e) {
    setStatus("Error: could not reach the server.");
  } finally {
    sendBtn.disabled = false;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadTargets);
document.getElementById("backBtn").addEventListener("click", closeTarget);
document.getElementById("sendBtn").addEventListener("click", sendPending);

loadTargets();
