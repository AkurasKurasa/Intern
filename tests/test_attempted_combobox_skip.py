"""
Regression test for the "already-attempted empty combobox" skip in
agent.py's OPT2 navigate branch (components/agent/agent.py, ~L2073).

Found live 2026-08-07, directly reported by the user ("Stuck again and so
many wasted steps" / "We should stop wasting steps like that, the work is
getting dirty"). The log showed 'Suffix' (an optional combobox, correctly
left blank per the record) getting the full click-open/check-value/
escape/tab treatment FOUR separate times across one single Policyholder
pass — 10 wasted steps on one already-known-blank field.

Root cause: the existing dead-end defenses only catch 3 IDENTICAL clicks
in a row at the same 10px-bucketed position (self._nochange_click_pos,
built for execution_dead_end_click_blacklist). But the transformer's own
click-position estimate for the same field wobbles slightly each time it
drifts back to it minutes apart (not consecutively) — enough to dodge that
bucket every time, so a fresh 3-strikes-then-blacklist cycle restarts
instead of the field staying skipped. The "treat as FILL" branch never
checked self._attempted_keys (label-based, stable regardless of pixel
drift) before doing the click at all.

Fix: check attempted_keys FIRST. If this exact combobox (by its
_attempt_key, the same label-based key Navigation Protocol and verify-at-
fill's bookkeeping already use) was already confirmed blank earlier this
record, skip straight to Tab — no click, no open/close, nothing to verify.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _combobox(label="Suffix", element_id="cb1", value=""):
    return {"element_id": element_id, "type": "comboboxcontrol", "label": label,
            "text": label, "value": value, "bbox": [1400, 240, 1600, 260],
            "window_role": "active"}


def _run_updated_navigate_branch(agent, cbox, elements):
    """Mirrors the CURRENT navigate-branch logic in agent.py's run() (post
    2026-08-07 fix): check attempted_keys before doing anything else."""
    already_attempted = agent._attempt_key(cbox, elements=elements) in agent._attempted_keys
    if already_attempted:
        agent._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return "skipped_no_click"
    agent._executor.execute({"action_type": "click", "click_position": [1500, 250]})
    agent._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["escape"]})
    agent._mark_attempted(cbox, elements=elements)
    agent._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
    return "clicked_then_tab"


class TestAlreadyAttemptedComboboxIsSkipped:
    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_first_visit_clicks_then_marks_attempted(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _combobox()
        elements = [cbox]

        outcome = _run_updated_navigate_branch(agent, cbox, elements)

        assert outcome == "clicked_then_tab"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 1
        assert agent._attempt_key(cbox, elements=elements) in agent._attempted_keys

    def test_second_visit_skips_the_click_entirely(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _combobox()
        elements = [cbox]

        _run_updated_navigate_branch(agent, cbox, elements)   # first visit: clicks + marks
        agent._executor.reset_mock()

        outcome = _run_updated_navigate_branch(agent, cbox, elements)   # second visit, later in the run

        assert outcome == "skipped_no_click"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0
        tab_calls = [c for c in agent._executor.execute.call_args_list
                     if c.args[0].get("keystrokes") == ["tab"]]
        assert len(tab_calls) == 1

    def test_skip_holds_even_when_the_click_position_estimate_drifts(self):
        """The actual live bug: the position-based blacklist misses this
        because the transformer's estimated click position for the SAME
        field isn't pixel-identical across separate approaches. attempted_keys
        is label-based, so it isn't fooled by the drift."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox_first_approach = _combobox(element_id="cb1")
        elements1 = [cbox_first_approach]
        _run_updated_navigate_branch(agent, cbox_first_approach, elements1)
        agent._executor.reset_mock()

        # Same field, re-observed later in the run: fresh element_id (scan-
        # position artifact), slightly different bbox (model's own estimate
        # drifted a few pixels) -- but the SAME label.
        cbox_second_approach = _combobox(element_id="cb_99")
        cbox_second_approach["bbox"] = [1404, 246, 1604, 266]
        elements2 = [cbox_second_approach]

        outcome = _run_updated_navigate_branch(agent, cbox_second_approach, elements2)

        assert outcome == "skipped_no_click"

    def test_different_field_with_the_same_type_is_not_skipped(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        suffix = _combobox(label="Suffix", element_id="cb1")
        _run_updated_navigate_branch(agent, suffix, [suffix])
        agent._executor.reset_mock()

        policy_status = _combobox(label="Policy Status", element_id="cb2")
        outcome = _run_updated_navigate_branch(agent, policy_status, [policy_status])

        assert outcome == "clicked_then_tab"
