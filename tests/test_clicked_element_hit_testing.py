"""
Regression test for recording_quality_gate._clicked_element().

Bug: it returned the FIRST element in state["elements"] whose bbox contained
the click point, not the most specific one. Elements overlap (window >
panel > button), and the window/root element is typically listed first in
UIA capture order, so every click anywhere inside the form window silently
resolved to the window element instead of the actual button — meaning
submit_reached never fired even on sessions that demonstrably clicked
Submit. Found 2026-08-06 after the user insisted they'd pressed Submit and
the quality gate still reported 0/5 sessions reaching it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from recording_quality_gate import _clicked_element


def _state(*elements):
    return {"elements": list(elements)}


def test_prefers_smallest_containing_bbox_over_first_in_array():
    state = _state(
        {"label": "Car Insurance Data Entry Form", "bbox": [854, 0, 1929, 903]},
        {"label": "footer_panel", "bbox": [864, 851, 1919, 892]},
        {"label": "Submit", "bbox": [1348, 858, 1442, 887]},
    )
    el = _clicked_element(state, [1400, 870])
    assert el["label"] == "Submit"


def test_two_adjacent_buttons_resolve_to_the_one_actually_clicked():
    state = _state(
        {"label": "outer_panel", "bbox": [864, 39, 1919, 893]},
        {"label": "Submit & New", "bbox": [1224, 858, 1335, 887]},
        {"label": "Submit", "bbox": [1348, 858, 1442, 887]},
    )
    assert _clicked_element(state, [1262, 871])["label"] == "Submit & New"
    assert _clicked_element(state, [1400, 871])["label"] == "Submit"


def test_no_match_returns_none():
    state = _state({"label": "Submit", "bbox": [1348, 858, 1442, 887]})
    assert _clicked_element(state, [0, 0]) is None


def test_missing_position_returns_none():
    state = _state({"label": "Submit", "bbox": [1348, 858, 1442, 887]})
    assert _clicked_element(state, None) is None
    assert _clicked_element(state, [1]) is None
