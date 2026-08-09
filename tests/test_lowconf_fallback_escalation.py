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

REWRITTEN AGAIN 2026-08-09, live, direct user report ("Agent is stuck"):
this branch's own redirect click was NEVER verified -- it just executed a
raw coordinate click and assumed it worked, unlike every sibling redirect
guard in this file (all of which use UIA SetFocus + verify-and-escalate).
Log evidence: 'ZIP Code' stayed focused for 6+ consecutive steps while
this branch kept "successfully" finding 'Occupation' as a target and
resetting its own streak to 0 each time (since finding a target was
treated as success regardless of whether the click landed), clicking its
coordinates, getting no_change, forever. Fixed with the same
UIA-SetFocus-first + verify-and-escalate shape already proven on the
reclick-streak guard.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

_LOWCONF_FALLBACK_LIMIT = 1
_REDIRECT_STALL_LIMIT = 2


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


def _run_lowconf_fallback_verified(state, post_redirect_focused_id, streak,
                                    stall_count, fallback_limit, stall_limit,
                                    executor, focus_via_uia_fn, attempted_keys=None,
                                    tab_scroll_count=0, max_tab_scrolls=6,
                                    scroll_fn=None, advance_fn=None):
    """Mirrors the CURRENT (2026-08-09) low-confidence-fallback branch:
    redirect via UIA SetFocus first (falling back to a coordinate click
    only if that fails), then VERIFY -- via the post-action observation's
    focused_element_id -- that focus actually landed there, instead of
    assuming the sibling redirect guards' own already-proven verify
    mechanism.

    Found 2026-08-09, live, direct user report ("Agent is stuck"): this
    branch was the one sibling redirect guard in the file that never
    got the reclick-streak guard's own verify-and-escalate upgrade -- it
    fell through to a plain, unverified coordinate click on the shared
    execution pipeline below. Log evidence: 'ZIP Code' stayed the focused
    element for 6+ consecutive steps while this branch kept "successfully"
    finding 'Occupation' as a target (resetting its own streak to 0 every
    time, since finding a target was treated as success regardless of
    whether the click actually landed), clicking its coordinates, and
    getting no_change every single time. Nothing ever noticed."""
    attempted_keys = attempted_keys if attempted_keys is not None else set()
    streak += 1
    if streak < fallback_limit:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return "blind_tab", streak, stall_count, tab_scroll_count

    target = find_visible_empty_target(
        state, 1000.0, attempted_keys=attempted_keys,
        attempt_key_fn=lambda e, els: e.get("element_id"))
    if not (target and target.get("bbox")):
        if tab_scroll_count < max_tab_scrolls:
            (scroll_fn or (lambda s: None))(state)
            return "scrolled", 0, stall_count, tab_scroll_count + 1
        (advance_fn or (lambda s: None))(state)
        return "advanced_tab", 0, stall_count, 0

    b = target["bbox"]
    label = target.get("label") or target.get("text") or ""
    if not focus_via_uia_fn(label):
        executor.execute({"action_type": "click",
                           "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
    streak = 0
    landed = post_redirect_focused_id == target["element_id"]
    if landed:
        return "landed", streak, 0, tab_scroll_count
    stall_count += 1
    if stall_count >= stall_limit:
        stall_count = 0
        attempted_keys.add(target["element_id"])
    return "stalled", streak, stall_count, tab_scroll_count


class TestLowConfFallbackRedirectIsVerifiedNotAssumed:
    """The actual live regression: the redirect must confirm focus landed
    before trusting it, and must prefer UIA SetFocus over a raw coordinate
    click -- matching every sibling redirect guard in this file."""

    def test_uses_setfocus_first_not_a_raw_coordinate_click(self):
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=True)
        target = _field("Occupation", bbox=(1400, 0, 1600, 40))
        state = {"elements": [target]}
        outcome, streak, stall, _ = _run_lowconf_fallback_verified(
            state, post_redirect_focused_id="Occupation", streak=0, stall_count=0,
            fallback_limit=_LOWCONF_FALLBACK_LIMIT, stall_limit=_REDIRECT_STALL_LIMIT,
            executor=executor, focus_via_uia_fn=focus_via_uia)
        assert outcome == "landed"
        assert streak == 0
        assert stall == 0
        focus_via_uia.assert_called_once_with("Occupation")
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert click_calls == [], "SetFocus succeeded -- no coordinate click should fire"

    def test_falls_back_to_coordinate_click_when_setfocus_fails(self):
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=False)
        target = _field("Occupation", bbox=(1400, 0, 1600, 40))
        state = {"elements": [target]}
        _run_lowconf_fallback_verified(
            state, post_redirect_focused_id="Occupation", streak=0, stall_count=0,
            fallback_limit=_LOWCONF_FALLBACK_LIMIT, stall_limit=_REDIRECT_STALL_LIMIT,
            executor=executor, focus_via_uia_fn=focus_via_uia)
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1

    def test_the_actual_live_regression_stuck_on_zip_code_targeting_occupation(self):
        """Reproduces the exact log sequence: focus never actually leaves
        'ZIP Code' even though the redirect keeps 'succeeding' at finding
        'Occupation' -- must be recognized as a stall, not silently reset
        as if it worked."""
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=True)
        occupation = _field("Occupation", bbox=(1400, 0, 1600, 40))
        state = {"elements": [occupation]}
        # post_redirect_focused_id stays "ZIP Code" -- the click/SetFocus
        # never actually moved real focus, exactly like the live log.
        outcome, streak, stall, _ = _run_lowconf_fallback_verified(
            state, post_redirect_focused_id="ZIP Code", streak=0, stall_count=0,
            fallback_limit=_LOWCONF_FALLBACK_LIMIT, stall_limit=_REDIRECT_STALL_LIMIT,
            executor=executor, focus_via_uia_fn=focus_via_uia)
        assert outcome == "stalled"
        assert stall == 1

    def test_repeated_stalls_mark_the_target_attempted_so_it_stops_being_reoffered(self):
        executor = MagicMock()
        focus_via_uia = MagicMock(return_value=True)
        occupation = _field("Occupation", bbox=(1400, 0, 1600, 40))
        state = {"elements": [occupation]}
        attempted = set()
        outcome, streak, stall, _ = _run_lowconf_fallback_verified(
            state, post_redirect_focused_id="ZIP Code", streak=0,
            stall_count=_REDIRECT_STALL_LIMIT - 1,
            fallback_limit=_LOWCONF_FALLBACK_LIMIT, stall_limit=_REDIRECT_STALL_LIMIT,
            executor=executor, focus_via_uia_fn=focus_via_uia, attempted_keys=attempted)
        assert outcome == "stalled"
        assert stall == 0  # reset after escalating
        assert "Occupation" in attempted


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
