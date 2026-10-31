"""Tests for scripts/rpa_baseline.py (Objective 7 RPA baseline) and Gate 3:
the scorer agrees with what the real form saves."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bc_fidelity import _parse_intake_record  # noqa: E402
from rpa_baseline import NORMAL_FORM, _load_form_module, build_steps, form_tab_order  # noqa: E402
from rpa_score import is_blank_instruction, score_submission  # noqa: E402

MESSY_FORM = NORMAL_FORM.with_name("car_insurance_form_wx_MESSY_TEST.py")
INTAKE = (ROOT / "data_entry_tasks" / "data_entry_intake.txt").read_text(encoding="utf-8")


def _names(pages):
    return [[c["name"] for c in p] for p in pages]


@pytest.fixture(scope="module")
def normal_pages():
    return form_tab_order(NORMAL_FORM)


@pytest.fixture(scope="module")
def messy_pages():
    return form_tab_order(MESSY_FORM)


def test_normal_form_has_eight_tabs_starting_with_policy_number(normal_pages):
    assert len(normal_pages) == 8
    assert normal_pages[0][0]["name"] == "policy_number"


def test_messy_form_differs_only_in_policy_tab_order(normal_pages, messy_pages):
    """The modified form keeps every control; only the Policy tab's order changes.
    This is the change the unedited macro is exposed to."""
    n, m = _names(normal_pages), _names(messy_pages)
    assert n[1:] == m[1:]
    assert sorted(n[0]) == sorted(m[0])
    assert n[0] != m[0]


def test_every_record_field_is_reachable_by_tab(normal_pages):
    on_form = {c["name"] for p in normal_pages for c in p}
    gold = _parse_intake_record(INTAKE, 1)
    assert set(gold) <= on_form


def test_macro_never_types_a_leave_blank_instruction(normal_pages):
    gold = _parse_intake_record(INTAKE, 1)
    typed = [s["keys"] for p in build_steps(normal_pages, gold) for s in p]
    assert not any(is_blank_instruction(k) for k in typed)
    # record 1's agency name has an em dash: the macro keeps it (typed as Unicode)
    assert any("—" in k for k in typed)


def test_build_steps_types_values_and_flips_only_changed_boxes():
    pages = [[
        {"name": "a", "kind": "text", "default": ""},
        {"name": "b", "kind": "choice", "default": "Active", "options": ["", "Active", "Lapsed"]},
        {"name": "c", "kind": "check", "default": False},
        {"name": "d", "kind": "check", "default": True},
        {"name": "e", "kind": "check", "default": False},
        {"name": "f", "kind": "text", "default": ""},
    ]]
    gold = {"a": "x1", "b": "Lapsed", "c": True, "d": True, "e": False}
    keys = [s["keys"] for s in build_steps(pages, gold)[0]]
    assert keys == ["x1", "{CHOICE:2}", "{SPACE}", "", "", ""]
    gold["b"] = "Active"                         # already the default: no keys
    assert build_steps(pages, gold)[0][1]["keys"] == ""


def test_section_bars_are_tab_stops_with_no_keys(normal_pages):
    """Gate 1 finding: a section-header bar takes a Tab stop of its own."""
    kinds = [c["kind"] for c in normal_pages[0]]
    assert kinds.count("stop") == 2          # "Agent / Agency" and "Flags" bars
    gold = _parse_intake_record(INTAKE, 1)
    steps = build_steps(normal_pages, gold)
    assert all(s["keys"] == "" for p in steps for s in p if s["kind"] == "stop")


def test_build_steps_clears_a_prefilled_box_the_record_says_to_leave_blank():
    pages = [[
        {"name": "acct", "kind": "choice", "default": "Checking", "options": ["", "Checking"]},
        {"name": "note", "kind": "text", "default": "prefilled"},
        {"name": "empty", "kind": "text", "default": ""},
    ]]
    gold = {"acct": "(leave blank)", "note": "(leave blank)", "empty": "(leave blank)"}
    assert [s["keys"] for s in build_steps(pages, gold)[0]] == ["{HOME}", "{CLEAR}", ""]


def test_gate3_perfect_fill_of_the_real_form_scores_fully_correct():
    """Gate 3 without a hand fill: set every control of the real form to
    record 1, collect the data exactly as Submit saves it, and score it."""
    import wx
    mod = _load_form_module(NORMAL_FORM)
    gold = _parse_intake_record(INTAKE, 1)
    app = wx.App(False)
    frame = mod.CarInsuranceFrame(None)
    try:
        for name, value in gold.items():
            ctrl = frame._controls[name]
            if is_blank_instruction(value):
                # a perfect operator leaves it empty -- or empties a box the
                # fresh form pre-fills (Account Type starts on "Checking")
                if isinstance(ctrl, wx.Choice):
                    ctrl.SetSelection(0)
                else:
                    ctrl.SetValue("")
                continue
            if isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(bool(value))
            elif isinstance(ctrl, wx.Choice):
                assert ctrl.SetStringSelection(str(value)) or value == "", (name, value)
            else:
                ctrl.SetValue(str(value))
        saved = frame._collect_data()
    finally:
        frame.Destroy()
        app.Destroy()
    s = score_submission(saved, 1, INTAKE)
    assert s["wrong_fields"] == []
    assert s["fully_correct"] is True
