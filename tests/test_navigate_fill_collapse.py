"""
Regression test for agent.py's "collapse navigate+fill into one step"
addition -- direct user request ("stop wasting steps... look up a lot at
once") after confirming (via cross-checking the actual value-lookup
mechanism) that value lookups were already batched (one parse of all 176
fields at record start, reused via a cache -- confirmed by "LLM call
skipped -- direct lookup already answered" and cache_size=176 dominating
every log tonight) and NOT the source of wasted steps. The real cost:
nearly every field took TWO separate step_idx iterations -- one step to
click/focus it (a plain navigate click), a second step for the top-of-loop
fillable-check to notice the newly-focused field and actually type into
it.

Fixed by checking, right after a plain navigate click's post-action
state_after is observed, whether the newly-focused field is an empty
editcontrol/input with a known record value -- if so, typing it
immediately in the SAME step instead of waiting for the next one.
Deliberately narrow: only plain navigate clicks (not redirects/combobox-
opens/checkbox clicks, which have their own already-correct multi-step
mechanics), only editcontrol/input (combobox/checkbox fills are their own
multi-click sequences), and only when a real value is found (a genuinely
blank field is left for the next step's own established blank-handling
logic).
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _attempt_key(elem):
    lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
    return lbl


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol", element_id=None):
    return {"element_id": element_id or label, "type": ftype, "label": label,
            "value": value, "bbox": list(bbox)}


def _try_collapse_navigate_fill(is_plain_navigate_click, prediction, state, state_after,
                                 lookup_fn, typed_keys=None, leave_blank_keys=None):
    """Mirrors the CURRENT (2026-08-09) collapse block in agent.py's
    run(): returns (new_prediction, collapsed: bool). Executor calls are
    represented as a list of dicts appended to `executed` for assertions."""
    typed_keys = typed_keys if typed_keys is not None else set()
    leave_blank_keys = leave_blank_keys if leave_blank_keys is not None else set()
    executed = []

    if not (is_plain_navigate_click and prediction.get("action_type") == "click"
            and state_after.get("focused_element_id")
            and state_after.get("focused_element_id") != state.get("focused_element_id")):
        return prediction, False, executed

    cn_fid = state_after["focused_element_id"]
    cn_els = state_after.get("elements", [])
    cn_el = next((e for e in cn_els if e.get("element_id") == cn_fid), None)
    if not (cn_el and (cn_el.get("type") or "").lower() in ("editcontrol", "input")
            and not (cn_el.get("value") or "").strip()):
        return prediction, False, executed

    cn_label = (cn_el.get("label") or cn_el.get("text") or "").strip()
    cn_key = _attempt_key(cn_el)
    if not (cn_label and cn_key not in typed_keys and cn_key not in leave_blank_keys):
        return prediction, False, executed

    cn_val = lookup_fn(cn_label)
    if not (cn_val and cn_el.get("bbox")):
        return prediction, False, executed

    cnb = cn_el["bbox"]
    executed.append({"action_type": "click",
                      "click_position": [(cnb[0] + cnb[2]) / 2, (cnb[1] + cnb[3]) / 2]})
    executed.append({"action_type": "keyboard", "text": cn_val})
    typed_keys.add(cn_key)
    return {"action_type": "keyboard", "text": cn_val}, True, executed


class TestCollapsesWhenANewEmptyFieldWithAKnownValueGetsFocused:
    def test_types_immediately_instead_of_waiting_for_the_next_step(self):
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Middle Name", value="", bbox=(100, 200, 300, 230), element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="Arthur")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup)

        assert collapsed is True
        assert new_pred == {"action_type": "keyboard", "text": "Arthur"}
        assert executed == [
            {"action_type": "click", "click_position": [200.0, 215.0]},
            {"action_type": "keyboard", "text": "Arthur"},
        ]
        lookup.assert_called_once_with("Middle Name")


class TestDoesNotCollapseOutsideItsNarrowScope:
    def test_not_a_plain_navigate_click_does_not_collapse(self):
        """A redirect/combobox-open/checkbox click has its own already-
        correct multi-step mechanics -- must not be touched."""
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Middle Name", value="", bbox=(100, 200, 300, 230), element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="Arthur")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=False,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup)

        assert collapsed is False
        assert executed == []
        lookup.assert_not_called()

    def test_combobox_field_does_not_collapse(self):
        """Combobox fills are their own multi-click open/select sequence,
        not a simple collapse candidate."""
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Gender", value="", bbox=(100, 200, 300, 230),
                            ftype="comboboxcontrol", element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="Male")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup)

        assert collapsed is False
        lookup.assert_not_called()

    def test_genuinely_blank_field_does_not_collapse(self):
        """No record value found -- must be left for the next step's own
        established blank-handling logic, not duplicated here."""
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Suffix", value="", bbox=(100, 200, 300, 230), element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup)

        assert collapsed is False
        assert executed == []

    def test_focus_did_not_actually_move_does_not_collapse(self):
        """A no_change click (nothing to collapse -- the click had no
        effect) must not be treated as a fill opportunity."""
        old_field = _field("First Name", value="James", element_id="e1")
        state = {"elements": [old_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field], "focused_element_id": "e1"}
        lookup = MagicMock()

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup)

        assert collapsed is False
        lookup.assert_not_called()

    def test_already_typed_field_does_not_collapse_again(self):
        """Defensive: a field already in typed_keys (e.g. from a prior
        record pass) must not be silently retyped."""
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Middle Name", value="", bbox=(100, 200, 300, 230), element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="Arthur")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup,
            typed_keys={"middle name"})

        assert collapsed is False
        lookup.assert_not_called()

    def test_already_confirmed_blank_field_does_not_collapse(self):
        """A field already known-blank this record must not get a fresh
        lookup attempt here either."""
        old_field = _field("First Name", value="James", element_id="e1")
        new_field = _field("Suffix", value="", bbox=(100, 200, 300, 230), element_id="e2")
        state = {"elements": [old_field, new_field], "focused_element_id": "e1"}
        state_after = {"elements": [old_field, new_field], "focused_element_id": "e2"}
        lookup = MagicMock(return_value="")

        new_pred, collapsed, executed = _try_collapse_navigate_fill(
            is_plain_navigate_click=True,
            prediction={"action_type": "click", "click_position": [200, 215]},
            state=state, state_after=state_after, lookup_fn=lookup,
            leave_blank_keys={"suffix"})

        assert collapsed is False
        lookup.assert_not_called()
