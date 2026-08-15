#!/usr/bin/env python3
"""
auto_demo.py  —  Deterministic BC trace generator.

Reads data from Notepad, fills the wx Car Insurance form via pyautogui,
logs every action as a demo trace (same format as DemoRecorder).
Produces N sessions for transformer training without manual recording.

Usage:
    python scripts/auto_demo.py --sessions 10 --record_num 1 --delay 0.3
"""

import sys
import os
import time
import json
import datetime
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [_ROOT, os.path.join(_ROOT, "components"), os.path.join(_ROOT, "scripts")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pyautogui

from observers.ui_observer.ui_observer import UIAutomationObserver
from data_sources.notepad_source import NotepadDataSource
from recorder.recorder import _fmt_state

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.05


# ── Helpers ───────────────────────────────────────────────────────────────────

def _center(elem):
    b = elem.get("bbox", [0, 0, 0, 0])
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _find_by_label(elements, label, etype=None, role="active"):
    ll = label.lower().strip()
    for e in elements:
        if role and e.get("window_role") != role:
            continue
        if etype and e.get("type") != etype:
            continue
        el = (e.get("label") or e.get("text") or "").strip().lower()
        if el == ll:
            return e
    return None


def _find_button(elements, label):
    ll = label.lower()
    for e in elements:
        if e.get("type") == "button":
            el = (e.get("label") or e.get("text") or "").strip().lower()
            if ll in el:
                return e
    return None


def _find_tab(elements, label):
    ll = label.lower()
    for e in elements:
        if e.get("type") == "tabitem":
            el = (e.get("label") or e.get("text") or "").strip().lower()
            if ll in el:
                return e
    return None


# ── Runner ────────────────────────────────────────────────────────────────────

class AutoDemoRunner:

    # Fields to fill per tab (label as shown in form, matching Notepad key names)
    TAB_FIELDS = {
        "Policy Holder": [
            "First Name", "Middle Name", "Last Name",
            "Date of Birth", "SSN", "Credit Score",
            "Email Address", "Home Phone", "Cell Phone",
            "Street Address 1", "Street Address 2",
            "City", "ZIP Code", "County",
            "DL Number", "DL Expiration",
        ],
        "Vehicle": [
            "VIN", "Year", "Model", "Trim / Sub-model",
            "Current Mileage", "Annual Miles Est.",
            "Purchase Date", "Purchase Price ($)", "Current Market Value ($)",
            "Lienholder/Lender", "Loan / Lease No.",
        ],
        "Coverage": [
            "Bodily Injury (k$/k$)", "Property Damage ($)",
            "Total Premium ($)",
        ],
        "Payment": [
            "Total Premium ($)", "Down Payment ($)", "Balance Due ($)",
            "Payment Due Date",
        ],
    }

    def __init__(self, sessions: int, record_num: int, delay: float):
        self.sessions   = sessions
        self.record_num = record_num
        self.delay      = delay
        self.observer   = UIAutomationObserver()
        self.data_src   = NotepadDataSource()
        self.out_root   = os.path.join(_ROOT, "data", "demos", "human")
        os.makedirs(self.out_root, exist_ok=True)

    # ── State ─────────────────────────────────────────────────────────────────

    def _state(self):
        return _fmt_state(self.observer.get_state())

    # ── Step loggers ──────────────────────────────────────────────────────────

    def _log_click(self, steps, sb, sa, pos):
        steps.append({
            "trace_id":   f"live_step_{len(steps):04d}",
            "timestamp":  datetime.datetime.now().isoformat(),
            "duration":   1.0,
            "type":       "form_filling",
            "state":      sb,
            "mouse":      {"actions": [{"position": [float(pos[0]), float(pos[1])],
                                        "type": "click",
                                        "timestamp": datetime.datetime.now().isoformat()}]},
            "keyboard":   {"actions": []},
            "next_state": sa,
        })

    def _log_keyboard(self, steps, sb, sa, text):
        steps.append({
            "trace_id":   f"live_step_{len(steps):04d}",
            "timestamp":  datetime.datetime.now().isoformat(),
            "duration":   1.0,
            "type":       "form_filling",
            "state":      sb,
            "mouse":      {"actions": []},
            "keyboard":   {"actions": [{"strokes": [{"pasted_text": text, "key": ""}]}]},
            "next_state": sa,
        })

    # ── Actions ───────────────────────────────────────────────────────────────

    def _click(self, steps, elem):
        cx, cy = _center(elem)
        sb = self._state()
        pyautogui.click(cx, cy)
        time.sleep(self.delay)
        sa = self._state()
        self._log_click(steps, sb, sa, (cx, cy))

    def _type(self, steps, value: str):
        sb = self._state()
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.08)
        pyautogui.typewrite(str(value), interval=0.04)
        time.sleep(self.delay)
        sa = self._state()
        self._log_keyboard(steps, sb, sa, str(value))

    def _fill(self, steps, elements, label) -> bool:
        value = self.data_src.lookup(label)
        if not value:
            return False
        elem = _find_by_label(elements, label)
        if not elem or elem.get("type") not in ("input", "combobox"):
            return False
        self._click(steps, elem)
        self._type(steps, value)
        return True

    def _switch_tab(self, steps, label):
        state = self._state()
        tab = _find_tab(state.get("elements", []), label)
        if not tab:
            print(f"  [warn] tab '{label}' not found — skipping")
            return False
        self._click(steps, tab)
        time.sleep(0.4)
        return True

    def _clear_form(self, steps):
        state = self._state()
        btn = _find_button(state.get("elements", []), "clear all")
        if btn:
            self._click(steps, btn)
            time.sleep(0.6)
        else:
            print("  [warn] 'Clear All' button not found")

    # ── Session ───────────────────────────────────────────────────────────────

    def run_session(self, idx: int) -> int:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        session_dir = os.path.join(self.out_root, f"session_{ts}_form_filling")
        os.makedirs(session_dir, exist_ok=True)
        steps = []

        self.data_src.refresh(self.record_num)

        self._clear_form(steps)

        for tab_label, fields in self.TAB_FIELDS.items():
            if not self._switch_tab(steps, tab_label):
                continue
            state = self._state()
            elems = state.get("elements", [])
            filled = 0
            for field in fields:
                if self._fill(steps, elems, field):
                    filled += 1
            print(f"    {tab_label}: {filled}/{len(fields)} fields filled")

        for i, step in enumerate(steps):
            path = os.path.join(session_dir, f"live_step_{i:04d}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(step, f, indent=2, ensure_ascii=False)

        print(f"  Session {idx+1}: {len(steps)} steps → {os.path.basename(session_dir)}")
        return len(steps)

    def run(self):
        print(f"Auto demo: {self.sessions} session(s), record #{self.record_num}")
        print(f"Make sure the wx form AND Notepad are open and visible.\n")
        time.sleep(2.0)  # give user time to focus the form

        total = 0
        for i in range(self.sessions):
            total += self.run_session(i)
            if i < self.sessions - 1:
                time.sleep(1.0)

        print(f"\nDone. {self.sessions} sessions, {total} total steps saved to data/demos/human/")
        print(f"Next: python scripts/train.py --trace_dir data/demos/human --epochs 50")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Auto BC demo trace generator")
    p.add_argument("--sessions",   type=int,   default=10,  help="Number of sessions")
    p.add_argument("--record_num", type=int,   default=1,   help="Which Notepad record to use")
    p.add_argument("--delay",      type=float, default=0.3, help="Seconds between actions")
    args = p.parse_args()

    AutoDemoRunner(
        sessions   = args.sessions,
        record_num = args.record_num,
        delay      = args.delay,
    ).run()


if __name__ == "__main__":
    main()
