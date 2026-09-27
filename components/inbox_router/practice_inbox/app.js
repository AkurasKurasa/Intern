// components/inbox_router/practice_inbox/app.js
let inboxMessages = [];
let openMessageId = null;
let searchQuery = "";
let currentView = "inbox"; // "inbox" | "starred"
let snackbarTimer = null;

let starredIds = new Set();
try {
  starredIds = new Set(JSON.parse(localStorage.getItem("inboxPractice.starred") || "[]"));
} catch (e) {
  starredIds = new Set();
}

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");
const inboxCount = document.getElementById("inboxCount");
const toolbarCount = document.getElementById("toolbarCount");
const searchInput = document.getElementById("searchInput");
const navInbox = document.getElementById("navInbox");
const navStarred = document.getElementById("navStarred");
const snackbar = document.getElementById("snackbar");
const replyBoxWrap = document.getElementById("replyBoxWrap");
const replyBody = document.getElementById("replyBody");
const sendBtn = document.getElementById("sendBtn");

// Reply/Forward/Schedule need real typed content first -- clicking the
// pill or the Snooze icon just reveals the box and remembers which one
// is pending. Archive needs nothing typed, so it records immediately.
let pendingDecision = null;

const STAR_FILLED = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>';
const STAR_OUTLINE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 15.4l3.76 2.27-1-4.28 3.32-2.88-4.38-.38L12 6l-1.71 4.04-4.38.38 3.32 2.88-1 4.28L12 15.4M12 2l2.81 6.63L22 9.24l-5.46 4.73L18.18 21 12 17.27 5.82 21l1.64-7.03L2 9.24l7.19-.61L12 2z"/></svg>';

function showSnackbar(message) {
  clearTimeout(snackbarTimer);
  snackbar.textContent = message;
  snackbar.hidden = false;
  snackbarTimer = setTimeout(() => { snackbar.hidden = true; }, 4000);
}

function snippetOf(bodyText) {
  const flat = (bodyText || "").replace(/\s+/g, " ").trim();
  return flat.length > 70 ? `${flat.slice(0, 70)}...` : flat;
}

function saveStarred() {
  try {
    localStorage.setItem("inboxPractice.starred", JSON.stringify([...starredIds]));
  } catch (e) {
    // Best-effort only -- starring is a local convenience, not a pipeline decision.
  }
}

async function loadInbox() {
  detailStatus.textContent = "";
  try {
    const resp = await fetch("/practice/api/inbox");
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    inboxMessages = data.messages || [];
    renderList();
  } catch (e) {
    inboxMessages = [];
    rowList.innerHTML = "";
    emptyState.hidden = false;
    emptyState.textContent = "Can't reach the local server -- is it running? Try refreshing in a few seconds.";
  }
}

function matchesSearch(email) {
  if (!searchQuery) return true;
  const haystack = `${email.sender || ""} ${email.sender_email || ""} ${email.subject || ""}`.toLowerCase();
  return haystack.includes(searchQuery);
}

function matchesView(email) {
  return currentView !== "starred" || starredIds.has(email.message_id);
}

function setView(view) {
  currentView = view;
  navInbox.classList.toggle("nav-item-active", view === "inbox");
  navInbox.classList.toggle("nav-item-muted", view !== "inbox");
  navStarred.classList.toggle("nav-item-active", view === "starred");
  navStarred.classList.toggle("nav-item-muted", view !== "starred");
  renderList();
}

function renderList() {
  const viewMessages = inboxMessages.filter(matchesView);
  const visible = viewMessages.filter(matchesSearch);

  rowList.innerHTML = "";
  inboxCount.textContent = inboxMessages.length > 0 ? String(inboxMessages.length) : "";
  toolbarCount.textContent = inboxMessages.length > 0 ? `1-${visible.length} of ${viewMessages.length}` : "";
  emptyState.hidden = visible.length > 0;
  if (currentView === "starred") {
    emptyState.textContent = "No starred emails yet. Click the star on an email to star it.";
  } else {
    emptyState.textContent = inboxMessages.length === 0
      ? "No emails to practice on. Click Refresh."
      : "No emails match your search.";
  }

  visible.forEach((email) => {
    const id = email.message_id;
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <input type="checkbox" class="row-checkbox" disabled title="Not available in this tool -- each email needs its own practice decision.">
      <span class="row-star ${starredIds.has(id) ? "starred" : ""}">${starredIds.has(id) ? STAR_FILLED : STAR_OUTLINE}</span>
      <span class="row-sender">${escapeHtml(email.sender || email.sender_email || "")}</span>
      <span class="row-snippet">
        <span class="row-subject">${escapeHtml(email.subject || "")}</span>
        <span class="row-preview"> - ${escapeHtml(snippetOf(email.body_text))}</span>
      </span>
    `;
    li.querySelector(".row-star").addEventListener("click", (e) => {
      e.stopPropagation();
      if (starredIds.has(id)) starredIds.delete(id); else starredIds.add(id);
      saveStarred();
      renderList();
    });
    li.addEventListener("click", () => openMessage(id));
    rowList.appendChild(li);
  });
}

function clearPendingSelection() {
  pendingDecision = null;
  replyBoxWrap.hidden = true;
  replyBody.value = "";
}

function openMessage(messageId) {
  const email = inboxMessages.find((e) => e.message_id === messageId);
  if (!email) return;
  openMessageId = messageId;
  document.getElementById("detailAvatar").textContent =
    (email.sender || email.sender_email || "?").charAt(0).toUpperCase();
  document.getElementById("detailSender").textContent = email.sender || email.sender_email || "";
  document.getElementById("detailSubject").textContent = email.subject || "";
  document.getElementById("detailBody").textContent = email.body_text || "(no body available)";
  clearPendingSelection();
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  clearPendingSelection();
  detailView.hidden = true;
  listView.hidden = false;
}

function selectDecision(decision) {
  pendingDecision = decision;
  replyBoxWrap.hidden = false;
  replyBody.placeholder = decision === "schedule"
    ? "Type your note -- this exact text is what gets recorded, nothing is written for you."
    : "Type your reply -- this exact text is what gets recorded, nothing is written for you.";
  replyBody.focus();
}

async function recordDecision(decision, body = "") {
  if (!openMessageId) return;
  try {
    const resp = await fetch("/practice/api/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: openMessageId, decision, reply_body: body }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      detailStatus.textContent = `Error: ${err.error || "record failed"}`;
      return;
    }
    closeMessage();
    showSnackbar("Recorded.");
  } catch (e) {
    detailStatus.textContent = "Error: could not reach the server.";
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadInbox);
document.getElementById("toolbarRefreshBtn").addEventListener("click", loadInbox);
document.getElementById("backBtn").addEventListener("click", closeMessage);
document.getElementById("archiveBtn").addEventListener("click", () => recordDecision("leave_alone"));
document.getElementById("scheduleBtn").addEventListener("click", () => selectDecision("schedule"));
document.getElementById("replyPillBtn").addEventListener("click", () => selectDecision("reply"));
document.getElementById("forwardPillBtn").addEventListener("click", () => selectDecision("forward"));
sendBtn.addEventListener("click", () => {
  if (!pendingDecision) return;
  recordDecision(pendingDecision, replyBody.value);
});
navInbox.addEventListener("click", () => setView("inbox"));
navStarred.addEventListener("click", () => setView("starred"));
searchInput.addEventListener("input", () => {
  searchQuery = searchInput.value.trim().toLowerCase();
  renderList();
});

loadInbox();
