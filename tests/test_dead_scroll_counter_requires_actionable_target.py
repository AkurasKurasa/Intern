"""
Regression test for agent.py's scroll-handling block in run() (around the
NavAction.SCROLL branch) -- the dead-scroll counter (_tab_scroll_count,
passed to navigation_protocol.decide() as dead_scroll_count) must only
reset when a scroll reveals something actually ACTIONABLE, not merely when
the view visibly moved.

Found 2026-08-08, live, direct user report: Navigation Protocol logged
"scroll moved the view -- new fields revealed" for 11 CONSECUTIVE steps in
a row, none of them stopping to fill anything. Root cause: the reset
condition only checked visible_field_signature(after) != visible_field_
signature(before) -- but that function's own docstring says its job is
proving the view PHYSICALLY moved ("not that nothing is left to fill").
Scrolling shifts almost every element's y-position, so the signature
differs on nearly every scroll regardless of whether anything fillable was
revealed. Resetting the counter on that alone meant it could never reach
max_dead_scrolls and hand off to ADVANCE_TAB -- the agent just kept
scrolling past non-actionable content (headers, already-filled fields
scrolling into view) indefinitely.

Fix: only reset the counter when has_visible_empty_target() confirms
there's something to act on in the new view. A scroll that reveals new but
non-actionable content still counts toward "still nothing to do here."
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import (
    has_visible_empty_target, visible_field_signature, find_visible_empty_target,
)

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _run_scroll_branch(sig_before_elements, sig_after_elements, tab_scroll_count):
    """Mirrors the CURRENT NavAction.SCROLL branch in agent.py's run()
    (2026-08-08 fix): only reset the counter when the new view actually has
    an actionable target, not just because the signature changed."""
    state_before = {"elements": sig_before_elements}
    state_after  = {"elements": sig_after_elements}
    sig_before = visible_field_signature(state_before, VIEWPORT_BOTTOM)
    sig_after  = visible_field_signature(state_after, VIEWPORT_BOTTOM)
    view_moved = sig_after != sig_before
    if view_moved:
        if has_visible_empty_target(state_after, VIEWPORT_BOTTOM):
            return 0
        return tab_scroll_count + 1
    return tab_scroll_count + 1


class TestCounterOnlyResetsForActionableContent:
    def test_scroll_shifting_an_already_filled_field_still_increments_the_counter(self):
        # The real live mechanism: an ALREADY-FILLED editcontrol shifts
        # y-position as the page scrolls -- same SIG_TYPES-eligible type in
        # both before/after, so the signature genuinely differs (proving the
        # view moved), but nothing new became actionable (it was never empty).
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100))]
        result = _run_scroll_branch(before, after, tab_scroll_count=0)
        assert result == 1, "a non-actionable reveal must NOT reset the counter to 0"

    def test_scroll_revealing_a_genuine_empty_field_resets_the_counter(self):
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100)),
                  _field("VIN", value="", bbox=(100, 300, 300, 330))]
        result = _run_scroll_branch(before, after, tab_scroll_count=1)
        assert result == 0, "a genuinely actionable reveal should reset the counter"

    def test_repeated_non_actionable_scrolls_accumulate_toward_the_dead_scroll_cap(self):
        """The actual live bug: 11 straight non-actionable 'reveals' should
        accumulate a rising counter, not stay pinned at 0 forever."""
        counter = 0
        before = [_field("A", value="already filled", bbox=(100, 100, 300, 130))]
        for i in range(5):
            # Same already-filled field, shifted up each "scroll" -- signature
            # changes (proves the view moved) but nothing actionable appears.
            after = [_field("A", value="already filled", bbox=(100, 100 - (i + 1) * 15, 300, 130 - (i + 1) * 15))]
            counter = _run_scroll_branch(before, after, counter)
            before = after
        assert counter == 5, "non-actionable scrolls must accumulate, not reset each time"

    def test_view_not_moving_at_all_also_increments(self):
        same = [_field("A", value="x", bbox=(100, 100, 300, 130))]
        result = _run_scroll_branch(same, same, tab_scroll_count=2)
        assert result == 3


def _run_scroll_branch_and_click(sig_before_elements, sig_after_elements, executor):
    """Mirrors agent.py's run() TWICE-follow-up fix: don't just detect an
    actionable target after a scroll and hope a LATER, separately-observed
    decide() call agrees -- act on the confirmed state immediately.

    First attempt routed this through self._focus_first_empty_field(), which
    still looped live: its own diagnostic showed _uia_focus_first_field's
    INDEPENDENT raw UIA tree walk found zero candidates in the correct pane,
    even though find_visible_empty_target (reading the already-settled state
    snapshot) had just found one right there -- two different systems
    disagreeing. Fixed by clicking find_visible_empty_target's OWN returned
    element directly instead: same data source for the check and the action,
    nothing left to disagree with."""
    state_before = {"elements": sig_before_elements}
    state_after  = {"elements": sig_after_elements}
    sig_before = visible_field_signature(state_before, VIEWPORT_BOTTOM)
    sig_after  = visible_field_signature(state_after, VIEWPORT_BOTTOM)
    if sig_after != sig_before:
        target = find_visible_empty_target(state_after, VIEWPORT_BOTTOM)
        if target is not None and target.get("bbox"):
            b = target["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
            return True
    return False


class TestScrollBranchActsImmediatelyOnConfirmedTarget:
    """Found live 2026-08-08, SAME night as the counter fix above, even with
    that fix already in place: a run logged 7 straight "actionable field
    revealed" scrolls in a row, nothing ever getting filled between them.
    Cause: the confirming check ran on one observation, then the loop
    `continue`d back to a totally FRESH self._observe() before deciding
    anything again -- two separate snapshots, moments apart, of a UI that
    isn't perfectly frame-stable, could disagree. Fix: act on the just-
    confirmed state directly instead of gambling a later recheck agrees.

    RE-VERIFIED LIVE, still looping: see _run_scroll_branch_and_click's own
    docstring for the second-round root cause and fix."""

    def test_clicks_the_confirmed_targets_own_bbox(self):
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100)),
                  _field("VIN", value="", bbox=(100, 300, 300, 330))]
        executor = MagicMock()
        acted = _run_scroll_branch_and_click(before, after, executor)
        assert acted is True
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1
        assert click_calls[0].args[0]["click_position"] == [200.0, 315.0]  # VIN's bbox center

    def test_no_click_when_nothing_actionable(self):
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100))]
        executor = MagicMock()
        acted = _run_scroll_branch_and_click(before, after, executor)
        assert acted is False
        executor.execute.assert_not_called()
