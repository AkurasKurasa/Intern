"""
scripts/rpa_uivision.py
=======================
Objective 7, re-run with a real RPA product: UI.Vision RPA (a9t9 software GmbH).

The panel chair asked to "test Intern against one simple tool (such as
UI.Vision) on the Scope 1 form". The first run used a PyAutoGUI script because
UI.Vision was not installed (scripts/rpa_baseline.py). This script puts
UI.Vision itself in that place. The task, the two forms, record 1, the scoring
and Intern's runs stay the same, so only the baseline system changes.

How UI.Vision drives a desktop form
-----------------------------------
UI.Vision's desktop mode (XModules) has no recorder and cannot read a native
app's controls, so a desktop macro is written by hand as XClick (screen
coordinates or an image) + XType (real keystrokes). Image matching would fail
on the modified form for its new colours alone, so the macro is keyboard-
driven, as fixed before testing in docs/rpa_comparison/README.md: one XClick on
the first field, record 1's values in the NORMAL form's Tab order, Ctrl+Tab
between pages, and an XClick on Submit.

Commands
--------
  python scripts/rpa_uivision.py build
      Built ONCE, on the NORMAL form. Opens it briefly (no clicks or keys) to
      read the screen positions of the first field and Submit, then writes
        docs/rpa_comparison/uivision/Objective7_Scope1_Record1.json  the macro
        docs/rpa_comparison/uivision/run_macro.html                  local page that hands
                                                                     the macro to UI.Vision
  python scripts/rpa_uivision.py browser
      Opens the dedicated Chrome profile used for the runs (one-time setup:
      install UI.Vision there; see docs/rpa_comparison/uivision/SETUP.md).
  python scripts/rpa_uivision.py run [--only gate|runs] [--runs 5]
      LIVE GUI -- only the user runs this (CLAUDE.md). Each run opens a fresh
      form, lets UI.Vision play the unedited macro, waits for the form's
      auto-saved submission, closes the form, and writes one row to
      docs/rpa_comparison/run_sheet_uivision.csv. Do not touch the PC while
      it runs.
Then:
  python scripts/rpa_score.py --sheet docs/rpa_comparison/run_sheet_uivision.csv \
      --submissions docs/rpa_comparison/submissions
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import _parse_intake_record  # noqa: E402
from rpa_baseline import (DEFAULT_INTAKE, NORMAL_FORM, _dpi_aware, build_steps,  # noqa: E402
                          capture_coordinates, find_form_hwnd, form_tab_order)

FORMS = {
    "normal":   NORMAL_FORM,
    "modified": NORMAL_FORM.with_name("car_insurance_form_wx_MESSY_TEST.py"),
}
TITLES = {"normal": "Car Insurance", "modified": "(Messy UI Test)"}
UIV_DIR = ROOT / "docs" / "rpa_comparison" / "uivision"
MACRO_NAME = "Objective7_Scope1_Record1"
MACRO_FILE = UIV_DIR / f"{MACRO_NAME}.json"
BUILD_FILE = UIV_DIR / "build.json"
RUN_PAGE = UIV_DIR / "run_macro.html"
LOG_DIR = UIV_DIR / "logs"
FORM_SUBMISSIONS = ROOT / "practice_apps" / "data" / "output" / "submissions"
KEEP_DIR = ROOT / "docs" / "rpa_comparison" / "submissions"
PYAUTOGUI_SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet.csv"
SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet_uivision.csv"
RUN_LOG = ROOT / "docs" / "rpa_comparison" / "run_log_uivision.jsonl"
SYSTEM = "UI.Vision"
SHEET_FIELDS = ["system", "form_version", "run", "record", "submission",
                "finished_without_help", "minutes", "notes"]

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
# A Chrome profile used only for these runs, so the user's own browser,
# tabs and settings are never touched and closeBrowser=1 closes only this one.
PROFILE = Path.home() / "AppData" / "Local" / "InternRPA" / "chrome-uivision"
STORE_URL = "https://chromewebstore.google.com/detail/gcbalfbdmfieckjlnblleoemohcganoc"

# Where the form is placed for the build and for every run (primary display,
# which is the only one UI.Vision's desktop clicks cover). The browser goes to
# the second display when there is one, so it never covers the form.
FORM_POS = (40, 40)
BROWSER_POS_SECOND = (1960, 40)
BROWSER_POS_PRIMARY = (1100, 40)
BROWSER_SIZE = (760, 760)


# ── build: the UI.Vision macro ────────────────────────────────────────────────

def _cmd(command: str, target: str = "", value: str = "", description: str = "") -> dict:
    return {"Command": command, "Target": target, "Value": value, "Description": description}


def _keys_to_xtype(keys: str) -> str:
    """One step's keys (rpa_baseline.build_steps format) in UI.Vision XType syntax."""
    if keys == "{SPACE}":
        return "${KEY_SPACE}"
    if keys.startswith("{CHOICE:"):
        return "${KEY_HOME}" + "${KEY_DOWN}" * int(keys[8:-1])
    if keys == "{HOME}":
        return "${KEY_HOME}"
    if keys == "{CLEAR}":
        return "${KEY_CTRL+KEY_A}${KEY_DEL}"
    if "${" in keys:
        # XType would read it as a variable or key name, not as text
        raise ValueError(f"value cannot be typed literally by XType: {keys!r}")
    return keys


def to_uivision_commands(macro: dict) -> list[dict]:
    """The baseline key sequence as UI.Vision commands. Pure function.

    Same sequence as rpa_baseline.replay(): click the first field, then per
    control its keys followed by Tab (except after a page's last control),
    Ctrl+Tab and a short pause between pages, and a click on Submit."""
    x, y = macro["coordinates"]["first_field"]
    sx, sy = macro["coordinates"]["submit"]
    out = [
        _cmd("XDesktopAutomation", "true", description="desktop mode: X commands go to the screen, "
                                                       "and the browser is not brought to the front"),
        _cmd("XClick", f"D{x},{y}", description="first field (fixed screen position)"),
    ]
    pages = macro["pages"]
    for p, steps in enumerate(pages):
        for i, s in enumerate(steps):
            text = _keys_to_xtype(s["keys"]) if s["keys"] else ""
            if i < len(steps) - 1:
                text += "${KEY_TAB}"
            if text:
                out.append(_cmd("XType", text, description=s["field"]))
        if p < len(pages) - 1:
            out.append(_cmd("XType", "${KEY_CTRL+KEY_TAB}", description=f"next tab ({p + 2} of {len(pages)})"))
            out.append(_cmd("pause", "400", description="let the tab switch settle"))
    out.append(_cmd("XClick", f"D{sx},{sy}", description="Submit (fixed screen position)"))
    return out


def macro_json(commands: list[dict], created: str) -> dict:
    return {"Name": MACRO_NAME, "CreationDate": created, "Commands": commands}


RUN_PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Objective 7 - UI.Vision run</title>
<style>body{font:16px/1.5 system-ui,sans-serif;margin:24px;max-width:640px}
#s{padding:12px;border-radius:6px;background:#eef}#s.err{background:#fdd}</style></head>
<body><h1>Objective 7: UI.Vision run</h1><p id="s">Waiting for the UI.Vision extension&hellip;</p>
<script>
// Hands the macro to UI.Vision the same way its embedded-macro pages do
// (event kantuSaveAndRunMacro). Needs, once: Chrome "Allow access to file
// URLs" for UI.Vision, and UI.Vision Settings > API > "Run embedded macros
// from local files". Command-line switches (direct=1, closeRPA, closeBrowser,
// savelog) are read by UI.Vision from this page's URL.
var MACRO = __MACRO__;
var s = document.getElementById('s');
function fail(msg) { s.className = 'err'; s.textContent = msg; }
var start = Date.now();
(function wait() {
  var root = document.documentElement;
  if (root.getAttribute('data-kantu')) {
    // UI.Vision removes the mark again shortly after if local-file runs are off
    return setTimeout(function () {
      if (!root.getAttribute('data-kantu')) {
        return fail('UI.Vision is installed, but local-file macro runs are off: ' +
                    'UI.Vision > Settings > API > tick "Run embedded macros from local files".');
      }
      var ok = false;
      window.addEventListener('kantuInvokeSuccess', function () {
        ok = true; s.textContent = 'UI.Vision accepted the macro and is running it.';
      });
      window.dispatchEvent(new CustomEvent('kantuSaveAndRunMacro', {detail: {direct: 1, json: MACRO}}));
      setTimeout(function () { if (!ok) fail('UI.Vision did not accept the macro.'); }, 15000);
    }, 1500);
  }
  if (Date.now() - start > 20000) {
    return fail('UI.Vision extension not found on this page. In chrome://extensions > UI.Vision > ' +
                'Details, turn on "Allow access to file URLs".');
  }
  setTimeout(wait, 250);
})();
</script></body></html>
"""


def run_page_html(macro: dict) -> str:
    return RUN_PAGE_TEMPLATE.replace("__MACRO__", json.dumps(macro, ensure_ascii=False, indent=1))


def _place_form(hwnd: int, topmost: bool) -> None:
    """Move the form to FORM_POS (size unchanged) and, during runs, keep it
    above other windows so the browser and UI.Vision's panel cannot cover it.
    Window placement only: no click or key is sent to the form."""
    import win32con
    import win32gui
    after = win32con.HWND_TOPMOST if topmost else win32con.HWND_TOP
    win32gui.SetWindowPos(hwnd, after, FORM_POS[0], FORM_POS[1], 0, 0,
                          win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
    time.sleep(0.6)


def _open_form(version: str) -> tuple[subprocess.Popen, int]:
    proc = subprocess.Popen([sys.executable, str(FORMS[version])], cwd=str(ROOT))
    hwnd = find_form_hwnd(TITLES[version])
    if not hwnd:
        proc.kill()
        raise RuntimeError(f"{version} form did not open")
    time.sleep(1.5)
    return proc, hwnd


def _close(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
    time.sleep(1.0)


def build(record_num: int = 1) -> dict:
    """Build the macro on the NORMAL form only, as any record-and-replay user would."""
    import win32gui
    _dpi_aware()
    t0 = time.time()
    gold = _parse_intake_record(DEFAULT_INTAKE.read_text(encoding="utf-8"), record_num)
    steps = build_steps(form_tab_order(NORMAL_FORM), gold)
    proc, hwnd = _open_form("normal")
    try:
        _place_form(hwnd, topmost=False)
        coords = capture_coordinates(hwnd)
        size = win32gui.GetWindowRect(hwnd)
    finally:
        _close(proc)
    base = {"built_on": NORMAL_FORM.name, "record": record_num,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "form_position": list(FORM_POS), "coordinates": coords, "pages": steps}
    macro = macro_json(to_uivision_commands(base), time.strftime("%Y-%m-%d"))
    UIV_DIR.mkdir(parents=True, exist_ok=True)
    MACRO_FILE.write_text(json.dumps(macro, indent=2, ensure_ascii=False), encoding="utf-8")
    RUN_PAGE.write_text(run_page_html(macro), encoding="utf-8")
    base["seconds"] = round(time.time() - t0, 1)
    base["window_rect"] = list(size)
    base["n_commands"] = len(macro["Commands"])
    BUILD_FILE.write_text(json.dumps(base, indent=2, ensure_ascii=False), encoding="utf-8")
    return base


# ── run ───────────────────────────────────────────────────────────────────────

def _log(event: dict) -> None:
    event = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    print(json.dumps(event, ensure_ascii=False), flush=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _submissions() -> set[str]:
    return {p.name for p in FORM_SUBMISSIONS.glob("*.json")} if FORM_SUBMISSIONS.exists() else set()


def _second_display() -> bool:
    import win32api
    return len(win32api.EnumDisplayMonitors()) > 1


def run_url(log_file: Path) -> str:
    """file:// URL of the run page with UI.Vision's command-line switches."""
    log = log_file.as_posix()
    return (RUN_PAGE.resolve().as_uri()
            + f"?direct=1&closeRPA=1&closeBrowser=1&savelog={log}")


def browser_args(url: str) -> list[str]:
    pos = BROWSER_POS_SECOND if _second_display() else BROWSER_POS_PRIMARY
    return [str(CHROME), f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
            f"--window-position={pos[0]},{pos[1]}", f"--window-size={BROWSER_SIZE[0]},{BROWSER_SIZE[1]}",
            "--new-window", url]


def read_status(log_file: Path) -> str:
    """First line of UI.Vision's savelog file, e.g. 'Status=OK' or the error."""
    if not log_file.exists():
        return ""
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return log_file.read_text(encoding=enc).splitlines()[0].strip()
        except (UnicodeDecodeError, IndexError):
            continue
    return ""


def _edit_texts(hwnd: int) -> list[str]:
    """Text of every text box in the form (read only), to see whether any
    keystroke has reached it yet."""
    import win32con
    import win32gui
    texts: list[str] = []

    def cb(h, _):
        if win32gui.GetClassName(h) == "Edit":
            buf = win32gui.PyMakeBuffer(512)
            n = win32gui.SendMessage(h, win32con.WM_GETTEXT, 256, buf)
            texts.append(bytes(buf[:n * 2]).decode("utf-16-le", "replace"))
    win32gui.EnumChildWindows(hwnd, cb, None)
    return texts


def run_once(version: str, tag: str, timeout: float, start_timeout: float = 75) -> dict:
    proc, hwnd = _open_form(version)
    _place_form(hwnd, topmost=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{tag}.txt"
    if log_file.exists():
        log_file.unlink()
    before, fresh = _submissions(), _edit_texts(hwnd)
    t0 = time.time()
    browser = subprocess.Popen(browser_args(run_url(log_file)))
    sub, t_submit, started, why = "", None, False, ""
    end = time.time() + timeout
    while time.time() < end:
        if not sub:
            new = sorted(_submissions() - before)
            if new:
                sub, t_submit = new[-1], time.time()
        if not started and _edit_texts(hwnd) != fresh:
            started = True
        if log_file.exists() and (sub or time.time() - t0 > 20):
            break                                   # UI.Vision finished (OK or error)
        if sub and time.time() - t_submit > 20:
            break                                   # submitted; no log written
        if not started and not sub and time.time() - t0 > start_timeout:
            why = f"no keystroke reached the form within {start_timeout:.0f} s"
            break
        if proc.poll() is not None:
            why = "form closed"
            break
        time.sleep(0.5)
    time.sleep(1.0)
    if not sub:
        new = sorted(_submissions() - before)
        sub = new[-1] if new else ""
    status = read_status(log_file)
    minutes = ((t_submit or time.time()) - t0) / 60
    _close(proc)
    if browser.poll() is None:
        time.sleep(3)                               # closeBrowser=1 should have closed it
    kept = ""
    if sub:
        KEEP_DIR.mkdir(parents=True, exist_ok=True)
        kept = f"{tag}__{sub}"
        shutil.copy2(FORM_SUBMISSIONS / sub, KEEP_DIR / kept)
    if sub:
        note = "reached Submit"
    elif why:
        note = f"no submission saved: {why}"
    else:
        note = f"no submission within {timeout / 60:.0f} min"
    note += f"; UI.Vision log: {status or 'none written'}"
    return {"submission": kept, "finished_without_help": "yes" if sub else "no",
            "minutes": f"{minutes:.2f}", "notes": note}


def gate_report(submission: str) -> str:
    """Score one gate submission against record 1."""
    from rpa_score import _load_json, score_submission
    if not submission:
        return "Gate failed: nothing was submitted."
    s = score_submission(_load_json(KEEP_DIR / submission), 1,
                         DEFAULT_INTAKE.read_text(encoding="utf-8"))
    head = (f"Gate: {s['fields_total'] - s['fields_wrong']}/{s['fields_total']} fields correct, "
            f"fully correct = {s['fully_correct']}")
    return head + ("" if s["fully_correct"] else f"\nWrong fields: {', '.join(s['wrong_fields'])}")


def new_sheet(pyautogui_sheet: Path, runs: int) -> list[dict]:
    """UI.Vision rows to fill, plus Intern's existing rows (same form, same record)."""
    rows = [{"system": SYSTEM, "form_version": "normal", "run": "setup"}]
    for version in ("normal", "modified"):
        rows += [{"system": SYSTEM, "form_version": version, "run": str(i), "record": "1"}
                 for i in range(1, runs + 1)]
    rows += [r for r in csv.DictReader(pyautogui_sheet.open(encoding="utf-8")) if r["system"] == "Intern"]
    return [{k: r.get(k, "") for k in SHEET_FIELDS} for r in rows]


def _load_sheet(runs: int) -> list[dict]:
    if SHEET.exists():
        return list(csv.DictReader(SHEET.open(encoding="utf-8")))
    return new_sheet(PYAUTOGUI_SHEET, runs)


def _save_sheet(rows: list[dict]) -> None:
    with SHEET.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in SHEET_FIELDS} for r in rows)


def put(rows: list[dict], version: str, run: str, result: dict) -> None:
    for r in rows:
        if r["system"] == SYSTEM and r["form_version"] == version and r["run"] == run:
            r.update(result)
            _save_sheet(rows)
            return
    raise KeyError((SYSTEM, version, run))


def open_browser() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(CHROME), f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", STORE_URL, "chrome://extensions"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("browser")
    r = sub.add_parser("run")
    r.add_argument("--only", choices=["gate", "runs"], default="runs")
    r.add_argument("--runs", type=int, default=5)
    r.add_argument("--timeout", type=float, default=480, help="seconds per run")
    args = ap.parse_args()

    if args.cmd == "build":
        b = build()
        print(f"Macro: {MACRO_FILE.relative_to(ROOT)} ({b['n_commands']} commands)\n"
              f"Run page: {RUN_PAGE.relative_to(ROOT)}\n"
              f"Coordinates: {b['coordinates']}  (form at {b['form_position']}, built in {b['seconds']} s)")
        return
    if args.cmd == "browser":
        open_browser()
        return

    _dpi_aware()
    if not MACRO_FILE.exists():
        sys.exit("Build the macro first: python scripts/rpa_uivision.py build")
    if not CHROME.exists():
        sys.exit(f"Chrome not found at {CHROME}")
    if args.only == "gate":
        res = run_once("normal", f"gate_uivision_normal_{time.strftime('%H%M%S')}", args.timeout)
        _log({"event": "gate", "form": "normal", **res})
        print("\n" + gate_report(res["submission"]))
        return
    rows = _load_sheet(args.runs)
    _save_sheet(rows)
    t0 = time.time()
    for version in ("normal", "modified"):
        for i in range(1, args.runs + 1):
            res = run_once(version, f"UIVision_{version}_{i}", args.timeout)
            put(rows, version, str(i), res)
            _log({"event": "run", "system": SYSTEM, "form": version, "run": i, **res})
    _log({"event": "done", "total_minutes": round((time.time() - t0) / 60, 1)})


if __name__ == "__main__":
    main()
