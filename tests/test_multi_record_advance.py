"""
Regression tests for multi-record advance in components/agent/agent.py.

Found 2026-08-12, directly asked ("make it attempt 1-5"): self._record_num
was set once at construction and NEVER reassigned anywhere in agent.py --
confirmed by grepping the whole file for every assignment to it and finding
none besides __init__. There was also no outer loop in run_task.py that
would restart the agent for a later record. A real run could therefore only
ever attempt record 1, no matter how many records the intake text held or
how well the model performed -- not a tuning problem, an unbuilt feature.

Fix, in three pieces:
  1. `_total_records_in()` -- how many record blocks _parse_records() found
     in the intake text (1 = single-record default, so any source without
     'RECORD N OF M' structure is completely unaffected).
  2. `_next_record_or_finished()` -- pure decision: is there a next record,
     or was this the last one.
  3. `_try_advance_tab()`'s existing last-tab-exhausted+Submit branch (which
     already, from an earlier fix, clicks Submit -- it just never did
     anything with self._record_num afterward) now calls these two and
     either advances (increments record_num, re-refreshes the record cache,
     sets self._record_just_advanced) or finishes (self._task_finished).
     run()'s main loop checks both flags once per iteration, at the top --
     not at each of _try_advance_tab's ~10 call sites -- so none of their
     existing logic needed to change.

These tests do NOT run a live agent against a real form (that's the user's
call per this project's standing rule) -- they exercise the real methods
directly with dry_run=True and a mocked executor/observer, the same pattern
already used by test_filled_combobox_reclick_guard.py and friends.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent, _next_record_or_finished, _total_records_in


# ── Pure helper functions ───────────────────────────────────────────────────

class TestNextRecordOrFinished:
    def test_advances_when_more_records_remain(self):
        assert _next_record_or_finished(current=1, total=5) == 2

    def test_advances_through_the_middle_of_a_run(self):
        assert _next_record_or_finished(current=3, total=5) == 4

    def test_none_on_the_last_record(self):
        assert _next_record_or_finished(current=5, total=5) is None

    def test_none_for_a_single_record_intake(self):
        """The un-multi-record default: total=1 always means 'finished
        after the first submit', unchanged from before this feature existed."""
        assert _next_record_or_finished(current=1, total=1) is None

    def test_none_if_current_somehow_exceeds_total(self):
        """Defensive: never advance past what the intake text actually has."""
        assert _next_record_or_finished(current=6, total=5) is None


class TestTotalRecordsIn:
    def test_counts_the_highest_record_number_found(self):
        records = {1: {"a": "1"}, 2: {"a": "2"}, 3: {"a": "3"}}
        assert _total_records_in(records) == 3

    def test_not_affected_by_dict_insertion_order(self):
        records = {3: {}, 1: {}, 2: {}}
        assert _total_records_in(records) == 3

    def test_empty_dict_defaults_to_one(self):
        """No 'RECORD N OF M' structure found -- behaves as single-record,
        exactly as every run did before this feature existed."""
        assert _total_records_in({}) == 1

    def test_single_record_intake_is_one(self):
        assert _total_records_in({1: {"a": "1"}}) == 1


# ── _try_advance_tab's record-transition behavior ───────────────────────────

def _tab(label, idx, bbox=None):
    return {"element_id": f"tab{idx}", "type": "tabitemcontrol", "window_role": "active",
            "label": label, "text": label, "bbox": bbox or [idx * 100, 0, idx * 100 + 90, 30]}


def _submit_button():
    return {"element_id": "submit", "type": "buttoncontrol", "window_role": "background",
            "label": "Submit", "text": "Submit", "bbox": [1800, 900, 1900, 930]}


def _make_agent(record_num=1, total_records=1, current_tab_idx=None, end_record=None):
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1, step_delay=0,
                      end_record=end_record)
    agent._executor = MagicMock()
    agent._observe = MagicMock(return_value={"elements": [], "window_title": "test"})
    agent._record_num = record_num
    agent._total_records = total_records
    if current_tab_idx is not None:
        agent._current_tab_idx = current_tab_idx
    return agent


# 3 tabs, no other on-screen elements -> _detect_active_tab_idx_raw finds no
# confirmed active panel and falls back to the tracker (self._current_tab_idx).
_THREE_TABS = [_tab("Policy", 0), _tab("Vehicle", 1), _tab("Payment", 2)]


class TestAdvancesToNextRecordWhenMoreRemain:
    def test_record_num_increments(self):
        agent = _make_agent(record_num=1, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        result = agent._try_advance_tab({"elements": elements})

        assert result is True
        assert agent._record_num == 2

    def test_task_finished_stays_false(self):
        agent = _make_agent(record_num=1, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._task_finished is False

    def test_record_just_advanced_flag_is_set(self):
        agent = _make_agent(record_num=1, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._record_just_advanced is True

    def test_submit_button_was_actually_clicked(self):
        agent = _make_agent(record_num=2, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1

    def test_advances_correctly_from_the_middle_of_a_multi_record_run(self):
        agent = _make_agent(record_num=3, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._record_num == 4


class TestFinishesOnTheLastRecord:
    def test_task_finished_is_set(self):
        agent = _make_agent(record_num=5, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        result = agent._try_advance_tab({"elements": elements})

        assert result is True
        assert agent._task_finished is True

    def test_record_num_unchanged_on_the_last_record(self):
        agent = _make_agent(record_num=5, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._record_num == 5

    def test_record_just_advanced_is_not_set_on_the_last_record(self):
        agent = _make_agent(record_num=5, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._record_just_advanced is False

    def test_single_record_run_finishes_after_one_submit(self):
        """The default (total_records=1, matching a source with no
        'RECORD N OF M' structure at all) -- must behave exactly as every
        run did before this feature existed."""
        agent = _make_agent(record_num=1, total_records=1, current_tab_idx=2)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._task_finished is True
        assert agent._record_just_advanced is False


class TestNoSubmitButtonIsUnaffected:
    """No regression for a form with no submit-like button on the last tab
    -- still just signals done, same as before this feature existed."""

    def test_returns_false_and_touches_no_record_state(self):
        agent = _make_agent(record_num=2, total_records=5, current_tab_idx=2)
        elements = _THREE_TABS  # no submit button

        result = agent._try_advance_tab({"elements": elements})

        assert result is False
        assert agent._record_num == 2
        assert agent._task_finished is False
        assert agent._record_just_advanced is False


class TestEndRecordCap:
    """--end_record (run_task.py) -> LLMAgent(end_record=...) caps how far
    the agent advances, independent of how many records the intake text
    actually has -- e.g. attempting only records 2-3 of a 5-record file."""

    def test_advances_within_the_capped_range(self):
        agent = _make_agent(record_num=2, total_records=5, current_tab_idx=2, end_record=3)
        elements = _THREE_TABS + [_submit_button()]

        result = agent._try_advance_tab({"elements": elements})

        assert result is True
        assert agent._record_num == 3
        assert agent._record_just_advanced is True

    def test_finishes_at_the_cap_even_though_more_records_exist_in_the_file(self):
        agent = _make_agent(record_num=3, total_records=5, current_tab_idx=2, end_record=3)
        elements = _THREE_TABS + [_submit_button()]

        result = agent._try_advance_tab({"elements": elements})

        assert result is True
        assert agent._record_num == 3          # unchanged -- did not advance to 4
        assert agent._task_finished is True

    def test_no_cap_advances_through_every_record_in_the_file(self):
        """end_record=None (the default) -- unaffected, matches the
        already-tested no-cap behavior."""
        agent = _make_agent(record_num=4, total_records=5, current_tab_idx=2, end_record=None)
        elements = _THREE_TABS + [_submit_button()]

        agent._try_advance_tab({"elements": elements})

        assert agent._record_num == 5
        assert agent._task_finished is False

    def test_cap_higher_than_the_intake_files_own_record_count_is_harmless(self):
        """--end_record 10 on a 5-record file -- the file's own count still
        wins, no crash, no attempt to read a record that doesn't exist."""
        agent = _make_agent(record_num=5, total_records=5, current_tab_idx=2, end_record=10)
        elements = _THREE_TABS + [_submit_button()]

        result = agent._try_advance_tab({"elements": elements})

        assert result is True
        assert agent._task_finished is True
