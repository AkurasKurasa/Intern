"""
Regression test for agent.py's combobox-click-fill branch -- when a
combobox's dropdown never renders (or its options never match the wanted
value), repeated failures on the SAME field must escalate to marking it
attempted and moving on, instead of retrying identically forever.

Found 2026-08-09, live, direct user report ("it didn't want to navigate"):
'Payment Frequency' hit this exact code path 121 times across ~4 minutes in
one run -- click, wait 3.2s for a dropdown that never renders, Escape,
repeat -- with zero streak tracking and zero escalation, only stopped when
the user gave up and interrupted the run. A SIBLING branch in the same
function (combobox is genuinely blank, nothing to fill) already had a
repeat-guard/blacklist mechanism; this branch (combobox HAS a wanted value,
but its dropdown won't cooperate) had none at all.

Fixed by tracking consecutive dropdown-render failures per field label; once
a field fails _COMBOBOX_DROPDOWN_FAIL_LIMIT times in a row, mark it attempted
(so Navigation Protocol stops offering it) and redirect to a different known
visible target instead of repeating the identical failed poll cycle again.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

VIEWPORT_BOTTOM = 1000.0
COMBOBOX_DROPDOWN_FAIL_LIMIT = 2


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _run_dropdown_fill_attempt(state, cb_label, dropdown_rendered, fail_counts, mark_attempted_fn, executor):
    """Mirrors the CURRENT (2026-08-09) combobox-click-fill branch in
    agent.py's run(): a failed attempt (dropdown never rendered, or no
    matching option) increments a per-label counter; at the limit, mark
    the field attempted and redirect to a different visible target instead
    of retrying the same field again."""
    if dropdown_rendered:
        fail_counts.pop(cb_label, None)
        return "filled"
    executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["escape"]})
    fail_counts[cb_label] = fail_counts.get(cb_label, 0) + 1
    if fail_counts[cb_label] >= COMBOBOX_DROPDOWN_FAIL_LIMIT:
        fail_counts.pop(cb_label, None)
        mark_attempted_fn(cb_label)
        target = find_visible_empty_target(state, VIEWPORT_BOTTOM,
                                            attempted_keys={cb_label},
                                            attempt_key_fn=lambda e, els: e["element_id"])
        if target and target.get("bbox"):
            b = target["bbox"]
            executor.execute({"action_type": "click",
                               "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
        return "escalated"
    return "retrying"


class TestComboboxDropdownFailureEscalates:
    def test_first_failure_just_retries_no_escalation_yet(self):
        """A single dropdown-render miss is tolerated -- could be a
        transient timing hiccup, don't over-react to one instance."""
        executor = MagicMock()
        fail_counts = {}
        mark_attempted = MagicMock()
        state = {"elements": [_field("Payment Frequency", value="", bbox=(100, 100, 300, 130))]}
        outcome = _run_dropdown_fill_attempt(
            state, "Payment Frequency", dropdown_rendered=False,
            fail_counts=fail_counts, mark_attempted_fn=mark_attempted, executor=executor)
        assert outcome == "retrying"
        assert fail_counts["Payment Frequency"] == 1
        mark_attempted.assert_not_called()

    def test_repeated_failure_marks_attempted_and_redirects_elsewhere(self):
        """The actual live regression: the SAME combobox keeps failing --
        at the limit, stop retrying it and move to a different real target."""
        executor = MagicMock()
        fail_counts = {"Payment Frequency": 1}
        mark_attempted = MagicMock()
        state = {"elements": [
            _field("Payment Frequency", value="", bbox=(100, 100, 300, 130), ftype="comboboxcontrol"),
            _field("Rental Limit", value="", bbox=(100, 200, 300, 230)),
        ]}
        outcome = _run_dropdown_fill_attempt(
            state, "Payment Frequency", dropdown_rendered=False,
            fail_counts=fail_counts, mark_attempted_fn=mark_attempted, executor=executor)
        assert outcome == "escalated"
        assert "Payment Frequency" not in fail_counts
        mark_attempted.assert_called_once_with("Payment Frequency")
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert {"action_type": "click", "click_position": [200.0, 215.0]} in calls

    def test_a_successful_fill_resets_the_counter(self):
        """A field that eventually succeeds shouldn't carry a stale fail
        count forward if it's ever revisited."""
        executor = MagicMock()
        fail_counts = {"Payment Frequency": 1}
        mark_attempted = MagicMock()
        state = {"elements": [_field("Payment Frequency", value="Monthly", bbox=(100, 100, 300, 130))]}
        outcome = _run_dropdown_fill_attempt(
            state, "Payment Frequency", dropdown_rendered=True,
            fail_counts=fail_counts, mark_attempted_fn=mark_attempted, executor=executor)
        assert outcome == "filled"
        assert "Payment Frequency" not in fail_counts
        mark_attempted.assert_not_called()

    def test_different_fields_track_failures_independently(self):
        """One stuck combobox must not affect another field's own streak."""
        executor = MagicMock()
        fail_counts = {"Payment Frequency": 1}
        mark_attempted = MagicMock()
        state = {"elements": [_field("Billing Method", value="", bbox=(100, 100, 300, 130))]}
        outcome = _run_dropdown_fill_attempt(
            state, "Billing Method", dropdown_rendered=False,
            fail_counts=fail_counts, mark_attempted_fn=mark_attempted, executor=executor)
        assert outcome == "retrying"
        assert fail_counts == {"Payment Frequency": 1, "Billing Method": 1}
        mark_attempted.assert_not_called()
