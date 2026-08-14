"""
Tests for components/agent/field_planner.py — the Field Planner.

Built 2026-08-14 as part of "plan-then-replay": FormFillerPlugin already
resolves most fields with zero transformer/LLM calls (UIA SetFocus-by-Name
+ a deterministic lookup-cache hit), but re-derives that decision through a
long guard cascade from scratch on every single step, one field at a time.
field_planner.py scans a whole visible tab ONCE and returns what every
fillable field on it resolves to, so a caller can replay the easy ones
mechanically instead. Pure functions, no side effects — same split
navigation_protocol.py already uses, for the same reason (unit-testable
without a live GUI).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.field_planner import (
    DivergenceStatus, PlannedField, Resolution,
    check_divergence, is_fast_replayable, plan_visible_fields, stable_field_key,
)

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol", **kw):
    e = {"element_id": label, "type": ftype, "label": label, "value": value,
         "bbox": list(bbox), "window_role": "active"}
    e.update(kw)
    return e


def _lookup_factory(table):
    """A lookup_fn mirroring FormFillerPlugin._lookup_field's signature."""
    def _lookup(field_name, section=""):
        key = f"{section} {field_name}".strip() if section else field_name
        return table.get(key, table.get(field_name, ""))
    return _lookup


# ------------------------------------------------------------- plan_visible_fields


class TestPlanVisibleFields:
    def test_lookup_hit_resolves_with_the_cached_value(self):
        state = {"elements": [_field("First Name")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                    lookup_fn=_lookup_factory({"First Name": "Alice"}))
        assert len(plan) == 1
        assert plan[0].resolution == Resolution.LOOKUP_HIT
        assert plan[0].expected_value == "Alice"

    def test_cache_miss_with_no_peek_fn_is_needs_llm(self):
        state = {"elements": [_field("Mystery Field")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan[0].resolution == Resolution.NEEDS_LLM

    def test_two_step_miss_protocol_peek_then_retry_resolves_as_lookup_hit(self):
        """Regression-critical: a single miss almost always just means 'not yet
        peeked', which the EXISTING code already resolves for free (no LLM).
        Marking NEEDS_LLM after only one attempt would silently regress
        fields the current reactive code already handles."""
        table = {}

        def _peek(state, field_name):
            table[field_name] = "Discovered Value"

        state = {"elements": [_field("Underwriter")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                    lookup_fn=_lookup_factory(table), peek_fn=_peek)
        assert plan[0].resolution == Resolution.LOOKUP_HIT
        assert plan[0].expected_value == "Discovered Value"

    def test_miss_after_peek_is_still_needs_llm(self):
        state = {"elements": [_field("Truly Unknown")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                    lookup_fn=_lookup_factory({}), peek_fn=lambda s, f: None)
        assert plan[0].resolution == Resolution.NEEDS_LLM

    def test_leave_blank_sentinel_resolves_as_lookup_blank(self):
        # "(none)" round-trips through .strip("()") to "none", which IS a
        # member of the skip set (mirroring _auto_fill/_lookup_field exactly).
        state = {"elements": [_field("Middle Name")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                    lookup_fn=_lookup_factory({"Middle Name": "(none)"}))
        assert plan[0].resolution == Resolution.LOOKUP_BLANK
        assert plan[0].expected_value == ""

    def test_already_filled_fields_are_excluded(self):
        state = {"elements": [_field("First Name", value="Alice")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan == []

    def test_background_elements_are_excluded(self):
        e = _field("First Name")
        e["window_role"] = "background"
        plan = plan_visible_fields({"elements": [e]}, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan == []

    def test_below_viewport_fields_are_excluded(self):
        state = {"elements": [_field("Underwriter", bbox=(100, 2000, 300, 2030))]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan == []

    def test_non_fillable_types_are_excluded(self):
        state = {"elements": [_field("Submit", ftype="buttoncontrol")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan == []

    def test_attempted_fields_are_excluded(self):
        state = {"elements": [_field("Suffix")]}
        plan = plan_visible_fields(
            state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}),
            attempted_keys={"suffix"}, attempt_key_fn=lambda e, els: (e.get("label") or "").lower(),
        )
        assert plan == []

    def test_section_is_passed_through_to_the_lookup(self):
        state = {"elements": [_field("First Name")]}
        plan = plan_visible_fields(
            state, VIEWPORT_BOTTOM,
            lookup_fn=_lookup_factory({"Driver 2 First Name": "Bob"}),
            section_fn=lambda state, e: "Driver 2",
        )
        assert plan[0].resolution == Resolution.LOOKUP_HIT
        assert plan[0].expected_value == "Bob"
        assert plan[0].section == "Driver 2"

    def test_repeated_label_disambiguation_gives_distinct_stable_keys(self):
        e1 = _field("First Name", bbox=(100, 100, 300, 130))
        e2 = _field("First Name", bbox=(100, 200, 300, 230))
        state = {"elements": [e1, e2]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert len(plan) == 2
        assert plan[0].stable_key != plan[1].stable_key
        assert plan[0].stable_key == ("first name", 0)
        assert plan[1].stable_key == ("first name", 1)

    def test_checkbox_yes_prefix_resolves_lookup_hit(self):
        state = {"elements": [_field("Has Prior Insurance", ftype="checkboxcontrol")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                    lookup_fn=_lookup_factory({"Has Prior Insurance": "Yes"}))
        assert plan[0].resolution == Resolution.LOOKUP_HIT

    def test_checkbox_words_resolve_lookup_hit(self):
        """Mirrors FormFillerPlugin._auto_check's exact parsing:
        ev.startswith("yes") or ev in {"check", "true", "1", "checked"}."""
        for word in ("check", "true", "1", "checked", "Yes", "YES please"):
            state = {"elements": [_field("Opt In", ftype="checkboxcontrol")]}
            plan = plan_visible_fields(state, VIEWPORT_BOTTOM,
                                        lookup_fn=_lookup_factory({"Opt In": word}))
            assert plan[0].resolution == Resolution.LOOKUP_HIT, word

    def test_checkbox_no_resolves_lookup_blank(self):
        state = {"elements": [_field("Opt In", ftype="checkboxcontrol")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({"Opt In": "No"}))
        assert plan[0].resolution == Resolution.LOOKUP_BLANK

    def test_checkbox_cache_miss_is_needs_llm_not_a_guess(self):
        state = {"elements": [_field("Opt In", ftype="checkboxcontrol")]}
        plan = plan_visible_fields(state, VIEWPORT_BOTTOM, lookup_fn=_lookup_factory({}))
        assert plan[0].resolution == Resolution.NEEDS_LLM


# ------------------------------------------------------------- is_fast_replayable


class TestIsFastReplayable:
    def test_editcontrol_is_fast_replayable(self):
        pf = PlannedField("k", "L", "editcontrol", None, "", "v", False, Resolution.LOOKUP_HIT)
        assert is_fast_replayable(pf) is True

    def test_comboboxcontrol_is_not_fast_replayable(self):
        pf = PlannedField("k", "L", "comboboxcontrol", None, "", "v", False, Resolution.LOOKUP_HIT)
        assert is_fast_replayable(pf) is False

    def test_checkboxcontrol_is_not_fast_replayable(self):
        pf = PlannedField("k", "L", "checkboxcontrol", None, "", "v", False, Resolution.LOOKUP_HIT)
        assert is_fast_replayable(pf) is False


# ------------------------------------------------------------- check_divergence


class TestCheckDivergence:
    def test_still_empty_and_present_is_pending(self):
        planned = PlannedField(stable_field_key(_field("First Name")), "First Name",
                                "editcontrol", None, "", "Alice", False, Resolution.LOOKUP_HIT)
        state = {"elements": [_field("First Name")]}
        assert check_divergence(planned, state) == DivergenceStatus.PENDING

    def test_matches_expected_value_is_satisfied(self):
        planned = PlannedField(stable_field_key(_field("First Name")), "First Name",
                                "editcontrol", None, "", "Alice", False, Resolution.LOOKUP_HIT)
        state = {"elements": [_field("First Name", value="Alice")]}
        assert check_divergence(planned, state) == DivergenceStatus.SATISFIED

    def test_vanished_field_is_diverged(self):
        planned = PlannedField(stable_field_key(_field("First Name")), "First Name",
                                "editcontrol", None, "", "Alice", False, Resolution.LOOKUP_HIT)
        state = {"elements": []}
        assert check_divergence(planned, state) == DivergenceStatus.DIVERGED

    def test_unexpected_non_empty_value_is_diverged(self):
        planned = PlannedField(stable_field_key(_field("First Name")), "First Name",
                                "editcontrol", None, "", "Alice", False, Resolution.LOOKUP_HIT)
        state = {"elements": [_field("First Name", value="Someone Else")]}
        assert check_divergence(planned, state) == DivergenceStatus.DIVERGED

    def test_needs_llm_field_with_new_content_is_diverged_not_satisfied(self):
        """No known expected value for a needs_llm field, so any new content
        must be treated conservatively (defer to the existing, tested
        reactive cascade) rather than assumed correct."""
        planned = PlannedField(stable_field_key(_field("Mystery")), "Mystery",
                                "editcontrol", None, "", "", False, Resolution.NEEDS_LLM)
        state = {"elements": [_field("Mystery", value="Something")]}
        assert check_divergence(planned, state) == DivergenceStatus.DIVERGED


# ------------------------------------------------------------- stable_field_key


class TestStableFieldKey:
    def test_unique_label_returns_bare_string(self):
        assert stable_field_key(_field("First Name"), [_field("First Name")]) == "first name"

    def test_no_elements_list_returns_bare_string(self):
        assert stable_field_key(_field("First Name")) == "first name"

    def test_collision_returns_label_rank_tuple(self):
        e1, e2 = _field("First Name"), _field("First Name")
        elements = [e1, e2]
        assert stable_field_key(e1, elements) == ("first name", 0)
        assert stable_field_key(e2, elements) == ("first name", 1)

    def test_unlabeled_element_falls_back_to_geometric_key(self):
        e = _field("", bbox=(40, 60, 80, 100))
        key = stable_field_key(e, [e])
        assert key[0] == "@"

    def test_drift_guard_matches_llmagent_attempt_key(self):
        """A third independent copy of the (label, rank) scheme now exists
        (agent.py, transformer.py, and this one) -- this project's own
        established convention is that such copies must be 'kept in sync'
        (see agent.py:_attempt_key's own docstring). Guard against silent
        drift by asserting identical output on shared fixtures."""
        from agent.agent import LLMAgent
        agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)

        fixtures = [
            [_field("First Name")],
            [_field("First Name"), _field("First Name")],
            [_field("First Name"), _field("First Name"), _field("First Name")],
            [_field("")],
            [_field("Last Name"), _field("First Name")],
        ]
        for elements in fixtures:
            for e in elements:
                assert stable_field_key(e, elements) == agent._attempt_key(e, elements=elements), \
                    (e, elements)
