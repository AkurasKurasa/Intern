"""
scripts/rpa_uivision.py
=======================
Objective 7, re-run with a real RPA product: UI.Vision RPA (a9t9 software GmbH),
played by its desktop app, Ui.Vision for Desktop.

The panel chair asked to "test Intern against one simple tool (such as
UI.Vision) on the Scope 1 form". The first run used a PyAutoGUI script because
UI.Vision was not installed (scripts/rpa_baseline.py). This script puts
UI.Vision itself in that place. The task, the two forms, record 1, the scoring
and Intern's runs stay the same, so only the baseline system changes.

How UI.Vision drives a desktop form
-----------------------------------
UI.Vision's desktop automation has no recorder and cannot read a native app's
controls, so a desktop macro is written as clicks (screen coordinates or an
image) + typed keys. Image matching would fail on the modified form for its
new colours alone, so the macro is keyboard-driven, as fixed before testing in
docs/rpa_comparison/README.md: one click on the first field, record 1's values
in the NORMAL form's Tab order, Ctrl+Tab between pages, and a click on Submit.

The macro is a UI.Vision desktop-app macro (`<name>.d.js`, "use desktop-app"),
played by `uivision-app.exe --run <file>`; the app logs "<name> completed
(Runtime N s)" when it ends. No browser is involved.

Commands
--------
  python scripts/rpa_uivision.py build
      Built ONCE, on the NORMAL form. Opens it briefly (no clicks or keys) to
      read the screen positions of the first field and Submit, then writes
      docs/rpa_comparison/uivision/Objective7_Scope1_Record1.d.js and build.json.
  python scripts/rpa_uivision.py run [--only gate|runs] [--runs 5]
      LIVE GUI. Each run opens a fresh form, lets UI.Vision play the unedited
      macro, waits for the form's auto-saved submission and the app's
      "completed" line, closes both, and writes one row to
      docs/rpa_comparison/run_sheet_uivision.csv. Do not touch the PC while
      it runs (moving the mouse to the top-left corner stops UI.Vision).
Then:
  python scripts/rpa_score.py --sheet docs/rpa_comparison/run_sheet_uivision.csv \
      --submissions docs/rpa_comparison/submissions
"""
from __future__ import annotations

import argparse
import csv
import json
import re
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
MACRO_NAME = "Objective7_Scope1_Record1.d.js"
MACRO_FILE = UIV_DIR / MACRO_NAME
BUILD_FILE = UIV_DIR / "build.json"
APP_LOG_DIR = UIV_DIR / "logs"
FORM_SUBMISSIONS = ROOT / "practice_apps" / "data" / "output" / "submissions"
KEEP_DIR = ROOT / "docs" / "rpa_comparison" / "submissions"
PYAUTOGUI_SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet.csv"
SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet_uivision.csv"
RUN_LOG = ROOT / "docs" / "rpa_comparison" / "run_log_uivision.jsonl"
SYSTEM = "UI.Vision"
SHEET_FIELDS = ["system", "form_version", "run", "record", "submission",
                "finished_without_help", "minutes", "notes"]

APP_EXE = Path.home() / "AppData" / "Local" / "UI.Vision" / "DesktopAutomation" / "uivision-app.exe"
APP_LOG = Path.home() / "AppData" / "Roaming" / "UiVision RPA" / "log.txt"

# Where the form is placed for the build and for every run (primary display,
# which is the only one UI.Vision's desktop clicks cover). It is kept above
# other windows during runs so UI.Vision's own window cannot cover it.
FORM_POS = (40, 40)


# ── build: the UI.Vision macro ────────────────────────────────────────────────

def _keys_to_xtype(keys: str) -> str:
    """One step's keys (rpa_baseline.build_steps format) in UI.Vision's
    ${KEY_...} typing grammar."""
    if keys == "{SPACE}":
        return "${KEY_SPACE}"
    if keys.startswith("{CHOICE:"):
        return "${KEY_HOME}" + "${KEY_DOWN}" * int(keys[8:-1])
    if keys == "{HOME}":
        return "${KEY_HOME}"
    if keys == "{CLEAR}":
        return "${KEY_CTRL+KEY_A}${KEY_DELETE}"
    if "${" in keys:
        # UI.Vision would read it as a key name, not as text
        raise ValueError(f"value cannot be typed literally by UI.Vision: {keys!r}")
    return keys


def _js(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def to_desktop_macro(macro: dict) -> str:
    """The baseline key sequence as a UI.Vision desktop-app macro. Pure function.

    Same sequence as rpa_baseline.replay(): click the first field, then per
    control its keys followed by Tab (except after a page's last control),
    Ctrl+Tab and a short pause between pages, and a click on Submit."""
    x, y = macro["coordinates"]["first_field"]
    sx, sy = macro["coordinates"]["submit"]
    lines = [
        '"use desktop-app";',
        "// Objective 7, Scope 1: enter record 1 of the intake packet into the car-insurance form.",
        f"// Built once on the NORMAL form ({macro.get('built_on', NORMAL_FORM.name)}) and NOT edited",
        "// before the modified-form runs. Keyboard-driven: UI.Vision cannot read the form's",
        "// controls, and image matching would fail on the modified form's colours.",
        f"uiv.desktop.mouse.click({x}, {y});   // first field (fixed screen position)",
        "uiv.sleep(300);",
    ]
    pages = macro["pages"]
    for p, steps in enumerate(pages):
        lines.append(f"// tab {p + 1} of {len(pages)}")
        for i, s in enumerate(steps):
            text = _keys_to_xtype(s["keys"]) if s["keys"] else ""
            if i < len(steps) - 1:
                text += "${KEY_TAB}"
            if text:
                lines.append(f"uiv.desktop.keyboard.type({_js(text)});   // {s['field']}")
        if p < len(pages) - 1:
            lines.append('uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab')
            lines.append("uiv.sleep(400);")
    lines.append(f"uiv.desktop.mouse.click({sx}, {sy});   // Submit (fixed screen position)")
    return "\n".join(lines) + "\n"


def _place_form(hwnd: int, topmost: bool) -> None:
    """Move the form to FORM_POS (size unchanged) and, during runs, keep it
    above other windows. Window placement only: no click or key is sent."""
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
    _dpi_aware()
    t0 = time.time()
    gold = _parse_intake_record(DEFAULT_INTAKE.read_text(encoding="utf-8"), record_num)
    steps = build_steps(form_tab_order(NORMAL_FORM), gold)
    proc, hwnd = _open_form("normal")
    try:
        _place_form(hwnd, topmost=False)
        coords = capture_coordinates(hwnd)
    finally:
        _close(proc)
    base = {"built_on": NORMAL_FORM.name, "record": record_num,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "form_position": list(FORM_POS), "coordinates": coords, "pages": steps}
    UIV_DIR.mkdir(parents=True, exist_ok=True)
    MACRO_FILE.write_text(to_desktop_macro(base), encoding="utf-8")
    base["seconds"] = round(time.time() - t0, 1)
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


def _app_log_lines() -> list[str]:
    if not APP_LOG.exists():
        return []
    return APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()


_RUNTIME = re.compile(r"Runtime\s+([\d.]+)\s*s", re.I)


def parse_app_log(lines: list[str], name: str = MACRO_NAME) -> tuple[str, float | None]:
    """(status, runtime seconds) of the macro run in these app-log lines.

    The app logs '<name> started', then '<name> completed (Runtime N s)'.
    status is 'completed', the text of an error / failed / stopped line, or ''
    while the macro is still running."""
    tag = f"[{name}]"
    for line in lines:
        if tag not in line:
            continue
        rest = line.split(tag, 1)[1].strip()
        if rest.startswith(f"{name} completed"):
            m = _RUNTIME.search(rest)
            return "completed", float(m.group(1)) if m else None
        if "[error]" in line or re.match(re.escape(name) + r" (failed|stopped|aborted)", rest):
            return rest, None
    return "", None


def _edit_texts(hwnd: int) -> list[str]:
    """Text of every text box in the form (read only), to see whether any
    keystroke has reached it yet."""
    import ctypes
    import win32con
    import win32gui
    texts: list[str] = []

    def cb(h, _):
        if win32gui.GetClassName(h) == "Edit":
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.SendMessageW(h, win32con.WM_GETTEXT, 256, buf)
            texts.append(buf.value)
    win32gui.EnumChildWindows(hwnd, cb, None)
    return texts


def _stop_app() -> None:
    subprocess.run(["taskkill", "/F", "/IM", APP_EXE.name], capture_output=True)
    time.sleep(1.0)


def run_once(version: str, tag: str, timeout: float, start_timeout: float = 60) -> dict:
    _stop_app()
    proc, hwnd = _open_form(version)
    _place_form(hwnd, topmost=True)
    before, fresh = _submissions(), _edit_texts(hwnd)
    log_start = len(_app_log_lines())
    t0 = time.time()
    subprocess.Popen([str(APP_EXE), "--run", str(MACRO_FILE)])
    sub, t_submit, started, why, status, runtime = "", None, False, "", "", None
    end = time.time() + timeout
    while time.time() < end:
        if not sub:
            new = sorted(_submissions() - before)
            if new:
                sub, t_submit = new[-1], time.time()
        if not started and _edit_texts(hwnd) != fresh:
            started = True
        status, runtime = parse_app_log(_app_log_lines()[log_start:])
        if status and (sub or status != "completed"):
            break                                   # UI.Vision finished (OK or not)
        if sub and time.time() - t_submit > 20:
            break                                   # submitted; no end line seen
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
    app_lines = _app_log_lines()[log_start:]
    APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    (APP_LOG_DIR / f"{tag}.txt").write_text("\n".join(app_lines) + "\n", encoding="utf-8")
    wall = ((t_submit or time.time()) - t0) / 60
    _close(proc)
    _stop_app()
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
    note += f"; UI.Vision: {status or 'no end line'}"
    if runtime is not None:
        note += f" (macro runtime {runtime:.1f} s; {wall:.2f} min incl. app start)"
    minutes = runtime / 60 if runtime is not None else wall
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    r = sub.add_parser("run")
    r.add_argument("--only", choices=["gate", "runs"], default="runs")
    r.add_argument("--runs", type=int, default=5)
    r.add_argument("--timeout", type=float, default=480, help="seconds per run")
    args = ap.parse_args()

    if args.cmd == "build":
        b = build()
        print(f"Macro: {MACRO_FILE.relative_to(ROOT)}\n"
              f"Coordinates: {b['coordinates']}  (form at {b['form_position']}, built in {b['seconds']} s)")
        return

    _dpi_aware()
    if not MACRO_FILE.exists():
        sys.exit("Build the macro first: python scripts/rpa_uivision.py build")
    if not APP_EXE.exists():
        sys.exit(f"Ui.Vision for Desktop not found at {APP_EXE}")
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
