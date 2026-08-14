"""
Ported from RJGanzon/Intern (coworker's Scope #2 progress), tests/test_rules.py
-- the rule-induction module ported 2026-08-15 alongside components/scope2/
rules/ and components/scope2/recorder/ (see DEVELOPERS.md's
scope2_coworker_codebase_integration entry). Everything here is pure logic
over synthetic inline data (perfect-separation detection, direction/cutoff
induction, option resolution) except the four "Milestone 6 done-when" tests
at the bottom, which need his real recorded demo sessions
(data/demos/v0_6rows.jsonl, v6b_6rows.jsonl) -- deliberately not ported
(those are his own recorded demonstrations, not something to fabricate) --
so those four skip cleanly via needs_sessions rather than failing.

Original docstring, preserved: "Milestone 6: Remarks is correctly derived
from Grade on V0, on both scales. The direction test is the one that
matters. A rule that is right on 0-100 and silently backwards on 1.00-5.00
would mislabel every student on half the instrument, and 3.8 is explicit
that the operator must be induced."

Adapted 2026-08-15 for this project's layout: the sys.path bootstrap points
at components/scope2 instead of a flat repo root, matching test_features.py/
test_matcher_resolver.py's own established convention. DEMOS points at
components/scope2/data/demos (doesn't exist here -- the needs_sessions skip
is expected to fire on every one of those four tests until real session data
is ever recorded and ported).
"""
import sys
from pathlib import Path

import pytest

_SCOPE2 = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(_SCOPE2))

from rules.detect import (  # noqa: E402
    STATUS_AMBIGUOUS, STATUS_NO_DRIVER, STATUS_OK, STATUS_ONE_CLASS,
    detect, separate,
)
from rules.induce import (  # noqa: E402
    STATUS_CONFIRMED, STATUS_PROPOSED, check_against_demonstrations, confirm,
    induce, snap_cutoff,
)
from rules.induce_from_session import induce_from_session  # noqa: E402
from rules.options import resolve_option  # noqa: E402

DEMOS = _SCOPE2 / "data" / "demos"
V0_SESSION = DEMOS / "v0_6rows.jsonl"
V6B_SESSION = DEMOS / "v6b_6rows.jsonl"

needs_sessions = pytest.mark.skipif(
    not (V0_SESSION.exists() and V6B_SESSION.exists()),
    reason="real demo sessions not ported -- see this file's own module docstring",
)


# ------------------------------------------------ 3.8 step 1: detection


def test_perfect_separation_yields_an_interval_not_a_point():
    """Given (88, Passed), (67, Failed), (91, Passed) any cut in (67, 88] is
    consistent - that open range is what the demonstrations actually say."""
    values = {0: 88, 1: 67, 2: 91}
    outcomes = {0: "Passed", 1: "Failed", 2: "Passed"}
    separation = separate(values, outcomes)

    assert separation.clean
    assert separation.low_class == "Failed"
    assert separation.high_class == "Passed"
    assert separation.interval == (67, 88)


def test_overlapping_classes_do_not_separate():
    # Passed at 80 and 90 straddles Failed at 85, so no single cut works.
    values = {0: 80, 1: 85, 2: 90}
    outcomes = {0: "Passed", 1: "Failed", 2: "Passed"}
    assert separate(values, outcomes) is None

    # Neighbouring but non-overlapping values still separate cleanly.
    assert separate({0: 80, 1: 90, 2: 85},
                    {0: "Passed", 1: "Failed", 2: "Passed"}) is not None


def test_all_one_class_is_reported_as_an_insufficient_demonstration():
    """3.8's degenerate case: with no negative example there is no threshold,
    and detecting that is itself a result worth reporting."""
    rows = {0: {"Grade": "85"}, 1: {"Grade": "91"}}
    detection = detect("Remarks", {0: "Passed", 1: "Passed"}, rows)

    assert detection.status == STATUS_ONE_CLASS
    assert "at least one row of the other outcome" in detection.reason
    assert induce(detection) is None


def test_several_equally_good_drivers_abstain_rather_than_guess():
    """3.8: 'if several fields separate the classes equally well, do not
    guess - abstain and ask'."""
    rows = {
        0: {"Grade": "85", "Year": "3"},
        1: {"Grade": "60", "Year": "1"},
    }
    detection = detect("Remarks", {0: "Passed", 1: "Failed"}, rows)

    assert detection.status == STATUS_AMBIGUOUS
    assert {d.driver_label for d in detection.drivers} == {"Grade", "Year"}
    assert induce(detection) is None


def test_a_single_clean_driver_is_found():
    rows = {
        0: {"Grade": "85", "Year": "3"},
        1: {"Grade": "60", "Year": "3"},
        2: {"Grade": "91", "Year": "2"},
    }
    detection = detect("Remarks", {0: "Passed", 1: "Failed", 2: "Passed"}, rows)

    assert detection.status == STATUS_OK
    assert detection.driver.driver_label == "Grade"


def test_no_numeric_driver_is_reported_not_forced():
    rows = {0: {"Course": "BS CS"}, 1: {"Course": "BS IT"}}
    detection = detect("Remarks", {0: "Passed", 1: "Failed"}, rows)

    assert detection.status == STATUS_NO_DRIVER
    assert induce(detection) is None


# --------------------------------------- 3.8 step 2: direction and cutoff


def test_cutoff_snaps_to_a_round_value_inside_the_interval():
    assert snap_cutoff(74, 85) == 75          # multiple of 5 preferred
    assert snap_cutoff(2.2, 3.03, upper_closed=False) == 3.0
    # No round value available: fall back to the midpoint.
    assert snap_cutoff(74.2, 74.6) == pytest.approx(74.4)


def test_snap_respects_which_end_of_the_interval_is_closed():
    """A cutoff on the wrong open end contradicts a demonstrated row."""
    # ">=" : (low, high] - low itself was demonstrated to fail.
    assert snap_cutoff(75, 80, upper_closed=True) == 80
    # "<=" : [low, high) - high itself was demonstrated to fail.
    assert snap_cutoff(75, 80, upper_closed=False) == 75


def test_direction_is_induced_from_the_data_not_assumed():
    rows = {0: {"Grade": "85"}, 1: {"Grade": "60"}, 2: {"Grade": "91"}}
    ascending = detect("Remarks", {0: "Passed", 1: "Failed", 2: "Passed"}, rows)
    rule = induce(ascending, options=["Passed", "Failed"])
    assert rule.operator == ">="
    assert rule.if_true == "Passed"

    # The same demonstration on a scale where low is good must flip.
    rows = {0: {"Grade": "1.75"}, 1: {"Grade": "4.00"}, 2: {"Grade": "1.25"}}
    descending = detect("Remarks", {0: "Passed", 1: "Failed", 2: "Passed"}, rows)
    flipped = induce(descending, options=["Passed", "Failed"])
    assert flipped.operator == "<="
    assert flipped.if_true == "Passed"


def test_a_rule_must_replay_the_rows_it_was_induced_from():
    rows = {0: {"Grade": "85"}, 1: {"Grade": "60"}, 2: {"Grade": "91"}}
    detection = detect("Remarks", {0: "Passed", 1: "Failed", 2: "Passed"}, rows)
    rule = induce(detection, options=["Passed", "Failed"])
    assert check_against_demonstrations(rule, detection) == []


# ------------------------------------------- 3.8 step 3: confirmation


def test_a_rule_starts_proposed_and_is_never_executable_until_confirmed():
    """3.8: 'never execute an unconfirmed threshold rule'."""
    rows = {0: {"Grade": "85"}, 1: {"Grade": "60"}}
    detection = detect("Remarks", {0: "Passed", 1: "Failed"}, rows)
    rule = induce(detection, options=["Passed", "Failed"])

    assert rule.status == STATUS_PROPOSED
    assert confirm(rule).status == STATUS_CONFIRMED


def test_a_corrected_cutoff_must_stay_inside_what_was_demonstrated():
    rows = {0: {"Grade": "85"}, 1: {"Grade": "60"}}
    detection = detect("Remarks", {0: "Passed", 1: "Failed"}, rows)
    rule = induce(detection, options=["Passed", "Failed"])

    confirm(rule, 80)
    assert rule.cutoff == 80
    with pytest.raises(ValueError, match="outside the demonstrated interval"):
        confirm(rule, 95)


def test_the_description_shows_the_interval_not_just_the_number():
    rows = {0: {"Grade": "85"}, 1: {"Grade": "60"}}
    detection = detect("Remarks", {0: "Passed", 1: "Failed"}, rows)
    text = induce(detection, options=["Passed", "Failed"]).describe()

    assert "Passed" in text and "Grade" in text
    assert "only narrow the cutoff to between" in text


# ------------------------------------------ 3.8 step 4: option matching


def test_an_outcome_resolves_to_the_option_the_form_actually_offers():
    assert resolve_option("Passed", ["Passed", "Failed"]).method == "exact"
    assert resolve_option("Passed", ["PASSED", "FAILED"]).option == "PASSED"


def test_option_matching_escalates_rather_than_picking_the_nearest():
    """3.10: 'if no option clears the similarity threshold, escalate rather
    than pick the nearest'."""
    unresolved = resolve_option("Passed", ["INC", "DRP", "FA"])
    assert not unresolved.resolved
    assert unresolved.reason

    assert not resolve_option("Passed", []).resolved
    assert not resolve_option("", ["Passed"]).resolved


# ---------------------------------------------- Milestone 6 done-when


@needs_sessions
def test_remarks_is_derived_from_grade_on_the_0_100_scale():
    results, _ = induce_from_session(V0_SESSION, auto_confirm=True)
    entry = next(e for e in results if e["field"] == "Remarks")
    rule = entry["rule"]

    assert entry["detection"].status == STATUS_OK
    assert rule.depends_on_field.startswith("Grade")
    assert rule.operator == ">="
    assert rule.cutoff == 75          # the sheet's true passing mark
    assert rule.if_true == "Passed"
    assert rule.status == STATUS_CONFIRMED
    assert entry["failures"] == []

    low, high = rule.observed_interval
    assert low < rule.cutoff <= high


@needs_sessions
def test_remarks_is_derived_from_grade_on_the_1_to_5_scale():
    """The same demonstration on the inverted scale must produce the opposite
    operator and the scale's own passing mark."""
    results, _ = induce_from_session(V6B_SESSION, auto_confirm=True)
    entry = next(e for e in results if e["field"] == "Remarks")
    rule = entry["rule"]

    assert entry["detection"].status == STATUS_OK
    assert rule.depends_on_field.startswith("Grade")
    assert rule.operator == "<="      # inverted, and induced rather than assumed
    assert rule.cutoff == 3.0         # 3.00 passes on the 1.00-5.00 scale
    assert rule.if_true == "Passed"
    assert entry["failures"] == []


@needs_sessions
def test_both_scales_resolve_their_options():
    for session in (V0_SESSION, V6B_SESSION):
        results, _ = induce_from_session(session, auto_confirm=True)
        entry = next(e for e in results if e["field"] == "Remarks")
        assert entry["options"]
        for outcome, resolution in entry["options"].items():
            assert resolution.resolved, f"{session.name}: {outcome} unresolved"


@needs_sessions
def test_the_rule_is_stated_against_a_field_never_a_sheet_column():
    """2.4: depends_on_field names a form field, so the rule survives its
    driver's source column being relabelled."""
    results, reconciliation = induce_from_session(V0_SESSION, auto_confirm=True)
    rule = next(e for e in results if e["field"] == "Remarks")["rule"]

    headers = {p.source_header for p in reconciliation.pairs}
    labels = {p.target_label for p in reconciliation.pairs}
    assert rule.depends_on_field in labels
    assert rule.depends_on_field not in headers
