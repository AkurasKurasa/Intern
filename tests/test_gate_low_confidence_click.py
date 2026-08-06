"""
Regression test for agent._gate_low_confidence_click().

Bug this locks down: the transformer's click-pointer confidence (ptr_conf)
was already being computed and logged on every click, but never acted on —
a live run clicked the tab strip with ptr_conf=0.20 and ended a tab pass
9 fields early (Policy: only 4 of 13 fields touched) because nothing ever
declined a low-confidence guess. Found 2026-08-07.

This does NOT hand WHERE-decisions to the LLM (forbidden in pure/Option-B
mode) — it only declines to act on an unreliable guess, falling back to the
same generic Tab-advance already used for a structurally invalid pointer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

from agent.agent import _gate_low_confidence_click, _CLICK_CONF_FLOOR


def test_low_confidence_plausible_click_is_gated_to_none():
    assert _gate_low_confidence_click([984, 136], 0.20) is None


def test_high_confidence_click_passes_through_unchanged():
    pos = [984, 136]
    assert _gate_low_confidence_click(pos, 0.92) is pos


def test_confidence_exactly_at_the_floor_passes_through():
    pos = [984, 136]
    assert _gate_low_confidence_click(pos, _CLICK_CONF_FLOOR) is pos


def test_structurally_invalid_pos_passes_through_regardless_of_confidence():
    # Nothing to gate — the caller's own "pointer invalid" check handles this.
    assert _gate_low_confidence_click(None, 0.0) is None
    assert _gate_low_confidence_click([0, 0], 0.99) == [0, 0]


def test_matches_the_real_live_failure_case():
    # The exact values from the live run that motivated this fix.
    assert _gate_low_confidence_click([995, 134], 0.29) is None
    assert _gate_low_confidence_click([1460, 250], 0.21) is None
