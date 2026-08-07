"""
Regression test for the low-confidence-pointer escalation in agent.py's
OPT2 navigate branch (components/agent/agent.py, ~L2238).

Found live 2026-08-07, directly requested by the user watching a run burn
steps: "Too much wasted steps. Whenever there are no longer any targets on
the screen (i.e., there is nothing left to fill) I need you to activate
Navigation Protocol so that there will be." The log showed bursts of 5-6
consecutive "pointer low-confidence... Tab fallback" steps in a row with
zero progress -- the transformer's own click-pointer wasn't confident
enough to act on anything, and the only existing response was to blindly
press Tab and hope, over and over.

Fix: track consecutive low-confidence/invalid-pointer fallbacks. After
_LOWCONF_FALLBACK_LIMIT in a row, stop guessing and ask Navigation
Protocol's find_visible_empty_target() directly:
  - a real target IS visible -> click it deterministically (bypass the
    unconfident pointer for one step).
  - nothing is visible either -> scroll (or advance the tab if scrolling
    is already exhausted) instead of another blind Tab.
A single low-confidence guess is left alone (self-corrects most of the
time); escalation only kicks in after repeated, proven-unproductive tries.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

_LOWCONF_FALLBACK_LIMIT = 3


def _field(label, value="", bbox=(1400, 500, 1600, 530)):
    return {"element_id": label, "type": "editcontrol", "label": label,
            "value": value, "bbox": list(bbox), "window_role": "active"}


def _run_updated_fallback_branch(executor, streak, state, viewport_bottom,
                                  attempted_keys=None, tab_scroll_count=0, max_tab_scrolls=6,
                                  scroll_fn=None, advance_fn=None):
    """Mirrors the CURRENT escalation logic in agent.py's run() (the `else`
    arm reached when the transformer's pointer is gated to None or invalid)."""
    streak += 1
    if streak < _LOWCONF_FALLBACK_LIMIT:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return "blind_tab", streak, tab_scroll_count

    target = find_visible_empty_target(
        state, viewport_bottom, attempted_keys=attempted_keys,
        attempt_key_fn=lambda e, els: (e.get("label") or "").lower())
    if target and target.get("bbox"):
        b = target["bbox"]
        executor.execute({"action_type": "click",
                           "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
        return "direct_click", 0, tab_scroll_count

    if tab_scroll_count < max_tab_scrolls:
        (scroll_fn or (lambda s: None))(state)
        return "scrolled", 0, tab_scroll_count + 1
    (advance_fn or (lambda s: None))(state)
    return "advanced_tab", 0, 0


class TestEscalationOnlyKicksInAfterRepeatedFailures:
    def test_single_low_confidence_guess_just_tabs(self):
        executor = MagicMock()
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=0, state={"elements": []}, viewport_bottom=1000.0)
        assert outcome == "blind_tab"
        assert streak == 1

    def test_below_limit_still_just_tabs(self):
        executor = MagicMock()
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 2, state={"elements": []},
            viewport_bottom=1000.0)
        assert outcome == "blind_tab"


class TestEscalationClicksTheKnownTargetDirectly:
    def test_at_the_limit_with_a_real_target_visible_clicks_it(self):
        executor = MagicMock()
        target = _field("Marital Status")
        state = {"elements": [target]}
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 1, state=state, viewport_bottom=1000.0)
        assert outcome == "direct_click"
        assert streak == 0
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1
        assert click_calls[0].args[0]["click_position"] == [1500.0, 515.0]

    def test_already_attempted_targets_are_not_re_clicked(self):
        executor = MagicMock()
        attempted = _field("Suffix")
        state = {"elements": [attempted]}
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 1, state=state, viewport_bottom=1000.0,
            attempted_keys={"suffix"})
        # No real target visible once Suffix is excluded -> falls to scroll/advance.
        assert outcome in ("scrolled", "advanced_tab")


class TestEscalationScrollsOrAdvancesWhenNothingIsVisible:
    def test_nothing_visible_and_scrolls_remaining_scrolls(self):
        executor = MagicMock()
        scroll_fn = MagicMock()
        outcome, streak, tab_scroll_count = _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 1, state={"elements": []},
            viewport_bottom=1000.0, tab_scroll_count=0, max_tab_scrolls=6, scroll_fn=scroll_fn)
        assert outcome == "scrolled"
        assert streak == 0
        assert tab_scroll_count == 1
        scroll_fn.assert_called_once()

    def test_nothing_visible_and_scrolls_exhausted_advances_tab_instead(self):
        executor = MagicMock()
        advance_fn = MagicMock()
        outcome, streak, tab_scroll_count = _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 1, state={"elements": []},
            viewport_bottom=1000.0, tab_scroll_count=6, max_tab_scrolls=6, advance_fn=advance_fn)
        assert outcome == "advanced_tab"
        assert streak == 0
        assert tab_scroll_count == 0
        advance_fn.assert_called_once()

    def test_no_click_executed_when_falling_back_to_scroll(self):
        executor = MagicMock()
        _run_updated_fallback_branch(
            executor, streak=_LOWCONF_FALLBACK_LIMIT - 1, state={"elements": []},
            viewport_bottom=1000.0)
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0
