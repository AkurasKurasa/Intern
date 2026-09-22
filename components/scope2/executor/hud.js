/* Live decision HUD - what the agent is deciding, shown on the page it is
   deciding about.

   WHY THIS EXISTS
   ---------------
   Scope #2 is the scope with the most interesting reasoning and the least
   visible evidence of it. Running `automate.py --show` opens a browser and
   fills fields; the browser says nothing about *why*. The two most defensible
   claims in the project - that it ABSTAINS on a decoy column rather than
   guessing, and that it DERIVES the pass mark instead of being told it -
   scroll past as grey text in a terminal nobody is watching.

   This puts both on the page, next to the thing they are claims about.

   WHY IT CANNOT CONTAMINATE THE EXPERIMENT
   ----------------------------------------
   mocksite/shared/styles.css is deliberate: "Styling is held constant across
   every variant so the experiment isolates structure and labelling." An
   overlay that restyled the portal would weaken the instrument. So:

     1. Everything below lives in a SHADOW ROOT. Its styles physically cannot
        reach the portal, and the portal's cannot reach it. No stylesheet is
        injected into the document.
     2. It contains no input, select or textarea, so the Page Scanner cannot
        mistake any of it for a field, and the label cascade never sees it.
     3. runner.py injects it only when show=True. A measurement run never
        loads this file at all.
     4. The only light-DOM write is an inline outline on the <tr> being
        filled, saved and restored exactly. Readback reads .value, not style.

   Nothing here is portal-specific or task-specific: it renders whatever the
   Resolver decided, so all eight variants get it for free. */

(function () {
  "use strict";

  if (window.__agentHUD) return;

  var C = {
    bg: "#11161d", panel: "#1a212b", line: "#2b3542", text: "#e6edf5",
    muted: "#8b9bb0", accent: "#4da3ff", ok: "#3fb950", warn: "#d29922",
    bad: "#f85149", derive: "#bc8cff"
  };

  var PANEL_W = 346;
  var GUTTER = PANEL_W + 32;

  var CSS = [
    /* The host is a zero-area, click-through anchor. Found live by the full
       suite: with a default-flow host, Playwright refused to click the
       portal's own Save button -- "<div data-agent-hud> intercepts pointer
       events" -- and a --show --commit run timed out having filled every row
       correctly. A HUD that can swallow one of the agent's clicks is a HUD
       that changes the run's outcome, so the whole thing is pointer-events:
       none, everywhere, with no exception. It is a heads-up display: it is
       read, never touched. That also means it cannot be scrolled by hand,
       which is why the sections below are sized to fit rather than to scroll. */
    ":host{all:initial;position:fixed;top:0;left:0;width:0;height:0;",
    "pointer-events:none;z-index:2147483647}",
    "*{box-sizing:border-box;margin:0;padding:0;pointer-events:none}",
    ".hud{position:fixed;top:16px;right:16px;width:", PANEL_W, "px;max-height:calc(100vh - 32px);",
    "display:flex;flex-direction:column;background:", C.bg, ";color:", C.text, ";",
    "border:1px solid ", C.line, ";border-radius:10px;overflow:hidden;contain:layout style;",
    "font:12px/1.5 Segoe UI,system-ui,sans-serif;box-shadow:0 12px 34px rgba(0,0,0,.42)}",

    ".hd{display:flex;align-items:center;gap:8px;padding:11px 13px;border-bottom:1px solid ", C.line, "}",
    ".dot{width:8px;height:8px;border-radius:50%;background:", C.accent, ";flex:none;",
    "animation:pulse 1.4s ease-in-out infinite}",
    "@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.35;transform:scale(.72)}}",
    ".dot.done{animation:none;background:", C.ok, "}",
    ".dot.bad{animation:none;background:", C.bad, "}",
    ".ttl{font-weight:700;letter-spacing:.02em}",
    ".meta{margin-left:auto;color:", C.muted, ";font-size:11px;text-align:right;line-height:1.35}",
    ".tag{display:inline-block;padding:1px 6px;border-radius:999px;border:1px solid ", C.line, ";",
    "font-size:10px;letter-spacing:.04em;text-transform:uppercase}",

    ".stage{padding:9px 13px;border-bottom:1px solid ", C.line, ";color:", C.muted, ";",
    "display:flex;align-items:center;gap:7px;min-height:34px}",
    ".stage b{color:", C.text, ";font-weight:600}",

    ".scroll{overflow-y:auto;flex:1;scrollbar-width:thin}",
    ".scroll::-webkit-scrollbar{width:7px}",
    ".scroll::-webkit-scrollbar-thumb{background:", C.line, ";border-radius:4px}",

    ".sec{padding:11px 13px;border-bottom:1px solid ", C.line, "}",
    ".sec:last-child{border-bottom:0}",
    ".sh{display:flex;align-items:baseline;gap:6px;margin-bottom:8px}",
    ".sh h3{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:", C.muted, ";font-weight:700}",
    ".sh .n{font-size:10px;color:", C.muted, ";margin-left:auto}",

    ".map{display:flex;flex-direction:column;gap:7px}",
    ".pair{display:grid;grid-template-columns:1fr auto;gap:2px 8px;align-items:baseline}",
    ".src{font-family:Consolas,monospace;font-size:11px;color:", C.text, ";",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".tgt{font-size:11px;color:", C.accent, ";white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".conf{font-variant-numeric:tabular-nums;font-size:10px;color:", C.muted, "}",
    ".bar{grid-column:1/-1;height:3px;border-radius:2px;background:", C.line, ";overflow:hidden}",
    /* Both bars below grow by transform, not by width. Animating width
       relayouts every frame, and a shadow root is NOT a layout boundary -
       that work lands in the same document the executor is driving, which
       is exactly the interference this file claims not to cause. scaleX
       runs on the compositor and touches no layout at all. */
    ".bar i{display:block;height:100%;width:100%;background:", C.accent, ";",
    "border-radius:2px;transform:scaleX(0);transform-origin:left center;",
    "transition:transform .5s cubic-bezier(.22,1,.36,1)}",

    ".item{display:flex;gap:7px;align-items:flex-start;padding:5px 0;font-size:11px}",
    ".item+.item{border-top:1px solid ", C.line, "}",
    ".ic{flex:none;width:14px;text-align:center;font-weight:700}",
    ".why{color:", C.muted, ";font-size:10px}",
    ".warn .ic{color:", C.warn, "}",
    ".derive .ic{color:", C.derive, "}",
    ".rule{font-size:11px;line-height:1.55}",
    ".rule em{font-style:normal;color:", C.derive, ";font-weight:600}",
    ".rule code{font-family:Consolas,monospace;color:", C.text, ";",
    "background:", C.panel, ";padding:1px 4px;border-radius:3px}",

    ".ft{padding:10px 13px;border-top:1px solid ", C.line, ";background:", C.panel, "}",
    ".pb{height:5px;border-radius:3px;background:", C.line, ";overflow:hidden;margin-bottom:8px}",
    ".pb i{display:block;height:100%;width:100%;background:", C.ok, ";",
    "transform:scaleX(0);transform-origin:left center;",
    "transition:transform .25s linear}",
    ".pb i.bad{background:", C.bad, "}",
    ".cnt{display:flex;gap:12px;font-size:11px;color:", C.muted, ";font-variant-numeric:tabular-nums}",
    ".cnt b{color:", C.text, "}",
    ".cnt .ok b{color:", C.ok, "}",
    ".cnt .bad b{color:", C.bad, "}",
    ".now{margin-top:7px;font-size:10px;color:", C.muted, ";font-family:Consolas,monospace;",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:15px}"
  ].join("");

  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }

  /* The induced rule, as a sentence rather than a JSON blob. This is the
     project's headline claim - it read the pass mark off the data - so it is
     worth spelling out in words a person can check at a glance. */
  function ruleSentence(rule) {
    var ops = { ">=": "≥", "<=": "≤", ">": ">", "<": "<" };
    var op = ops[rule.operator] || rule.operator;
    var f = document.createDocumentFragment();
    f.appendChild(el("em", null, rule.field));
    f.appendChild(document.createTextNode(" is "));
    f.appendChild(el("code", null, rule.if_true));
    f.appendChild(document.createTextNode(" when "));
    f.appendChild(el("code", null, rule.depends_on_field + " " + op + " " + rule.cutoff));
    f.appendChild(document.createTextNode(", otherwise "));
    f.appendChild(el("code", null, rule.if_false));
    f.appendChild(document.createTextNode("."));
    return f;
  }

  function num(v) { return v == null ? "?" : Number(v).toFixed(2); }

  var HUD = {
    _host: null, _rowEl: null, _prevOutline: null, _prevPad: null,
    _done: 0, _failed: 0, _total: 0,

    init: function (payload) {
      payload = payload || {};
      this.destroy();

      var host = el("div");
      host.setAttribute("data-agent-hud", "");
      host.setAttribute("aria-hidden", "true");
      var root = host.attachShadow({ mode: "open" });
      var style = el("style");
      style.textContent = CSS;
      root.appendChild(style);

      var hud = el("div", "hud");

      var hd = el("div", "hd");
      this._dot = el("span", "dot");
      hd.appendChild(this._dot);
      hd.appendChild(el("span", "ttl", "Agent"));
      var meta = el("div", "meta");
      meta.appendChild(el("span", "tag", payload.dry_run === false ? "commit" : "dry run"));
      meta.appendChild(el("div", null, payload.variant || ""));
      hd.appendChild(meta);
      hud.appendChild(hd);

      this._stage = el("div", "stage");
      this._stage.appendChild(el("b", null, "Starting"));
      hud.appendChild(this._stage);

      var scroll = el("div", "scroll");

      var assigns = payload.assignments || [];
      if (assigns.length) {
        var s1 = el("div", "sec"), h1 = el("div", "sh");
        h1.appendChild(el("h3", null, "Learned mapping"));
        h1.appendChild(el("span", "n", String(assigns.length)));
        s1.appendChild(h1);
        var map = el("div", "map");
        assigns.forEach(function (a) {
          var p = el("div", "pair");
          p.appendChild(el("div", "src", a.source_header));
          p.appendChild(el("div", "conf", num(a.score)));
          p.appendChild(el("div", "tgt", "→ " + a.target_label));
          p.appendChild(el("div"));
          var bar = el("div", "bar"), fill = el("i");
          bar.appendChild(fill);
          p.appendChild(bar);
          map.appendChild(p);
          var pct = Math.max(0, Math.min(1, a.score || 0));
          requestAnimationFrame(function () {
            fill.style.transform = "scaleX(" + pct + ")";
          });
        });
        s1.appendChild(map);
        scroll.appendChild(s1);
      }

      /* The abstention claim: refusing to map a decoy column is a result, not
         a gap, so it gets its own section rather than being omitted. */
      var abst = payload.abstained || [];
      var openFields = payload.unmapped_fields || [];
      if (abst.length || openFields.length) {
        var s2 = el("div", "sec"), h2 = el("div", "sh");
        h2.appendChild(el("h3", null, "Refused to guess"));
        h2.appendChild(el("span", "n", String(abst.length + openFields.length)));
        s2.appendChild(h2);
        abst.forEach(function (a) {
          var it = el("div", "item warn");
          it.appendChild(el("span", "ic", "⊘"));
          var body = el("div");
          body.appendChild(el("div", null, a.source_header + " — abstained"));
          body.appendChild(el("div", "why", "score " + num(a.score) +
            ", margin " + num(a.margin) + " — too close to call"));
          it.appendChild(body);
          s2.appendChild(it);
        });
        openFields.forEach(function (label) {
          var it = el("div", "item warn");
          it.appendChild(el("span", "ic", "○"));
          var body = el("div");
          body.appendChild(el("div", null, label));
          body.appendChild(el("div", "why", "no source column — left empty"));
          it.appendChild(body);
          s2.appendChild(it);
        });
        scroll.appendChild(s2);
      }

      var rules = payload.derived_rules || [];
      if (rules.length) {
        var s3 = el("div", "sec"), h3 = el("div", "sh");
        h3.appendChild(el("h3", null, "Derived, not copied"));
        h3.appendChild(el("span", "n", String(rules.length)));
        s3.appendChild(h3);
        rules.forEach(function (r) {
          var it = el("div", "item derive");
          it.appendChild(el("span", "ic", "ƒ"));
          var body = el("div");
          var p = el("div", "rule");
          p.appendChild(ruleSentence(r));
          body.appendChild(p);
          if (r.observed_interval) {
            body.appendChild(el("div", "why", "cutoff located between " +
              r.observed_interval[0] + " and " + r.observed_interval[1] +
              " from the demonstration alone"));
          }
          it.appendChild(body);
          s3.appendChild(it);
        });
        scroll.appendChild(s3);
      }

      hud.appendChild(scroll);

      var ft = el("div", "ft");
      this._pb = el("i");
      var pb = el("div", "pb");
      pb.appendChild(this._pb);
      ft.appendChild(pb);
      var cnt = el("div", "cnt");
      this._okN = el("b", null, "0");
      this._badN = el("b", null, "0");
      this._totN = el("b", null, "0");
      var okW = el("span", "ok");
      okW.appendChild(this._okN);
      okW.appendChild(document.createTextNode(" verified"));
      var badW = el("span", "bad");
      badW.appendChild(this._badN);
      badW.appendChild(document.createTextNode(" failed"));
      var totW = el("span");
      totW.appendChild(document.createTextNode("of "));
      totW.appendChild(this._totN);
      cnt.appendChild(okW);
      cnt.appendChild(badW);
      cnt.appendChild(totW);
      ft.appendChild(cnt);
      this._now = el("div", "now");
      ft.appendChild(this._now);
      hud.appendChild(ft);

      root.appendChild(hud);
      document.body.appendChild(host);
      this._host = host;

      /* Reserve the panel's width instead of floating over the page.
         Screenshotted the first version and found it sitting on top of the
         Remarks column and the Save button - the two things a viewer most
         wants to watch, since Remarks is the rule-derived field. The portal
         centres itself in whatever width it is given, so a right-hand gutter
         on <html> slides it clear rather than hiding anything.

         This is layout, not styling: it is applied identically to all eight
         variants, only ever under --show, and restored on destroy, so the
         instrument's "styling is held constant across variants" claim is
         untouched and a measurement run never sees it. */
      this._prevPad = document.documentElement.style.paddingRight;
      document.documentElement.style.paddingRight = GUTTER + "px";

      this._total = payload.total_rows || 0;
      this._totN.textContent = String(this._total);
      return true;
    },

    stage: function (text) {
      if (!this._stage) return false;
      this._stage.textContent = "";
      this._stage.appendChild(el("b", null, text));
      return true;
    },

    /* Highlight the row being filled and bring it into view. The only
       light-DOM write this file makes - an inline outline, saved and
       restored, which no readback or checkbox check ever reads. */
    row: function (index, studentId) {
      if (!this._host) return false;
      this._release();
      var rows = document.querySelectorAll("#records-body tr");
      var tr = rows[index];
      if (tr) {
        this._rowEl = tr;
        this._prevOutline = tr.style.outline;
        tr.style.outline = "2px solid " + C.accent;
        tr.style.outlineOffset = "-2px";
        try {
          tr.scrollIntoView({ block: "center", behavior: "smooth" });
        } catch (e) {
          tr.scrollIntoView();
        }
      }
      if (this._now) {
        this._now.textContent = "row " + (index + 1) + "  ·  " + studentId;
      }
      return true;
    },

    cell: function (label, value) {
      if (!this._now) return false;
      var v = String(value == null ? "" : value);
      if (v.length > 22) v = v.slice(0, 21) + "…";
      this._now.textContent = label + " ← " + v;
      return true;
    },

    rowDone: function (ok) {
      if (ok) { this._done += 1; } else { this._failed += 1; }
      if (this._okN) this._okN.textContent = String(this._done);
      if (this._badN) this._badN.textContent = String(this._failed);
      var seen = this._done + this._failed;
      if (this._pb && this._total) {
        this._pb.style.transform = "scaleX(" + (seen / this._total) + ")";
        this._pb.className = this._failed ? "bad" : "";
      }
      return true;
    },

    finish: function (summary) {
      summary = summary || {};
      this._release();
      if (this._dot) this._dot.className = "dot " + (this._failed ? "bad" : "done");
      if (this._stage) {
        this._stage.textContent = "";
        this._stage.appendChild(el("b", null, summary.status || "Done"));
      }
      if (this._now) this._now.textContent = summary.detail || "";
      return true;
    },

    _release: function () {
      if (this._rowEl) {
        this._rowEl.style.outline = this._prevOutline || "";
        this._rowEl.style.outlineOffset = "";
        this._rowEl = null;
        this._prevOutline = null;
      }
    },

    destroy: function () {
      this._release();
      if (this._prevPad !== null) {
        document.documentElement.style.paddingRight = this._prevPad;
        this._prevPad = null;
      }
      var old = document.querySelector("[data-agent-hud]");
      if (old && old.parentNode) old.parentNode.removeChild(old);
      this._host = null;
      this._done = 0;
      this._failed = 0;
      this._total = 0;
      return true;
    }
  };

  window.__agentHUD = HUD;
})();
