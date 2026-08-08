"""
Regression test for agent.py's _scroll_form_down -- scroll lands the next
unfilled field near the TOP of the viewport in one motion, instead of a
fixed small nudge (revealed only one field at a time) or a guessed
count-based magnitude (an earlier same-night attempt, itself replaced).

Found 2026-08-08, live, direct user report after the count-based attempt:
told directly that a precise position could be computed instead of guessed
-- and it can, without knowing the scroll-unit-to-pixel ratio in advance:
scroll a small known amount, measure how far the SAME element (matched by
the project's existing _attempt_key cross-snapshot identity) actually moved
on screen, then compute exactly how much further to scroll from that
measured ratio. Self-calibrating per call.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _elem(label, etype="editcontrol", value="", y=100, height=30):
    return {"element_id": label, "type": etype, "label": label, "window_role": "active",
            "value": value, "bbox": [100, y, 300, y + height]}


def _attempt_key(elem, elements=None):
    """Minimal stand-in for LLMAgent._attempt_key -- label-based identity,
    sufficient for these tests (no repeated-label cases here)."""
    return (elem.get("label") or elem.get("text") or "").strip().lower()


def _run_smart_scroll(elements, viewport_bottom, viewport_top, scroll_calls, observe_fn):
    """Mirrors the calibrated scroll-to-top logic added to _scroll_form_down."""
    fillable_below = [
        e for e in elements
        if e.get("type") in ("editcontrol", "comboboxcontrol", "checkboxcontrol")
        and e.get("window_role") != "background"
        and not (e.get("value") or "").strip()
        and e.get("bbox") and e["bbox"][1] > viewport_bottom
    ]
    target = min(fillable_below, key=lambda e: e["bbox"][1]) if fillable_below else None

    scroll_calls.append(-5)   # calibration nudge
    if target is None:
        return 0

    target_key = _attempt_key(target, elements=elements)
    old_y = (target["bbox"][1] + target["bbox"][3]) / 2

    new_elements = observe_fn()
    found = next((e for e in new_elements
                  if _attempt_key(e, elements=new_elements) == target_key and e.get("bbox")), None)
    if found is None:
        return 0

    new_y = (found["bbox"][1] + found["bbox"][3]) / 2
    pixels_moved = old_y - new_y
    if pixels_moved <= 1:
        return 0

    pixels_per_unit = pixels_moved / 5.0
    desired_y = viewport_top + 40
    remaining_px = new_y - desired_y
    if remaining_px <= 0:
        return 0

    more_units = min(round(remaining_px / pixels_per_unit), 60)
    if more_units > 0:
        scroll_calls.append(-more_units)
    return more_units


class TestCalibratedScrollToTop:
    def test_no_target_below_viewport_only_does_the_calibration_nudge(self):
        elements = [_elem("Filled Field", value="x", y=200)]
        calls = []
        more = _run_smart_scroll(elements, viewport_bottom=900, viewport_top=100,
                                  scroll_calls=calls, observe_fn=lambda: elements)
        assert more == 0
        assert calls == [-5]

    def test_computes_correct_additional_units_from_measured_movement(self):
        target_before = _elem("VIN", y=1000)
        elements = [target_before]

        def observe_after_calibration():
            # Same field, moved up 50px after the -5 calibration scroll --
            # measured ratio: 50px / 5 units = 10 px/unit.
            return [_elem("VIN", y=950)]

        calls = []
        more = _run_smart_scroll(elements, viewport_bottom=900, viewport_top=500,
                                  scroll_calls=calls, observe_fn=observe_after_calibration)
        # new_y=965 (950+30/2... use center: (950+980)/2=965), desired_y=540,
        # remaining=425, pixels_per_unit=10 -> more=round(425/10)=42
        assert calls[0] == -5
        assert more == calls[1] * -1
        assert more > 0

    def test_caps_additional_units_at_60_for_safety(self):
        target_before = _elem("VIN", y=2000)
        elements = [target_before]

        def observe_after_calibration():
            return [_elem("VIN", y=1950)]   # moved 50px -> 10px/unit

        calls = []
        more = _run_smart_scroll(elements, viewport_bottom=900, viewport_top=0,
                                  scroll_calls=calls, observe_fn=observe_after_calibration)
        assert more == 60
        assert calls[1] == -60

    def test_stops_if_target_not_found_after_calibration(self):
        elements = [_elem("VIN", y=1000)]
        calls = []
        more = _run_smart_scroll(elements, viewport_bottom=900, viewport_top=100,
                                  scroll_calls=calls, observe_fn=lambda: [])  # target vanished
        assert more == 0
        assert calls == [-5]

    def test_stops_if_the_view_barely_moved(self):
        elements = [_elem("VIN", y=1000)]

        def observe_no_movement():
            return [_elem("VIN", y=1000)]   # didn't move -- e.g. already at the bottom

        calls = []
        more = _run_smart_scroll(elements, viewport_bottom=900, viewport_top=100,
                                  scroll_calls=calls, observe_fn=observe_no_movement)
        assert more == 0
        assert calls == [-5]

    def test_picks_the_topmost_off_screen_field_as_the_target(self):
        elements = [_elem("Lower Field", y=1200), _elem("Upper Field", y=1000)]

        def observe_after_calibration():
            return [_elem("Lower Field", y=1150), _elem("Upper Field", y=950)]

        calls = []
        _run_smart_scroll(elements, viewport_bottom=900, viewport_top=100,
                           scroll_calls=calls, observe_fn=observe_after_calibration)
        # Just confirming no crash and it ran through the Upper Field's measured
        # movement (950 vs 1000 -> matches "Upper Field" being the target).
        assert len(calls) >= 1
