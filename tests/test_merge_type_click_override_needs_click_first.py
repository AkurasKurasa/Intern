"""
Regression test for _merge()'s type->click override now requiring the
focused field to actually need a click first (components/agent/agent.py,
~L4678, _TRANSFORMER_TYPE_OVERRIDE_THRESHOLD block).

Found live 2026-08-07 ("Still not fixed btw, new loop"), the run right
after the fill-click-anchoring fix started landing merge-overridden
clicks exactly on the focused field instead of drifting elsewhere.
'Down Payment ($)' and 'Payment Due Date' (both plain editcontrol) looped
forever: LLM correctly decided "type" with the real value, but _merge()
overrode it into a "click" purely because the transformer's action-type
head said click with high confidence -- which it always does (documented
elsewhere as a collapsed, near-constant-confidence head, unrelated to
what's actually focused). A checkbox or combobox genuinely needs a click
before it can be filled; a plain text field does not. Clicking an
already-focused text field does nothing at all ("Validator -> no_change"
every time), so the "type" decision -- which had the correct value -- was
silently discarded, forever, every time focus landed on that field.

This bug pre-dates the anchoring fix -- it was always there. It only
became VISIBLE once the anchoring fix started landing the override's
click precisely on the already-focused field instead of drifting
elsewhere by accident (where an accidental miss sometimes produced a
different, non-"no_change" outcome that masked the underlying issue).

Fix: the override now also requires the CURRENTLY FOCUSED element's own
type to be checkbox/combobox -- the only widget types that genuinely need
a click before they can be filled. Plain editcontrol/input fields skip
the override entirely and fall through to being typed into directly.
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


class TestOverrideRequiresClickFirstFieldType:
    def test_plain_edit_field_is_not_overridden_into_a_click(self):
        """The actual bug: 'Down Payment ($)' is an editcontrol, doesn't
        need a click -- the type decision (with the real value) must be
        preserved and executed as typing."""
        agent = _make_agent()
        field = _field("Down Payment ($)", "editcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 286]}
        llm_action = {"action_type": "type", "text": "374.84"}

        result = agent._merge(t_pred, 0.95, llm_action, state)

        assert result["action_type"] == "keyboard"
        assert result.get("text") == "374.84"

    def test_input_type_is_also_not_overridden(self):
        agent = _make_agent()
        field = _field("Payment Due Date", "input")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 356]}
        llm_action = {"action_type": "type", "text": "04/01/2026"}

        result = agent._merge(t_pred, 0.94, llm_action, state)

        assert result["action_type"] == "keyboard"
        assert result.get("text") == "04/01/2026"

    def test_checkbox_still_gets_overridden_into_a_click(self):
        """Checkboxes genuinely need a click -- this behavior must be
        unchanged."""
        agent = _make_agent()
        field = _field("Auto-Pay Enrolled", "checkboxcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 426]}
        llm_action = {"action_type": "type", "text": "YES (check)"}

        result = agent._merge(t_pred, 0.96, llm_action, state)

        assert result["action_type"] == "click"

    def test_combobox_still_gets_overridden_into_a_click(self):
        agent = _make_agent()
        field = _field("Payment Frequency", "comboboxcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 320]}
        llm_action = {"action_type": "type", "text": "Monthly"}

        result = agent._merge(t_pred, 0.95, llm_action, state)

        assert result["action_type"] == "click"

    def test_low_transformer_confidence_still_never_overrides_regardless_of_type(self):
        agent = _make_agent()
        field = _field("Auto-Pay Enrolled", "checkboxcontrol")
        state = _state_with_focus(field)
        t_pred = {"action_type": "click", "click_position": [1484, 426]}
        llm_action = {"action_type": "type", "text": "YES (check)"}

        result = agent._merge(t_pred, 0.50, llm_action, state)

        assert result["action_type"] == "keyboard"
