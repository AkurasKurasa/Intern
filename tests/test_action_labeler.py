"""
tests/test_action_labeler.py
==============================
Regression tests for components/recorder/action_labeler.py — verb
classification from raw trace steps (mouse/keyboard + before/after state).

Hand-built fixture traces, one per verb branch, so a future edit to the
classification logic gets caught here instead of only being noticed after
a retrain. Also includes a real-corpus smoke test (skipped if the demo data
directory isn't present) as a coarse regression net.

Run from the repo root:
    python -m pytest tests/test_action_labeler.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from components.recorder.action_labeler import translate_step, translate_session
from components.agent.semantic_action import Verb


def _elem(eid, etype, label="", value="", bbox=(0, 0, 100, 20), role="active"):
    return {
        "element_id": eid, "type": etype, "label": label, "text": label,
        "value": value, "bbox": list(bbox), "window_role": role,
        "focused": False, "confidence": 1.0,
    }


def _trace(elements, next_elements=None, mouse_actions=None, keyboard_groups=None,
           focused_element_id=None):
    state = {"screen_resolution": [1920, 1200], "focused_element_id": focused_element_id,
              "elements": elements}
    next_state = {"elements": next_elements if next_elements is not None else elements}
    return {
        "state": state,
        "next_state": next_state,
        "mouse": {"actions": mouse_actions or []},
        "keyboard": {"actions": keyboard_groups or []},
    }


class TestClickClassification:

    def test_checkbox_click_is_toggle(self):
        elems = [_elem("e1", "checkboxcontrol", "Renewal", bbox=(10, 10, 50, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [20, 20]}])
        a = translate_step(trace)
        assert a.verb == Verb.TOGGLE
        assert a.target_idx == 0

    def test_combobox_click_with_value_change_is_select_option(self):
        before = [_elem("e1", "comboboxcontrol", "Status", value="", bbox=(10, 10, 100, 30))]
        after  = [_elem("e1", "comboboxcontrol", "Status", value="Active", bbox=(10, 10, 100, 30))]
        trace  = _trace(before, next_elements=after,
                        mouse_actions=[{"type": "click", "position": [50, 20]}])
        a = translate_step(trace)
        assert a.verb == Verb.SELECT_OPTION
        assert a.value == "Active"

    def test_combobox_click_with_no_value_change_is_focus(self):
        elems = [_elem("e1", "comboboxcontrol", "Status", value="Active", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [50, 20]}])
        a = translate_step(trace)
        assert a.verb == Verb.FOCUS

    def test_tab_click_is_invoke(self):
        elems = [_elem("e1", "tabitemcontrol", "Policy", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [50, 20]}])
        assert translate_step(trace).verb == Verb.INVOKE

    def test_button_click_is_invoke(self):
        elems = [_elem("e1", "buttoncontrol", "Submit", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [50, 20]}])
        assert translate_step(trace).verb == Verb.INVOKE

    def test_edit_click_is_focus(self):
        elems = [_elem("e1", "editcontrol", "Name", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [50, 20]}])
        assert translate_step(trace).verb == Verb.FOCUS

    def test_click_outside_any_element_is_unresolved_invoke(self):
        elems = [_elem("e1", "editcontrol", "Name", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, mouse_actions=[{"type": "click", "position": [900, 900]}])
        a = translate_step(trace)
        assert a.verb == Verb.INVOKE
        assert a.target_idx is None

    def test_double_click_and_drag_resolve_like_click(self):
        elems = [_elem("e1", "buttoncontrol", "Submit", bbox=(10, 10, 100, 30))]
        for mtype in ("double_click", "drag"):
            trace = _trace(elems, mouse_actions=[{"type": mtype, "position": [50, 20]}])
            assert translate_step(trace).verb == Verb.INVOKE

    def test_element_id_collision_across_windows_does_not_leak_value(self):
        """Foreground and background windows can reuse the same element_id —
        the diff must be scoped by (element_id, window_role), not id alone."""
        before = [
            _elem("e1", "comboboxcontrol", "Status", value="", bbox=(10, 10, 100, 30), role="active"),
            _elem("e1", "documentcontrol", "Notepad", value="old notepad text",
                  bbox=(500, 500, 600, 600), role="background"),
        ]
        after = [
            _elem("e1", "comboboxcontrol", "Status", value="", bbox=(10, 10, 100, 30), role="active"),
            _elem("e1", "documentcontrol", "Notepad", value="NEW notepad text — unrelated edit",
                  bbox=(500, 500, 600, 600), role="background"),
        ]
        trace = _trace(before, next_elements=after,
                       mouse_actions=[{"type": "click", "position": [50, 20]}])
        a = translate_step(trace)
        # The background element's value changed, not the clicked combobox's —
        # must NOT be reported as SELECT_OPTION with the Notepad text as value.
        assert a.verb == Verb.FOCUS
        assert a.value == ""


class TestKeyboardClassification:

    def test_typed_text_is_set_value(self):
        elems = [_elem("e1", "editcontrol", "Name", bbox=(10, 10, 100, 30))]
        trace = _trace(elems, focused_element_id="e1", keyboard_groups=[
            {"strokes": [{"pasted_text": "James", "key": ""}]}
        ])
        a = translate_step(trace)
        assert a.verb == Verb.SET_VALUE
        assert a.value == "James"
        assert a.target_idx == 0

    def test_declared_hotkey_is_hotkey_verb(self):
        trace = _trace([], keyboard_groups=[{"hotkey": "ctrl+a", "strokes": []}])
        a = translate_step(trace)
        assert a.verb == Verb.HOTKEY
        assert a.keystrokes == ["ctrl", "a"]

    def test_undeclared_nonprintable_stroke_is_hotkey(self):
        trace = _trace([], keyboard_groups=[
            {"strokes": [{"pasted_text": "", "key": "Key.tab"}]}
        ])
        a = translate_step(trace)
        assert a.verb == Verb.HOTKEY
        assert a.keystrokes == ["Key.tab"]


class TestScrollAndWait:

    def test_scroll_action_is_scroll_to(self):
        trace = _trace([], mouse_actions=[{"type": "scroll", "position": [5, 5], "dy": 3}])
        a = translate_step(trace)
        assert a.verb == Verb.SCROLL_TO
        assert a.direction == "down"

    def test_negative_dy_is_scroll_up(self):
        trace = _trace([], mouse_actions=[{"type": "scroll", "position": [5, 5], "dy": -2}])
        assert translate_step(trace).direction == "up"

    def test_no_input_is_wait(self):
        trace = _trace([])
        assert translate_step(trace).verb == Verb.WAIT


# ══════════════════════════════════════════════════════════════════════════════
#  Real-corpus smoke test (skipped if demo data isn't present)
# ══════════════════════════════════════════════════════════════════════════════

_DEMO_ROOT = ROOT / "data" / "demos" / "eight_Tabs"


@pytest.mark.skipif(not _DEMO_ROOT.exists(), reason="demo corpus not present")
def test_translates_real_session_without_error():
    sessions = sorted(_DEMO_ROOT.glob("session_*"))
    assert sessions, "expected at least one session directory"
    session = sessions[0]
    n = 0
    for _fpath, action in translate_session(str(session)):
        assert action.verb in Verb
        n += 1
    assert n > 0
