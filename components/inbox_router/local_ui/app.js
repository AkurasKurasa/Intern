// components/inbox_router/local_ui/app.js
let pendingEmails = [];
let openMessageId = null;
let searchQuery = "";
let currentView = "inbox"; // "inbox" | "starred"
let selectedIds = new Set();
let lastVisibleIds = [];
let snackbarTimer = null;

let starredIds = new Set();
try {
  starredIds = new Set(JSON.parse(localStorage.getItem("inboxDispatch.starred") || "[]"));
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
const selectAllCheckbox = document.getElementById("selectAllCheckbox");
const bulkBar = document.getElementById("bulkBar");
const bulkCount = document.getElementById("bulkCount");
const bulkConfirmBtn = document.getElementById("bulkConfirmBtn");
const snackbar = document.getElementById("snackbar");
const replyBoxWrap = document.getElementById("replyBoxWrap");
const replyBody = document.getElementById("replyBody");
const scheduleDatesWrap = document.getElementById("scheduleDatesWrap");
const eventWhen = document.getElementById("eventWhen");
const EVENT_DEFAULT_DURATION_MINUTES = 30;
const forwardToWrap = document.getElementById("forwardToWrap");
const forwardTo = document.getElementById("forwardTo");

// The real-Gmail-style icons/pills below set this directly -- there's no
// separate "pick from a list, then click Override" step anymore. Clicking
// the real thing you want (Reply, Forward, Schedule's snooze icon, the
// flag star, the archive icon) IS the action, the same way it is in real
// Gmail. Reply/Forward/Schedule need typed content first, so they only
// set this and reveal the text box; Archive/Flag need nothing typed, so
// they call performDecision() immediately.
let pendingDecision = null;

const SNACKBAR_TEXT = {
  reply: "Reply sent.", forward: "Forwarded.", schedule: "Scheduled.",
  flag: "Flagged.", leave_alone: "Archived.",
};

const STAR_FILLED = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>';
const STAR_OUTLINE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 15.4l3.76 2.27-1-4.28 3.32-2.88-4.38-.38L12 6l-1.71 4.04-4.38.38 3.32 2.88-1 4.28L12 15.4M12 2l2.81 6.63L22 9.24l-5.46 4.73L18.18 21 12 17.27 5.82 21l1.64-7.03L2 9.24l7.19-.61L12 2z"/></svg>';

function snippetOf(bodyText) {
  const flat = (bodyText || "").replace(/\s+/g, " ").trim();
  return flat.length > 70 ? `${flat.slice(0, 70)}...` : flat;
}

function saveStarred() {
  try {
    localStorage.setItem("inboxDispatch.starred", JSON.stringify([...starredIds]));
  } catch (e) {
    // Best-effort only -- starring is a local convenience, not a pipeline decision.
  }
}

function showSnackbar(message) {
  clearTimeout(snackbarTimer);
  snackbar.textContent = message;
  snackbar.hidden = false;
  snackbarTimer = setTimeout(() => { snackbar.hidden = true; }, 4000);
}

async function loadInbox() {
  detailStatus.textContent = "";
  try {
    const resp = await fetch("/api/inbox");
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    const data = await resp.json();
    pendingEmails = data.pending || [];
    renderList();
  } catch (e) {
    pendingEmails = [];
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
  selectedIds = new Set([...selectedIds].filter((id) => pendingEmails.some((e) => e.message_id === id)));

  const viewEmails = pendingEmails.filter(matchesView);
  const visible = viewEmails.filter(matchesSearch);
  lastVisibleIds = visible.map((e) => e.message_id);

  rowList.innerHTML = "";
  inboxCount.textContent = pendingEmails.length > 0 ? String(pendingEmails.length) : "";
  emptyState.hidden = visible.length > 0;
  if (currentView === "starred") {
    emptyState.textContent = "No starred emails yet. Click the star on an email to star it.";
  } else {
    emptyState.textContent = pendingEmails.length === 0
      ? "No pending emails. Click Refresh to check again."
      : "No emails match your search.";
  }

  visible.forEach((email) => {
    const id = email.message_id;
    const starred = starredIds.has(id);
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <input type="checkbox" class="row-checkbox" ${selectedIds.has(id) ? "checked" : ""}>
      <span class="row-star ${starred ? "starred" : ""}">${starred ? STAR_FILLED : STAR_OUTLINE}</span>
      <span class="row-sender">${escapeHtml(email.sender || email.sender_email || "")}</span>
      <span class="row-snippet">
        <span class="row-subject">${escapeHtml(email.subject || "")}</span>
        <span class="row-preview"> - ${escapeHtml(snippetOf(email.body_text))}</span>
      </span>
      <span class="row-badge">${escapeHtml(email.decision || "")}</span>
    `;
    li.querySelector(".row-checkbox").addEventListener("click", (e) => {
      e.stopPropagation();
      if (e.target.checked) selectedIds.add(id); else selectedIds.delete(id);
      renderList();
    });
    li.querySelector(".row-star").addEventListener("click", (e) => {
      e.stopPropagation();
      if (starredIds.has(id)) starredIds.delete(id); else starredIds.add(id);
      saveStarred();
      renderList();
    });
    li.addEventListener("click", () => openMessage(id));
    rowList.appendChild(li);
  });

  const showBulk = selectedIds.size > 0;
  bulkBar.hidden = !showBulk;
  toolbarCount.hidden = showBulk;
  if (showBulk) {
    bulkCount.textContent = `${selectedIds.size} selected`;
  } else {
    toolbarCount.textContent = pendingEmails.length > 0 ? `1-${visible.length} of ${viewEmails.length}` : "";
  }
  selectAllCheckbox.checked = lastVisibleIds.length > 0 && lastVisibleIds.every((id) => selectedIds.has(id));
  selectAllCheckbox.indeterminate = !selectAllCheckbox.checked && lastVisibleIds.some((id) => selectedIds.has(id));
}

function openMessage(messageId) {
  const email = pendingEmails.find((e) => e.message_id === messageId);
  if (!email) return;
  openMessageId = messageId;
  pendingDecision = null;
  document.getElementById("detailAvatar").textContent =
    (email.sender || email.sender_email || "?").charAt(0).toUpperCase();
  document.getElementById("detailSender").textContent = email.sender || email.sender_email || "";
  document.getElementById("detailSubject").textContent = email.subject || "";
  document.getElementById("detailRationale").textContent = email.rationale || "";
  document.getElementById("detailBody").textContent = email.body_text || "(no body available)";
  document.getElementById("detailDecision").textContent = email.decision || "";
  replyBody.value = "";
  replyBody.name = email.message_id;
  eventWhen.value = "";
  forwardTo.value = "";
  replyBoxWrap.hidden = true;
  scheduleDatesWrap.hidden = true;
  forwardToWrap.hidden = true;
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  pendingDecision = null;
  replyBody.value = "";
  detailView.hidden = true;
  listView.hidden = false;
}

// Reply/Forward/Schedule need real typed content first -- clicking the
// pill or the Snooze icon just reveals the box and remembers which one
// is pending; nothing is sent until Send is clicked.
function selectPendingDecision(decision) {
  pendingDecision = decision;
  replyBoxWrap.hidden = false;
  scheduleDatesWrap.hidden = decision !== "schedule";
  forwardToWrap.hidden = decision !== "forward";
  replyBody.placeholder = decision === "schedule"
    ? "Type your note -- this exact text is what gets recorded, nothing is written for you."
    : "Type your reply -- this exact text is what gets sent, nothing is written for you.";
  if (decision === "forward") forwardTo.focus(); else replyBody.focus();
}

// The one real submit path for all six decisions. Whether this counts as
// "confirming what Intern suggested" or "overriding it" is decided here,
// invisibly to the person clicking -- they just clicked the real thing
// they wanted, the same as in real Gmail, but the project still needs
// that distinction recorded for its own accuracy metrics.
async function performDecision(decision, replyBodyText = "", startVal = "", endVal = "", forwardToVal = "") {
  const email = pendingEmails.find((e) => e.message_id === openMessageId);
  if (!email) return;
  const isConfirm = decision === email.decision;
  const url = isConfirm ? "/api/confirm" : "/api/override";
  const body = isConfirm
    ? { message_id: openMessageId, decision, reply_body: replyBodyText,
        event_start: startVal, event_end: endVal, forward_to: forwardToVal }
    : { message_id: openMessageId, new_decision: decision, reason: "user action",
        reply_body: replyBodyText, event_start: startVal, event_end: endVal, forward_to: forwardToVal };
  const resp = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    detailStatus.textContent = `Error: ${err.error || "request failed"}`;
    return;
  }
  showSnackbar(SNACKBAR_TEXT[decision] || "Done.");
  await loadInbox();
  closeMessage();
}

// The human only ever picks one moment -- a start time and a duration is
// two decisions for something that's really one ("schedule this for 4pm").
// The end time Intern's own Calendar event needs is derived here, not
// asked for, same as how Reply/Forward ask for exactly one thing each.
function addMinutes(datetimeLocalValue, minutes) {
  if (!datetimeLocalValue) return "";
  const d = new Date(datetimeLocalValue);
  d.setMinutes(d.getMinutes() + minutes);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function sendPending() {
  if (!pendingDecision) return;
  if (pendingDecision === "forward" && !forwardTo.value.trim()) {
    detailStatus.textContent = "Error: type who you're forwarding this to.";
    forwardTo.focus();
    return;
  }
  if (pendingDecision === "schedule" && !eventWhen.value) {
    detailStatus.textContent = "Error: pick when this should happen.";
    eventWhen.focus();
    return;
  }
  const startVal = pendingDecision === "schedule" ? eventWhen.value : "";
  const endVal = pendingDecision === "schedule" ? addMinutes(eventWhen.value, EVENT_DEFAULT_DURATION_MINUTES) : "";
  performDecision(pendingDecision, replyBody.value, startVal, endVal, forwardTo.value);
}

async function confirmSelected() {
  const ids = [...selectedIds];
  for (const id of ids) {
    const email = pendingEmails.find((e) => e.message_id === id);
    if (!email) continue;
    await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: id, decision: email.decision }),
    });
  }
  const count = ids.length;
  selectedIds.clear();
  await loadInbox();
  showSnackbar(count === 1 ? "1 email confirmed." : `${count} emails confirmed.`);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadInbox);
document.getElementById("toolbarRefreshBtn").addEventListener("click", loadInbox);
document.getElementById("backBtn").addEventListener("click", closeMessage);
document.getElementById("archiveBtn").addEventListener("click", () => performDecision("leave_alone"));
document.getElementById("flagBtn").addEventListener("click", () => performDecision("flag"));
document.getElementById("scheduleBtn").addEventListener("click", () => selectPendingDecision("schedule"));
document.getElementById("replyPillBtn").addEventListener("click", () => selectPendingDecision("reply"));
document.getElementById("forwardPillBtn").addEventListener("click", () => selectPendingDecision("forward"));
document.getElementById("sendBtn").addEventListener("click", sendPending);
navInbox.addEventListener("click", () => setView("inbox"));
navStarred.addEventListener("click", () => setView("starred"));
selectAllCheckbox.addEventListener("change", () => {
  if (selectAllCheckbox.checked) {
    lastVisibleIds.forEach((id) => selectedIds.add(id));
  } else {
    lastVisibleIds.forEach((id) => selectedIds.delete(id));
  }
  renderList();
});
bulkConfirmBtn.addEventListener("click", confirmSelected);
searchInput.addEventListener("input", () => {
  searchQuery = searchInput.value.trim().toLowerCase();
  renderList();
});

loadInbox();
