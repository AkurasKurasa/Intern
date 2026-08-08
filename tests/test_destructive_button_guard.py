"""
Regression test for agent.py's OPT2 navigate branch -- the transformer's own
low-confidence pointer must never be allowed to click a destructive button
(Clear All, Reset, Delete, ...) as an incidental side effect of navigation.

Found 2026-08-08, live: a completely unremarkable navigation click
(ptr_conf=0.53, no different from dozens of other clicks that step) happened
to land on a real 'Clear All' button. Confirmed via the popped window's own
class name ('#32770' -- a genuine Windows dialog box, not a misidentified
sibling window) that this was a real confirmation dialog, which then got
dismissed with Escape on every subsequent step for the rest of the run,
blocking all further progress.

There was already a similar guard for accidentally clicking Submit early
(_SUBMIT_KW / _find_submit_button), but it (a) only covered finishing early,
not destroying data, and (b) was gated behind `not self._no_autohandlers` --
disabled entirely in the exact mode this run used. _DESTRUCTIVE_KW /
_find_destructive_button_at apply unconditionally, in every mode.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _find_destructive_button_at, _DESTRUCTIVE_KW


def _button(text, bbox):
    return {"type": "buttoncontrol", "text": text, "label": text, "bbox": list(bbox)}


class TestFindDestructiveButtonAt:
    def test_finds_a_clear_all_button_at_the_click_position(self):
        btn = _button("Clear All", (1400, 830, 1520, 870))
        elements = [btn]
        found = _find_destructive_button_at(elements, [1459, 848])
        assert found is btn

    def test_finds_reset_and_delete_variants_too(self):
        for label in ("Reset Form", "Delete Record", "Erase", "Discard Changes", "Cancel All"):
            btn = _button(label, (100, 100, 300, 130))
            found = _find_destructive_button_at([btn], [200, 115])
            assert found is btn, f"expected to catch {label!r}"

    def test_returns_none_when_click_position_misses_the_button(self):
        btn = _button("Clear All", (1400, 830, 1520, 870))
        found = _find_destructive_button_at([btn], [200, 115])
        assert found is None

    def test_returns_none_for_a_normal_fillable_field_at_the_position(self):
        field = {"type": "editcontrol", "text": "First Name", "label": "First Name",
                  "bbox": [100, 100, 300, 130]}
        found = _find_destructive_button_at([field], [200, 115])
        assert found is None

    def test_does_not_false_positive_on_submit_or_save(self):
        """Submit-like buttons have their own separate, deliberate guard
        (_SUBMIT_KW) -- this set must not overlap with it."""
        for label in ("Submit", "Save", "OK", "Done"):
            assert not any(kw in label.lower() for kw in _DESTRUCTIVE_KW), (
                f"{label!r} should not match _DESTRUCTIVE_KW"
            )


def _run_navigate_click_guard(elements, click_pos, executor):
    """Mirrors the CURRENT guard added to agent.py's OPT2 navigate branch:
    a destructive button at the pointer's target position gets Tab'd instead
    of clicked."""
    btn = _find_destructive_button_at(elements, click_pos)
    if btn is not None:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return False
    executor.execute({"action_type": "click", "click_position": click_pos})
    return True


class TestNavigateBranchRefusesDestructiveClicks:
    def test_tabs_instead_of_clicking_a_destructive_button(self):
        from unittest.mock import MagicMock
        executor = MagicMock()
        btn = _button("Clear All", (1400, 830, 1520, 870))
        clicked = _run_navigate_click_guard([btn], [1459, 848], executor)
        assert clicked is False
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]

    def test_still_clicks_normal_navigation_targets(self):
        from unittest.mock import MagicMock
        executor = MagicMock()
        field = {"type": "editcontrol", "text": "Last Name", "label": "Last Name",
                  "bbox": [100, 100, 300, 130]}
        clicked = _run_navigate_click_guard([field], [200, 115], executor)
        assert clicked is True
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [200, 115]}]
