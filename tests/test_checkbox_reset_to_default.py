"""
Regression test for car_insurance_form_wx.py's _clear_all_fields() (called
by Submit, ~L887) resetting checkboxes to their OWN declared default
instead of blanket False.

Found live 2026-08-08, reported directly: "Submit button doesnt reset
Checkboxes." Six checkboxes are declared default=True: 'Paperless /
e-Delivery' (Policy), 'Airbags'/'ABS'/'Daytime Running Lights' (Vehicle),
'Uninsured/Underinsured Motorist' (Coverage), 'Auto-Pay Enrolled'
(Payment) -- all checked on the form's first load, matching every
record's own ground-truth data (all answer "checked" for every record).
_clear_all_fields() used to set every checkbox to False unconditionally,
so record 2 onward opened the form with these six WRONGLY unchecked --
Submit's reset should make the form look like it did on first load, not
like every checkbox got manually unchecked.

Fix: _check() now also stores each checkbox's declared default in
self._checkbox_defaults[name]; _clear_all_fields() resets each checkbox
to that stored default instead of a hardcoded False.

Uses a real wx.App + CarInsuranceFrame instance (no display needed on
Windows, confirmed live) rather than mocking wx -- this is form-widget
behavior, not agent logic, so there's nothing meaningful to fake.
"""
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "car_insurance_entry"))


@pytest.fixture
def form():
    app = wx.App(False)
    from car_insurance_form_wx import CarInsuranceFrame
    frame = CarInsuranceFrame(None)
    yield frame
    try:
        frame.Destroy()
    except Exception:
        pass
    try:
        app.Destroy()
    except Exception:
        pass


class TestCheckboxDefaultsAreCaptured:
    def test_every_checkbox_has_a_recorded_default(self, form):
        checkbox_names = [name for name, ctrl in form._controls.items()
                           if isinstance(ctrl, wx.CheckBox)]
        assert checkbox_names   # sanity: the form actually has checkboxes
        for name in checkbox_names:
            assert name in form._checkbox_defaults

    def test_true_default_checkboxes_are_recorded_as_true(self, form):
        # Known True-default fields across three different tabs/sections.
        for name in ("paperless", "v_airbags", "v_abs", "v_daytime_lights",
                      "cov_um_uim", "pay_auto_pay"):
            assert form._checkbox_defaults[name] is True

    def test_false_default_checkboxes_are_recorded_as_false(self, form):
        for name in ("renewal_flag", "esign", "v_salvage", "cov_pip", "disc_military"):
            assert form._checkbox_defaults[name] is False


class TestClearAllFieldsRespectsCheckboxDefaults:
    def test_true_default_checkbox_resets_to_checked_not_unchecked(self, form):
        """The actual bug: 'Auto-Pay Enrolled' etc. must come back CHECKED
        after Submit, matching how the form looked on first load -- not
        unchecked, which every subsequent record's data disagrees with."""
        ctrl = form._controls["pay_auto_pay"]
        ctrl.SetValue(True)          # simulate the agent/human having checked it
        form._clear_all_fields()
        assert ctrl.GetValue() is True

    def test_false_default_checkbox_still_resets_to_unchecked(self, form):
        ctrl = form._controls["renewal_flag"]
        ctrl.SetValue(True)          # simulate it having been checked this record
        form._clear_all_fields()
        assert ctrl.GetValue() is False

    def test_all_six_true_default_checkboxes_survive_a_reset(self, form):
        true_default_fields = ["paperless", "v_airbags", "v_abs",
                                "v_daytime_lights", "cov_um_uim", "pay_auto_pay"]
        for name in true_default_fields:
            form._controls[name].SetValue(False)   # simulate all unchecked mid-record
        form._clear_all_fields()
        for name in true_default_fields:
            assert form._controls[name].GetValue() is True, name

    def test_text_and_choice_fields_still_reset_to_blank(self, form):
        """Unaffected by this fix -- only checkbox handling changed."""
        text_ctrl = form._controls["policy_number"]
        text_ctrl.SetValue("PAI-2026-00441")
        choice_ctrl = form._controls["policy_status"]
        choice_ctrl.SetSelection(1)

        form._clear_all_fields()

        assert text_ctrl.GetValue() == ""
        assert choice_ctrl.GetSelection() == 0
