// components/inbox_router/local_ui/app.js
let pendingEmails = [];
let openMessageId = null;
let searchQuery = "";
let currentView = "inbox"; // "inbox" | "starred"
let selectedIds = new Set();
let lastVisibleIds = [];

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
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <input type="checkbox" class="row-checkbox" ${selectedIds.has(id) ? "checked" : ""}>
      <span class="row-star ${starredIds.has(id) ? "starred" : ""}">${starredIds.has(id) ? "&#9733;" : "&#9734;"}</span>
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
  document.getElementById("detailAvatar").textContent =
    (email.sender || email.sender_email || "?").charAt(0).toUpperCase();
  document.getElementById("detailSender").textContent = email.sender || email.sender_email || "";
  document.getElementById("detailSubject").textContent = email.subject || "";
  document.getElementById("detailRationale").textContent = email.rationale || "";
  document.getElementById("detailBody").textContent = email.body_text || "(no body available)";
  document.getElementById("detailDecision").textContent = email.decision || "";
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  detailView.hidden = true;
  listView.hidden = false;
}

async function confirmCurrent() {
  const email = pendingEmails.find((e) => e.message_id === openMessageId);
  if (!email) return;
  const resp = await fetch("/api/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: openMessageId, decision: email.decision }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    detailStatus.textContent = `Error: ${err.error || "confirm failed"}`;
    return;
  }
  detailStatus.textContent = "Confirmed.";
  await loadInbox();
  closeMessage();
}

async function overrideCurrent() {
  const newDecision = document.getElementById("overrideSelect").value;
  const resp = await fetch("/api/override", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: openMessageId, new_decision: newDecision, reason: "manual override" }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    detailStatus.textContent = `Error: ${err.error || "override failed"}`;
    return;
  }
  detailStatus.textContent = "Overridden.";
  await loadInbox();
  closeMessage();
}

async function archiveCurrent() {
  if (!openMessageId) return;
  const resp = await fetch("/api/override", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: openMessageId, new_decision: "leave_alone", reason: "archived" }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    detailStatus.textContent = `Error: ${err.error || "archive failed"}`;
    return;
  }
  await loadInbox();
  closeMessage();
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
  selectedIds.clear();
  await loadInbox();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

document.getElementById("refreshBtn").addEventListener("click", loadInbox);
document.getElementById("toolbarRefreshBtn").addEventListener("click", loadInbox);
document.getElementById("backBtn").addEventListener("click", closeMessage);
document.getElementById("confirmBtn").addEventListener("click", confirmCurrent);
document.getElementById("overrideBtn").addEventListener("click", overrideCurrent);
document.getElementById("archiveBtn").addEventListener("click", archiveCurrent);
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
