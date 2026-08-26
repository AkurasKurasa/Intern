// components/inbox_router/practice_inbox/app.js
let inboxMessages = [];
let openMessageId = null;
let searchQuery = "";

const rowList = document.getElementById("rowList");
const emptyState = document.getElementById("emptyState");
const listView = document.getElementById("listView");
const detailView = document.getElementById("detailView");
const detailStatus = document.getElementById("detailStatus");
const inboxCount = document.getElementById("inboxCount");
const toolbarCount = document.getElementById("toolbarCount");
const searchInput = document.getElementById("searchInput");

function snippetOf(bodyText) {
  const flat = (bodyText || "").replace(/\s+/g, " ").trim();
  return flat.length > 70 ? `${flat.slice(0, 70)}...` : flat;
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

function renderList() {
  rowList.innerHTML = "";
  inboxCount.textContent = inboxMessages.length > 0 ? String(inboxMessages.length) : "";
  const visible = inboxMessages.filter(matchesSearch);
  toolbarCount.textContent = inboxMessages.length > 0 ? `1-${visible.length} of ${inboxMessages.length}` : "";
  emptyState.hidden = visible.length > 0;
  emptyState.textContent = inboxMessages.length === 0
    ? "No emails to practice on. Click Refresh."
    : "No emails match your search.";
  visible.forEach((email) => {
    const li = document.createElement("li");
    li.className = "row-item";
    li.innerHTML = `
      <span class="row-checkbox" aria-hidden="true"></span>
      <span class="row-star" aria-hidden="true">&#9734;</span>
      <span class="row-sender">${escapeHtml(email.sender || email.sender_email || "")}</span>
      <span class="row-snippet">
        <span class="row-subject">${escapeHtml(email.subject || "")}</span>
        <span class="row-preview"> - ${escapeHtml(snippetOf(email.body_text))}</span>
      </span>
    `;
    li.addEventListener("click", () => openMessage(email.message_id));
    rowList.appendChild(li);
  });
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
  listView.hidden = true;
  detailView.hidden = false;
}

function closeMessage() {
  openMessageId = null;
  detailView.hidden = true;
  listView.hidden = false;
}

async function recordDecision(decision) {
  if (!openMessageId) return;
  try {
    const resp = await fetch("/practice/api/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: openMessageId, decision }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      detailStatus.textContent = `Error: ${err.error || "record failed"}`;
      return;
    }
    closeMessage();
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
document.querySelectorAll(".btn-action").forEach((btn) => {
  btn.addEventListener("click", () => recordDecision(btn.dataset.decision));
});
searchInput.addEventListener("input", () => {
  searchQuery = searchInput.value.trim().toLowerCase();
  renderList();
});

loadInbox();
