"""
Regression test for agent.py's run() -- clicking the focused field's own
bbox right before typing into it, not just on verify-at-fill's retries.

Found 2026-08-08, live: even with verify-at-fill's settle-poll fix (which
waits for the UI's VALUE to catch up after typing), one field ("Years
Continuously Insured", a plain editcontrol -- no different from dozens of
other fields that worked fine) kept failing verify for multiple real
seconds -- too long to be a value-settle race. But verify-at-fill's own
retry path, which re-clicks the field before retyping, reliably recovered
it every single time. That's evidence real OS keyboard focus hadn't
actually landed on the field yet even though UIA already reported it as
focused -- a focus-TRANSITION race, distinct from the value-settle race
fixed earlier the same night. Since re-clicking is already proven to fix
it (via the retry path), doing the same click before the FIRST attempt
should prevent needing the retry at all.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _run_pre_type_click(executor, focused_el, pred_text="9", pred_keystrokes=None):
    """Mirrors the click-before-type block added to agent.py's run() right
    before the pre-type-clear ctrl+a check."""
    pred_keystrokes = pred_keystrokes or []
    _nav_keys = {"tab", "return", "enter", "escape", "Key.tab",
                 "Key.return", "Key.enter", "Key.escape"}
    is_nav_only = not pred_text and all(k in _nav_keys for k in pred_keystrokes)
    if not is_nav_only and focused_el:
        if focused_el.get("bbox"):
            b = focused_el["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
        foc_val = (focused_el.get("value") or "").strip()
        if foc_val:
            executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["ctrl+a"]})


class TestClickBeforeTypeFocusRace:
    def test_clicks_the_focused_fields_own_bbox_before_typing(self):
        executor = MagicMock()
        focused_el = {"element_id": "e1", "type": "editcontrol",
                      "label": "Years Continuously Insured", "value": "",
                      "bbox": [1400, 560, 1600, 590]}
        _run_pre_type_click(executor, focused_el, pred_text="9")
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1
        assert click_calls[0].args[0]["click_position"] == [1500.0, 575.0]

    def test_no_click_when_focused_element_has_no_bbox(self):
        executor = MagicMock()
        focused_el = {"element_id": "e1", "type": "editcontrol", "value": ""}
        _run_pre_type_click(executor, focused_el, pred_text="9")
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_no_click_for_nav_only_actions(self):
        """Tab/Enter/Escape-only predictions aren't typing into a field --
        no reason to click anything first."""
        executor = MagicMock()
        focused_el = {"element_id": "e1", "type": "editcontrol", "value": "",
                      "bbox": [1400, 560, 1600, 590]}
        _run_pre_type_click(executor, focused_el, pred_text="", pred_keystrokes=["tab"])
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_still_clears_existing_value_after_the_click(self):
        """The pre-existing 'pre-type clear' ctrl+a behavior must survive --
        this fix adds a click, it doesn't replace the clear-before-overwrite."""
        executor = MagicMock()
        focused_el = {"element_id": "e1", "type": "editcontrol", "value": "old value",
                      "bbox": [1400, 560, 1600, 590]}
        _run_pre_type_click(executor, focused_el, pred_text="new value")
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls[0]["action_type"] == "click"
        assert calls[1] == {"action_type": "keyboard", "key_count": 1, "keystrokes": ["ctrl+a"]}

    def test_no_click_when_nothing_is_focused(self):
        executor = MagicMock()
        _run_pre_type_click(executor, focused_el=None, pred_text="9")
        executor.execute.assert_not_called()
