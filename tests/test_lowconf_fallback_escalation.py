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

First fix (2026-08-07): track consecutive low-confidence/invalid-pointer
fallbacks, tolerate 2 blind Tabs, escalate to a direct click on the 3rd.

REWRITTEN 2026-08-08 on direct, repeated, escalating user instruction
("Do not use Tab to fucking navigate you piece of shit") after a live run
still showed Tab being used as a navigation guess -- moderate-confidence
repeats onto an already-resolved dead field (e.g. 'Suffix', hit 3+ times in
one run) don't even trip the low-confidence gate, so tolerating ANY blind
Tab before redirecting was still producing visible, wasteful Tab-hopping.
_LOWCONF_FALLBACK_LIMIT dropped from 3 to 1: the very FIRST low-confidence
or invalid pointer now goes straight to Navigation Protocol's known target
via click -- no Tab-and-hope tolerated at all. Tab is still used elsewhere
for its own legitimate job (committing a just-selected combobox value,
moving off a just-filled field) -- only removed here as a navigation guess.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

_LOWCONF_FALLBACK_LIMIT = 1


def _field(label, value="", bbox=(1400, 500, 1600, 530)):
    return {"element_id": label, "type": "editcontrol", "label": label,
            "value": value, "bbox": list(bbox), "window_role": "active"}


def _run_updated_fallback_branch(executor, streak, state, viewport_bottom,
                                  attempted_keys=None, tab_scroll_count=0, max_tab_scrolls=6,
                                  scroll_fn=None, advance_fn=None):
    """Mirrors the CURRENT escalation logic in agent.py's run() (the `else`
    arm reached when the transformer's pointer is gated to None or invalid),
    now with _LOWCONF_FALLBACK_LIMIT=1 -- every occurrence escalates
    immediately, never tolerating a blind Tab first."""
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


class TestEveryLowConfidenceGuessEscalatesImmediately:
    """The core behavioral change: with _LOWCONF_FALLBACK_LIMIT=1, there is
    no "tolerate a blind Tab first" state left -- the very first
    low-confidence/invalid pointer redirects to a known target."""

    def test_first_low_confidence_guess_with_a_target_visible_clicks_it_directly(self):
        executor = MagicMock()
        target = _field("Marital Status")
        state = {"elements": [target]}
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=0, state=state, viewport_bottom=1000.0)
        assert outcome == "direct_click"
        assert streak == 0
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1
        assert click_calls[0].args[0]["click_position"] == [1500.0, 515.0]
        tab_calls = [c for c in executor.execute.call_args_list
                     if c.args[0].get("action_type") == "keyboard"]
        assert tab_calls == [], "no Tab should ever be issued as a navigation guess"

    def test_already_attempted_targets_are_not_re_clicked(self):
        executor = MagicMock()
        attempted = _field("Suffix")
        state = {"elements": [attempted]}
        outcome, streak, _ = _run_updated_fallback_branch(
            executor, streak=0, state=state, viewport_bottom=1000.0,
            attempted_keys={"suffix"})
        # No real target visible once Suffix is excluded -> falls to scroll/advance,
        # never a blind Tab.
        assert outcome in ("scrolled", "advanced_tab")


class TestEscalationScrollsOrAdvancesWhenNothingIsVisible:
    def test_nothing_visible_and_scrolls_remaining_scrolls(self):
        executor = MagicMock()
        scroll_fn = MagicMock()
        outcome, streak, tab_scroll_count = _run_updated_fallback_branch(
            executor, streak=0, state={"elements": []},
            viewport_bottom=1000.0, tab_scroll_count=0, max_tab_scrolls=6, scroll_fn=scroll_fn)
        assert outcome == "scrolled"
        assert streak == 0
        assert tab_scroll_count == 1
        scroll_fn.assert_called_once()

    def test_nothing_visible_and_scrolls_exhausted_advances_tab_instead(self):
        executor = MagicMock()
        advance_fn = MagicMock()
        outcome, streak, tab_scroll_count = _run_updated_fallback_branch(
            executor, streak=0, state={"elements": []},
            viewport_bottom=1000.0, tab_scroll_count=6, max_tab_scrolls=6, advance_fn=advance_fn)
        assert outcome == "advanced_tab"
        assert streak == 0
        assert tab_scroll_count == 0
        advance_fn.assert_called_once()

    def test_no_click_executed_when_falling_back_to_scroll(self):
        executor = MagicMock()
        _run_updated_fallback_branch(
            executor, streak=0, state={"elements": []},
            viewport_bottom=1000.0)
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_no_tab_executed_anywhere_in_the_escalation_ladder(self):
        """The user's exact instruction, verified across every branch of the
        ladder: neither the direct-click path, the scroll path, nor the
        advance-tab path ever issues a Tab keystroke."""
        executor = MagicMock()
        scroll_fn = MagicMock()
        advance_fn = MagicMock()
        _run_updated_fallback_branch(
            executor, streak=0, state={"elements": []}, viewport_bottom=1000.0,
            tab_scroll_count=0, max_tab_scrolls=0, scroll_fn=scroll_fn, advance_fn=advance_fn)
        tab_calls = [c for c in executor.execute.call_args_list
                     if c.args[0].get("action_type") == "keyboard"
                     and c.args[0].get("keystrokes") == ["tab"]]
        assert tab_calls == []
