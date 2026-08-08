"""
Regression test for agent.py's OPT2 navigate branch -- the transformer's own
low-confidence pointer must never be allowed to click ANY action button as an
incidental side effect of navigating between fields.

Found 2026-08-08, live: a completely unremarkable navigation click
(ptr_conf=0.53, no different from dozens of other clicks that step) happened
to land on a real 'Clear All' button. Confirmed via the popped window's own
class name ('#32770' -- a genuine Windows dialog box, not a misidentified
sibling window) that this was a real confirmation dialog, which then got
dismissed with Escape on every subsequent step for the rest of the run,
blocking all further progress.

FIRST FIX only matched destructive-sounding keywords (_DESTRUCTIVE_KW:
clear/reset/delete/erase/discard/cancel). Direct pushback ("It's not just the
Clear All") was right -- reading car_insurance_form_wx.py's own footer button
list directly found FIVE buttons sharing one row: Submit, Clear All, Print
Preview, Load Record, Save Record. 'Load Record' silently overwrites every
field from a loaded file; 'Print Preview' opens its own separate modal.
Neither matches any destructive keyword, and a keyword list is exactly the
whack-a-mole trap that needs a new entry every time a form adds one more
button with an unanticipated label.

THE ACTUALLY-GENERALIZING FIX: _find_destructive_button_at no longer checks
keywords at all -- it matches ANY button-type element at the click position.
The navigate branch (moving the pointer between FIELDS) has no legitimate
reason to ever click a button while doing that; the one place a button
SHOULD get clicked deliberately (Submit, once genuinely done) already goes
through its own separate, dedicated path (_find_submit_button, called
directly by the stuck-guard) that never flows through this check at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _find_destructive_button_at


def _button(text, bbox):
    return {"type": "buttoncontrol", "text": text, "label": text, "bbox": list(bbox)}


class TestFindDestructiveButtonAt:
    def test_finds_the_clear_all_button_that_caused_the_real_incident(self):
        btn = _button("Clear All", (1400, 830, 1520, 870))
        found = _find_destructive_button_at([btn], [1459, 848])
        assert found is btn

    def test_catches_buttons_that_match_no_destructive_keyword_at_all(self):
        """The actual gap the keyword-only version had: 'Load Record'
        silently overwrites every field from a file, 'Print Preview' opens
        its own modal, 'Save Record' can trigger its own file dialog too --
        none of them sound destructive, all of them derail a run the same
        way 'Clear All' did. A blanket button refusal catches all five
        footer buttons uniformly, with no keyword list to keep extending."""
        for label in ("Print Preview", "Load Record", "Save Record"):
            btn = _button(label, (100, 100, 300, 130))
            found = _find_destructive_button_at([btn], [200, 115])
            assert found is btn, f"expected to catch {label!r}"

    def test_catches_submit_too_when_hit_via_the_uncertain_navigate_click(self):
        """Submit has its own separate, deliberate path (_find_submit_button,
        used only by the stuck-guard once a record is genuinely finished) --
        but if the UNCERTAIN navigate pointer stumbles onto it incidentally,
        that's exactly as unintended as stumbling onto Clear All, and must
        be refused the same way."""
        btn = _button("Submit", (100, 100, 300, 130))
        found = _find_destructive_button_at([btn], [200, 115])
        assert found is btn

    def test_returns_none_when_click_position_misses_the_button(self):
        btn = _button("Clear All", (1400, 830, 1520, 870))
        found = _find_destructive_button_at([btn], [200, 115])
        assert found is None

    def test_returns_none_for_a_normal_fillable_field_at_the_position(self):
        field = {"type": "editcontrol", "text": "First Name", "label": "First Name",
                  "bbox": [100, 100, 300, 130]}
        found = _find_destructive_button_at([field], [200, 115])
        assert found is None

    def test_returns_none_for_a_tab_strip_item_at_the_position(self):
        """Switching tabs via the transformer's own pointer must still work
        -- tab headers are a different element type (tabitemcontrol), not
        buttoncontrol, so they're never caught by this check."""
        tab = {"type": "tabitemcontrol", "text": "Vehicle", "label": "Vehicle",
               "bbox": [100, 100, 300, 130]}
        found = _find_destructive_button_at([tab], [200, 115])
        assert found is None


def _run_navigate_click_guard(elements, click_pos, executor):
    """Mirrors the CURRENT guard added to agent.py's OPT2 navigate branch:
    ANY button at the pointer's target position gets Tab'd instead of
    clicked."""
    btn = _find_destructive_button_at(elements, click_pos)
    if btn is not None:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return False
    executor.execute({"action_type": "click", "click_position": click_pos})
    return True


class TestNavigateBranchRefusesAnyButtonClick:
    def test_tabs_instead_of_clicking_clear_all(self):
        from unittest.mock import MagicMock
        executor = MagicMock()
        btn = _button("Clear All", (1400, 830, 1520, 870))
        clicked = _run_navigate_click_guard([btn], [1459, 848], executor)
        assert clicked is False
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]

    def test_tabs_instead_of_clicking_load_record(self):
        from unittest.mock import MagicMock
        executor = MagicMock()
        btn = _button("Load Record", (1400, 830, 1520, 870))
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
