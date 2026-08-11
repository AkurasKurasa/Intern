/* Raw DOM context for every form control on the page.

   This file deliberately makes no decisions. It resolves no labels, classifies
   no fields and ranks nothing - it only reads what the DOM says and hands it to
   labeling/resolve.py, which is the single place a label is decided (see 3.5).
   Adding a "pick the best name here" shortcut would reintroduce exactly the
   drift the split exists to prevent.

   The Chrome extension of 3.2 should emit this same shape so both sides feed
   one cascade. */

() => {
  const text = (node) => (node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "");

  // aria-labelledby is a space-separated id list; the accessible name is the
  // concatenation of those elements' text, in the order given.
  const ariaName = (el) => {
    const explicit = el.getAttribute("aria-label");
    if (explicit && explicit.trim()) return explicit.trim();
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    if (!ids.length) return "";
    return ids
      .map((id) => text(document.getElementById(id)))
      .filter(Boolean)
      .join(" ");
  };

  // Rule 5: the nearest preceding text. In a table that is the <th> governing
  // this cell's column; otherwise the previous sibling's text.
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

  // Which column of a repeating sheet this control belongs to. The header cell
  // is the stable identity; data-key is recorded separately as ground truth and
  // is never used to resolve a label.
  const columnKey = (el, order) => {
    const cell = el.closest("td, th");
    const table = el.closest("table");
    if (cell && table) {
      const row = cell.parentElement;
      const columnIndex = Array.prototype.indexOf.call(row.children, cell);
      const headRow = table.tHead && table.tHead.rows[0];
      const header = headRow && headRow.children[columnIndex];
      if (header) {
        return {
          key: (table.id || "table") + ":col" + columnIndex,
          column_index: columnIndex,
          header_id: header.id || null,
          header_text: text(header),
          row_index: Array.prototype.indexOf.call(
            row.parentElement ? row.parentElement.children : [], row),
        };
      }
    }
    // Not in a table: every control is its own column of one.
    return { key: "field:" + order, column_index: null, header_id: null,
             header_text: "", row_index: null };
  };

  const controls = Array.from(
    document.querySelectorAll("input, select, textarea")
  ).filter((el) => el.type !== "hidden");

  return controls.map((el, order) => {
    const wrappingLabel = el.closest("label");
    const forLabel = el.id
      ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
      : null;

    const isSelect = el.tagName === "SELECT";
    const options = isSelect
      ? Array.from(el.options).map((o) => o.value).filter((v) => v !== "")
      : null;

    const rect = el.getBoundingClientRect();
    const column = columnKey(el, order);

    return {
      // --- cascade inputs, consumed by labeling/resolve.py ---
      label_for: text(forLabel),
      label_wrapping: wrappingLabel ? text(wrappingLabel) : "",
      aria: ariaName(el),
      placeholder: el.getAttribute("placeholder") || "",
      preceding_text: precedingText(el),
      name: el.getAttribute("name") || "",

      // --- field descriptor, per the 2.2 shape ---
      id: el.id || null,
      input_type: isSelect ? "select" : (el.tagName === "TEXTAREA" ? "textarea" : el.type),
      tag: el.tagName.toLowerCase(),
      required: el.required === true,
      disabled: el.disabled === true,
      readonly: el.readOnly === true,
      maxlength: el.maxLength && el.maxLength > 0 ? el.maxLength : null,
      min: el.getAttribute("min"),
      max: el.getAttribute("max"),
      step: el.getAttribute("step"),
      options: options,
      value: el.type === "checkbox" || el.type === "radio" ? null : el.value,
      dom_order: order,
      visible: rect.width > 0 && rect.height > 0,

      // --- sheet grouping ---
      column_key: column.key,
      column_index: column.column_index,
      header_id: column.header_id,
      header_text: column.header_text,
      row_index: column.row_index,

      // --- ground truth, for evaluation only ---
      truth_key: el.dataset ? (el.dataset.key || null) : null,
    };
  });
}
