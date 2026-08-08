"""
Regression test for agent.py's OPT2 fill-decision gate and NAVIGATE-branch
reclick guard -- both must trust a genuine "typed real text" record over a
live "is it empty" value read, but WITHOUT reintroducing the click-only-
marks-attempted regression that execution_payment_tab_oscillation_fix's
round-3 writeup already found and fixed once.

Found 2026-08-08, live: 'Years Continuously Insured' -- typed and CONFIRMED
filled once already, several steps earlier in the same run -- got navigated
back to, read as empty by a later live snapshot, and re-entered the fill
branch fully (a real LLM lookup, a real paste, a real 2-retry verify-at-fill
cycle) 4 times in a row. self._record_attempt() marks a field "attempted"
on BOTH keyboard actions AND plain navigation clicks (click -> element under
the cursor, keyboard -> focused element) -- so self._attempted_keys alone
can't distinguish "a real value was typed here" from "the pointer merely
passed through here once." The first attempt at this fix checked
self._attempted_keys directly for editcontrol/comboboxcontrol, which would
have silently reintroduced the exact regression from execution_payment_tab_
oscillation_fix's round-3 writeup: a stray navigation click (e.g. the
low-confidence-fallback escalation's direct click) onto a genuinely-empty
field would permanently exclude it from ever being filled.

Fix: a new self._typed_keys set, populated only when a keyboard action
carried real (non-empty, stripped) text -- never on a bare click, never on
a housekeeping "tab"/"ctrl+a" keystroke. The fill-gate and reclick-guard
checks were changed to use self._typed_keys instead of self._attempted_keys
for editcontrol/comboboxcontrol (checkboxes are unaffected -- for them,
click IS the fill action, so the general attempted set was already correct).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _attempt_key(elem, elements=None):
    """Mirrors Agent._attempt_key (label-primary, no disambiguation needed
    for these single-field tests)."""
    lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
    if not lbl:
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)
    return lbl


def _record_attempt(attempted_keys, typed_keys, state, prediction):
    """Mirrors the CURRENT Agent._record_attempt (2026-08-08 fix): marks
    attempted on any keyboard/click target, but only adds to typed_keys when
    the action was a keyboard action carrying real text."""
    at = prediction.get("action_type")
    elements = state.get("elements", [])
    elem = None
    if at == "keyboard":
        fid = state.get("focused_element_id")
        elem = next((e for e in elements if e.get("element_id") == fid), None)
    elif at == "click":
        px, py = (prediction.get("click_position") or [None, None])
        elem = next((e for e in elements
                     if e.get("bbox") and e["bbox"][0] <= px <= e["bbox"][2]
                     and e["bbox"][1] <= py <= e["bbox"][3]), None) if px is not None else None
    if elem is not None:
        attempted_keys.add(_attempt_key(elem, elements=elements))
        if at == "keyboard" and (prediction.get("text") or "").strip():
            typed_keys.add(_attempt_key(elem, elements=elements))


def _fill_gate_blocks_refill(typed_keys, fe2):
    """Mirrors the fill-decision gate's _fe2_already_attempted check."""
    key = _attempt_key(fe2)
    return key in typed_keys


class TestTypedKeysOnlyGainEntriesFromRealTyping:
    def test_keyboard_action_with_text_is_recorded_as_typed(self):
        attempted, typed = set(), set()
        field = {"element_id": "e1", "type": "editcontrol",
                  "label": "Years Continuously Insured", "value": ""}
        state = {"elements": [field], "focused_element_id": "e1"}
        _record_attempt(attempted, typed, state,
                         {"action_type": "keyboard", "text": "9"})
        assert _attempt_key(field) in attempted
        assert _attempt_key(field) in typed

    def test_bare_navigation_click_is_attempted_but_not_typed(self):
        """The exact scenario execution_payment_tab_oscillation_fix's round-3
        regression covered: a click-to-focus with no value ever entered must
        NOT count as 'filled'."""
        attempted, typed = set(), set()
        field = {"element_id": "e1", "type": "editcontrol",
                  "label": "Expiration Date", "value": "",
                  "bbox": [100, 100, 300, 130]}
        state = {"elements": [field]}
        _record_attempt(attempted, typed, state,
                         {"action_type": "click", "click_position": [200, 115]})
        assert _attempt_key(field) in attempted
        assert _attempt_key(field) not in typed

    def test_housekeeping_tab_keystroke_is_not_typed(self):
        attempted, typed = set(), set()
        field = {"element_id": "e1", "type": "editcontrol",
                  "label": "Suffix", "value": ""}
        state = {"elements": [field], "focused_element_id": "e1"}
        _record_attempt(attempted, typed, state,
                         {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        assert _attempt_key(field) in attempted
        assert _attempt_key(field) not in typed


class TestFillGateTrustsTypedKeysOverAStaleEmptyRead:
    def test_field_typed_earlier_is_not_refilled_even_if_live_read_is_empty(self):
        """The actual live bug: 'Years Continuously Insured' typed+confirmed
        earlier, later re-focused and read back as empty by a stale snapshot."""
        attempted, typed = set(), set()
        field = {"element_id": "e1", "type": "editcontrol",
                  "label": "Years Continuously Insured", "value": ""}
        state = {"elements": [field], "focused_element_id": "e1"}
        _record_attempt(attempted, typed, state,
                         {"action_type": "keyboard", "text": "9"})

        # Field is re-focused later; live value snapshot reads empty (the race).
        stale_reread = {"element_id": "e1", "type": "editcontrol",
                         "label": "Years Continuously Insured", "value": ""}
        assert _fill_gate_blocks_refill(typed, stale_reread) is True

    def test_genuinely_empty_field_merely_clicked_past_still_gets_filled(self):
        """The regression this fix must NOT reintroduce: a field only ever
        clicked (never typed into) must still be eligible for the fill branch."""
        attempted, typed = set(), set()
        field = {"element_id": "e1", "type": "editcontrol",
                  "label": "Expiration Date", "value": "",
                  "bbox": [100, 100, 300, 130]}
        state = {"elements": [field]}
        _record_attempt(attempted, typed, state,
                         {"action_type": "click", "click_position": [200, 115]})

        assert _fill_gate_blocks_refill(typed, field) is False
