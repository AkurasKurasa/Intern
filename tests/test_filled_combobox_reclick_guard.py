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

EXTENDED AGAIN 2026-08-08 to plain editcontrol/input fields -- reported
directly: "Wasted steps in Coverage due to loops" (traced to Vehicle --
the run hadn't reached Coverage yet, but the mechanism is identical).
Confirmed live: the navigate branch's pointer oscillated between TWO
already-filled fields for 24+ steps straight -- 'Number of Doors' (a
combobox, already covered) and 'Model' (a plain text field, NOT covered).
Each individual re-click on a text field is cheap in isolation (a click +
Tab, no data-corruption risk since clicking a text field doesn't change
its value) -- exactly why this was deliberately left open before
(execution_payment_tab_oscillation_fix's round 3: "bounded by the same
general navigation dynamics... not a special-cased loop anymore"). That
reasoning covered a single stray click, not a genuine, repeating,
multi-field OSCILLATION -- the cumulative cost across dozens of steps is
real. Same value+attempted logic as the combobox case, since a plain
field's value accurately reflects real state (unlike a checkbox).

EXTENDED AGAIN 2026-08-12 to fields in self._leave_blank_keys -- found
live: 831 of ~894 steps in one 40-minute run (93%) were the navigate
pointer oscillating between two correctly-blank fields, 'Lienholder
Address' and 'Lienholder/Lender'; real fills stopped completely for the
last ~34 minutes. Neither field was ever in self._typed_keys (nothing gets
typed into a field the record deliberately wants blank), so the
edit-field branch above never protected them -- a leave-blank decision is
just as final and deliberate as a genuine typed value, not a stray
navigation click that should stay reclickable. Not type-gated (any control
type can be left blank), unlike the other three branches.
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


def _edit(label="Model", element_id="e1", value="Accord", bbox=(1400, 307, 1600, 337)):
    return {"element_id": element_id, "type": "editcontrol", "label": label,
            "text": label, "value": value, "bbox": list(bbox), "window_role": "active"}


def _run_updated_reclick_guard(agent, click_pos, elements):
    """Mirrors the CURRENT reclick guard in agent.py's run(): find the
    element under the click, and Tab instead of clicking if it's an
    already-filled+attempted combobox, an already-attempted checkbox, an
    already-filled+attempted plain text field, OR any field (any type)
    already in agent._leave_blank_keys."""
    elem = agent._elem_at({"elements": elements}, click_pos)
    ty = (elem.get("type") or "").lower() if elem else ""
    key = agent._attempt_key(elem, elements=elements) if elem else None
    combobox_filled = (ty in ("comboboxcontrol", "combobox")
                        and bool((elem.get("value") or "").strip())
                        and key in agent._attempted_keys)
    checkbox_attempted = ty in ("checkboxcontrol", "checkbox") and key in agent._attempted_keys
    edit_filled = (ty in ("editcontrol", "input")
                   and bool((elem.get("value") or "").strip())
                   and key in agent._attempted_keys)
    leave_blank = key in agent._leave_blank_keys
    if combobox_filled or checkbox_attempted or edit_filled or leave_blank:
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


class TestAlreadyFilledEditFieldIsNotReclicked:
    """Found live 2026-08-08: "Wasted steps in Coverage due to loops" --
    traced to Vehicle tab, where the navigate branch's pointer oscillated
    between 'Number of Doors' (a combobox, already guarded) and 'Model' (a
    plain text field, NOT guarded) for 24+ steps straight."""

    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_pointer_drifting_onto_an_already_filled_text_field_is_blocked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _edit()   # 'Model' -> 'Accord'
        elements = [field]
        agent._mark_attempted(field, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 320], elements)

        assert outcome == "guarded_tab"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_unattempted_text_field_can_still_be_clicked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _edit()
        elements = [field]
        # Never marked attempted -- agent hasn't touched this field yet.

        outcome = _run_updated_reclick_guard(agent, [1500, 320], elements)

        assert outcome == "clicked"

    def test_attempted_but_still_empty_text_field_is_not_blocked(self):
        """An attempted field with NO real value yet (e.g. a failed type
        attempt) must stay clickable -- this guard only blocks fields that
        are genuinely already filled, matching the combobox case's own
        non-empty-value requirement."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _edit(value="")
        elements = [field]
        agent._mark_attempted(field, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 320], elements)

        assert outcome == "clicked"

    def test_a_different_unattempted_field_is_unaffected(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        filled = _edit(label="Model", element_id="e1", bbox=(1400, 307, 1600, 337))
        other = _edit(label="Trim / Sub-model", element_id="e2", value="",
                       bbox=(1400, 341, 1600, 371))
        elements = [filled, other]
        agent._mark_attempted(filled, elements=elements)

        outcome = _run_updated_reclick_guard(agent, [1500, 356], elements)   # clicking 'Trim / Sub-model'

        assert outcome == "clicked"


class TestConfirmedLeaveBlankFieldIsNotReclicked:
    """Found live 2026-08-12: 831 of ~894 steps in one 40-minute run (93%)
    were the navigate pointer oscillating between two correctly-blank
    fields, 'Lienholder Address' and 'Lienholder/Lender'. Both were
    genuinely blank per the record's own data -- nothing was ever typed
    into them, so the plain-edit-field branch (which checks _typed_keys,
    deliberately not _attempted_keys) never protected them. A leave-blank
    decision is final and deliberate, same as a real typed value."""

    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_pointer_drifting_onto_a_leave_blank_edit_field_is_blocked(self):
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _edit(label="Lienholder Address", value="")
        elements = [field]
        key = agent._attempt_key(field, elements=elements)
        agent._leave_blank_keys.add(key)

        outcome = _run_updated_reclick_guard(agent, [1500, 320], elements)

        assert outcome == "guarded_tab"
        click_calls = [c for c in agent._executor.execute.call_args_list
                       if c.args[0].get("action_type") == "click"]
        assert len(click_calls) == 0

    def test_the_exact_live_scenario_two_blank_fields_oscillating(self):
        """Both fields from the actual incident, in one state -- neither
        should ever be re-clicked once both are confirmed leave-blank."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        lienholder = _edit(label="Lienholder/Lender", element_id="e1", value="",
                            bbox=(1400, 546, 1600, 576))
        address = _edit(label="Lienholder Address", element_id="e2", value="",
                         bbox=(1400, 580, 1600, 610))
        elements = [lienholder, address]
        agent._leave_blank_keys.add(agent._attempt_key(lienholder, elements=elements))
        agent._leave_blank_keys.add(agent._attempt_key(address, elements=elements))

        outcome_1 = _run_updated_reclick_guard(agent, [1500, 561], elements)
        outcome_2 = _run_updated_reclick_guard(agent, [1500, 595], elements)

        assert outcome_1 == "guarded_tab"
        assert outcome_2 == "guarded_tab"

    def test_not_yet_confirmed_blank_field_is_still_clickable(self):
        """A field that's merely been clicked/attempted but never reached a
        real leave-blank decision must stay reclickable -- this guard only
        blocks a CONFIRMED, deliberate leave-blank, not a stray visit."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        field = _edit(label="Lienholder Address", value="")
        elements = [field]
        agent._mark_attempted(field, elements=elements)
        # Deliberately NOT added to agent._leave_blank_keys.

        outcome = _run_updated_reclick_guard(agent, [1500, 320], elements)

        assert outcome == "clicked"

    def test_leave_blank_applies_regardless_of_control_type(self):
        """Not type-gated like the other three branches -- a checkbox or
        combobox can be left-blank too."""
        agent = self._make_agent()
        agent._executor = MagicMock()
        box = _checkbox(label="Salvage Title")
        elements = [box]
        agent._leave_blank_keys.add(agent._attempt_key(box, elements=elements))

        outcome = _run_updated_reclick_guard(agent, [1500, 425], elements)

        assert outcome == "guarded_tab"
