"""
Regression test for agent.py's [FOCUS-DIAG] diagnostic (logging-only, no
behavior change).

Found 2026-08-09, live, direct user report ("Check most recent logs,
skipped most fields in Vehicle after Current Mileage, what the fuck.").
logs/latest.log confirmed, by cross-checking car_insurance_form_wx.py's own
field order, that 'Annual Miles Est.' (the very next field after 'Current
Mileage') and 'Garaging Location' were both silently skipped -- never once
mentioned anywhere in the log, not even as a blank-field lookup. A plain
navigate click landed right where 'Annual Miles Est.' should sit ("Focus
moved after click" = ok), but the next step never logged "LLM focused-field
lookup" for it, meaning the OPT2 fast path's `_t_is_type` check evaluated
False -- with nothing in the log explaining why.

Rather than guess (the field could be missing from `state["elements"]`
this cycle due to element-id churn -- the same bug class already fixed
twice elsewhere in this file, for state_validator.py and _attempt_key --
or the click could have genuinely landed on a non-fillable/already-handled
element), added a diagnostic that logs the EXACT reason the next time this
happens: either "focused id not found in this scan" (churn) or the
found element's own type/value/attempted flags. Logging only, no
behavior change -- this test mirrors the exact condition and message
shape rather than driving the full 6000+ line run() method.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _focus_diag(fid2, fe2, fe2_ty, fe2_val, fe2_chk_attempted, fe2_confirmed_blank,
                 fe2_already_attempted, t_is_type, elements_count):
    """Mirrors the CURRENT (2026-08-09) [FOCUS-DIAG] block in agent.py's
    run(): returns the diagnostic message that WOULD be logged, or None if
    the diagnostic doesn't fire (matching the real code's condition
    exactly)."""
    if fid2 and not t_is_type and not (fe2 and fe2_ty in ("checkboxcontrol", "checkbox")):
        if fe2 is None:
            return ("id_churn", fid2, elements_count)
        return ("found_but_excluded", fe2_ty, fe2_val, fe2_chk_attempted,
                 fe2_confirmed_blank, fe2_already_attempted)
    return None


class TestFocusDiagFiresOnTheIdChurnCase:
    def test_focused_id_missing_from_this_scan_logs_churn_diagnostic(self):
        result = _focus_diag(
            fid2="elem_42", fe2=None, fe2_ty="", fe2_val="",
            fe2_chk_attempted=False, fe2_confirmed_blank=False,
            fe2_already_attempted=False, t_is_type=False, elements_count=159)
        assert result == ("id_churn", "elem_42", 159)


class TestFocusDiagFiresOnTheFoundButExcludedCase:
    def test_wrong_type_focused_logs_what_it_actually_was(self):
        """The actual live-suspected case: something DID get focused after
        the navigate click, but it wasn't recognized as fillable -- must
        log its real type/value instead of staying silent."""
        result = _focus_diag(
            fid2="elem_7", fe2={"label": "Annual Miles Est.", "type": "label", "value": ""},
            fe2_ty="label", fe2_val="", fe2_chk_attempted=False,
            fe2_confirmed_blank=False, fe2_already_attempted=False,
            t_is_type=False, elements_count=159)
        assert result == ("found_but_excluded", "label", "", False, False, False)

    def test_already_non_empty_field_logs_the_exclusion_reason(self):
        result = _focus_diag(
            fid2="elem_9", fe2={"label": "Current Mileage", "type": "editcontrol", "value": "38450"},
            fe2_ty="editcontrol", fe2_val="38450", fe2_chk_attempted=False,
            fe2_confirmed_blank=False, fe2_already_attempted=False,
            t_is_type=False, elements_count=159)
        assert result == ("found_but_excluded", "editcontrol", "38450", False, False, False)


class TestFocusDiagStaysSilentWhenNotNeeded:
    def test_does_not_fire_when_the_fast_path_is_about_to_handle_it_normally(self):
        """t_is_type=True means the normal fill path is about to run --
        no mystery to diagnose, must not fire."""
        result = _focus_diag(
            fid2="elem_9", fe2={"label": "Annual Miles Est.", "type": "editcontrol", "value": ""},
            fe2_ty="editcontrol", fe2_val="", fe2_chk_attempted=False,
            fe2_confirmed_blank=False, fe2_already_attempted=False,
            t_is_type=True, elements_count=159)
        assert result is None

    def test_does_not_fire_when_nothing_is_focused_at_all(self):
        result = _focus_diag(
            fid2=None, fe2=None, fe2_ty="", fe2_val="",
            fe2_chk_attempted=False, fe2_confirmed_blank=False,
            fe2_already_attempted=False, t_is_type=False, elements_count=159)
        assert result is None

    def test_does_not_fire_for_checkboxes_already_explained_by_toggle_state(self):
        """Checkboxes have their own, already-diagnosed reason for reading
        value='' forever (ui_observer's TogglePattern fix, 2026-08-09
        earlier tonight) -- re-flagging them here would just be noise for
        an already-understood case."""
        result = _focus_diag(
            fid2="elem_3", fe2={"label": "Homeowner", "type": "checkboxcontrol", "value": ""},
            fe2_ty="checkboxcontrol", fe2_val="", fe2_chk_attempted=True,
            fe2_confirmed_blank=False, fe2_already_attempted=False,
            t_is_type=False, elements_count=159)
        assert result is None
