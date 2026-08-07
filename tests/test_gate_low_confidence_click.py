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

EXTENDED 2026-08-07: the general 0.30 floor wasn't enough — twice live, the
model clicked away to the Vehicle tab (skipping unfilled Policyholder
fields) at confidence 0.38-0.39, comfortably above 0.30. A wrong tab-strip
click is far more costly than a wrong same-tab field click (it skips an
entire tab, not one step), so tab-strip clicks now need a stricter floor
(_TAB_CLICK_CONF_FLOOR, 0.50) — general, not specific to which tab.

EXTENDED AGAIN 2026-08-07: confidence alone couldn't catch every stuck loop.
A live run clicked the SAME empty, optional combobox 30+ times at HIGH
confidence (0.91) — correctly recognized as "nothing to fill" every single
time, but the handling code `continue`d immediately, bypassing the general
repeat-action guard. Added a `blacklist` param: once a position is proven
unproductive (tracked by the caller), the gate rejects it outright
regardless of confidence.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

from agent.agent import (
    _gate_low_confidence_click, _is_tab_strip_click, _round_click_pos,
    _CLICK_CONF_FLOOR, _TAB_CLICK_CONF_FLOOR,
)


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


_VEHICLE_TAB = {"type": "tabitemcontrol", "window_role": "active",
                "bbox": [1050, 120, 1150, 150], "label": "Vehicle"}
_SOME_FIELD  = {"type": "editcontrol", "window_role": "active",
                "bbox": [200, 400, 500, 430], "label": "First Name"}


class TestTabStripStricterFloor:
    def test_matches_the_second_real_live_failure_case(self):
        # ptr_conf=0.38-0.39 clicking the Vehicle tab, twice live, skipping
        # unfilled Policyholder fields — both cleared the old 0.30 floor.
        pos = [1074, 134]
        elements = [_VEHICLE_TAB, _SOME_FIELD]
        assert _gate_low_confidence_click(pos, 0.39, elements=elements) is None
        assert _gate_low_confidence_click(pos, 0.38, elements=elements) is None

    def test_high_confidence_tab_click_still_passes(self):
        pos = [1074, 134]
        elements = [_VEHICLE_TAB, _SOME_FIELD]
        assert _gate_low_confidence_click(pos, 0.85, elements=elements) is pos

    def test_same_confidence_still_passes_for_a_non_tab_click(self):
        # 0.39 is below the TAB floor but clears the general field floor —
        # only tab-strip clicks get the stricter bar.
        pos = [350, 415]
        elements = [_VEHICLE_TAB, _SOME_FIELD]
        assert _gate_low_confidence_click(pos, 0.39, elements=elements) is pos

    def test_without_elements_falls_back_to_the_general_floor(self):
        # Backward-compatible default for any call site that can't supply
        # elements — same behavior as before this fix existed.
        pos = [1074, 134]
        assert _gate_low_confidence_click(pos, 0.39) is pos


class TestIsTabStripClick:
    def test_true_when_position_is_inside_a_tab_bbox(self):
        assert _is_tab_strip_click([1074, 134], [_VEHICLE_TAB]) is True

    def test_false_when_position_is_outside_every_tab_bbox(self):
        assert _is_tab_strip_click([350, 415], [_VEHICLE_TAB]) is False

    def test_false_for_no_position_or_no_elements(self):
        assert _is_tab_strip_click(None, [_VEHICLE_TAB]) is False
        assert _is_tab_strip_click([1074, 134], None) is False

    def test_ignores_background_tab_elements(self):
        bg_tab = dict(_VEHICLE_TAB, window_role="background")
        assert _is_tab_strip_click([1074, 134], [bg_tab]) is False


class TestBlacklistOverridesConfidence:
    def test_matches_the_real_live_failure_case(self):
        # The exact bug: HIGH confidence (0.91), same combobox, 30+ times.
        pos = [1493, 320]
        blacklist = {_round_click_pos(pos)}
        assert _gate_low_confidence_click(pos, 0.91, blacklist=blacklist) is None

    def test_blacklisted_position_rejected_even_with_max_confidence(self):
        pos = [1493, 320]
        blacklist = {_round_click_pos(pos)}
        assert _gate_low_confidence_click(pos, 1.0, blacklist=blacklist) is None

    def test_nearby_jitter_still_matches_the_blacklist_bucket(self):
        # 10px bucketing — a couple pixels of pointer noise between two
        # predictions of "the same" click must not dodge the blacklist.
        blacklist = {_round_click_pos([1493, 320])}
        assert _gate_low_confidence_click([1491, 318], 0.91, blacklist=blacklist) is None

    def test_unblacklisted_position_unaffected(self):
        pos = [700, 500]
        blacklist = {_round_click_pos([1493, 320])}
        assert _gate_low_confidence_click(pos, 0.91, blacklist=blacklist) is pos

    def test_no_blacklist_behaves_as_before(self):
        pos = [1493, 320]
        assert _gate_low_confidence_click(pos, 0.91) is pos
        assert _gate_low_confidence_click(pos, 0.91, blacklist=set()) is pos


class TestRoundClickPos:
    def test_buckets_to_the_nearest_10px(self):
        assert _round_click_pos([1493, 320]) == (1490, 320)
        assert _round_click_pos([1495, 324]) == (1500, 320)

    def test_matches_the_bucketing_merge_already_uses(self):
        # Same formula as _merge()'s own no_change blacklist — a shared
        # convention, not a coincidence.
        pos = [1456.7, 389.9]
        assert _round_click_pos(pos) == (round(pos[0] / 10) * 10, round(pos[1] / 10) * 10)
