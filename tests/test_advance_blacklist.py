"""
Regression test for _advance_blacklist_pos — stopping the transformer's own
pointer from immediately undoing a tab the system just deliberately left.

Found live 2026-08-07: after a run finally made real progress (past three
prior loop fixes), it burned roughly a quarter of its 200-step budget on a
repeating tug-of-war. Navigation Protocol correctly decided Vehicle tab was
exhausted (two scrolls revealed nothing new) and _try_advance_tab moved to
Coverage. The VERY NEXT step, the transformer's own click-pointer (0.73-0.76
confidence — not low enough for the existing confidence gate to block)
clicked straight back onto Vehicle's tab button. This repeated 12-13 times
in one run, ~4 steps per cycle, before the run finally hit max_steps.

Fix: _try_advance_tab now blacklists the tab-strip position it's LEAVING
(not the one it's going to) in self._advance_blacklist_pos, unioned into
the same confidence-gate blacklist mechanism built earlier tonight for the
dead-end combobox loop. Bounded to at most one entry — replaced, not
accumulated, on every advance — so a real later revisit is never
permanently blocked, only an immediate reversal of the last deliberate move.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _gate_low_confidence_click, _round_click_pos


class TestAdvanceBlacklistBlocksImmediateReversal:
    def test_matches_the_real_live_failure_case(self):
        # Vehicle's tab-strip position, moderate-high confidence (0.73-0.76) —
        # not low enough for the plain confidence floor to catch.
        vehicle_tab_pos = [1074, 134]
        advance_blacklist = {_round_click_pos(vehicle_tab_pos)}
        assert _gate_low_confidence_click(vehicle_tab_pos, 0.76, blacklist=advance_blacklist) is None
        assert _gate_low_confidence_click(vehicle_tab_pos, 0.73, blacklist=advance_blacklist) is None

    def test_blocks_even_at_high_confidence(self):
        pos = [1074, 134]
        advance_blacklist = {_round_click_pos(pos)}
        assert _gate_low_confidence_click(pos, 0.99, blacklist=advance_blacklist) is None

    def test_does_not_affect_other_positions(self):
        left_tab_pos = [1074, 134]
        other_pos = [500, 600]
        advance_blacklist = {_round_click_pos(left_tab_pos)}
        assert _gate_low_confidence_click(other_pos, 0.76, blacklist=advance_blacklist) is other_pos

    def test_union_with_nochange_blacklist_blocks_both(self):
        # The real call site unions self._nochange_click_pos | self._advance_blacklist_pos.
        left_tab_pos = _round_click_pos([1074, 134])
        dead_end_pos = _round_click_pos([1493, 320])
        combined = {left_tab_pos} | {dead_end_pos}
        assert _gate_low_confidence_click([1074, 134], 0.76, blacklist=combined) is None
        assert _gate_low_confidence_click([1493, 320], 0.91, blacklist=combined) is None

    def test_empty_advance_blacklist_blocks_nothing(self):
        pos = [1074, 134]
        assert _gate_low_confidence_click(pos, 0.76, blacklist=set()) is pos


class TestBlacklistIsBoundedToOneEntry:
    """Mirrors _try_advance_tab's own logic: `self._advance_blacklist_pos = {...}`
    (assignment, not .add()) — each new advance REPLACES the previous entry
    rather than accumulating, so a later, different tab isn't blocked by an
    earlier one that's no longer relevant."""

    def test_replacing_the_set_drops_the_old_entry(self):
        blacklist = {_round_click_pos([1074, 134])}   # left Vehicle
        # ... later, a different advance happens (left Coverage instead) ...
        blacklist = {_round_click_pos([1144, 134])}   # REPLACED, not unioned
        assert _round_click_pos([1074, 134]) not in blacklist
        assert _round_click_pos([1144, 134]) in blacklist
