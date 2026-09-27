/* Row highlighter for Scope #2's executor.

   WHAT THIS IS, AND WHAT IT NO LONGER IS
   --------------------------------------
   This used to be a full decision panel rendered into the portal page inside a
   shadow root. The panel moved to the Electron app's floating Agent window, so
   Scope #1 and Scope #2 now report through the same surface instead of each
   having its own -- a direct request, for uniformity.

   What stayed is the one thing that cannot live outside the page: outlining
   the row currently being filled and scrolling it into view. On a 50-row run
   that is the difference between watching the agent work and watching a table
   change at random.

   WHY IT STILL CANNOT CONTAMINATE THE EXPERIMENT
   ----------------------------------------------
   The same rules the panel obeyed, for the same reasons:

     1. It adds NO element to the document. The panel needed a shadow root;
        a row outline needs nothing but an inline style on a <tr>.
     2. It creates no input, select or textarea, so the Page Scanner cannot
        mistake anything here for a field.
     3. runner.py injects it only when show=True. A headless measurement run
        never loads this file.
     4. The only write is an inline outline, saved and restored exactly.
        Readback reads .value, and the control check reads checkboxes --
        neither reads style. */

(function () {
  "use strict";

  if (window.__agentHUD) return;

  var ACCENT = "#1f5fd0";   // matches the portal's own accent

  var state = { row: null, prevOutline: null, prevOffset: null };

  function release() {
    if (state.row) {
      state.row.style.outline = state.prevOutline || "";
      state.row.style.outlineOffset = state.prevOffset || "";
      state.row = null;
      state.prevOutline = null;
      state.prevOffset = null;
    }
    return true;
  }

  window.__agentHUD = {
    /* Outline the row being filled and bring it into view. Index is the row's
       position as the executor found it, which is the same walk the scanner
       used, so it always refers to the row the next write targets. */
    row: function (index) {
      release();
      var rows = document.querySelectorAll("#records-body tr");
      var tr = rows[index];
      if (!tr) return false;

      state.row = tr;
      state.prevOutline = tr.style.outline;
      state.prevOffset = tr.style.outlineOffset;
      tr.style.outline = "2px solid " + ACCENT;
      tr.style.outlineOffset = "-2px";

      try {
        tr.scrollIntoView({ block: "center", behavior: "smooth" });
      } catch (e) {
        tr.scrollIntoView();
      }
      return true;
    },

    release: release,

    /* Kept so a re-run in the same page starts clean. */
    destroy: function () {
      release();
      return true;
    }
  };
})();
