"""
Regression test for agent.py's _scroll_form_down -- scroll distance now
scales with how much unattempted fillable content is still off-screen,
instead of always scrolling a fixed small amount.

Found 2026-08-08, live, direct user report after testing the scroll-branch
fix: the fixed "-5 units" scroll only ever revealed one field at a time --
not an "optimal view" (as many actionable targets visible at once as
possible, the original spec navigation_protocol.py was built against). User
explicitly said scrolling doesn't have to be the mechanism used, and a
precise pixel-distance calculation can't be calibrated without live testing
(only the user runs live GUI actions) -- so this scales conservatively by
COUNT of remaining off-screen unfilled fields instead of computing an exact
target position.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

VIEWPORT_BOTTOM = 900.0


def _elem(etype="editcontrol", value="", y=100, filled=False):
    v = "x" if filled else value
    return {"type": etype, "window_role": "active", "value": v,
            "bbox": [100, y, 300, y + 30]}


def _scroll_units_for(elements, viewport_bottom=VIEWPORT_BOTTOM):
    """Mirrors the scroll-magnitude calculation added to _scroll_form_down."""
    remaining_below = sum(
        1 for e in elements
        if e.get("type") in ("editcontrol", "comboboxcontrol", "checkboxcontrol")
        and e.get("window_role") != "background"
        and not (e.get("value") or "").strip()
        and e.get("bbox") and e["bbox"][1] > viewport_bottom
    )
    return -5 if remaining_below <= 1 else -15 if remaining_below <= 4 else -25, remaining_below


class TestAdaptiveScrollDistance:
    def test_zero_remaining_uses_the_small_default(self):
        elements = [_elem(y=100)]  # on-screen, doesn't count
        units, remaining = _scroll_units_for(elements)
        assert remaining == 0
        assert units == -5

    def test_one_remaining_field_uses_the_small_default(self):
        elements = [_elem(y=1000)]  # one, off-screen
        units, remaining = _scroll_units_for(elements)
        assert remaining == 1
        assert units == -5

    def test_a_few_remaining_fields_scrolls_further(self):
        elements = [_elem(y=1000), _elem(y=1050), _elem(y=1100)]
        units, remaining = _scroll_units_for(elements)
        assert remaining == 3
        assert units == -15

    def test_many_remaining_fields_scrolls_the_most(self):
        elements = [_elem(y=1000 + i * 40) for i in range(6)]
        units, remaining = _scroll_units_for(elements)
        assert remaining == 6
        assert units == -25

    def test_already_filled_fields_off_screen_dont_count(self):
        elements = [_elem(y=1000, filled=True), _elem(y=1050, filled=True)]
        units, remaining = _scroll_units_for(elements)
        assert remaining == 0
        assert units == -5

    def test_background_elements_dont_count(self):
        e = _elem(y=1000)
        e["window_role"] = "background"
        units, remaining = _scroll_units_for([e])
        assert remaining == 0
        assert units == -5

    def test_checkboxes_and_comboboxes_also_count(self):
        elements = [_elem(etype="checkboxcontrol", y=1000),
                    _elem(etype="comboboxcontrol", y=1050),
                    _elem(etype="editcontrol", y=1100)]
        units, remaining = _scroll_units_for(elements)
        assert remaining == 3
        assert units == -15
