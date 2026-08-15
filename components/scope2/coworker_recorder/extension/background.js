/* Background service worker for the demonstration recorder (3.2).

   Buffers events from the content script and writes them out as JSONL, in the
   same shape recorder/events.py reads. It stores raw context only: like the
   content script, it decides no labels. */

const BUFFER_KEY = "demo_events";

async function buffer() {
  const stored = await chrome.storage.local.get(BUFFER_KEY);
  return stored[BUFFER_KEY] || [];
}

async function append(event) {
  const events = await buffer();
  events.push(event);
  await chrome.storage.local.set({ [BUFFER_KEY]: events });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.kind === "demo-event") {
    append(message.event).then(() => sendResponse({ ok: true }));
    return true; // keep the channel open for the async reply
  }

  if (message && message.kind === "demo-export") {
    buffer().then((events) => {
      const jsonl = events.map((e) => JSON.stringify(e)).join("\n") + "\n";
      // data: URL rather than a Blob - a service worker has no URL.createObjectURL.
      const url = "data:application/x-ndjson;charset=utf-8," + encodeURIComponent(jsonl);
      chrome.downloads.download({
        url,
        filename: message.filename || "demo_session.jsonl",
        saveAs: true,
      });
      sendResponse({ ok: true, count: events.length });
    });
    return true;
  }

  if (message && message.kind === "demo-clear") {
    chrome.storage.local.set({ [BUFFER_KEY]: [] }).then(() => sendResponse({ ok: true }));
    return true;
  }

  return false;
});
