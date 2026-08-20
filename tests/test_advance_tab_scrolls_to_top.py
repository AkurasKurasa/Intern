"""
Regression test for _try_advance_tab() resetting scroll position on a
normal tab switch -- using the SAFE, UIA-only mechanism.

Real live bug, three rounds:

(1) Direct report ("Driver 2 was still not filled"). Traced with real log
evidence: switching to the Drivers tab, Driver 2's own topmost fields
(First Name, Last Name, Date of Birth, Gender, DL Number, DL Issuing
State, DL Expiration) never appeared as batch-fill targets at all. Root
cause: _try_advance_tab's ordinary tab-click path never reset scroll
position -- only its rare "no tabs found" fallback did. A long tab
(Coverage, scrolled deep down) could leave the newly-active Drivers tab
starting mid-page instead of its own top, silently scrolling Driver 2's
topmost fields out of the initial viewport.

(2) Fixed by wiring the EXISTING _scroll_form_to_top into the ordinary
path -- but that function does a REAL physical mouse click, Ctrl+Home, and
several scroll-wheel events. Direct report: "it got slower... so many
wrong things got filled" -- a stray click landing wrong, or a scroll-wheel
event catching a combobox, running on every tab switch instead of the
rare fallback it was designed for. Reverted.

(3) Fixed again with _scroll_form_to_top_uia_percent instead (see
test_scroll_form_to_top_uia.py) -- ONE native UIA ScrollPattern call, no
mouse, no keyboard, no scroll-wheel simulation, reusing the exact
mechanism already proven live for the downward-scroll case. Direct
request: "fix the Driver 2 problem and not fill things where they're not
supposed to be filled while maintaining the same speed we had earlier."

Still re-observes after the click before calling the scroll-reset: `state`
passed into _try_advance_tab is from BEFORE the switch, so its elements
belong to the tab being LEFT -- _scroll_form_to_top_uia_percent's anchor
search (via _find_scrollable_pane_uia) needs a field name that actually
exists on the NEW tab to find the right scrollable pane, not the old one's.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def _tabs_state():
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


class TestNormalTabAdvanceResetsScrollPositionSafely:
    def test_advancing_to_the_next_tab_re_observes_and_resets_scroll_via_uia(self):
        agent = _make_agent()
        agent._current_tab_idx = 3   # currently on Coverage (idx 3)
        state = _tabs_state()
        fresh_state = {"elements": [_tab("Drivers", (500, 100, 600, 130))],
                        "marker": "fresh"}
        agent._observe = MagicMock(return_value=fresh_state)
        agent._scroll_form_to_top_uia_percent = MagicMock(return_value=True)

        result = agent._try_advance_tab(state)

        assert result is True
        agent._observe.assert_called_once()
        agent._scroll_form_to_top_uia_percent.assert_called_once_with(fresh_state)

    def test_never_calls_the_old_risky_pyautogui_based_scroll_to_top(self):
        """The whole point of the fix -- must use the safe UIA-only route,
        never the physical mouse/keyboard one that caused the regression."""
        agent = _make_agent()
        agent._current_tab_idx = 3
        state = _tabs_state()
        agent._observe = MagicMock(return_value={"elements": []})
        agent._scroll_form_to_top = MagicMock()
        agent._scroll_form_to_top_uia_percent = MagicMock(return_value=True)

        agent._try_advance_tab(state)

        agent._scroll_form_to_top.assert_not_called()
        agent._scroll_form_to_top_uia_percent.assert_called_once()

    def test_scroll_reset_uses_the_fresh_state_not_the_stale_pre_switch_one(self):
        agent = _make_agent()
        agent._current_tab_idx = 3
        stale_state = _tabs_state()
        fresh_state = {"elements": [], "marker": "fresh_not_stale"}
        agent._observe = MagicMock(return_value=fresh_state)
        agent._scroll_form_to_top_uia_percent = MagicMock(return_value=True)

        agent._try_advance_tab(stale_state)

        called_with = agent._scroll_form_to_top_uia_percent.call_args[0][0]
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
        agent._scroll_form_to_top_uia_percent = MagicMock(
            side_effect=lambda *a, **k: calls_order.append("scroll_to_top"))

        agent._try_advance_tab(state)

        assert calls_order == ["click", "scroll_to_top"]

    def test_a_failed_scroll_reset_does_not_fail_the_whole_advance(self):
        """_scroll_form_to_top_uia_percent returning False (no scrollable
        pane found, UIA unavailable, etc.) must not be treated as an
        error -- the tab switch itself already succeeded."""
        agent = _make_agent()
        agent._current_tab_idx = 3
        state = _tabs_state()
        agent._observe = MagicMock(return_value={"elements": []})
        agent._scroll_form_to_top_uia_percent = MagicMock(return_value=False)

        result = agent._try_advance_tab(state)

        assert result is True


class TestLastTabExhaustedPathIsUnaffected:
    def test_last_tab_with_no_submit_button_does_not_reset_scroll(self):
        agent = _make_agent()
        agent._current_tab_idx = 7   # already on Payment, the last tab
        state = _tabs_state()
        agent._observe = MagicMock()
        agent._scroll_form_to_top_uia_percent = MagicMock()

        result = agent._try_advance_tab(state)

        assert result is False
        agent._scroll_form_to_top_uia_percent.assert_not_called()


class TestNoTabsFoundFallbackStillUsesTheOldRoute:
    """The pre-existing 'no tabs found at all' fallback already calls
    _scroll_form_to_top (the pyautogui-based one) -- this fix is scoped
    only to the ordinary tab-click path, not this rare fallback, so it
    must be completely unaffected."""

    def test_no_tabs_found_still_calls_the_original_scroll_to_top(self):
        agent = _make_agent()
        agent._current_tab_idx = 0
        empty_state = {"elements": []}
        agent._observe = MagicMock(return_value={"elements": []})
        agent._scroll_form_to_top = MagicMock()
        agent._scroll_form_to_top_uia_percent = MagicMock()

        result = agent._try_advance_tab(empty_state)

        assert result is False
        agent._scroll_form_to_top.assert_called_once()
        agent._scroll_form_to_top_uia_percent.assert_not_called()
