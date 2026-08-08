"""
Regression test for agent.py's "block re-clicking an already-checked
checkbox" guard -- when the transformer's own click prediction lands on a
checkbox that's already checked, the guard correctly blocks the click (so
it can't accidentally uncheck it), but its fallback must redirect to a
known visible target instead of a blind Tab.

Found 2026-08-09, live, direct user report ("Navigation error"): the
transformer's own click prediction landed on the SAME already-checked
checkbox ('Uninsured/Underinsured Motorist') 10 CONSECUTIVE steps in a row.
The guard correctly blocked every one of those clicks, but its fallback was
a blind Tab -- a fourth spot using Tab-as-navigation that got missed in an
earlier sweep this same session (this path is entirely separate from the
reclick-streak guard, which only covers edit/combobox fields, not this
specific "block re-clicking a checked checkbox" check). Blind Tab happened
to mostly work here since a plain Tab through a checklist section usually
lands on the next checkbox -- but it's the same gamble on OS focus order
this project has repeatedly found unreliable elsewhere.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _next_prediction_when_already_checked(state, focus_via_uia_fn):
    """Mirrors the CURRENT (2026-08-09) already-checked-checkbox guard in
    agent.py's run(): redirect to a known visible target (via UIA SetFocus,
    falling back to a click) instead of a blind Tab. Only falls back to Tab
    if nothing else is visible to redirect to."""
    target = find_visible_empty_target(state, VIEWPORT_BOTTOM)
    if target and target.get("bbox"):
        label = (target.get("label") or target.get("text") or "").strip()
        if label and focus_via_uia_fn(label):
            return {"action_type": "no_op"}
        b = target["bbox"]
        return {"action_type": "click", "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]}
    return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}


class TestAlreadyCheckedCheckboxRedirectsInsteadOfBlindTab:
    def test_redirects_via_setfocus_when_it_succeeds(self):
        """The actual live regression: a real target is visible -- must
        SetFocus onto it directly (no_op prediction, since SetFocus already
        did the work) instead of Tabbing and hoping."""
        state = {"elements": [
            _field("Multi-Car", value="", bbox=(100, 200, 300, 230), ftype="checkboxcontrol"),
        ]}
        focus_via_uia = MagicMock(return_value=True)
        prediction = _next_prediction_when_already_checked(state, focus_via_uia)
        assert prediction == {"action_type": "no_op"}
        focus_via_uia.assert_called_once_with("Multi-Car")

    def test_falls_back_to_click_when_setfocus_fails(self):
        """UIA SetFocus not available/failed -- fall back to a direct click
        on the known target's own bbox, still not a blind Tab."""
        state = {"elements": [
            _field("Multi-Car", value="", bbox=(100, 200, 300, 230), ftype="checkboxcontrol"),
        ]}
        focus_via_uia = MagicMock(return_value=False)
        prediction = _next_prediction_when_already_checked(state, focus_via_uia)
        assert prediction == {"action_type": "click", "click_position": [200.0, 215.0]}

    def test_falls_back_to_tab_only_when_nothing_else_is_visible(self):
        """No genuinely empty target exists -- the one remaining case where
        Tab is still safe, since there's nothing else to click or focus."""
        state = {"elements": []}
        focus_via_uia = MagicMock(return_value=True)
        prediction = _next_prediction_when_already_checked(state, focus_via_uia)
        assert prediction == {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}
        focus_via_uia.assert_not_called()
