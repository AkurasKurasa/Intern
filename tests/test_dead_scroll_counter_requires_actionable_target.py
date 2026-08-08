"""
Regression test for agent.py's scroll-handling block in run() (the
NavAction.SCROLL branch) -- the dead-scroll counter (_tab_scroll_count,
passed to navigation_protocol.decide() as dead_scroll_count) must only
reset when a scroll makes genuine progress toward the optimal view (more
actionable targets simultaneously visible than before), not merely when
the view visibly moved, and not merely when SOME target is visible.

REWRITTEN 2026-08-08 after the "optimal view" redesign exposed a second-
generation version of this exact bug class. The original version of this
file (and of the SCROLL branch itself) reset the counter whenever
has_visible_empty_target() found ANY actionable field, then clicked that
field's bbox directly and `continue`d -- correct under the OLD decide()
rule ("is at least one target visible? then stop scrolling"). Once decide()
was redesigned to only return WAIT when cur == best (the maximum
simultaneously-visible count, not just "at least one"), that old click-and-
continue logic started fighting the new rule: it kept re-clicking whatever
field was nearest (never typing into it, since `continue` skips the
transformer's turn) while decide() kept saying SCROLL again immediately,
because cur was still less than best. Live symptom, direct user report:
"you're just scrolling slowly, filling nothing" -- the SAME field ('First
Name') got refocused 4 times in a row, 25px apart, with zero fills.

Fixed by deleting the speculative click entirely and rebasing the dead-
scroll counter on optimal_view_counts()'s cur (the same metric decide()
itself uses) instead of has_visible_empty_target()'s yes/no: progress means
cur increased, not "something is visible."
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import visible_field_signature, optimal_view_counts

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _run_scroll_branch(sig_before_elements, sig_after_elements, tab_scroll_count):
    """Mirrors the CURRENT NavAction.SCROLL branch in agent.py's run()
    (2026-08-08 redesign): only reset the counter when the new view has
    STRICTLY MORE simultaneously-actionable targets than before -- the same
    cur metric decide() itself uses, not a yes/no visibility check."""
    state_before = {"elements": sig_before_elements}
    state_after  = {"elements": sig_after_elements}
    sig_before = visible_field_signature(state_before, VIEWPORT_BOTTOM)
    sig_after  = visible_field_signature(state_after, VIEWPORT_BOTTOM)
    view_moved = sig_after != sig_before
    cur_before, _ = optimal_view_counts(state_before, VIEWPORT_BOTTOM)
    cur_after, _  = optimal_view_counts(state_after, VIEWPORT_BOTTOM)
    if view_moved and cur_after > cur_before:
        return 0
    return tab_scroll_count + 1


class TestCounterOnlyResetsOnGenuineProgress:
    def test_scroll_shifting_an_already_filled_field_still_increments_the_counter(self):
        # An ALREADY-FILLED editcontrol shifts y-position as the page
        # scrolls -- the signature genuinely differs (proving the view
        # moved), but cur stays 0 both before and after (nothing empty).
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100))]
        result = _run_scroll_branch(before, after, tab_scroll_count=0)
        assert result == 1, "a non-actionable reveal must NOT reset the counter to 0"

    def test_scroll_revealing_a_genuine_empty_field_resets_the_counter(self):
        before = [_field("Vehicle Info", value="already filled", bbox=(100, 100, 300, 130))]
        after  = [_field("Vehicle Info", value="already filled", bbox=(100, 70, 300, 100)),
                  _field("VIN", value="", bbox=(100, 300, 300, 330))]
        result = _run_scroll_branch(before, after, tab_scroll_count=1)
        assert result == 0, "cur going 0 -> 1 is genuine progress -- must reset the counter"

    def test_one_visible_target_staying_at_one_does_not_reset_the_counter(self):
        """The actual live regression, isolated: the SAME single empty field
        just shifts position (still exactly 1 actionable target visible,
        neither more nor fewer) -- cur is unchanged, so this must NOT look
        like progress, even though has_visible_empty_target() would say
        True both times (that's the old, now-removed check)."""
        before = [_field("First Name", value="", bbox=(100, 200, 300, 230))]
        after  = [_field("First Name", value="", bbox=(100, 175, 300, 205))]
        result = _run_scroll_branch(before, after, tab_scroll_count=2)
        assert result == 3, "cur staying at 1 is NOT progress -- must accumulate toward the dead-scroll cap"

    def test_repeated_non_actionable_scrolls_accumulate_toward_the_dead_scroll_cap(self):
        """11-straight-scrolls-nothing-filled shape: accumulate, don't reset."""
        counter = 0
        before = [_field("A", value="already filled", bbox=(100, 100, 300, 130))]
        for i in range(5):
            after = [_field("A", value="already filled", bbox=(100, 100 - (i + 1) * 15, 300, 130 - (i + 1) * 15))]
            counter = _run_scroll_branch(before, after, counter)
            before = after
        assert counter == 5, "non-actionable scrolls must accumulate, not reset each time"

    def test_view_not_moving_at_all_also_increments(self):
        same = [_field("A", value="x", bbox=(100, 100, 300, 130))]
        result = _run_scroll_branch(same, same, tab_scroll_count=2)
        assert result == 3

    def test_progress_toward_a_larger_best_resets_the_counter_even_below_best(self):
        """A long tab with many targets should keep resetting the counter
        for as long as EACH scroll keeps adding more simultaneously-visible
        targets, even though cur hasn't reached best yet -- "a long tab can
        scroll as many times as it genuinely has real content" (decide()'s
        own docstring)."""
        before = [_field("A", value="", bbox=(100, 100, 300, 130))]
        after = [_field("A", value="", bbox=(100, 100, 300, 130)),
                 _field("B", value="", bbox=(100, 140, 300, 170))]
        result = _run_scroll_branch(before, after, tab_scroll_count=3)
        assert result == 0, "cur going 1 -> 2 is progress, regardless of how far best still is"


class TestScrollBranchNoLongerClicksSpeculatively:
    """The regression's actual fix: the SCROLL branch must not act on
    anything it finds mid-scroll. decide() alone -- called fresh, at the top
    of the loop, on the post-scroll state -- decides when scrolling stops
    and hands control to the normal transformer/OPT2 step flow, which
    already owns clicking AND filling, verified. This class documents the
    absence of the old behavior rather than mirroring removed code -- there
    is no click-producing function left in the SCROLL branch to unit test."""

    def test_optimal_view_counts_alone_determines_whether_scrolling_continues(self):
        """Sanity check on the exact metric the SCROLL branch now uses:
        cur < best means "still not optimal, scrolling was right to
        continue" -- there is no separate has_visible_empty_target gate
        deciding this anymore."""
        state = {"elements": [
            _field("Lonely Field", value="", bbox=(100, 50, 300, 80)),
            _field("Packed A", value="", bbox=(100, 1010, 300, 1040)),
            _field("Packed B", value="", bbox=(100, 1060, 300, 1090)),
        ]}
        cur, best = optimal_view_counts(state, VIEWPORT_BOTTOM)
        assert cur == 1
        assert best == 2
        assert cur < best, "one visible target with a denser view still reachable must not look 'done'"
