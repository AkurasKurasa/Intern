"""
scripts/rpa_baseline.py
=======================
Objective 7: the rule-based RPA baseline for the Intern comparison.

UI.Vision was not available on the test machine, so the baseline is the
PyAutoGUI fallback named in docs/rpa_comparison/README.md: a keyboard-driven
record-and-replay macro, built the same way the README describes for
UI.Vision (one click at fixed coordinates, then type each value + Tab, a
next-tab keystroke at each tab boundary, then Submit).

Two steps, like any record-and-replay tool:

  record   Built ONCE, on the NORMAL form only. Saves a macro file with
           (a) the fixed key sequence for record N, in the normal form's Tab
           order, and (b) the screen coordinates of the first field and of
           the Submit button.
  replay   Plays the saved macro back. It never looks at the form: no labels,
           no control names, no images. On the modified form the macro is NOT
           edited, so any change in Tab order sends values into other boxes.

Usage
-----
  python scripts/rpa_baseline.py record [--record 1] [--out docs/rpa_comparison/baseline_macro.json]
  python scripts/rpa_baseline.py replay [--macro docs/rpa_comparison/baseline_macro.json]

`replay` assumes the form is already open and in front. scripts/rpa_run_all.py
drives the full 20-run comparison around it.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import _parse_intake_record  # noqa: E402
from rpa_score import is_blank_instruction  # noqa: E402

NORMAL_FORM = ROOT / "practice_apps" / "car_insurance_entry" / "car_insurance_form_wx.py"
DEFAULT_MACRO = ROOT / "docs" / "rpa_comparison" / "baseline_macro.json"
DEFAULT_INTAKE = ROOT / "data_entry_tasks" / "data_entry_intake.txt"
FORM_TITLE = "Car Insurance"


# ── record: the key sequence ──────────────────────────────────────────────────

def _load_form_module(form_path: Path):
    spec = importlib.util.spec_from_file_location("_rpa_form", form_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def form_tab_order(form_path: Path = NORMAL_FORM) -> list[list[dict]]:
    """Every Tab stop of each notebook page, in keyboard (Tab) order.

    Builds the form in-process without showing it and walks each page's
    children depth-first -- wx's Tab order is creation order, which is also
    the on-screen top-to-bottom order of this form.

    Gate 1 (2026-11-01) showed the coloured section-header bars are Tab stops
    too: a wx.Panel with nothing focusable inside takes focus itself. Those
    are kept as kind "stop" (press Tab, type nothing), as a person recording
    the macro would. Each page's list starts at the control the form focuses
    on a tab switch (_focus_first_input: first fillable DIRECT child)."""
    import wx
    mod = _load_form_module(form_path)
    app = wx.App(False)
    frame = mod.CarInsuranceFrame(None)
    fillable = (wx.CheckBox, wx.Choice, wx.TextCtrl)

    def has_fillable(win) -> bool:
        return any(isinstance(c, fillable) or has_fillable(c) for c in win.GetChildren())

    pages = []
    try:
        for i in range(frame.nb.GetPageCount()):
            order: list[dict] = []
            page = frame.nb.GetPage(i)

            def walk(win):
                for child in win.GetChildren():
                    if isinstance(child, wx.Panel) and not has_fillable(child):
                        order.append({"name": child.GetName(), "kind": "stop", "default": ""})
                    elif isinstance(child, wx.CheckBox):
                        order.append({"name": child.GetName(), "kind": "check",
                                      "default": bool(child.GetValue())})
                    elif isinstance(child, wx.Choice):
                        order.append({"name": child.GetName(), "kind": "choice",
                                      "options": [child.GetString(k) for k in range(child.GetCount())],
                                      "default": child.GetStringSelection()})
                    elif isinstance(child, wx.TextCtrl):
                        order.append({"name": child.GetName(), "kind": "text",
                                      "default": child.GetValue()})
                    else:
                        walk(child)

            walk(page)
            first = next(c.GetName() for c in page.GetChildren() if isinstance(c, fillable))
            start = next(k for k, c in enumerate(order) if c["name"] == first)
            pages.append(order[start:])
    finally:
        frame.Destroy()
        app.Destroy()
    return pages


def build_steps(pages: list[list[dict]], gold: dict) -> list[list[dict]]:
    """Turn the normal form's Tab order + one intake record into fixed keystrokes.

    Per control: type the value (text, or a dropdown's option text), or press
    Space to flip a checkbox whose wanted state differs from its default, or
    nothing. The `field` name is kept only so a person can read the macro;
    replay() never uses it."""
    out = []
    for order in pages:
        steps = []
        for c in order:
            want = gold.get(c["name"])
            keys = ""
            if c["kind"] == "stop":
                pass                                      # just Tab past it
            elif c["kind"] == "check":
                if want is not None and bool(want) != c["default"]:
                    keys = "{SPACE}"
            elif want not in (None, "", False) and not is_blank_instruction(want):
                if str(want) != str(c["default"]):
                    if c["kind"] == "choice" and str(want) in c["options"]:
                        # Gate 1 (2026-11-01): typing "Honda" into a native
                        # dropdown jumps by the LAST letter ("a" -> Acura), so
                        # the recorded keys are Home + Down x position.
                        keys = "{CHOICE:%d}" % c["options"].index(str(want))
                    else:
                        keys = str(want)
            elif want is not None and c["default"]:
                # Record says leave it empty, but the fresh form pre-fills it
                # (e.g. Account Type starts on "Checking"): clear it.
                keys = "{HOME}" if c["kind"] == "choice" else "{CLEAR}"
            steps.append({"field": c["name"], "kind": c["kind"], "keys": keys})
        out.append(steps)
    return out


# ── record: the coordinates ───────────────────────────────────────────────────

def _dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def find_form_hwnd(title_fragment: str = FORM_TITLE, timeout: float = 20.0) -> int:
    import win32gui
    end = time.time() + timeout
    while time.time() < end:
        found = []

        def cb(h, _):
            if win32gui.IsWindowVisible(h) and title_fragment in win32gui.GetWindowText(h):
                found.append(h)
        win32gui.EnumWindows(cb, None)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


def _center(rect) -> tuple[int, int]:
    l, t, r, b = rect
    return (l + r) // 2, (t + b) // 2


def capture_coordinates(hwnd: int) -> dict:
    """Screen centres of the first visible text box (Policy Number on the
    normal form) and of the Submit button."""
    import win32gui
    edits, submit = [], []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        cls = win32gui.GetClassName(h)
        if cls == "Edit" and not edits:
            edits.append(h)
        elif cls == "Button" and win32gui.GetWindowText(h) == "Submit":
            submit.append(h)
    win32gui.EnumChildWindows(hwnd, cb, None)
    if not edits or not submit:
        raise RuntimeError("could not find the first text box or the Submit button")
    return {"first_field": _center(win32gui.GetWindowRect(edits[0])),
            "submit": _center(win32gui.GetWindowRect(submit[0])),
            "window": list(win32gui.GetWindowRect(hwnd))}


def record(record_num: int, out: Path, intake: Path = DEFAULT_INTAKE,
           form_path: Path = NORMAL_FORM) -> dict:
    _dpi_aware()
    gold = _parse_intake_record(intake.read_text(encoding="utf-8"), record_num)
    steps = build_steps(form_tab_order(form_path), gold)
    proc = subprocess.Popen([sys.executable, str(form_path)])
    try:
        hwnd = find_form_hwnd()
        if not hwnd:
            raise RuntimeError("form window did not appear")
        time.sleep(1.5)
        coords = capture_coordinates(hwnd)
    finally:
        proc.kill()
    macro = {
        "built_on": form_path.name,
        "record": record_num,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "coordinates": coords,
        "next_tab_key": "ctrl+tab",
        "pages": steps,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(macro, indent=2), encoding="utf-8")
    return macro


# ── replay ────────────────────────────────────────────────────────────────────

def _type_unicode(text: str, interval: float = 0.01) -> None:
    """Type text as Unicode key events (SendInput + KEYEVENTF_UNICODE).

    pyautogui.write() silently drops characters it has no key for, such as the
    em dash in record 1's agency name. A real RPA tool types them, so the
    baseline must too, or it would be penalised for a library limitation."""
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP = 0x0004, 0x0002
    for ch in text:
        raw = ch.encode("utf-16-le")                     # 1 code unit, or 2 for a surrogate pair
        for code in (int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)):
            seq = (INPUT * 2)()
            for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                seq[j].type = 1
                seq[j].u.ki = KEYBDINPUT(0, code, flags, 0, 0)
            ctypes.windll.user32.SendInput(2, seq, ctypes.sizeof(INPUT))
        time.sleep(interval)


def replay(macro: dict, key_interval: float = 0.01, tab_settle: float = 0.4) -> None:
    """Play the macro blind: fixed click, fixed keys, fixed Submit click."""
    _dpi_aware()
    import pyautogui
    pyautogui.PAUSE = 0.02
    pyautogui.FAILSAFE = True          # mouse to a screen corner aborts the run
    x, y = macro["coordinates"]["first_field"]
    pyautogui.click(x, y)
    time.sleep(0.3)
    pages = macro["pages"]
    for p, steps in enumerate(pages):
        for i, s in enumerate(steps):
            if s["keys"] == "{SPACE}":
                pyautogui.press("space")
            elif s["keys"].startswith("{CHOICE:"):
                pyautogui.press("home")
                pyautogui.press("down", presses=int(s["keys"][8:-1]))
            elif s["keys"] == "{HOME}":
                pyautogui.press("home")       # a dropdown's first option is the blank one
            elif s["keys"] == "{CLEAR}":
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("delete")
            elif s["keys"]:
                _type_unicode(s["keys"], interval=key_interval)
            if i < len(steps) - 1:
                pyautogui.press("tab")
        if p < len(pages) - 1:
            pyautogui.hotkey(*macro["next_tab_key"].split("+"))
            time.sleep(tab_settle)
    sx, sy = macro["coordinates"]["submit"]
    pyautogui.click(sx, sy)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--record", type=int, default=1)
    r.add_argument("--out", default=str(DEFAULT_MACRO))
    p = sub.add_parser("replay")
    p.add_argument("--macro", default=str(DEFAULT_MACRO))
    args = ap.parse_args()
    if args.cmd == "record":
        m = record(args.record, Path(args.out))
        n = sum(len(s) for s in m["pages"])
        print(f"Macro written to {args.out}: {n} controls over {len(m['pages'])} tabs, "
              f"coordinates {m['coordinates']}")
    else:
        replay(json.loads(Path(args.macro).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
