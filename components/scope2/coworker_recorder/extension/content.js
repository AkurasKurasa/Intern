/* Browser Recorder content script (3.2).

   Records values landing in controls, with the raw DOM context the label
   cascade needs. It resolves no labels - that happens once, in
   labeling/resolve.py (3.5). Emitting a decided name here would be the drift
   the split exists to prevent.

   Capture is on `change`/`blur` as well as `paste`, so a typed value and a
   dropdown choice are both observed, not only a paste (3.2).

   The file is written to run in two places without modification: as an MV3
   content script, where it posts events to the background worker, and injected
   directly by Playwright, where it appends to window.__demo. That is what makes
   the recorder testable without packaging an extension.  */

(() => {
  if (window.__demoRecorder) return;

  const state = { seq: 0, url: location.href, touched: {}, lastWrite: {} };
  window.__demo = window.__demo || [];

  const text = (node) =>
    node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "";

  const ariaName = (el) => {
    const explicit = el.getAttribute("aria-label");
    if (explicit && explicit.trim()) return explicit.trim();
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (!ids.length) return "";
    return ids.map((id) => text(document.getElementById(id))).filter(Boolean).join(" ");
  };

  const precedingText = (el) => {
    const cell = el.closest("td, th");
    if (cell) {
      const row = cell.parentElement;
      const table = el.closest("table");
      if (row && table) {
        const columnIndex = Array.prototype.indexOf.call(row.children, cell);
        const headRow = table.tHead && table.tHead.rows[0];
        if (headRow && headRow.children[columnIndex]) {
          return text(headRow.children[columnIndex]);
        }
      }
    }
    let sibling = el.previousElementSibling;
    while (sibling) {
      const t = text(sibling);
      if (t) return t;
      sibling = sibling.previousElementSibling;
    }
    return text(el.parentElement && el.parentElement.previousElementSibling);
  };

  // Which record this write belongs to. A sheet portal marks each control with
  // its row; a single-record form is one row per submission.
  const rowOf = (el) => {
    if (el.dataset && el.dataset.row !== undefined) return Number(el.dataset.row);
    const cell = el.closest("tr");
    if (cell && cell.parentElement) {
      return Array.prototype.indexOf.call(cell.parentElement.children, cell);
    }
    return 0;
  };

  const domOrder = (el) => {
    const all = Array.from(document.querySelectorAll("input, select, textarea"));
    return all.indexOf(el);
  };

  // Which column of a repeating sheet this control sits in. On a sheet portal
  // the accessible name of one control names its row as well as its column
  // ("Grade 0-100 Abad, Andrea A."), so events have to be groupable by column
  // before a field label means anything. Same key shape as the Page Scanner.
  const columnKey = (el) => {
    const cell = el.closest("td, th");
    const table = el.closest("table");
    if (cell && table) {
      const row = cell.parentElement;
      const columnIndex = Array.prototype.indexOf.call(row.children, cell);
      const headRow = table.tHead && table.tHead.rows[0];
      if (headRow && headRow.children[columnIndex]) {
        return (table.id || "table") + ":col" + columnIndex;
      }
    }
    return "field:" + (el.id || el.name || domOrder(el));
  };

  const rawContext = (el) => {
    const wrappingLabel = el.closest("label");
    const forLabel = el.id
      ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
      : null;
    const isSelect = el.tagName === "SELECT";

    return {
      // cascade inputs - consumed by labeling/resolve.py, never decided here
      label_for: text(forLabel),
      label_wrapping: wrappingLabel ? text(wrappingLabel) : "",
      aria: ariaName(el),
      placeholder: el.getAttribute("placeholder") || "",
      preceding_text: precedingText(el),
      name: el.getAttribute("name") || "",

      id: el.id || null,
      input_type: isSelect ? "select" : (el.tagName === "TEXTAREA" ? "textarea" : el.type),
      required: el.required === true,
      maxlength: el.maxLength && el.maxLength > 0 ? el.maxLength : null,
      options: isSelect
        ? Array.from(el.options).map((o) => o.value).filter((v) => v !== "")
        : null,
      dom_order: domOrder(el),
      column_key: columnKey(el),
    };
  };

  /* Fill order is taken from first focus, not from emit time.

     `change` on a text input fires when focus *leaves* it, so emitting in
     arrival order puts Grade after Remarks when the user tabbed from one to the
     other - the exact inversion the rule inducer must not see, since a field
     can only derive from one already filled. Order is therefore stamped when
     the user starts editing a control. */
  const touchOrder = (el) => {
    const key = rowOf(el) + " " + columnKey(el);
    if (!(key in state.touched)) state.touched[key] = state.seq++;
    return state.touched[key];
  };

  const emit = (el, trigger) => {
    /* One write, one event.

       A paste fires `paste` and then `change` when focus leaves, and both are
       genuine observations of the same value landing in the same cell. The
       Reconciler would collapse them anyway - last write wins per row - but
       logging each write twice makes the live output unreadable and doubles
       the session file for no information. */
    const cell = rowOf(el) + " " + columnKey(el);
    if (state.lastWrite[cell] === el.value) return;
    state.lastWrite[cell] = el.value;

    const event = {
      t: new Date().toISOString(),
      source: "browser",
      url: state.url,
      value: el.type === "checkbox" || el.type === "radio" ? "" : el.value,
      trigger,
      row: rowOf(el),
      seq: touchOrder(el),
      context: rawContext(el),
    };

    window.__demo.push(event);
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      try {
        chrome.runtime.sendMessage({ kind: "demo-event", event });
      } catch (e) {
        /* the page may outlive the worker; the in-page buffer still holds it */
      }
    }
  };

  const isDataControl = (el) =>
    el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName) &&
    !["checkbox", "radio", "button", "submit", "reset", "file", "hidden"].includes(el.type);

  // Stamp fill order the moment the user enters a control, before any value
  // arrives, so ordering survives change-on-blur.
  document.addEventListener("focusin", (e) => {
    if (isDataControl(e.target)) touchOrder(e.target);
  }, true);

  // `paste` fires before the value settles, so the read is deferred a tick.
  document.addEventListener("paste", (e) => {
    const el = e.target;
    if (!isDataControl(el)) return;
    setTimeout(() => emit(el, "paste"), 0);
  }, true);

  document.addEventListener("change", (e) => {
    const el = e.target;
    if (!isDataControl(el)) return;
    // Mark the value as reported so the blur that follows does not emit it a
    // second time. Both listeners are needed - change misses some programmatic
    // fills, blur misses a select - but one write should produce one event.
    if (el.dataset) el.dataset.demoLast = el.value;
    emit(el, el.tagName === "SELECT" ? "select" : "type");
  }, true);

  document.addEventListener("blur", (e) => {
    const el = e.target;
    if (!isDataControl(el)) return;
    if (el.dataset && el.dataset.demoLast === el.value) return;
    if (el.value === "") return;
    if (el.dataset) el.dataset.demoLast = el.value;
    emit(el, "type");
  }, true);

  window.__demoRecorder = {
    events: () => window.__demo,
    clear: () => { window.__demo.length = 0; state.seq = 0; state.touched = {}; state.lastWrite = {}; },
  };
})();
