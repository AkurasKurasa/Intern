"""
Regression test for the "don't re-click an already-filled combobox" guard
in agent.py's OPT2 navigate branch (components/agent/agent.py, ~L2066).

Found live 2026-08-07, investigating a user report ("Stuck on Marital
Status") from a run that otherwise looked healthy. The log showed:
  step 24: 'Marital Status' correctly filled -> 'Married' (dedicated
           combobox-fill handler)
  step 28: transformer's own pointer clicks @ (1493,460) -- Marital
           Status's own (closed) combobox -- at ptr_conf=0.58, comfortably
           above both confidence floors. A plain click TOGGLES a closed
           combobox open (+6 elements, confirmed in the log).
  step 29: transformer clicks @ (1493,542) -- a list-item position -- and
           the dropdown closes again (-6 elements), via the GENERIC
           navigate-click path, which has no value-matching logic at all
           (unlike the dedicated fill handler's LLM-value + option-match
           flow). It can't tell whether it just re-selected 'Married' or
           silently overwrote it with whatever was under the cursor.

This is a real correctness risk, not just wasted steps: the run's own
Value Accuracy metric can't even catch it, since it only tracks typed text
fields, never combobox selections.

Fix: before executing ANY navigate-branch click, check whether it lands on
a combobox that (a) already has a non-empty value and (b) is already in
attempted_keys (i.e. the agent itself filled it earlier this record). If
so, Tab past instead of clicking -- the same label-based attempted_keys
mechanism already used for the empty-combobox skip
(execution_attempted_combobox_position_drift), extended to the filled case
because re-toggling a filled combobox can destroy correct data, not just
waste a step.

EXTENDED 2026-08-07 to checkboxes -- reported plainly: "Same problem
still" (Auto-Pay Enrolled). The fill-click-anchoring fix closed the FILL
branch's re-entry, but the NAVIGATE branch's own pointer could still
independently drift back onto an already-checked checkbox's position on
its own -- 12 wasted click+Tab cycles in one run, each caught downstream
by the Win32 guard (not destructive) but never stopped before the click.
A checkbox has no "value" to check (ui_observer never reports real
checked state) -- attempted alone is enough here, unlike comboboxes which
also require a non-empty value.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _combobox(label="Marital Status", element_id="cb1", value="Married",
              bbox=(1400, 445, 1600, 475)):
    return {"element_id": element_id, "type": "comboboxcontrol", "label": label,
            "text": label, "value": value, "bbox": list(bbox), "window_role": "active"}


def _checkbox(label="Auto-Pay Enrolled", element_id="cb1", bbox=(1400, 410, 1600, 440)):
    return {"element_id": element_id, "type": "checkboxcontrol", "label": label,
            "text": label, "value": "", "bbox": list(bbox), "window_role": "active"}


def _run_updated_reclick_guard(agent, click_pos, elements):
    """Mirrors the CURRENT reclick guard in agent.py's run(): find the
    element under the click, and Tab instead of clicking if it's an
    already-filled+attempted combobox OR an already-attempted checkbox."""
    elem = agent._elem_at({"elements": elements}, click_pos)
    ty = (elem.get("type") or "").lower() if elem else ""
    key = agent._attempt_key(elem, elements=elements) if elem else None
    combobox_filled = (ty in ("comboboxcontrol", "combobox")
                        and bool((elem.get("value") or "").strip())
                        and key in agent._attempted_keys)
    checkbox_attempted = ty in ("checkboxcontrol", "checkbox") and key in agent._attempted_keys
    if combobox_filled or checkbox_attempted:
        agent._executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return "guarded_tab"
    agent._executor.execute({"action_type": "click", "click_position": click_pos})
    return "clicked"


class TestAlreadyFilledComboboxIsNotReclicked:
    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_pointer_drifting_onto_a_filled_attempted_combobox_is_blocked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _combobox()
        elements = [cbox]
        agent._mark_attempted(cbox, elements=elements)   # already filled earlier this record

        outcome = _run_updated_reclick_guard(agent, [1500, 460], elements)

        assert outcome == "guarded_tab"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_unattempted_filled_combobox_can_still_be_clicked(self):
        """A combobox with a non-empty DEFAULT value the agent never
        touched (e.g. a form default like 'Full Coverage') must still be
        clickable -- the record may legitimately want it changed. Only
        blocks re-clicks on fields the agent itself already confirmed."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _combobox(label="Policy Type", value="Full Coverage")
        elements = [cbox]
        # Never marked attempted -- agent hasn't touched this field yet.

        outcome = _run_updated_reclick_guard(agent, [1500, 460], elements)

        assert outcome == "clicked"

    def test_empty_combobox_is_not_affected_by_this_guard(self):
        """An empty, already-attempted combobox (e.g. blank Suffix) is
        handled by the separate empty-combobox skip, not this one -- this
        guard only fires when value is non-empty."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _combobox(label="Suffix", value="")
        elements = [cbox]
        agent._mark_attempted(cbox, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 460], elements)

        assert outcome == "clicked"

    def test_click_on_a_different_unrelated_field_is_unaffected(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        filled = _combobox(label="Marital Status", element_id="cb1", bbox=(1400, 445, 1600, 475))
        other = _combobox(label="Occupation", element_id="cb2", value="", bbox=(1400, 500, 1600, 530))
        elements = [filled, other]
        agent._mark_attempted(filled, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 515], elements)   # clicking 'Occupation'

        assert outcome == "clicked"


class TestAlreadyAttemptedCheckboxIsNotReclicked:
    """Found live 2026-08-07: "Same problem still" -- 'Auto-Pay Enrolled'
    stopped re-entering the FILL branch (fixed by anchoring the fill click
    to the focused field's bbox), but the NAVIGATE branch's own pointer
    kept independently drifting back onto its screen position 12 times in
    one run. Each was caught by the downstream Win32 guard and converted
    to Tab -- not destructive -- but never stopped before the click, so it
    still cost a wasted click+Tab every single time."""

    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_pointer_drifting_onto_an_already_checked_checkbox_is_blocked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _checkbox()
        elements = [cbox]
        agent._mark_attempted(cbox, elements=elements)   # already checked earlier this record

        outcome = _run_updated_reclick_guard(agent, [1500, 425], elements)

        assert outcome == "guarded_tab"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_unattempted_checkbox_can_still_be_clicked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        cbox = _checkbox()
        elements = [cbox]
        # Never marked attempted -- agent hasn't touched this checkbox yet.

        outcome = _run_updated_reclick_guard(agent, [1500, 425], elements)

        assert outcome == "clicked"

    def test_a_different_unattempted_checkbox_is_unaffected(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        attempted = _checkbox(label="Auto-Pay Enrolled", element_id="cb1", bbox=(1400, 410, 1600, 440))
        other = _checkbox(label="Paperless Billing", element_id="cb2", bbox=(1400, 460, 1600, 490))
        elements = [attempted, other]
        agent._mark_attempted(attempted, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 475], elements)   # clicking 'Paperless Billing'

        assert outcome == "clicked"
