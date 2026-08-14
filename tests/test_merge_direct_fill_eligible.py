"""
Regression tests for _merge()'s direct_fill_eligible flag
(components/agent/agent.py, l_type == "type" branch, ~L7470-7484).

Built 2026-08-14, following a real live investigation on the actual
running practice form: sending a raw Win32 WM_SETTEXT directly to a text
field's native window handle reliably sets an EditControl's value in one
call, with no click and no simulated keystrokes -- confirmed correct via
two independent readback methods that agree with each other. Live-tested
against a ComboBoxControl and found to be a silent no-op (value
unchanged) -- comboboxes must never be marked eligible. Checkboxes
already have their own separate, working mechanism (BM_SETCHECK) and
must not collide with this one either.

direct_fill_eligible is decided here cheaply (no I/O, reusing
_tp_fel_ty -- the focused element's own type, already computed a few
lines above for the pre-existing click-override check) and deliberately
scoped to a strict equality against "editcontrol" -- exactly the one
control type live-tested this session -- not a broader "in (...)"
membership check. The live HWND itself is resolved separately, later,
only when this flag is set (see the focus/clear block, not tested here --
that needs a live UIA control, which is out of scope for a pure _merge()
unit test).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent


def _make_agent():
    return LLMAgent(goal="test goal", dry_run=True, max_steps=1)


def _field(label, elem_type, element_id="e1", bbox=(1400, 270, 1600, 300)):
    return {"element_id": element_id, "type": elem_type, "label": label,
            "text": label, "value": "", "bbox": list(bbox), "window_role": "active"}


def _state_with_focus(elem):
    return {"focused_element_id": elem["element_id"], "elements": [elem]}


class TestDirectFillEligibility:
    def test_editcontrol_is_eligible(self):
        agent = _make_agent()
        field = _field("Policy Number", "editcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 286]}
        llm_action = {"action_type": "type", "text": "POL-000123"}

        result = agent._merge(t_pred, 0.95, llm_action, state)

        assert result["action_type"] == "keyboard"
        assert result.get("direct_fill_eligible") is True

    def test_comboboxcontrol_is_not_eligible(self):
        """Live-tested this session: WM_SETTEXT is a silent no-op on a
        combobox -- must never be marked eligible."""
        agent = _make_agent()
        field = _field("Policy Status", "comboboxcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 320]}
        llm_action = {"action_type": "type", "text": "Active"}

        result = agent._merge(t_pred, 0.95, llm_action, state)

        # Comboboxes hit the click-override branch (needs a click first),
        # not the direct-fill branch at all -- but even if that were ever
        # to change, this must never come back True for a combobox.
        assert not result.get("direct_fill_eligible")

    def test_checkboxcontrol_is_not_eligible(self):
        """Checkboxes already have their own separate, working mechanism
        (BM_SETCHECK) -- must not collide with this one."""
        agent = _make_agent()
        field = _field("Auto-Pay Enrolled", "checkboxcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 426]}
        llm_action = {"action_type": "type", "text": "YES (check)"}

        result = agent._merge(t_pred, 0.96, llm_action, state)

        assert not result.get("direct_fill_eligible")

    def test_input_type_is_not_silently_included(self):
        """"input" is a distinct focused-element type from "editcontrol"
        elsewhere in this codebase's own click-override check -- direct
        fill deliberately stays scoped to only the one type actually
        live-tested this session, not silently widened to "input" too."""
        agent = _make_agent()
        field = _field("Payment Due Date", "input")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 356]}
        llm_action = {"action_type": "type", "text": "04/01/2026"}

        result = agent._merge(t_pred, 0.94, llm_action, state)

        assert result["action_type"] == "keyboard"  # still typed, just not via direct-fill
        assert not result.get("direct_fill_eligible")

    def test_no_focused_element_is_safe_and_not_eligible(self):
        agent = _make_agent()
        t_pred = {"action_type": "click", "click_position": [1484, 286]}
        llm_action = {"action_type": "type", "text": "POL-000123"}
        state = {"focused_element_id": None, "elements": []}

        result = agent._merge(t_pred, 0.95, llm_action, state)  # must not raise

        assert not result.get("direct_fill_eligible")
