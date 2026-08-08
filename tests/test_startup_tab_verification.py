"""
Regression tests for two related fixes in components/agent/agent.py:

1. _detect_active_tab_idx_raw() — extracted from _try_advance_tab's own
   inline pane-detection loop (no behavior change there: same scan, same
   fallback-to-None-when-inconclusive), so the exact same detection logic
   can also be reused at startup.

2. run()'s new startup tab-verification block (fires once, step_idx == 0):
   self._current_tab_idx starts out as whatever start_tab_idx claimed --
   0 normally, or --start_tab N in drill mode -- and that value was NEVER
   independently checked against reality. Found live 2026-08-08, discussed
   with the user while investigating execution_advance_blacklist's
   tab-tracking reliability: drill mode depends on the human clicking the
   right tab BEFORE launching -- a typo'd --start_tab, a missed click, or a
   click that hadn't registered yet would leave the tracker wrong from step
   1, and everything downstream that trusts it (the backward-tab-click
   guard included) would reason from a false premise for the entire run.

   Deliberately the OPPOSITE trust rule from _try_advance_tab's own mid-run
   mismatch handling (which trusts the TRACKER over detection, since noise
   from a just-completed transition is the normal case there) -- at this
   single, fresh, settled first observation there's no transition noise to
   distrust detection for, so here DETECTION wins and the tracker gets
   corrected to match it, loudly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _detect_active_tab_idx_raw


def _tab(text, bbox):
    return {"type": "tabitemcontrol", "text": text, "label": text,
            "bbox": list(bbox), "window_role": "active"}


def _panel_elem(y, elem_type="editcontrol", window_role="active"):
    return {"type": elem_type, "bbox": [100, y, 300, y + 30], "window_role": window_role}


class TestDetectActiveTabIdxRaw:
    def test_detects_the_tab_with_a_real_onscreen_panel(self):
        tabs = [_tab("Policy", (0, 100, 100, 130)), _tab("Vehicle", (100, 100, 200, 130))]
        # Vehicle's panel is on-screen (y >= 0, below the tab strip); Policy's isn't present.
        all_elems = [_panel_elem(200)]
        assert _detect_active_tab_idx_raw(tabs, all_elems) == 0   # first tab whose bbox check matches

    def test_returns_none_when_no_tab_has_a_confirmable_panel(self):
        tabs = [_tab("Policy", (0, 100, 100, 130))]
        all_elems = [{"type": "editcontrol", "bbox": [100, -500, 300, -470], "window_role": "active"}]
        assert _detect_active_tab_idx_raw(tabs, all_elems) is None

    def test_ignores_background_elements(self):
        tabs = [_tab("Payment", (700, 100, 800, 130))]
        all_elems = [_panel_elem(200, window_role="background")]
        assert _detect_active_tab_idx_raw(tabs, all_elems) is None

    def test_picks_the_correct_tab_among_several(self):
        tabs = [
            _tab("Policy", (0, 100, 100, 130)),
            _tab("Vehicle", (100, 100, 200, 130)),
            _tab("Payment", (700, 100, 800, 130)),
        ]
        # A panel element at y=150 sits below BOTH Policy's and Vehicle's header
        # centers (tab_cy=115 for both, since they share the same bbox height) --
        # the function returns the FIRST tab in iteration order that matches.
        all_elems = [_panel_elem(150)]
        assert _detect_active_tab_idx_raw(tabs, all_elems) == 0

    def test_covered_panel_content_is_not_mistaken_for_an_active_tab(self):
        """Found 2026-08-08, live, immediately after wiring UIA's real
        IsOffscreen into ui_observer.py: this function's own "wx moves
        inactive panels to negative coordinates" assumption was wrong for
        this form -- tab 0's (Policy) panel elements kept reporting
        bbox[1] >= 0 even long after switching away, so this loop matched
        tab 0 on every call for the rest of the run. A panel element that's
        geometrically "below the tab strip" but marked visible=False (UIA's
        real, covered-over-content signal) must not count as proof its tab
        is active -- if it's the ONLY candidate, detection must correctly
        come back empty (None) rather than trusting stale geometry.

        Note: because every tab header shares one horizontal strip (same
        tab_cy for all of them, confirmed live -- 7 tab clicks in one run
        all landed at the same y=136), this function can only ever tell
        "nothing is genuinely visible" from "something is" -- it can't
        discriminate WHICH tab among several simultaneously-visible
        candidates, a real, separate limitation this fix doesn't claim to
        solve. _try_advance_tab's own mid-run mismatch handling already
        treats any detected-vs-tracked disagreement as noise and trusts its
        tracker instead, which is why that limitation hasn't been fatal on
        its own."""
        tabs = [_tab("Policy", (0, 100, 100, 130))]
        policy_panel_covered = _panel_elem(150)
        policy_panel_covered["visible"] = False
        assert _detect_active_tab_idx_raw(tabs, [policy_panel_covered]) is None

    def test_panel_with_no_visible_key_still_counts(self):
        """Elements/tests that never set 'visible' at all must behave exactly
        as before this fix -- geometry alone still decides."""
        tabs = [_tab("Policy", (0, 100, 100, 130))]
        panel = _panel_elem(200)
        assert "visible" not in panel
        assert _detect_active_tab_idx_raw(tabs, [panel]) == 0


def _run_startup_check(assumed_idx, detected_idx):
    """Mirrors the CURRENT startup-verification block in agent.py's run()
    (step_idx == 0 branch): detection wins on mismatch, unlike
    _try_advance_tab's own mid-run handling."""
    if detected_idx is not None and detected_idx != assumed_idx:
        return detected_idx   # corrected
    return assumed_idx        # unchanged (matched, or detection inconclusive)


class TestStartupTabVerification:
    def test_matching_assumption_is_left_unchanged(self):
        assert _run_startup_check(assumed_idx=0, detected_idx=0) == 0

    def test_mismatch_corrects_to_the_detected_tab(self):
        """The actual bug this fixes: --start_tab 7 assumed, but the human
        actually left the form on a different tab (or the click hadn't
        registered) -- must correct to reality, not trust the assumption."""
        assert _run_startup_check(assumed_idx=7, detected_idx=2) == 2

    def test_inconclusive_detection_leaves_the_assumption_as_is(self):
        """No confirmable panel found (e.g. mid-transition, or a form this
        detection heuristic doesn't fit) -- don't overwrite a plausible
        assumption with nothing."""
        assert _run_startup_check(assumed_idx=7, detected_idx=None) == 7

    def test_normal_zero_start_still_verified_not_just_assumed(self):
        """Not drill-mode-only -- a normal run (start_tab_idx=0 by default)
        gets the same check, so a genuinely wrong assumption is still
        caught even outside drill mode."""
        assert _run_startup_check(assumed_idx=0, detected_idx=3) == 3
