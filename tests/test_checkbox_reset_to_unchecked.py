"""
Regression test for car_insurance_form_wx.py's _clear_all_fields() (called
by Submit, ~L887) always resetting checkboxes to UNCHECKED.

Direct user spec 2026-08-08: "When I press Submit I want all the checked
checkboxes to become unchecked." An earlier version of this fix (same day)
restored each checkbox to its own construction-time default instead (6 of
them default to checked) -- the wrong call, corrected here per explicit
direction.

Also found the same day, investigating why even the ORIGINAL plain
ctrl.SetValue(False) reportedly wasn't visibly working: the agent checks
boxes via a raw Win32 BM_SETCHECK message sent straight to the native
control (agent.py does this deliberately -- "pyautogui clicks don't
toggle wx checkboxes"), bypassing wx's own click/event pipeline entirely.
wx's Python-level CheckBox object never observes that change, so its
cached internal value can silently disagree with the REAL native/visual
state -- ctrl.SetValue(False) only reliably syncs wx's own model, which
may not be enough once the two have already diverged this way. Fix now
also sends BM_SETCHECK directly to force the actual native widget
unchecked, matching the exact mechanism the agent already uses to check
them -- so the reset can't be defeated by that same desync.
"""
import sys
from pathlib import Path

import pytest

wx = pytest.importorskip("wx")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "practice_apps" / "car_insurance_entry"))


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


class TestClearAllFieldsAlwaysUnchecksCheckboxes:
    def test_a_true_default_checkbox_resets_to_unchecked(self, form):
        """The direct spec: even checkboxes that START checked (e.g.
        'Auto-Pay Enrolled', default=True) must come back UNCHECKED after
        Submit -- not restored to their original checked default."""
        ctrl = form._controls["pay_auto_pay"]
        assert ctrl.GetValue() is True   # sanity: starts checked, per its own default
        form._clear_all_fields()
        assert ctrl.GetValue() is False

    def test_a_false_default_checkbox_that_got_checked_resets_to_unchecked(self, form):
        ctrl = form._controls["renewal_flag"]
        ctrl.SetValue(True)   # simulate having been checked this record
        form._clear_all_fields()
        assert ctrl.GetValue() is False

    def test_every_checkbox_on_the_form_ends_up_unchecked(self, form):
        checkboxes = [ctrl for ctrl in form._controls.values() if isinstance(ctrl, wx.CheckBox)]
        assert checkboxes   # sanity: the form actually has checkboxes
        for ctrl in checkboxes:
            ctrl.SetValue(True)   # simulate every checkbox having been checked
        form._clear_all_fields()
        for ctrl in checkboxes:
            assert ctrl.GetValue() is False

    def test_text_and_choice_fields_still_reset_to_blank(self, form):
        """Unaffected by this fix -- only checkbox handling changed."""
        text_ctrl = form._controls["policy_number"]
        text_ctrl.SetValue("PAI-2026-00441")
        choice_ctrl = form._controls["policy_status"]
        choice_ctrl.SetSelection(1)

        form._clear_all_fields()

        assert text_ctrl.GetValue() == ""
        assert choice_ctrl.GetSelection() == 0


class TestNativeWin32StateIsForcedInSync:
    """The desync theory: a checkbox checked via a raw Win32 message (like
    the agent does) may leave wx's own cached value already reading False
    even while the box visually stays checked -- meaning a later
    ctrl.SetValue(False) could believe there's nothing to do. Simulates
    that exact scenario directly against the real native control."""

    def test_native_control_is_forced_unchecked_via_bm_setcheck(self, form):
        import win32api
        BM_SETCHECK, BM_GETCHECK, BST_CHECKED = 0x00F1, 0x00F0, 1
        ctrl = form._controls["esign"]   # default=False
        # Check it at the NATIVE level only, exactly like agent.py does --
        # NOT via ctrl.SetValue(), so wx's own cached value never learns
        # about it.
        win32api.SendMessage(ctrl.GetHandle(), BM_SETCHECK, BST_CHECKED, 0)
        assert win32api.SendMessage(ctrl.GetHandle(), BM_GETCHECK, 0, 0) == BST_CHECKED

        form._clear_all_fields()

        assert win32api.SendMessage(ctrl.GetHandle(), BM_GETCHECK, 0, 0) != BST_CHECKED
