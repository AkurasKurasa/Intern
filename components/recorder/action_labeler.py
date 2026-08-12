"""
recorder/action_labeler.py
===========================
Translates one raw recorded trace step (mouse/keyboard events + before/after
state) into a single SemanticAction — the Universal Semantic Action Space
label used by the dataset builder and, eventually, the agent's merge step.

NOTE: this is unrelated to components/trace_translator/ (the HTML/CV UI
element-detection package for perception). Deliberately named differently
to avoid confusion between "detect UI elements on a page" and "label a
recorded demo step with a semantic verb."

Classification keys ONLY on: which control-type the input landed on, and
whether that element's own value/checked-state differs between `state` and
`next_state`. No field names, no tab names, no app names — obeys the
project's NO-HARDCODE rule (see .claude/CLAUDE.md).

This module is additive/offline: it does not change the live dataset build
in transformer.py or the live agent loop. Run it standalone to compare its
labels against the current `_decode_actions` classification before wiring it
into training.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

# Same dual sys.path bootstrap recorder.py itself uses, so this module works
# both as `python -m components.recorder.action_labeler` and run/imported
# directly (`python components/recorder/action_labeler.py`).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))    # components/recorder/
_COMP_DIR = os.path.dirname(_THIS_DIR)                     # components/
_ROOT_DIR = os.path.dirname(_COMP_DIR)                      # Intern/
for _p in (_ROOT_DIR, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from agent.semantic_action import SemanticAction, Verb
except ImportError:
    from components.agent.semantic_action import SemanticAction, Verb

# Control-type buckets — control-type is a UNIVERSAL widget property (every
# app has buttons/checkboxes/comboboxes), never a per-form field name.
_CHECKBOX_TYPES = {"checkboxcontrol", "checkbox"}
_COMBOBOX_TYPES = {"comboboxcontrol", "combobox"}
_TAB_TYPES      = {"tabitemcontrol", "tabitem"}
_BUTTON_TYPES   = {"buttoncontrol", "button"}
_TEXT_ENTRY_TYPES = {"editcontrol", "input", "spincontrolcontrol", "spincontrol"}

# Same "real click target" gate the transformer dataset already uses —
# decorative containers (pane/window/document) never resolve to a target.
_CLICK_TARGET_CTRL_TYPES = {
    "input", "button", "combobox", "checkbox", "listitem",
    "tabitem", "radiobutton", "hyperlink", "menuitem", "text",
    "datetime", "calendar",
    "editcontrol", "buttoncontrol", "comboboxcontrol", "checkboxcontrol",
    "listitemcontrol", "tabitemcontrol", "radiobuttoncontrol",
    "hyperlinkcontrol", "menuitemcontrol", "textcontrol",
    "datetimecontrol", "calendarcontrol",
}


def _find_click_target_idx(mouse_actions: List[Dict], elements: List[Dict]) -> int:
    """Same containment+smallest-area rule as transformer._find_click_elem_idx."""
    clicks = [a for a in mouse_actions if a.get("type") in ("click", "double_click", "drag")]
    if not clicks:
        return -1
    pos = clicks[0].get("position", clicks[0].get("src", [0, 0]))
    px, py = float(pos[0]), float(pos[1])
    best_idx, best_area = -1, float("inf")
    for idx, elem in enumerate(elements):
        ctype = (elem.get("type") or "").lower()
        if ctype not in _CLICK_TARGET_CTRL_TYPES:
            continue
        bbox = elem.get("bbox", [])
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in bbox[:4])
        if x2 <= x1 or y2 <= y1 or not (x1 <= px <= x2 and y1 <= py <= y2):
            continue
        area = (x2 - x1) * (y2 - y1)
        if area < best_area:
            best_area, best_idx = area, idx
    return best_idx


def _find_focused_entry_idx(state: Dict[str, Any], elements: List[Dict]) -> int:
    focused_id = state.get("focused_element_id")
    if not focused_id:
        return -1
    for idx, elem in enumerate(elements):
        if elem.get("element_id") == focused_id:
            return idx
    return -1


def _elem_key(elem: Dict) -> tuple:
    # element_id resets PER-WINDOW (foreground form and background Notepad both
    # emit "elem_8", "elem_18", ...) — window_role disambiguates which window's
    # element this id actually names.
    return (elem.get("element_id"), elem.get("window_role"))


def _next_value(elem: Dict, next_by_key: Dict[tuple, Dict]) -> Optional[str]:
    nxt = next_by_key.get(_elem_key(elem))
    return nxt.get("value") if nxt is not None else None


def translate_step(trace: Dict[str, Any]) -> SemanticAction:
    """One trace JSON dict (as loaded) -> one SemanticAction label."""
    state       = trace.get("state", {}) or {}
    next_state  = trace.get("next_state") or state
    elements    = state.get("elements", []) or []
    next_by_key = {_elem_key(e): e for e in next_state.get("elements", []) or []}

    mouse_actions    = (trace.get("mouse") or {}).get("actions", []) or []
    keyboard_groups  = (trace.get("keyboard") or {}).get("actions", []) or []

    # ── scroll ──────────────────────────────────────────────────────────────
    scrolls = [a for a in mouse_actions if a.get("type") == "scroll"]
    if scrolls:
        pos = scrolls[0].get("position", [0, 0])
        dy  = float(scrolls[0].get("dy", 0))
        return SemanticAction(
            verb=Verb.SCROLL_TO, position=tuple(pos),
            direction="down" if dy > 0 else "up", clicks=max(1, int(abs(dy))),
        )

    # ── click / double_click / drag → verb decided by control-type + diff ──
    has_click = any(a.get("type") in ("click", "double_click", "drag") for a in mouse_actions)
    if has_click:
        idx = _find_click_target_idx(mouse_actions, elements)
        clicks = [a for a in mouse_actions if a.get("type") in ("click", "double_click", "drag")]
        pos = clicks[0].get("position", clicks[0].get("src", [0, 0]))
        if idx < 0:
            return SemanticAction(verb=Verb.INVOKE, position=tuple(pos), target_idx=None,
                                  reason="click on non-interactive/unresolved target")
        elem  = elements[idx]
        ctype = (elem.get("type") or "").lower()
        nval  = _next_value(elem, next_by_key)

        if ctype in _CHECKBOX_TYPES:
            return SemanticAction(verb=Verb.TOGGLE, target_idx=idx, position=tuple(pos))
        if ctype in _COMBOBOX_TYPES:
            if nval is not None and nval != elem.get("value", ""):
                return SemanticAction(verb=Verb.SELECT_OPTION, target_idx=idx,
                                      position=tuple(pos), value=nval)
            return SemanticAction(verb=Verb.FOCUS, target_idx=idx, position=tuple(pos),
                                  reason="combobox opened, no value settled this step")
        if ctype in _TAB_TYPES or ctype in _BUTTON_TYPES:
            return SemanticAction(verb=Verb.INVOKE, target_idx=idx, position=tuple(pos))
        if ctype in _TEXT_ENTRY_TYPES:
            return SemanticAction(verb=Verb.FOCUS, target_idx=idx, position=tuple(pos))
        # any other real click target: value changed this step -> SET_VALUE label
        # is wrong (SET_VALUE is a keyboard verb), so default to FOCUS.
        return SemanticAction(verb=Verb.FOCUS, target_idx=idx, position=tuple(pos))

    # ── keyboard ────────────────────────────────────────────────────────────
    if keyboard_groups:
        parts: List[str] = []
        raw_keys: List[str] = []
        hotkey_name = None
        for g in keyboard_groups:
            hk = g.get("hotkey", "")
            if hk:
                hotkey_name = hk.lower()
            for s in g.get("strokes", []):
                pasted = s.get("pasted_text", "")
                if pasted:
                    parts.append(pasted)
                    continue
                k = s.get("key", "")
                raw_keys.append(k)
                if len(k) == 1 and k.isprintable():
                    parts.append(k)
        typed_text = "".join(parts)

        if typed_text and not hotkey_name:
            idx = _find_focused_entry_idx(state, elements)
            return SemanticAction(verb=Verb.SET_VALUE, target_idx=(idx if idx >= 0 else None),
                                  value=typed_text)
        if hotkey_name:
            return SemanticAction(verb=Verb.HOTKEY, keystrokes=hotkey_name.split("+"))
        if raw_keys:
            # non-printable strokes with no declared hotkey (e.g. lone Tab/Enter/
            # Backspace groups the recorder didn't tag) -> still a HOTKEY, just a
            # single-key one.
            return SemanticAction(verb=Verb.HOTKEY, keystrokes=raw_keys)

    return SemanticAction(verb=Verb.WAIT)


def translate_session(session_dir: str, glob: str = "live_step_*.json"):
    """Yield (Path, SemanticAction) for every trace file in a session dir, in order."""
    import json
    from pathlib import Path

    root = Path(session_dir)
    for fpath in sorted(root.glob(glob)):
        try:
            with fpath.open(encoding="utf-8") as f:
                trace = json.load(f)
        except Exception as exc:
            print(f"[Translator] Skipping {fpath.name}: {exc}")
            continue
        yield fpath, translate_step(trace)


if __name__ == "__main__":
    import argparse
    import sys
    from collections import Counter

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Translate a demo session to SemanticActions (offline check).")
    ap.add_argument("session_dir")
    ap.add_argument("--show", type=int, default=20, help="print this many labeled steps")
    args = ap.parse_args()

    counts = Counter()
    shown = 0
    for fpath, action in translate_session(args.session_dir):
        counts[action.verb.value] += 1
        if shown < args.show:
            extra = f" value={action.value!r}" if action.value else ""
            print(f"{fpath.name:28s} {action.verb.value:14s} target_idx={action.target_idx}{extra}")
            shown += 1
    print("\n--- verb histogram ---")
    for verb, n in counts.most_common():
        print(f"{verb:14s} {n}")
