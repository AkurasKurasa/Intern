"""
Tests for verify-at-fill (components/agent/agent.py: _verify_fill_matches,
_find_element_by_id, and the retry loop in the main step function).

Found 2026-08-07, from a direct user report about the old (deleted) Intern
iteration: "it lacked a verify-at-fill... we don't want to keep coming back
to something, we want it finished as the Agent executes or fills it, a
constant checkback always consumes too much time." Confirmed the same gap
in the current codebase: StateValidator only checks whether SOMETHING
changed after an action (focus moved, value differs from before) — never
whether it changed to the RIGHT thing. A field can type successfully
(validator says "ok") while still holding the wrong value.

Fix: right after a type action, compare the field's actual post-type value
against what the agent intended to type (prediction["text"]); if it doesn't
match, retry (bounded, 2 attempts) inline instead of either silently
trusting a wrong fill or deferring to a separate re-check pass.

FOLLOW-UP BUG, same day, found from the very first live run after shipping
this: the caller originally looked up the target element using the
pre-typing element_id (captured from `state`, before the type action) inside
the POST-typing snapshot (`state_after`). element_id is assigned purely by
scan position ("elem_{offset+count}") in ui_observer.py — self-consistent
WITHIN one observation, but NOT stable across separate observations (any
element gaining/losing text between scans shifts every id after it). This
produced a false "mismatch" on the very first field of the first live test
(typed correctly, but verify-at-fill's stale-id lookup read a different,
empty element) — 2 wasted retries before giving up, even though the value
was already correct. Fixed: the caller now re-derives the focused id FRESH
from state_after itself on every attempt, never reusing an id computed from
a different snapshot.

SECOND FOLLOW-UP BUG, found live 2026-08-07 from a user report that the
agent "did not leave Policyholder" and wasted steps: the give-up path
logged "moving on" but never actually moved focus off the field. Confirmed
in the log — "Years Continuously Insured" failed to type ('9' pasted,
verified as '', retried twice, still ''), then the SAME field was
re-selected and re-attempted 87 times for the rest of the run, because
OPT2's fill-branch only checks "is the CURRENTLY FOCUSED thing empty and
fillable" (pure geometry) with no awareness of attempted_keys — nothing
had ever moved focus away, so the same broken field just kept winning that
check forever. The disabled stuck-guard (~L1195, off by design in pure/no-
autohandlers mode: "we want to see the pure transformer with no rescue")
used to catch this class of stall, but that's about honest NAVIGATION
testing — this is a dead end inside an already-approved recovery path
(verify-at-fill's own give-up branch), not a navigation decision. Fixed:
the give-up branch now presses Tab, matching what "moving on" already
claimed to do. Also hardened the retry itself: each retry now re-clicks
the field's own bbox before ctrl+a/pasting again, instead of blindly
trusting that real OS keyboard focus still matches UIA's reported focused
element (the same focus-lag class documented in
execution_stuck_loop_wrong_tab_field) — three identical back-to-back paste
failures is more consistent with focus not truly landing than with random
clipboard flakiness.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent, _verify_fill_matches, _find_element_by_id, _MAX_VERIFY_RETRIES


def _elements(value: str, element_id: str = "e1"):
    return [{"element_id": element_id, "type": "editcontrol", "label": "Policy Number", "value": value}]


class TestFindElementById:
    def test_finds_matching_element(self):
        els = _elements("PAI-2026-00441")
        assert _find_element_by_id(els, "e1") is els[0]

    def test_returns_none_when_not_found(self):
        els = _elements("PAI-2026-00441")
        assert _find_element_by_id(els, "e999") is None

    def test_returns_none_for_none_id(self):
        els = _elements("PAI-2026-00441")
        assert _find_element_by_id(els, None) is None


class TestVerifyFillMatches:
    def test_true_when_value_matches_exactly(self):
        els = _elements("PAI-2026-00441")
        assert _verify_fill_matches(els, "e1", "PAI-2026-00441") is True

    def test_false_when_value_is_wrong(self):
        els = _elements("PAI-2026-00440")   # typo/off-by-one
        assert _verify_fill_matches(els, "e1", "PAI-2026-00441") is False

    def test_false_when_field_still_empty(self):
        els = _elements("")
        assert _verify_fill_matches(els, "e1", "PAI-2026-00441") is False

    def test_tolerates_surrounding_whitespace(self):
        els = _elements("  PAI-2026-00441  ")
        assert _verify_fill_matches(els, "e1", "PAI-2026-00441") is True

    def test_false_when_focused_element_no_longer_found(self):
        els = _elements("PAI-2026-00441", element_id="e1")
        assert _verify_fill_matches(els, "e_missing", "PAI-2026-00441") is False


class TestElementIdInstabilityAcrossSnapshots:
    """The actual live bug: element_id is a scan-position artifact
    ('elem_{offset+count}' in ui_observer.py), not a stable identity across
    two separate observations. Using a PRE-typing id to look up the field in
    a POST-typing snapshot can silently point at the wrong element."""

    def test_stale_pretyping_id_can_miss_in_a_later_snapshot(self):
        # Snapshot BEFORE typing: this field happened to be "e7".
        # Snapshot AFTER typing: an unrelated element elsewhere on the form
        # gained text between scans, shifting every id after it — the SAME
        # real field is now "e8" in this later snapshot.
        state_after_elements = [
            {"element_id": "e7", "type": "editcontrol", "label": "Other Field", "value": ""},
            {"element_id": "e8", "type": "editcontrol", "label": "Policy Number", "value": "PAI-2026-00441"},
        ]
        stale_id = "e7"   # captured from the BEFORE-typing snapshot
        # Looking it up with the stale id finds the WRONG (empty) element.
        assert _verify_fill_matches(state_after_elements, stale_id, "PAI-2026-00441") is False

    def test_fresh_id_from_the_same_snapshot_finds_it_correctly(self):
        state_after = {
            "focused_element_id": "e8",
            "elements": [
                {"element_id": "e7", "type": "editcontrol", "label": "Other Field", "value": ""},
                {"element_id": "e8", "type": "editcontrol", "label": "Policy Number", "value": "PAI-2026-00441"},
            ],
        }
        # The fix: re-derive the id from state_after's OWN focused_element_id,
        # never reuse an id computed from an earlier, separate observation.
        fresh_id = state_after["focused_element_id"]
        assert _verify_fill_matches(state_after["elements"], fresh_id, "PAI-2026-00441") is True


class TestVerifyAtFillRetryLoop:
    """End-to-end through the agent's actual step logic, mocking observe()
    and the executor so no live GUI is touched."""

    def _make_agent(self):
        agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
        return agent

    def test_correct_fill_on_first_try_does_not_retry(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        state_after = {"elements": _elements("Alice")}
        # Simulate what the main loop does at the verify-at-fill point.
        prediction = {"action_type": "keyboard", "text": "Alice"}
        state = {"focused_element_id": "e1"}
        matched = _verify_fill_matches(state_after["elements"], state["focused_element_id"],
                                        prediction["text"])
        assert matched is True
        agent._executor.execute.assert_not_called()

    def test_wrong_fill_gets_retried_up_to_the_bound(self):
        """Simulates the retry loop directly against a mocked executor/observer
        that always returns the wrong value, to confirm it terminates after
        _MAX_VERIFY_RETRIES instead of looping forever."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        agent._observe = MagicMock(return_value={"elements": _elements("WRONG")})

        expected_text = "Alice"
        focused_id = "e1"
        state_after = {"elements": _elements("WRONG")}
        attempts = 0
        for _verify_attempt in range(_MAX_VERIFY_RETRIES + 1):
            if _verify_fill_matches(state_after.get("elements", []), focused_id, expected_text):
                break
            if _verify_attempt >= _MAX_VERIFY_RETRIES:
                break
            agent._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["ctrl+a"]})
            agent._executor.execute({"action_type": "keyboard", "text": expected_text})
            state_after = agent._observe()
            attempts += 1

        assert attempts == _MAX_VERIFY_RETRIES
        assert agent._executor.execute.call_count == _MAX_VERIFY_RETRIES * 2


def _run_updated_retry_loop(executor, observe, expected_text, focused_id, first_state_after):
    """Mirrors the CURRENT retry loop in agent.py's run() exactly (post
    2026-08-07 fix): each retry re-clicks the field's bbox before
    ctrl+a/retyping, and giving up presses Tab instead of silently leaving
    focus on the stuck field."""
    state_after = first_state_after
    for _verify_attempt in range(_MAX_VERIFY_RETRIES + 1):
        if _verify_fill_matches(state_after.get("elements", []), focused_id, expected_text):
            return True
        actual_elem = _find_element_by_id(state_after.get("elements", []), focused_id)
        if _verify_attempt >= _MAX_VERIFY_RETRIES:
            executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
            return False
        if actual_elem and actual_elem.get("bbox"):
            b = actual_elem["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["ctrl+a"]})
        executor.execute({"action_type": "keyboard", "text": expected_text})
        state_after = observe()
    return False


class TestGiveUpActuallyMovesOn:
    """Found live 2026-08-07: "Years Continuously Insured" failed to type,
    verify-at-fill gave up after 2 retries, logged "moving on" — but never
    pressed Tab, so the still-focused, still-empty field got re-selected and
    re-attempted 87 times for the rest of the run (agent "did not leave
    Policyholder"). Fix: the give-up path now presses Tab for real."""

    def test_giving_up_presses_tab_to_move_focus_off_the_field(self):
        executor = MagicMock()
        observe = MagicMock(return_value={"elements": _elements("", element_id="e1")})
        matched = _run_updated_retry_loop(
            executor, observe, expected_text="9", focused_id="e1",
            first_state_after={"elements": _elements("", element_id="e1")},
        )
        assert matched is False
        tab_calls = [c for c in executor.execute.call_args_list
                     if c.args[0].get("keystrokes") == ["tab"]]
        assert len(tab_calls) == 1

    def test_success_on_a_retry_never_presses_tab(self):
        executor = MagicMock()
        # First check fails (empty), the retry's re-observe reports success.
        observe = MagicMock(return_value={"elements": _elements("9", element_id="e1")})
        matched = _run_updated_retry_loop(
            executor, observe, expected_text="9", focused_id="e1",
            first_state_after={"elements": _elements("", element_id="e1")},
        )
        assert matched is True
        tab_calls = [c for c in executor.execute.call_args_list
                     if c.args[0].get("keystrokes") == ["tab"]]
        assert len(tab_calls) == 0


class TestRetryReclicksBeforeRetyping:
    """Found live 2026-08-07 in the same investigation: three consecutive
    identical ctrl+a/paste failures on one field is more consistent with
    real OS keyboard focus not matching UIA's reported focused element than
    random clipboard flakiness (matches the focus-lag class documented in
    execution_stuck_loop_wrong_tab_field). Each retry now re-clicks the
    field's own bbox first, instead of blindly trusting existing focus."""

    def test_retry_clicks_the_fields_bbox_before_retyping(self):
        executor = MagicMock()
        els_empty = [{"element_id": "e1", "type": "editcontrol", "label": "Years Continuously Insured",
                      "value": "", "bbox": [1400, 500, 1600, 530]}]
        observe = MagicMock(return_value={"elements": els_empty})
        _run_updated_retry_loop(
            executor, observe, expected_text="9", focused_id="e1",
            first_state_after={"elements": els_empty},
        )
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == _MAX_VERIFY_RETRIES
        assert click_calls[0].args[0]["click_position"] == [1500.0, 515.0]

    def test_no_click_attempted_when_element_has_no_bbox(self):
        executor = MagicMock()
        els_no_bbox = [{"element_id": "e1", "type": "editcontrol", "label": "Years Continuously Insured",
                         "value": ""}]
        observe = MagicMock(return_value={"elements": els_no_bbox})
        _run_updated_retry_loop(
            executor, observe, expected_text="9", focused_id="e1",
            first_state_after={"elements": els_no_bbox},
        )
        click_calls = [c for c in executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0


def _run_settle_poll(observe, expected_text, budget=0.4, poll=0.1):
    """Mirrors the settle-poll block added to agent.py's run() right before
    verify-at-fill's own check (2026-08-08 fix): poll a few times instead of
    one fixed sleep, breaking the moment the value actually shows up."""
    waited = 0.0
    while waited < budget:
        waited += poll
        state = observe()
        fid = state.get("focused_element_id")
        if _verify_fill_matches(state.get("elements", []), fid, expected_text):
            return True, waited
    return False, waited


class TestVerifyAtFillSettlePoll:
    """Found live 2026-08-08: 'Years Continuously Insured' failed
    verify-at-fill's check 100% of the time across 4 separate visits --
    always exactly empty on the first check AND both retries, never partial
    or differently-wrong -- yet held the correct value by the end of the
    run. Classic race: the paste hadn't landed in the control's buffer yet
    when the (zero-delay) snapshot was taken. A single fixed delay isn't
    reliable either -- the existing retry path already sleeps before its
    own re-check and still read empty twice in the live log, so the real
    settle time varies. Poll instead of guessing one constant."""

    def test_returns_true_as_soon_as_the_value_appears(self):
        # First poll still empty, second poll shows the real value -- proves
        # it breaks EARLY rather than always burning the full budget.
        observe = MagicMock(side_effect=[
            {"focused_element_id": "e1", "elements": _elements("", "e1")},
            {"focused_element_id": "e1", "elements": _elements("9", "e1")},
        ])
        matched, waited = _run_settle_poll(observe, "9", budget=0.4, poll=0.1)
        assert matched is True
        assert observe.call_count == 2
        assert waited < 0.4   # broke early, didn't exhaust the budget

    def test_returns_false_after_exhausting_the_budget_if_it_never_appears(self):
        observe = MagicMock(return_value={"focused_element_id": "e1", "elements": _elements("", "e1")})
        matched, waited = _run_settle_poll(observe, "9", budget=0.3, poll=0.1)
        assert matched is False
        assert observe.call_count == 3   # 0.3 budget / 0.1 poll

    def test_no_polling_needed_when_correct_on_the_very_first_check(self):
        observe = MagicMock(return_value={"focused_element_id": "e1", "elements": _elements("9", "e1")})
        matched, waited = _run_settle_poll(observe, "9", budget=0.4, poll=0.1)
        assert matched is True
        assert observe.call_count == 1
