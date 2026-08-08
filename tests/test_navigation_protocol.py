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
    find_visible_empty_target, optimal_view_counts,
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


class TestCheckboxFieldsAreFillableTargets:
    """Found live 2026-08-07: a run never reached the Drivers tab, stuck in a
    Policyholder->Vehicle->Coverage->Policyholder cycle. Traced to 23
    consecutive pure-scroll steps on the Coverage tab (18 checkboxes across
    "Additional Coverages" and "Discounts Applied" in
    car_insurance_form_wx.py's _build_coverage_tab) with zero field
    interaction. Root cause: _FILLABLE_TYPES omitted checkbox types entirely,
    so has_visible_empty_target() could never return True for a screen made
    only of checkboxes -- decide() kept returning SCROLL, and the view kept
    genuinely changing (other comboboxes drifting in/out of frame at the
    margins) so the dead-scroll cap never tripped either. ui_observer.py maps
    wx.CheckBox -> "checkbox" (not "checkboxcontrol"), the same
    mapped-vs-raw split that caused the listitem bug earlier this session --
    both spellings are included defensively here and in _SIG_TYPES."""

    def test_checkbox_alone_on_screen_is_recognized_as_a_target(self):
        state = {"elements": [_field("Roadside Assistance", ftype="checkbox")]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is True

    def test_legacy_checkboxcontrol_spelling_also_recognized(self):
        state = {"elements": [_field("GAP Insurance", ftype="checkboxcontrol")]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is True

    def test_decide_waits_instead_of_scrolling_forever_on_a_checkbox_only_screen(self):
        """This is the exact scenario that produced the 23-step scroll stretch:
        a screen with nothing but checkboxes must make decide() return WAIT,
        not SCROLL, so the transformer gets a turn instead of being skipped."""
        state = {"elements": [
            _field("Uninsured/Underinsured Motorist", ftype="checkbox", bbox=(100, 100, 300, 130)),
            _field("Personal Injury Protection (PIP)", ftype="checkbox", bbox=(100, 140, 300, 170)),
            _field("Rental Reimbursement", ftype="checkbox", bbox=(100, 180, 300, 210)),
        ]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.WAIT

    def test_clicked_checkbox_stops_being_a_target_once_marked_attempted(self):
        """Prevents the opposite failure mode: once the transformer has acted
        on a checkbox, attempted_keys must let decide() move on instead of
        re-offering the same box forever."""
        state = {"elements": [_field("Roadside Assistance", ftype="checkbox")]}
        assert has_visible_empty_target(
            state, VIEWPORT_BOTTOM,
            attempted_keys={"roadside assistance"},
            attempt_key_fn=lambda e, els: (e.get("label") or "").lower(),
        ) is False


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

    def test_checkbox_type_contributes_to_the_signature(self):
        """_SIG_TYPES previously said "checkboxcontrol" only, but
        ui_observer.py's _CTRL_TYPE_MAP produces "checkbox" (no suffix) for
        real wx.CheckBox controls -- so checkboxes silently never affected
        the signature at all. Confirms both spellings now count."""
        state_without = {"elements": []}
        state_with = {"elements": [_field("Roadside Assistance", ftype="checkbox",
                                            bbox=(0, 100, 50, 120))]}
        assert (visible_field_signature(state_without, VIEWPORT_BOTTOM)
                != visible_field_signature(state_with, VIEWPORT_BOTTOM))


class TestDecide:
    def test_waits_when_an_empty_target_is_visible(self):
        state = {"elements": [_field("First Name")]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.WAIT

    def test_scrolls_when_nothing_visible_but_a_target_exists_below_the_fold(self):
        """Redesigned 2026-08-08 for the "optimal view" decision (see
        optimal_view_counts): an empty state ({"elements": []}) has best=0
        (nothing reachable anywhere), so it now correctly means ADVANCE_TAB,
        not SCROLL — scrolling can never help if there's genuinely nothing
        below either. SCROLL is only correct when a real target exists
        off-screen (best > 0) but isn't visible yet (cur < best)."""
        state = {"elements": [_field("Underwriter", bbox=(100, 2000, 300, 2030))]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0, max_dead_scrolls=2)
        assert d.action == NavAction.SCROLL

    def test_advances_tab_when_nothing_is_reachable_anywhere(self):
        """No elements at all -> best=0 -> immediately ADVANCE_TAB, even with
        dead_scroll_count=0. Scrolling would never reveal anything, so there's
        no reason to burn a dead-scroll attempt finding that out first."""
        state = {"elements": []}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0, max_dead_scrolls=2)
        assert d.action == NavAction.ADVANCE_TAB

    def test_advances_tab_once_dead_scroll_cap_reached_despite_a_target_below(self):
        """A real target exists below the fold (best > 0) but scrolling has
        repeatedly failed to reach it (e.g. the pane can't move that far) —
        give up once the dead-scroll cap trips instead of spinning forever."""
        state = {"elements": [_field("Underwriter", bbox=(100, 2000, 300, 2030))]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=2, max_dead_scrolls=2)
        assert d.action == NavAction.ADVANCE_TAB

    def test_a_long_tab_can_scroll_as_many_times_as_it_has_real_content(self):
        """dead_scroll_count only counts DEAD scrolls (caller resets it whenever
        a field is visible) — decide() itself doesn't cap total scrolls."""
        state = {"elements": [_field("Field 40")]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=39, max_dead_scrolls=2)
        assert d.action == NavAction.WAIT


class TestOptimalViewCounts:
    """Added 2026-08-08 per direct user correction: "nothing's visible ->
    scroll down once. Wrong... Literally find the optimal view." cur is how
    many actionable targets are visible right now; best is the most that
    could EVER be simultaneously visible at some scroll position — the
    stopping condition for scrolling is cur == best, not cur > 0."""

    def test_cur_equals_best_when_all_targets_already_fit_on_screen(self):
        state = {"elements": [
            _field("First Name", bbox=(100, 100, 300, 130)),
            _field("Last Name", bbox=(100, 140, 300, 170)),
        ]}
        cur, best = optimal_view_counts(state, VIEWPORT_BOTTOM)
        assert (cur, best) == (2, 2)

    def test_best_counts_off_screen_targets_by_position_not_current_visibility(self):
        """Two targets are stacked 50px apart just below the fold — they'd
        both fit in one viewport-sized window once scrolled to, so best
        should be 2 even though cur (what's visible right now) is 0."""
        state = {"elements": [
            _field("Underwriter", bbox=(100, 1010, 300, 1040)),
            _field("Adjuster", bbox=(100, 1060, 300, 1090)),
        ]}
        cur, best = optimal_view_counts(state, VIEWPORT_BOTTOM)
        assert cur == 0
        assert best == 2

    def test_best_is_less_than_total_when_targets_are_spread_too_far_apart(self):
        """Three targets, each pair further apart than one viewport height —
        no single scroll position can ever show more than one at a time."""
        state = {"elements": [
            _field("A", bbox=(100, 100, 300, 130)),
            _field("B", bbox=(100, 100 + VIEWPORT_BOTTOM * 2, 300, 130 + VIEWPORT_BOTTOM * 2)),
            _field("C", bbox=(100, 100 + VIEWPORT_BOTTOM * 4, 300, 130 + VIEWPORT_BOTTOM * 4)),
        ]}
        cur, best = optimal_view_counts(state, VIEWPORT_BOTTOM)
        assert best == 1

    def test_targets_already_scrolled_past_are_excluded_from_best(self):
        """bbox top < 0 means the target is above the current fold — this
        module only scrolls DOWN, so it can never be brought back into view
        and shouldn't inflate the "best achievable" count."""
        state = {"elements": [
            _field("Scrolled Past", bbox=(100, -200, 300, -170)),
            _field("Still Reachable", bbox=(100, 100, 300, 130)),
        ]}
        cur, best = optimal_view_counts(state, VIEWPORT_BOTTOM)
        assert best == 1

    def test_zero_and_zero_when_nothing_fillable_remains(self):
        state = {"elements": [_field("Done", value="filled")]}
        assert optimal_view_counts(state, VIEWPORT_BOTTOM) == (0, 0)


class TestDecidePrefersMaximizingSimultaneousTargets:
    """The core behavioral change: decide() must keep scrolling toward the
    view with the MOST actionable targets, not stop at the first view with
    ANY target — directly what the user asked for over the old "nothing's
    visible -> scroll once" logic."""

    def test_keeps_scrolling_past_a_single_visible_target_toward_a_denser_view(self):
        """One target is already visible (old logic would WAIT here), but
        two more are just below the fold and would ALSO fit in view if
        scrolled to — decide() should prefer reaching that denser view."""
        state = {"elements": [
            _field("Lonely Field", bbox=(100, 50, 300, 80)),
            _field("Packed A", bbox=(100, 1010, 300, 1040)),
            _field("Packed B", bbox=(100, 1060, 300, 1090)),
        ]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.SCROLL

    def test_waits_once_the_current_view_already_matches_the_best_achievable(self):
        """Same field count on screen as anywhere else achievable — this IS
        the optimal view, so decide() must stop scrolling and let the
        transformer act, even though more targets exist further below."""
        state = {"elements": [
            _field("Visible A", bbox=(100, 50, 300, 80)),
            _field("Visible B", bbox=(100, 100, 300, 130)),
            _field("Far Below", bbox=(100, 3000, 300, 3030)),
        ]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.WAIT


class TestFindVisibleEmptyTarget:
    """Added 2026-08-07, directly requested by the user watching a live run
    burn steps on repeated low-confidence Tab-fallbacks: "Whenever there are
    no longer any targets on the screen... activate Navigation Protocol so
    that there will be." has_visible_empty_target only ever answered
    yes/no; a caller stuck with an unconfident pointer needs the actual
    ELEMENT to fall back to, deterministically."""

    def test_returns_the_matching_element(self):
        target = _field("First Name")
        state = {"elements": [target]}
        found = find_visible_empty_target(state, VIEWPORT_BOTTOM)
        assert found is target

    def test_returns_none_when_nothing_matches(self):
        state = {"elements": [_field("First Name", value="Alice")]}
        assert find_visible_empty_target(state, VIEWPORT_BOTTOM) is None

    def test_skips_attempted_and_returns_the_next_real_target(self):
        attempted = _field("Suffix", bbox=(100, 100, 300, 130))
        wanted = _field("Marital Status", bbox=(100, 140, 300, 170))
        state = {"elements": [attempted, wanted]}
        found = find_visible_empty_target(
            state, VIEWPORT_BOTTOM,
            attempted_keys={"suffix"},
            attempt_key_fn=lambda e, els: (e.get("label") or "").lower(),
        )
        assert found is wanted

    def test_has_visible_empty_target_stays_consistent_with_find(self):
        """The bool version is now powered by this one — same answer, no
        drift between the two possible."""
        state_with = {"elements": [_field("First Name")]}
        state_without = {"elements": [_field("First Name", value="Alice")]}
        assert has_visible_empty_target(state_with, VIEWPORT_BOTTOM) is True
        assert find_visible_empty_target(state_with, VIEWPORT_BOTTOM) is not None
        assert has_visible_empty_target(state_without, VIEWPORT_BOTTOM) is False


class TestVisibleFlagTrustsUiaOverGeometry:
    """Found 2026-08-08, live: a run needed zero explicit SCROLL decisions
    from decide() at all (every field's bbox y-coordinate fell within the
    guessed window-rect viewport_bottom), yet the user watched the on-screen
    view visibly creep down one field at a time as each field got clicked --
    the target app's own scroll panel auto-scrolling a newly-focused control
    into view, invisible to this module because it only ever had a
    geometric ESTIMATE of what's on-screen, never the real answer.

    ui_observer.py now reads UIA's own IsOffscreen property into each
    element's "visible" key instead of hardcoding it True. has_visible_
    empty_target / find_visible_empty_target / visible_field_signature now
    all also require e["visible"] (default True, so any state/test that
    doesn't set it is unaffected) -- trusting the authoritative signal
    instead of estimating a second time from window geometry."""

    def test_field_within_geometric_bounds_but_marked_not_visible_is_excluded(self):
        e = _field("Street Address 1", bbox=(100, 500, 300, 530))
        e["visible"] = False
        state = {"elements": [e]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is False
        assert find_visible_empty_target(state, VIEWPORT_BOTTOM) is None

    def test_field_marked_visible_true_is_still_found(self):
        e = _field("Street Address 1", bbox=(100, 500, 300, 530))
        e["visible"] = True
        state = {"elements": [e]}
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is True

    def test_field_with_no_visible_key_defaults_to_visible(self):
        """Existing states/tests that never set 'visible' at all must behave
        exactly as before this fix -- geometry alone still decides."""
        state = {"elements": [_field("First Name")]}
        assert "visible" not in state["elements"][0]
        assert has_visible_empty_target(state, VIEWPORT_BOTTOM) is True

    def test_decide_scrolls_instead_of_waiting_when_the_only_candidate_is_offscreen(self):
        e = _field("Street Address 1", bbox=(100, 500, 300, 530))
        e["visible"] = False
        state = {"elements": [e]}
        d = decide(state, VIEWPORT_BOTTOM, dead_scroll_count=0)
        assert d.action == NavAction.SCROLL

    def test_visible_field_signature_excludes_not_visible_fields(self):
        visible_e = _field("First Name", bbox=(100, 100, 300, 130))
        hidden_e  = _field("Middle Name", bbox=(100, 140, 300, 170))
        hidden_e["visible"] = False
        sig = visible_field_signature({"elements": [visible_e, hidden_e]}, VIEWPORT_BOTTOM)
        labels = {s[0] for s in sig}
        assert "first name" in labels
        assert "middle name" not in labels
