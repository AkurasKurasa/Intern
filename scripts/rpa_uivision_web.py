"""
scripts/rpa_uivision_web.py
===========================
Objective 7, Scopes 2 and 3, re-run with a real RPA product: the UI.Vision RPA
browser extension (a9t9 software GmbH), the same product used for Scope 1
(scripts/rpa_uivision.py, its desktop app).

The earlier Scope 2 and 3 baselines were scripts that imitated an RPA tool
(scripts/rpa_scope2.py, scripts/rpa_scope3.py). Here UI.Vision itself does the
work, on the same tasks, data and interface changes; Intern's runs are reused.

How UI.Vision is run
--------------------
The official extension package is downloaded from the Chrome Web Store servers
(`setup`) and loaded, unchanged, into the Chromium that Playwright installs
(branded Chrome no longer allows loading an extension from the command line).
Its store public key is kept, so it has its store id. Each macro is loaded the
way a person does it: UI.Vision's window -> import the macro file -> select it
-> Play. Playwright only presses those buttons, navigates the play tab to the
page to work on, and reads the results; it never touches the portal or the
inbox itself. The one setting changed from the defaults: the side panel is off,
so UI.Vision uses its own window (a side panel cannot be opened by a script).

Scope 2 (grade sheet -> web portal)
  The macro is what UI.Vision's recorder saves when the 50 rows are entered
  on the base portal: for each row, `type` Course, Year, Grade and `select`
  Remarks, then `click` Save All Rows. Each step's locator and backup
  locators are computed with the recorder's own code (src/common/inspector.js,
  MIT): `id=` first, then name, three XPaths and CSS. Built once on v0_base,
  replayed unedited on each of the 8 portal versions. Scored from what the
  portal saved (its own Save validation), per student, against the grade sheet.

Scope 3 (inbox routing)
  A UI.Vision JavaScript macro that applies the same mail rules as the earlier
  scripted baseline (exact sender -> the action taken before; subject keywords;
  no-reply senders), working through the practice inbox page: open each
  message, read sender and subject, press the matching button (Reply / Forward
  / Schedule / Leave alone) or go back, leaving it for a person. The page is
  the app's own practice inbox, served by a stand-in server here so no
  decision is written into the app's training data. Run on the original inbox
  and on each surface change.

Usage
  python scripts/rpa_uivision_web.py setup
  python scripts/rpa_uivision_web.py scope2 [--only v0_base]
  python scripts/rpa_uivision_web.py scope3
LIVE GUI: a browser window opens and UI.Vision drives it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import io
import json
import re
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

EXT_ID = "gcbalfbdmfieckjlnblleoemohcganoc"
CRX_URL = ("https://clients2.google.com/service/update2/crx?response=redirect&prodversion=146.0"
           f"&acceptformat=crx2,crx3&x=id%3D{EXT_ID}%26uc")
HOME = Path.home() / "AppData" / "Local" / "InternRPA"
EXT_DIR = HOME / "uivision-extension"
PROFILE = HOME / "chromium-uivision"
OUT = ROOT / "docs" / "rpa_comparison" / "uivision_web"
PORTAL_PORT, INBOX_PORT = 8765, 8797
MOCKSITE = ROOT / "practice_apps" / "mocksite"
PRACTICE_INBOX = ROOT / "components" / "inbox_router" / "practice_inbox"


# ── setup: the official extension, unpacked with its store key ────────────────

def _varint(b: bytes, i: int):
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7


def _fields(b: bytes):
    i, out = 0, []
    while i < len(b):
        key, i = _varint(b, i)
        if key & 7 == 2:
            ln, i = _varint(b, i)
            out.append((key >> 3, b[i:i + ln]))
            i += ln
        elif key & 7 == 0:
            _, i = _varint(b, i)
        else:
            raise ValueError("unexpected protobuf wire type")
    return out


def extension_id(public_key: bytes) -> str:
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(public_key).hexdigest()[:32])


def unpack_crx(data: bytes, out: Path, want_id: str = EXT_ID) -> dict:
    """Unpack a CRX3 package and write its store key into manifest.json."""
    if data[:4] != b"Cr24" or struct.unpack("<I", data[4:8])[0] != 3:
        raise ValueError("not a CRX3 package")
    hlen = struct.unpack("<I", data[8:12])[0]
    header, payload = data[12:12 + hlen], data[12 + hlen:]
    keys = [dict(_fields(proof)).get(1) for num, proof in _fields(header) if num == 2]
    key = next((k for k in keys if k and extension_id(k) == want_id), None)
    if key is None:
        raise ValueError(f"package is not signed with the key of {want_id}")
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        for name in z.namelist():
            if not str((out / name).resolve()).startswith(str(out.resolve())):
                raise ValueError(f"unsafe path in package: {name}")
        z.extractall(out)
    mf = out / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8-sig"))
    manifest["key"] = base64.b64encode(key).decode()
    mf.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def setup() -> None:
    with urllib.request.urlopen(CRX_URL, timeout=120) as r:
        data = r.read()
    m = unpack_crx(data, EXT_DIR)
    print(f"UI.Vision {m['version']} unpacked to {EXT_DIR} (id {EXT_ID})")


# ── local servers ─────────────────────────────────────────────────────────────

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_portal() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(MOCKSITE / "serve.py"), "--port", str(PORTAL_PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class InboxServer:
    """Stand-in for the app's practice endpoints: serves the practice inbox page
    unchanged, a given message list, and records decisions here, not in the app."""

    def __init__(self, port: int = INBOX_PORT):
        self.messages: list[dict] = []
        self.decisions: dict[str, dict] = {}
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                files = {"/practice/": ("index.html", "text/html"), "/practice/index.html": ("index.html", "text/html"),
                         "/practice/app.js": ("app.js", "application/javascript"),
                         "/practice/style.css": ("style.css", "text/css")}
                if path in files:
                    name, ctype = files[path]
                    return self._send(200, (PRACTICE_INBOX / name).read_bytes(), ctype)
                if path == "/practice/api/inbox":
                    return self._send(200, json.dumps({"messages": outer.messages}).encode())
                self._send(404, b"{}")

            def do_POST(self):
                if self.path.split("?")[0] == "/practice/api/record":
                    data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    outer.decisions[data["message_id"]] = {"decision": data["decision"],
                                                           "reply_body": data.get("reply_body", "")}
                    return self._send(200, b'{"ok": true}')
                self._send(404, b"{}")

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


# ── UI.Vision in Chromium, operated through its own window ────────────────────

class UIVision:
    def __init__(self, pw):
        import shutil
        if not (EXT_DIR / "manifest.json").exists():
            sys.exit("Run `python scripts/rpa_uivision_web.py setup` first.")
        shutil.rmtree(PROFILE, ignore_errors=True)          # fresh, default settings every session
        self.ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport=None,
            args=[f"--disable-extensions-except={EXT_DIR}", f"--load-extension={EXT_DIR}",
                  "--window-position=1960,30", "--window-size=1450,830"])
        sw = self.ctx.service_workers[0] if self.ctx.service_workers else \
            self.ctx.wait_for_event("serviceworker", timeout=30000)
        time.sleep(3)
        self.version = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))["version"]
        self.config = sw.evaluate("() => new Promise(r => chrome.storage.local.get('config', o => r(o.config || {})))")
        sw.evaluate("""() => new Promise(r => chrome.storage.local.get('config', o => {
            chrome.storage.local.set({config: Object.assign({}, o.config || {},
                {showSidePanel: false, oneTimeShowSidePanel: false})}, r); }))""")
        for pg in list(self.ctx.pages):
            if "ui.vision" in pg.url:
                pg.close()
        self.tab = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ide = self.ctx.new_page()
        self.ide.goto(f"chrome-extension://{EXT_ID}/popup.html")
        time.sleep(5)

    def import_macro(self, path: Path, name: str) -> None:
        self.ide.set_input_files("#select_json_files_for_macros", str(path))
        time.sleep(2)
        self.ide.get_by_text(name, exact=False).first.click()
        time.sleep(1)

    def log_text(self) -> str:
        return self.ide.evaluate("() => document.body.innerText")

    def play(self, name: str, url: str, timeout: float = 600) -> dict:
        """Open `url` in the play tab, press Play on macro `name`, wait for UI.Vision to finish.

        A table macro logs 'Playing macro <name>' ... 'Macro completed (Runtime N s)';
        a JavaScript macro logs '<name> started' ... '<name> completed/failed ... (Runtime N s)'."""
        self.tab.goto(url)
        self.tab.wait_for_load_state("load")
        time.sleep(1.5)
        self.ide.keyboard.press("Escape")                    # close any dialog left by the last run
        clear = self.ide.get_by_role("button", name="Clear", exact=True)
        if clear.count():
            clear.first.click()
        self.tab.bring_to_front()
        t0 = time.time()
        self.ide.get_by_role("button", name="Play", exact=True).click()
        status, log = "", ""
        while time.time() - t0 < timeout:
            time.sleep(1)
            text = self.log_text()
            k = max(text.rfind("Playing macro"), text.rfind(f"{name} started"))
            seg = text[k:] if k >= 0 else ""
            m = re.search(r"(Macro completed|" + re.escape(name) + r"[^\n]* completed)[^\n]*", seg)
            e = re.search(r"\[error\]\s*\n?([^\n]+)", seg)
            if m or e:
                status = "completed" if m and not e else f"error: {e.group(1).strip()}"
                log = seg
                break
        else:
            status = "no end seen"
            log = self.log_text()
        time.sleep(1.5)
        runtime = re.search(r"Runtime ([\d.]+)s", log)
        return {"status": status, "seconds": round(time.time() - t0, 1),
                "runtime_s": float(runtime.group(1)) if runtime else None, "log": log[-4000:]}

    def close(self):
        self.ctx.close()


# ── Scope 2 ───────────────────────────────────────────────────────────────────

# UI.Vision's recorder (src/common/inspector.js, MIT licence), ported as-is:
# the candidate locators it saves for an element and the one it picks.
RECORDER_JS = r"""
(ids) => {
  const and = list => list.reduce((p, c) => p && c, true);
  const byXPath = x => document.evaluate(x, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  const getTagIndex = dom => Array.from(dom.parentNode.childNodes)
      .filter(n => n.nodeType === dom.nodeType && n.tagName === dom.tagName)
      .reduce((prev, node, i) => prev !== null ? prev : (node === dom ? i + 1 : prev), null);
  const relativeXPath = dom => {
    if (!dom) return null;
    if (dom.nodeType === 3) return '@text';
    const index = getTagIndex(dom), tag = dom.tagName.toLowerCase();
    return index > 1 ? `${tag}[${index}]` : tag;
  };
  const xpath = dom => {
    const helper = (dom, cur, list) => {
      if (!dom) return null;
      if (!cur) return dom.nodeType === 3 ? helper(dom.parentNode) : helper(dom, dom, []);
      if (!cur.parentNode) return ['html'].concat(list);
      if (cur.tagName === 'BODY') return ['html', 'body'].concat(list);
      if (cur.id) return [`*[@id="${cur.id}"]`].concat(list);
      return helper(dom, cur.parentNode, [relativeXPath(cur)].concat(list));
    };
    const parts = helper(dom);
    return (parts[0] === 'html' ? '/' : '//') + parts.join('/');
  };
  const xpathPosition = dom => {
    let path = '', current = dom;
    try {
      while (current !== null) {
        let p;
        if (current.parentNode != null) p = '/' + relativeXPath(current);
        else if (current.tagName === 'BODY') p = 'html/body';
        else p = '/' + current.nodeName.toLowerCase();
        path = p + path;
        const locator = '/' + path;
        if (dom === byXPath(locator)) return locator;
        current = current.parentNode;
      }
    } catch (e) {}
    return null;
  };
  const attributeValue = v => v.indexOf("'") < 0 ? `'${v}'` : `"${v}"`;
  const xpathAttr = dom => {
    const PREF = ['id', 'name', 'value', 'type', 'action', 'onclick'];
    const atts = {};
    for (const a of dom.attributes) atts[a.name] = a.value;
    const names = [];
    for (const n of PREF) {
      if (atts[n] != null) {
        names.push(n);
        const loc = '//' + dom.nodeName.toLowerCase() + '[' + names.map(k => '@' + k + '=' + attributeValue(atts[k])).join(' and ') + ']';
        if (dom === byXPath(loc)) return loc;
      }
    }
    return null;
  };
  const selector = dom => {
    if (dom.nodeType !== 1) return '';
    if (dom.tagName === 'BODY') return 'body';
    if (dom.id) return '#' + dom.id;
    const classes = (dom.getAttribute('class') || '').split(/\s+/g).filter(Boolean);
    const children = Array.from(dom.parentNode.childNodes).filter(e => e.nodeType === 1);
    const sameTag = children.filter(e => e.tagName === dom.tagName);
    const sameClass = children.filter(e => { const cs = (e.getAttribute('class') || '').split(/\s+/g); return and(classes.map(c => cs.indexOf(c) !== -1)); });
    let extra = '';
    if (sameTag.length === 1) extra = '';
    else if (classes.length && sameClass.length === 1) extra = '.' + classes.join('.');
    else extra = ':nth-child(' + (1 + children.findIndex(e => e === dom)) + ')';
    return selector(dom.parentNode) + ' > ' + dom.tagName.toLowerCase() + extra;
  };
  const resolve = loc => {
    const i = loc.indexOf('='), m = loc.slice(0, i), v = loc.slice(i + 1);
    if (m === 'id') return document.getElementById(v);
    if (m === 'name') return document.getElementsByName(v)[0] || null;
    if (m === 'xpath') return byXPath(v);
    if (m === 'css') return document.querySelector(v);
    return null;
  };
  return ids.map(id => {
    const dom = document.getElementById(id);
    if (!dom) return null;
    const c = [];
    if (dom.id) c.push('id=' + dom.id);
    const name = dom.getAttribute('name');
    if (name) c.push('name=' + name);
    c.push('xpath=' + xpath(dom));
    const xa = xpathAttr(dom); if (xa) c.push('xpath=' + xa);
    const xp = xpathPosition(dom); if (xp) c.push('xpath=' + xp);
    c.push('css=' + selector(dom));
    const chosen = c.find(l => { try { return resolve(l) === dom; } catch (e) { return false; } }) || null;
    return {target: chosen, targets: c};
  });
}
"""

SCOPE2_VARIANTS = ["v0_base", "v1_reordered", "v2_relabeled", "v3_extra_fields",
                   "v4_unassociated", "v5_near_duplicates", "v6a_options", "v6b_scale"]
SCOPE2_MACRO = "Objective7_Scope2_GradeSheet"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def scope2_steps(expected: dict) -> list[tuple[str, str, str]]:
    """(command, element id on v0_base, value) for every recorded step. Pure."""
    steps = []
    for sid, e in expected.items():
        steps += [("type", f"course_{sid}", e["course"]), ("type", f"year_{sid}", _num(e["year"])),
                  ("type", f"grade_{sid}", _num(e["grade"])), ("select", f"remarks_{sid}", "label=" + e["remarks"])]
    steps.append(("click", "submit-btn", ""))
    return steps


def scope2_macro(steps, locators, save_alternatives: bool) -> dict:
    cmds = []
    for (cmd, _, value), loc in zip(steps, locators):
        c = {"Command": cmd, "Target": loc["target"], "Value": value}
        if save_alternatives:
            c["Targets"] = loc["targets"]
        cmds.append(c)
    return {"Name": SCOPE2_MACRO, "CreationDate": time.strftime("%Y-%m-%d"), "Commands": cmds}


def score_portal(state: dict, expected: dict) -> dict:
    """Score one portal after the run. Pure.

    rows_correct    rows the portal SAVED with all four targets right
    values_correct  of the 4 x 50 target values now in the cells (staged)
    noncourse       year, grade, remarks in the cells (3 x 50)"""
    from rpa_scope2 import _same
    staged = {r["student_id"]: r for r in state["staged"]}
    committed = {r["student_id"]: r for r in state["committed"]}
    targets = ("course", "year", "grade", "remarks")
    rows_ok = vals = noncourse = saved = 0
    for sid, exp in expected.items():
        s, c = staged.get(sid, {}), committed.get(sid, {})
        ok_s = [_same(s.get(k), exp[k]) for k in targets]
        vals += sum(ok_s)
        noncourse += sum(ok_s[1:])
        if any(c.get(k) for k in targets):
            saved += 1
        rows_ok += all(_same(c.get(k), exp[k]) for k in targets)
    first = next(iter(expected))
    placed = sum(_same(staged.get(first, {}).get(k), expected[first][k]) for k in targets)
    return {"rows": len(expected), "rows_correct": rows_ok, "rows_saved": saved,
            "values_correct": vals, "values_total": 4 * len(expected),
            "noncourse_correct": noncourse, "noncourse_total": 3 * len(expected),
            "fields_placed_row1": placed, "save_status": state.get("status", "")}


PORTAL_STATE_JS = """() => {
  const P = window.__portal;
  return {staged: P.records.map((_, i) => P.read(i)), committed: P.records.map(r => Object.assign({}, r)),
          status: (document.getElementById('form-status') || {}).textContent || ''};
}"""


def run_scope2(only: list[str] | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    from rpa_scope2 import expected_rows
    OUT.mkdir(parents=True, exist_ok=True)
    expected = expected_rows()
    steps = scope2_steps(expected)
    portal = start_portal()
    res = {"tool": None, "variants": {}}
    try:
        time.sleep(1.5)
        with sync_playwright() as pw:
            uiv = UIVision(pw)
            res["tool"] = f"UI.Vision RPA {uiv.version} (Chrome extension, in Playwright Chromium)"
            # "Recording" on the base portal: the recorder's own locator choice per element
            uiv.tab.goto(f"http://127.0.0.1:{PORTAL_PORT}/v0_base/index.html")
            uiv.tab.wait_for_load_state("networkidle")
            locators = uiv.tab.evaluate(RECORDER_JS, [eid for _, eid, _ in steps])
            missing = [steps[i][1] for i, l in enumerate(locators) if not l or not l["target"]]
            if missing:
                raise RuntimeError(f"elements missing on v0_base: {missing[:5]}")
            alt = bool(uiv.config.get("saveAlternativeLocators", True))
            macro = scope2_macro(steps, locators, alt)
            mpath = OUT / f"{SCOPE2_MACRO}.json"
            mpath.write_text(json.dumps(macro, indent=2, ensure_ascii=False), encoding="utf-8")
            res["macro"] = {"file": mpath.relative_to(ROOT).as_posix(), "commands": len(macro["Commands"]),
                            "backup_locators_saved": alt, "built_on": "v0_base"}
            uiv.import_macro(mpath, SCOPE2_MACRO)
            for v in only or SCOPE2_VARIANTS:
                run = uiv.play(SCOPE2_MACRO, f"http://127.0.0.1:{PORTAL_PORT}/{v}/index.html")
                state = uiv.tab.evaluate(PORTAL_STATE_JS)
                (OUT / f"scope2_{v}_log.txt").write_text(run["log"], encoding="utf-8")
                (OUT / f"scope2_{v}_portal.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
                s = score_portal(state, expected)
                res["variants"][v] = {"uivision": {**s, "macro_status": run["status"], "runtime_s": run["runtime_s"],
                                                   "wall_s": run["seconds"]}}
                print(f"{v:<20} {run['status'][:70]:<72} rows {s['rows_correct']}/50 saved {s['rows_saved']} "
                      f"values {s['values_correct']}/200 non-course {s['noncourse_correct']}/150 "
                      f"({run['runtime_s']} s)", flush=True)
            uiv.close()
    finally:
        portal.kill()
    prev = json.loads((ROOT / "docs" / "rpa_comparison" / "scope2_results.json").read_text(encoding="utf-8"))
    for v in res["variants"]:
        res["variants"][v]["intern"] = prev["variants"][v]["intern"]
        res["variants"][v]["scripted_baseline"] = prev["variants"][v]["baseline"]
    (OUT / "scope2_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


# ── Scope 3 ───────────────────────────────────────────────────────────────────

SCOPE3_MACRO = "Objective7_Scope3_MailRules"
TEMPLATES = {"reply": "Thanks for your message. I will get back to you shortly.",
             "forward": "Forwarding this to the right person.",
             "schedule": "Scheduling this."}


def scope3_script(sender_rules: dict, keyword_rules: list) -> str:
    """The mail rules as a UI.Vision JavaScript macro. Same rules, same order
    as rpa_scope3.baseline_decide()."""
    kw = ",\n  ".join(f"[/{pat}/i, {json.dumps(act)}]" for pat, act in keyword_rules)
    return f"""// Objective 7, Scope 3: mail rules written from the five messages the user already handled.
// Same rules as a mail client's: exact sender -> the action taken before; subject keywords;
// no-reply senders are left alone; anything else is left for a person.
var SENDER_RULES = {json.dumps({k.lower(): v for k, v in sender_rules.items()}, indent=1)};
var KEYWORD_RULES = [
  {kw}
];
var NOREPLY = ["no-reply", "noreply", "newsletter"];
var TEMPLATES = {json.dumps(TEMPLATES)};
function decide(addr, subject) {{
  if (SENDER_RULES.hasOwnProperty(addr)) return SENDER_RULES[addr];
  for (var k = 0; k < KEYWORD_RULES.length; k++) if (KEYWORD_RULES[k][0].test(subject)) return KEYWORD_RULES[k][1];
  if (NOREPLY.indexOf(addr.split("@")[0]) >= 0) return "leave_alone";
  return "handoff";
}}
var BUTTON = {{reply: "id=replyPillBtn", forward: "id=forwardPillBtn", schedule: "id=scheduleBtn"}};
var n = uiv.$$("css=#rowList li.row-item").length;
uiv.log("messages in the inbox: " + n);
for (var i = 0; i < n; i++) {{
  uiv.page.click(uiv.$$("css=#rowList li.row-item")[i]);
  var sender = uiv.$("id=detailSender", {{hasText: true}}).text;
  var subject = uiv.$("id=detailSubject").text;
  var m = /<([^>]+)>/.exec(sender);
  var addr = (m ? m[1] : sender).trim().toLowerCase();
  var d = decide(addr, subject);
  uiv.log((i + 1) + ". " + addr + " | " + subject + " -> " + d);
  if (d === "leave_alone") {{
    uiv.page.click("id=archiveBtn");
  }} else if (BUTTON[d]) {{
    uiv.page.click(BUTTON[d]);
    uiv.page.type("id=replyBody", TEMPLATES[d]);
    uiv.page.click("id=sendBtn");
  }} else {{
    uiv.page.click("id=backBtn");
  }}
  // wait until the list is visible again: the page returns to it once the decision is recorded
  uiv.$("css=#rowList li.row-item", {{timeout: 15}});
}}
"""


def _msg_dict(m) -> dict:
    return {"message_id": m.id, "sender": m.sender, "sender_email": m.sender_email,
            "subject": m.subject, "body_text": m.body_text}


def run_scope3() -> dict:
    from playwright.sync_api import sync_playwright
    import rpa_scope3 as S3
    OUT.mkdir(parents=True, exist_ok=True)
    labels = json.loads(S3.LABELS.read_text(encoding="utf-8"))["labels"]
    inbox, sent = S3.load()
    history = [m for m in inbox if m.id in S3.HISTORY]
    test = [m for m in inbox if m.id not in S3.HISTORY]
    rules = S3.build_baseline_rules(history, sent)
    script = scope3_script(rules[0], rules[1])
    mpath = OUT / f"{SCOPE3_MACRO}.json"
    mpath.write_text(json.dumps({"Name": SCOPE3_MACRO, "Script": script}, indent=2), encoding="utf-8")
    conds = {"original": lambda m: m, "changed": S3.perturb,
             **{c: (lambda c: lambda m: S3.perturb(m, (c,)))(c) for c in S3.CHANGES}}
    srv = InboxServer()
    res = {"conditions": {}, "decisions": {}, "macro": {"file": mpath.relative_to(ROOT).as_posix()}}
    try:
        with sync_playwright() as pw:
            uiv = UIVision(pw)
            res["tool"] = f"UI.Vision RPA {uiv.version} (Chrome extension, JavaScript macro)"
            uiv.import_macro(mpath, SCOPE3_MACRO)
            for cond, fn in conds.items():
                srv.messages = [_msg_dict(fn(m)) for m in test]
                srv.decisions = {}
                run = uiv.play(SCOPE3_MACRO, f"http://127.0.0.1:{INBOX_PORT}/practice/")
                dec = {m.id: srv.decisions.get(m.id, {}).get("decision", S3.HANDOFF) for m in test}
                res["decisions"][cond] = dec
                res["conditions"][cond] = {**S3.metrics(dec, labels), "macro_status": run["status"],
                                           "runtime_s": run["runtime_s"]}
                (OUT / f"scope3_{cond}_log.txt").write_text(run["log"], encoding="utf-8")
                c = res["conditions"][cond]
                print(f"{cond:<10} {run['status'][:50]:<52} correct {c['lenient']}/25 handoff {c['handoff']} "
                      f"wrong {c['wrong_action']} ({run['runtime_s']} s)", flush=True)
            uiv.close()
    finally:
        srv.close()
    prev = json.loads(S3.OUT.read_text(encoding="utf-8"))["per_message"]
    for cond in conds:
        u = res["decisions"][cond]
        intern = {i: prev[i][f"intern_{cond}"] for i in labels}
        scripted = {i: prev[i][f"baseline_{cond}"] for i in labels}
        b = sum(intern[i] in labels[i]["acceptable"] and u[i] not in labels[i]["acceptable"] for i in labels)
        c = sum(intern[i] not in labels[i]["acceptable"] and u[i] in labels[i]["acceptable"] for i in labels)
        res["conditions"][cond]["mcnemar_vs_intern"] = {"intern_only_correct": b, "uivision_only_correct": c,
                                                        "p": S3.mcnemar_exact(b, c)}
        res["conditions"][cond]["same_as_scripted_rules"] = sum(u[i] == scripted[i] for i in labels)
        res["conditions"][cond]["unchanged_vs_original"] = sum(u[i] == res["decisions"]["original"][i] for i in labels)
    (OUT / "scope3_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    s2 = sub.add_parser("scope2")
    s2.add_argument("--only", nargs="*")
    sub.add_parser("scope3")
    a = ap.parse_args()
    if a.cmd == "setup":
        setup()
    elif a.cmd == "scope2":
        run_scope2(a.only)
    else:
        run_scope3()


if __name__ == "__main__":
    main()
