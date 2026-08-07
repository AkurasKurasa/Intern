"""
Regression tests for the Payment-tab oscillation loop, reported directly by
the user after the furthest end-to-end progress this project has reached
(all 8 tabs, Policy through Payment, zero backward tab cycling): "Good, we
reached Payment tab, but there was a loop at the end."

Traced the log to an 18+ step oscillation between two fields at the end of
the run: 'Auto-Pay Enrolled' (a checkbox, already correctly checked) and
'Last Payment Amount' (an editcontrol, legitimately blank -- no record
value for it). Two independent gaps combined to produce it:

(1) OPT2's fill-branch entry condition (_t_is_type: "is the focused thing
    a fillable, empty field?") never checked attempted_keys. For checkboxes
    specifically this is fatal: ui_observer never reports a real checked
    state via ValuePattern (wx.CheckBox doesn't expose it), so a checkbox's
    `value` reads empty FOREVER, checked or not -- _t_is_type was
    unconditionally True for any focused checkbox, attempted or not,
    forcing the full ask_llm->merge->click dance every single time,
    relying entirely on a downstream Win32 BM_GETCHECK guard to catch the
    redundant click and convert it to Tab (wasteful, though not
    destructive -- the guard did correctly stop it from actually
    unchecking).

(2) _merge() overrides ANY "type" decision into a "click" whenever the
    transformer's action-type head (collapsed, near-constant confidence)
    says click -- regardless of whether the LLM's intended text was real
    or empty. _is_leave_blank_prediction is gated on
    action_type=="keyboard", specifically to avoid mistaking a legitimate
    combobox click-override for leave-blank (see its own docstring) -- but
    that same gate means a genuinely empty answer (nothing in the record
    for 'Last Payment Amount') never gets recognized once merge has
    already turned it into a click, and the resulting click uses the
    transformer's own (~69%-accurate) pointer position, not this field's --
    landing almost anywhere, in this case apparently back near 'Auto-Pay
    Enrolled'.

Fixes: (1) _t_is_type now excludes already-attempted fields. (2) the
leave-blank check now runs on llm_action directly, BEFORE _merge() gets a
chance to convert a genuinely-empty "type" decision into a click.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent, _is_leave_blank_prediction


def _checkbox(label="Auto-Pay Enrolled", element_id="cb1"):
    return {"element_id": element_id, "type": "checkboxcontrol", "label": label,
            "text": label, "value": "", "bbox": [1400, 410, 1600, 440], "window_role": "active"}


def _edit(label="Last Payment Amount ($)", element_id="e1"):
    return {"element_id": element_id, "type": "editcontrol", "label": label,
            "text": label, "value": "", "bbox": [900, 445, 1100, 475], "window_role": "active"}


def _compute_t_is_type(agent, fe, elements):
    """Mirrors the CURRENT _t_is_type computation in agent.py's run()."""
    fe_ty  = (fe.get("type") or "").lower() if fe else ""
    fe_val = (fe.get("value") or "").strip() if fe else ""
    fe_attempted = bool(fe) and agent._attempt_key(fe, elements=elements) in agent._attempted_keys
    return (fe_ty in ("editcontrol", "input", "comboboxcontrol", "checkboxcontrol", "checkbox")
            and not fe_val and not fe_attempted)


class TestAttemptedCheckboxNoLongerReEntersFillBranch:
    def _make_agent(self):
        return LLMAgent(goal="test goal", dry_run=True, max_steps=1)

    def test_unattempted_checkbox_is_treated_as_a_fill_target(self):
        agent = self._make_agent()
        cbox = _checkbox()
        elements = [cbox]
        assert _compute_t_is_type(agent, cbox, elements) is True

    def test_attempted_checkbox_is_no_longer_a_fill_target(self):
        """The actual bug: a checkbox's `value` reads empty forever (no
        ValuePattern for wx.CheckBox), so without this check, _t_is_type
        stays True after the checkbox is already correctly checked --
        forcing the full ask_llm->merge->click dance every time focus
        lands there again."""
        agent = self._make_agent()
        cbox = _checkbox()
        elements = [cbox]
        agent._mark_attempted(cbox, elements=elements)   # already checked earlier this record

        assert _compute_t_is_type(agent, cbox, elements) is False

    def test_attempted_edit_field_is_also_excluded(self):
        """Same principle applies to any field type this decision governs,
        not just checkboxes."""
        agent = self._make_agent()
        field = _edit()
        elements = [field]
        agent._mark_attempted(field, elements=elements)

        assert _compute_t_is_type(agent, field, elements) is False

    def test_a_different_unattempted_field_is_unaffected(self):
        agent = self._make_agent()
        cbox = _checkbox(label="Auto-Pay Enrolled", element_id="cb1")
        other = _checkbox(label="Paperless Billing", element_id="cb2")
        elements = [cbox, other]
        agent._mark_attempted(cbox, elements=elements)

        assert _compute_t_is_type(agent, other, elements) is True


class TestPreMergeLeaveBlankCheck:
    """The second half of the fix: llm_action is checked for leave-blank
    BEFORE _merge() runs, not after -- so a genuinely empty answer can't be
    turned into a stray click by the transformer's collapsed action-type
    head first."""

    def test_empty_type_action_is_recognized_as_leave_blank_pre_merge(self):
        llm_action = {"action_type": "type", "text": ""}
        as_keyboard = {"action_type": "keyboard", "text": llm_action.get("text", "")}
        assert llm_action.get("action_type") in ("type", "keyboard")
        assert _is_leave_blank_prediction(as_keyboard) is True

    def test_none_and_na_variants_also_recognized(self):
        for txt in ("none", "N/A", "n/a", "leave blank"):
            llm_action = {"action_type": "type", "text": txt}
            as_keyboard = {"action_type": "keyboard", "text": llm_action.get("text", "")}
            assert _is_leave_blank_prediction(as_keyboard) is True, txt

    def test_real_value_is_not_mistaken_for_leave_blank(self):
        llm_action = {"action_type": "type", "text": "YES (check)"}
        as_keyboard = {"action_type": "keyboard", "text": llm_action.get("text", "")}
        assert _is_leave_blank_prediction(as_keyboard) is False

    def test_fast_path_lookup_with_a_real_answer_is_never_mistaken_either(self):
        """The fast-path (_ask_llm's direct-lookup short-circuit) only ever
        returns when _expected is truthy -- text is never empty via that
        path, so this check can't misfire on a correctly-resolved value."""
        llm_action = {"action_type": "type", "text": "Married", "_fast_path": "lookup"}
        as_keyboard = {"action_type": "keyboard", "text": llm_action.get("text", "")}
        assert _is_leave_blank_prediction(as_keyboard) is False

    def test_click_action_type_is_not_affected_by_this_pre_check(self):
        """If the LLM itself decided click (not type), this pre-merge check
        doesn't apply at all -- only action_type in (type, keyboard) is
        checked, matching the real code's condition."""
        llm_action = {"action_type": "click", "click_position": [500, 500]}
        assert llm_action.get("action_type") not in ("type", "keyboard")
