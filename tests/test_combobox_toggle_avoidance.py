"""
Regression test for _open_dropdown_items() — the actual root cause fix for
a "Body Type"/"Sedan" loop that survived two earlier, real-but-incomplete
fixes (listitem type-name matching, then poll-window widening).

Found live 2026-08-07: a run's own log proved the dropdown poll was waiting
the full, widened window (3.2s) and STILL finding 0 items — for a value
('Sedan') confirmed to be a genuinely correct, exact-match option in the
form's own source. The element count jumped +13 between the PRECEDING
step (nominally on a different, adjacent field) and the "Body Type" step —
the prior step's own click had already popped this combobox's dropdown open
by accident. The dedicated handler then blindly clicked "to open" it again
— a combobox click TOGGLES, so this closed it — guaranteeing every
subsequent poll found nothing, because there was genuinely nothing open.

Fix: check whether a dropdown is already open (real listitem elements
present) BEFORE clicking to open one, in both combobox-fill handlers, and
skip the redundant (destructive) click when it's already open.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _open_dropdown_items


def _listitem(text, element_id="li1"):
    return {"element_id": element_id, "type": "listitem", "text": text,
            "window_role": "active", "bbox": [100, 100, 300, 130]}


def _combobox(element_id="cb1"):
    return {"element_id": element_id, "type": "comboboxcontrol",
            "window_role": "active", "bbox": [100, 50, 300, 80]}


class TestOpenDropdownItems:
    def test_empty_when_no_listitems_present(self):
        assert _open_dropdown_items([_combobox()]) == []

    def test_finds_listitem_type(self):
        items = _open_dropdown_items([_combobox(), _listitem("Sedan")])
        assert len(items) == 1
        assert items[0]["text"] == "Sedan"

    def test_finds_legacy_listitemcontrol_type(self):
        li = _listitem("Sedan")
        li["type"] = "listitemcontrol"
        assert len(_open_dropdown_items([li])) == 1

    def test_finds_multiple_items(self):
        items = [_listitem("Sedan", "li1"), _listitem("SUV", "li2"),
                 _listitem("Truck", "li3")]
        assert len(_open_dropdown_items(items)) == 3

    def test_ignores_background_listitems(self):
        li = _listitem("Sedan")
        li["window_role"] = "background"
        assert _open_dropdown_items([li]) == []

    def test_ignores_listitems_without_bbox(self):
        li = _listitem("Sedan")
        del li["bbox"]
        assert _open_dropdown_items([li]) == []

    def test_empty_elements_list(self):
        assert _open_dropdown_items([]) == []


class TestTogglePrevention:
    """The actual bug scenario: a dropdown that's already open (from a prior,
    unrelated click) must be detected so the caller skips a redundant click
    that would toggle it closed instead of using what's already there."""

    def test_already_open_dropdown_is_detected(self):
        state_elements = [
            _combobox(),
            _listitem("", "li0"), _listitem("Sedan", "li1"), _listitem("SUV", "li2"),
        ]
        already_open = _open_dropdown_items(state_elements)
        assert len(already_open) == 3
        # Caller logic: `if already_open: skip the click`
        should_click = not already_open
        assert should_click is False

    def test_closed_dropdown_correctly_signals_a_click_is_needed(self):
        state_elements = [_combobox()]
        already_open = _open_dropdown_items(state_elements)
        should_click = not already_open
        assert should_click is True
