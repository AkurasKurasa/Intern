"""
Regression test for components/agent/agent.py's _refresh_record_cache().

Found live 2026-08-13, from a real run's log (run_task_20260813_005301.log):
a dense cluster of 4+ identical no_change steps, all on the 'Renewal Policy'
checkbox. Record 2's intake data wanted it 'NO' (unchecked), but it was
already checked live -- the uncheck logic that exists specifically for this
case (~L3935, added 2026-08-12) requires `_flabel not in self._checked_fields`
to fire, and self._checked_fields ("checkboxes already clicked this run", per
its own __init__ comment) was never cleared anywhere in the file. Once a
checkbox label is added to it in ANY record, both the uncheck-branch's guard
and the click-guard's `_chk_label in self._checked_fields` check treat it as
permanently off-limits -- even though checkbox state is decided fresh every
record (the form itself resets checkboxes to unchecked on Submit; see
test_checkbox_reset_to_unchecked.py) and different records can legitimately
want opposite values for the same-labeled field. With no path back to
checking OR unchecking it, the agent just redirected off the field and back
onto it, forever.

_checked_fields was the one set missing from the per-record reset block that
already clears _attempted_keys/_typed_keys/_advance_blacklist_pos/
_leave_blank_keys on every self._record_num change -- fixed by adding it to
that same block, same pattern, same trigger condition.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0)
    agent._executor = MagicMock()
    return agent


_TWO_RECORD_TEXT = (
    "=== RECORD 1 OF 2 ===\n"
    "Renewal Policy: YES\n"
    "=== RECORD 2 OF 2 ===\n"
    "Renewal Policy: NO\n"
)


class TestCheckedFieldsResetOnRecordAdvance:
    def test_checked_fields_cleared_when_record_num_changes(self):
        agent = _make_agent()
        agent._read_notepad_full_text = MagicMock(return_value=_TWO_RECORD_TEXT)
        agent._checked_fields.add("Renewal Policy")   # simulate: checked during record 1

        agent._record_num = 2
        agent._refresh_record_cache({"elements": []})

        assert "Renewal Policy" not in agent._checked_fields, (
            "a checkbox checked in an earlier record must not stay "
            "permanently blocked from being re-decided (checked or "
            "unchecked) on a later record"
        )

    def test_checked_fields_untouched_when_record_num_is_unchanged(self):
        """Only a genuine record change should reset this -- re-observing
        the SAME record repeatedly (normal mid-record polling) must not
        wipe out checkbox bookkeeping the agent still needs this record."""
        agent = _make_agent()
        agent._read_notepad_full_text = MagicMock(return_value=_TWO_RECORD_TEXT)
        agent._checked_fields.add("Renewal Policy")
        agent._record_num = 1
        agent._attempted_record_num = 1   # already "current" per this record

        agent._refresh_record_cache({"elements": []})

        assert "Renewal Policy" in agent._checked_fields

    def test_other_per_record_state_still_resets_alongside_it(self):
        """Not a narrower fix that only touches _checked_fields -- confirms
        it joins the existing reset without disturbing the other sets."""
        agent = _make_agent()
        agent._read_notepad_full_text = MagicMock(return_value=_TWO_RECORD_TEXT)
        agent._checked_fields.add("Renewal Policy")
        agent._attempted_keys.add(("renewal policy", "checkboxcontrol"))
        agent._leave_blank_keys.add(("some field", "editcontrol"))

        agent._record_num = 2
        agent._refresh_record_cache({"elements": []})

        assert agent._checked_fields == set()
        assert agent._attempted_keys == set()
        assert agent._leave_blank_keys == set()

    def test_single_record_intake_never_triggers_a_reset(self):
        """No 'RECORD N OF M' structure -> _parse_records returns {} ->
        the whole record-cache branch is skipped, same as before this fix."""
        agent = _make_agent()
        agent._read_notepad_full_text = MagicMock(
            return_value="Renewal Policy: NO\nPolicy Number: PAI-2026-00441\n")
        agent._checked_fields.add("Renewal Policy")

        agent._refresh_record_cache({"elements": []})

        assert "Renewal Policy" in agent._checked_fields
