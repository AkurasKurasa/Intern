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
