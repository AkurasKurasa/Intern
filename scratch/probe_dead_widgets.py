"""Targeted probe: the two chronic dead widgets, through the REAL fix path.

Exercises agent._act_on_element (the 2026-07-11 dead-widget fixes: ValuePattern
→ RangeValuePattern → keystrokes for spins; child-walk + SelectionItemPattern
for long dropdowns) on exactly two controls, nothing else:

    'Years Continuously Insured'  (wx SpinCtrl — rejects paste AND keystrokes)
    'State'                       (50-option combobox — target below the fold)

Run:  python scratch/probe_dead_widgets.py [--record 2]
Prep: form open on the POLICYHOLDER tab, scrolled anywhere (UIA finds the
      controls off-screen too). Click the form during the countdown.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "components"))

from components.agent.agent import LLMAgent
from data_sources.notepad_source import _parse_records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", type=int, default=2,
                    help="Intake record whose values to fill (default 2 — its State "
                         "is 'Texas', below the dropdown fold: the hard case).")
    args = ap.parse_args()

    intake = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "data_entry_tasks", "data_entry_intake.txt")
    records = _parse_records(open(intake, encoding="utf-8").read())
    rec = records.get(args.record, {})
    targets = [
        ("Years Continuously Insured", "editcontrol"),
        ("State",                      "comboboxcontrol"),
    ]
    values = {}
    for label, _ in targets:
        values[label] = next((v for k, v in rec.items() if k.lower() == label.lower()), "")
    print(f"record {args.record} values: {values}")
    if not all(values.values()):
        print("WARN: some values missing from the record — that field will be skipped.")

    print("\nClick the FORM (Policyholder tab) now…")
    for i in range(5, 0, -1):
        print(f"  {i}…", end="\r")
        time.sleep(1)
    print("  GO!   ")

    import uiautomation as uia
    import win32gui
    hwnd = win32gui.GetForegroundWindow()
    print(f"window: {win32gui.GetWindowText(hwnd)!r}")

    # Minimal agent shell — just what _act_on_element/_resolve_live_control need.
    agent = object.__new__(LLMAgent)
    agent._locked_hwnd = hwnd

    root = uia.ControlFromHandle(hwnd)
    finders = {"editcontrol": "EditControl", "comboboxcontrol": "ComboBoxControl"}

    for label, etype in targets:
        val = values.get(label, "")
        if not val:
            continue
        ctrl = getattr(root, finders[etype])(searchDepth=25, Name=label)
        if not ctrl.Exists(maxSearchSeconds=1.0):
            print(f"FAIL  {label!r}: control not found in UIA tree (wrong tab?)")
            continue
        r = ctrl.BoundingRectangle
        elem = {"label": label, "type": etype,
                "bbox": [r.left, r.top, r.right, r.bottom]}
        before = ""
        try:
            before = (ctrl.GetValuePattern().Value or "").strip()
        except Exception:
            pass
        ok = agent._act_on_element(elem, val)
        after = ""
        try:
            after = (ctrl.GetValuePattern().Value or "").strip()
        except Exception:
            pass
        verdict = "PASS" if (ok and val.lower().startswith(after.lower()[:4]) or after.lower() == val.lower()) \
                  else ("PASS(reported)" if ok else "FAIL")
        print(f"{verdict:14s} {label!r}: {before!r} → {after!r}   (wanted {val!r}, act_on_element={ok})")

    print("\nCheck the form visually — the two fields should hold the values above.")


if __name__ == "__main__":
    main()
