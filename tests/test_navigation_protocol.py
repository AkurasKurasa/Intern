"""
Tests for components/agent/navigation_protocol.py — the Navigation Protocol.

Implemented 2026-08-07 at the user's explicit request: "a system protocol
that replaces the direct mimicking of user scrolling... the system itself
navigates the GUI... to maximize empty targets on screen for the
Transformer/Agent to utilize." Consolidates logic that was previously
duplicated inline across agent.py's step loop (a step-count "drought guard"
and a separate visibility-driven "scroll-reveal" block) into one pure,
testable decision surface: given a state and how many scrolls have already
failed to reveal anything new, decide WAIT / SCROLL / ADVANCE_TAB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import (
    NavAction, decide, has_visible_empty_target, visible_field_signature,
)

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


class TestHasVisibleEmptyTarget:
    def test_true_when_an_empty_fillable_field_is_on_screen(self):
        state = {"elements": [_field("First Name")]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is True

    def test_false_when_the_only_field_is_already_filled(self):
        state = {"elements": [_field("First Name", value="Alice")]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is False

    def test_false_when_no_fillable_fields_at_all(self):
        state = {"elements": [{"element_id": "btn", "type": "buttoncontrol",
                                "bbox": [0, 0, 10, 10], "window_role": "active"}]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is False

    def test_false_when_the_empty_field_is_below_the_viewport(self):
        state = {"elements": [_field("Underwriter", bbox=(100, 2000, 300, 2030))]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is False

    def test_false_when_field_already_marked_attempted(self):
        state = {"elements": [_field("Suffix")]}
        assert has_visible_empty_target(
            state, VIEWPORT_BOTTOM,
            attempted_keys={"suffix"},
            attempt_key_fn=lambda e, els: (e.get("label") or "").lower(),
        ) is False

    def test_background_elements_are_ignored(self):
        e = _field("First Name")
        e["window_role"] = "background"
        state = {"elements": [e]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is False


class TestVisibleFieldSignature:
    def test_signature_changes_when_new_field_appears(self):
        state_before = {"elements": [_field("A", bbox=(0, 100, 50, 120))]}
        state_after  = {"elements": [_field("A", bbox=(0, 100, 50, 120)),
                                      _field("B", bbox=(0, 200, 50, 220))]}
        assert (visible_field_signature(state_before, VIEWPORT_BOTTOM)
                != visible_field_signature(state_after, VIEWPORT_BOTTOM))

    def test_signature_identical_for_unchanged_view(self):
        state = {"elements": [_field("A", bbox=(0, 100, 50, 120))]}
        assert (visible_field_signature(state, VIEWPORT_BOTTOM)
                == visible_field_signature(state, VIEWPORT_BOTTOM))


class TestDecide:
    def test_waits_when_an_empty_target_is_visible(self):
        state = {"elements": [_field("First Name")]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.WAIT

    def test_scrolls_when_nothing_visible_and_under_the_dead_scroll_cap(self):
        state = {"elements": []}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0, max_dead_scrolls=2)
        assert d.action == NavAction.SCROLL

    def test_advances_tab_once_dead_scroll_cap_reached(self):
        state = {"elements": []}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=2, max_dead_scrolls=2)
        assert d.action == NavAction.ADVANCE_TAB

    def test_a_long_tab_can_scroll_as_many_times_as_it_has_real_content(self):
        """dead_scroll_count only counts DEAD scrolls (caller resets it whenever
        a field is visible) — decide() itself doesn't cap total scrolls."""
        state = {"elements": [_field("Field 40")]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=39, max_dead_scrolls=2)
        assert d.action == NavAction.WAIT
