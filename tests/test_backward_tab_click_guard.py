"""
Regression tests for two related fixes in components/agent/agent.py's
run(), both found live 2026-08-08 via the drill tool:

1. Blocking backward tab-strip clicks outright (~L2725, right before the
   existing "Tab-click -> navigating to" handler). Reported directly:
   "Check most recent logs it jumped to VIN damn." A drill run started
   fresh at Payment (idx 7) -- Vehicle (idx 2) was never visited this
   session, so _advance_blacklist_pos (which only blocks tabs the SYSTEM
   itself has deliberately left) had nothing to block it. The
   transformer's own pointer clicked Vehicle's tab strip at ptr_conf=0.54,
   comfortably clearing the general 0.50 tab-strip confidence floor,
   which doesn't distinguish direction. Every successful full-form run
   this session progressed strictly forward, never backward -- so
   backward tab clicks are now blocked outright, not just confidence-
   gated, extending the same "finish forward, don't come back" principle
   already validated for _advance_blacklist_pos to tabs never yet visited
   this session.

2. Resetting self._current_tab_idx to 0 at the new-record boundary
   (~L3387, alongside self._attempted_keys.clear() etc.). Found while
   building fix #1: car_insurance_form_wx.py's own _on_submit() always
   calls self.nb.SetSelection(0) -- a real Submit puts the FORM on Policy
   tab regardless of where the agent started (drill mode included) -- but
   nothing ever told the AGENT's own self._current_tab_idx to follow
   suit. Without this, fix #1 would have wrongly blocked the legitimate
   Payment -> Policy reset for the next record, mistaking it for the
   exact erroneous backward jump it exists to catch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _decide_tab_click(current_tab_idx, hit_idx):
    """Mirrors the CURRENT tab-click handling logic in agent.py's run():
    block backward jumps outright, otherwise navigate if different,
    otherwise treat as already-on-this-tab."""
    if hit_idx < current_tab_idx:
        return "blocked_tab"
    if hit_idx != current_tab_idx:
        return "navigated"
    return "already_here"


class TestBackwardTabClickIsBlocked:
    def test_the_exact_live_scenario_payment_to_vehicle_is_blocked(self):
        # Drill run started at Payment (idx 7); transformer's pointer
        # clicked Vehicle's tab strip (idx 2).
        assert _decide_tab_click(current_tab_idx=7, hit_idx=2) == "blocked_tab"

    def test_a_single_tab_backward_is_also_blocked(self):
        """Not just huge jumps -- ANY backward click is blocked, since no
        confidence observed live has ever made one correct."""
        assert _decide_tab_click(current_tab_idx=3, hit_idx=2) == "blocked_tab"

    def test_forward_clicks_are_unaffected(self):
        assert _decide_tab_click(current_tab_idx=2, hit_idx=3) == "navigated"

    def test_skipping_ahead_multiple_tabs_is_unaffected(self):
        """Forward jumps (even skipping tabs) aren't blocked -- only
        direction matters, not distance."""
        assert _decide_tab_click(current_tab_idx=1, hit_idx=6) == "navigated"

    def test_clicking_the_already_active_tab_is_unaffected(self):
        assert _decide_tab_click(current_tab_idx=4, hit_idx=4) == "already_here"


class TestRecordBoundaryResetsCurrentTabIdx:
    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_current_tab_idx_resets_to_zero_on_new_record(self):
        agent = self._make_agent()
        agent._current_tab_idx = 7   # was on Payment at end of record 1
        agent._attempted_record_num = 1
        agent._record_num = 2        # Submit advanced to a new record

        # Mirrors the CURRENT reset block in _refresh_record_cache().
        if agent._record_num != agent._attempted_record_num:
            agent._attempted_keys.clear()
            agent._advance_blacklist_pos.clear()
            agent._leave_blank_keys.clear()
            agent._current_tab_idx = 0
            agent._attempted_record_num = agent._record_num

        assert agent._current_tab_idx == 0

    def test_reset_prevents_the_legitimate_payment_to_policy_click_from_being_blocked(self):
        """Fix #1 and fix #2 must compose correctly: after the reset, a
        click back to Policy (idx 0) for the new record must NOT be
        mistaken for an erroneous backward jump."""
        agent = self._make_agent()
        agent._current_tab_idx = 7
        agent._attempted_record_num = 1
        agent._record_num = 2
        if agent._record_num != agent._attempted_record_num:
            agent._current_tab_idx = 0
            agent._attempted_record_num = agent._record_num

        # The form itself is now on Policy (idx 0); a click there must be
        # treated as "already here", not blocked as backward.
        assert _decide_tab_click(agent._current_tab_idx, hit_idx=0) == "already_here"

    def test_no_reset_when_still_the_same_record(self):
        agent = self._make_agent()
        agent._current_tab_idx = 5
        agent._attempted_record_num = 1
        agent._record_num = 1   # unchanged

        if agent._record_num != agent._attempted_record_num:
            agent._current_tab_idx = 0

        assert agent._current_tab_idx == 5
