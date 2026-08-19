"""
Regression test for _try_advance_tab() resetting scroll position on a
normal tab switch.

Real live bug, direct reports across two rounds ("Driver 2 returns
empty... also add a way to distinguish similar bare label names", then,
after that fix landed, "Driver 2 was still not filled"). Traced with real
log evidence (run_task_20260819_204110.log): switching to the Drivers tab,
Driver 2's own topmost fields (First Name, Last Name, Date of Birth,
Gender, DL Number, DL Issuing State, DL Expiration -- the first 4-7 fields
declared for that section) never appeared as batch-fill targets at all --
not filled, not confirmed-blank, not even a control-resolution warning.
Driver 2's LOWER fields (Relationship, Accidents, Violations, both
checkboxes) and every one of Driver 3's fields (further down the page)
filled correctly in the same run. No scroll event of any kind occurred
during the whole Drivers tab visit.

Root cause: _scroll_form_to_top already existed, with the exact right
intent ("so that _focus_first_empty_field always starts from the first
visible field, not mid-page") -- but was only ever wired into
_try_advance_tab's RARE "no tabs found at all" fallback branch. The
ORDINARY path (a next tab exists, gets clicked) never called it. A
long tab (e.g. Coverage, scrolled deep down before this switch) can leave
the newly-active tab's own view starting mid-page instead of at its own
top -- Driver 2's topmost fields silently scrolled out of the initial
viewport, and Navigation Protocol's own scroll-reveal only ever scrolls
DOWN to find more content, never up to check what was already skipped
above, so they were never revisited before the tab was judged exhausted
and the agent moved on.

Fix: the normal tab-click path now re-observes after the click (the state
passed into _try_advance_tab is from BEFORE the switch, so the elements
_scroll_form_to_top would compute a click point from must be fresh --
using the stale state could target the OLD tab's now-hidden controls) and
calls _scroll_form_to_top with that fresh state, mirroring the exact
technique the "no tabs found" fallback already used.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _tab(text, bbox):
    return {"element_id": text, "type": "tabitemcontrol", "text": text,
            "label": text, "bbox": list(bbox), "window_role": "active"}


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1,
                      step_delay=0, disable_auto_handlers=True)
    agent._executor = MagicMock()
    agent._executor.execute = MagicMock()
    return agent


def _tabs_state(active_idx=0):
    tabs = [
        _tab("Policy",       (100, 100, 200, 130)),
        _tab("Policyholder", (200, 100, 300, 130)),
        _tab("Vehicle",      (300, 100, 400, 130)),
        _tab("Coverage",     (400, 100, 500, 130)),
        _tab("Drivers",      (500, 100, 600, 130)),
        _tab("History",      (600, 100, 700, 130)),
        _tab("Claims",       (700, 100, 800, 130)),
        _tab("Payment",      (800, 100, 900, 130)),
    ]
    return {"elements": tabs}


class TestNormalTabAdvanceResetsScrollPosition:
    def test_advancing_to_the_next_tab_re_observes_and_scrolls_to_top(self):
        agent = _make_agent()
        agent._current_tab_idx = 3   # currently on Coverage (idx 3)
        state = _tabs_state()
        fresh_state = {"elements": [_tab("Drivers", (500, 100, 600, 130))],
                        "marker": "fresh"}
        agent._observe = MagicMock(return_value=fresh_state)
        agent._scroll_form_to_top = MagicMock()

        result = agent._try_advance_tab(state)

        assert result is True
        agent._observe.assert_called_once()
        agent._scroll_form_to_top.assert_called_once_with(fresh_state)

    def test_scroll_to_top_uses_the_fresh_state_not_the_stale_pre_switch_one(self):
        """The state passed into _try_advance_tab is from BEFORE the click
        -- its elements belong to the tab being LEFT. _scroll_form_to_top
        computes a click point from whatever elements it's given, so
        feeding it the stale state could click into the wrong (old) tab's
        now-hidden content instead of the new tab's own."""
        agent = _make_agent()
        agent._current_tab_idx = 3
        stale_state = _tabs_state()
        fresh_state = {"elements": [], "marker": "fresh_not_stale"}
        agent._observe = MagicMock(return_value=fresh_state)
        agent._scroll_form_to_top = MagicMock()

        agent._try_advance_tab(stale_state)

        called_with = agent._scroll_form_to_top.call_args[0][0]
        assert called_with is fresh_state
        assert called_with is not stale_state

    def test_tab_click_happens_before_the_scroll_reset(self):
        agent = _make_agent()
        agent._current_tab_idx = 3
        state = _tabs_state()
        agent._observe = MagicMock(return_value={"elements": []})
        calls_order = []
        agent._executor.execute = MagicMock(
            side_effect=lambda *a, **k: calls_order.append("click"))
        agent._scroll_form_to_top = MagicMock(
            side_effect=lambda *a, **k: calls_order.append("scroll_to_top"))

        agent._try_advance_tab(state)

        assert calls_order == ["click", "scroll_to_top"]


class TestLastTabExhaustedPathIsUnaffected:
    """The Submit-click branch (last tab, no next tab to advance to) is a
    completely different code path -- must not also trigger a scroll-to-top
    call, which belongs only to the ordinary tab-to-tab advance."""

    def test_last_tab_with_no_submit_button_does_not_scroll_to_top(self):
        agent = _make_agent()
        agent._current_tab_idx = 7   # already on Payment, the last tab
        state = _tabs_state()
        agent._observe = MagicMock()
        agent._scroll_form_to_top = MagicMock()

        result = agent._try_advance_tab(state)

        assert result is False
        agent._scroll_form_to_top.assert_not_called()


class TestNoTabsFoundFallbackStillWorksUnchanged:
    """The pre-existing 'no tabs found at all' fallback already had its own
    scroll-to-top call -- this fix must not double it up or otherwise
    change that separate branch's behavior."""

    def test_no_tabs_found_still_calls_scroll_to_top_exactly_once(self):
        agent = _make_agent()
        agent._current_tab_idx = 0
        empty_state = {"elements": []}
        agent._observe = MagicMock(return_value={"elements": []})
        agent._scroll_form_to_top = MagicMock()

        result = agent._try_advance_tab(empty_state)

        assert result is False
        agent._scroll_form_to_top.assert_called_once()
