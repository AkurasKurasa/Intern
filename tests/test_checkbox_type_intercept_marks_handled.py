"""
Regression test for the "type into a checkbox" intercept path
(components/agent/agent.py, ~L4502-4573) failing to mark a "leave
unchecked" checkbox as handled -- a real infinite loop found live.

Direct report: "it halted at Drivers and it's not moving." Traced with
real log evidence (run_task_20260821_001922.log): "Checkbox 'Driver 2 --
SR-22 Required' already checked -- redirecting instead of blind Tab."
fired repeatedly, even while the step's own focused field had already
moved on to a DIFFERENT checkbox ('Driver 2 -- Excluded Driver') -- the
redirect kept sending the run back to the same already-resolved field.

Root cause: this block has TWO branches -- "should check" (line ~4507)
and "should NOT check, i.e. leave unchecked" (line ~4533) -- gated on
`_flabel not in self._checked_fields`. The "should check" branch adds
itself to self._checked_fields once handled (line ~4518). The "leave
unchecked" branch never does -- it unchecks the box (or confirms it's
already unchecked) and moves on, but never records that fact. The very
next time this same checkbox is offered as a redirect target (a normal,
expected occurrence -- nothing else marks it attempted from THIS path in
a way that reliably excludes it going forward), the exact same "leave
unchecked" logic runs again, forever, since self._checked_fields never
remembers it.

A second, related inconsistency: the "already handled" check on both
branches compares the BARE label (`_flabel`, from the focused element's
own accessible Name) against self._checked_fields, but the "should check"
branch's own bookkeeping adds the SECTION-QUALIFIED label (`_flabel_full`,
via _detect_section) instead -- a key-format mismatch that, independent
of the missing add(), means the "already handled" check can never
correctly recognize a bare-vs-full mismatched entry either. Fixed by
using `_flabel_full` consistently for the check AND the add, in BOTH
branches.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _checkbox_intercept_window():
    idx = _SOURCE.index('if _fel.get("type") == "checkboxcontrol":')
    return _SOURCE[idx:idx + 7000]


class TestCheckboxLeaveUncheckedBranchMarksItselfHandled:
    def test_should_check_branch_adds_to_checked_fields(self):
        """Baseline -- confirms the existing, already-correct branch's own
        bookkeeping call is still present and unbroken by this fix."""
        window = _checkbox_intercept_window()
        should_chk_idx = window.index("if _should_chk and")
        not_should_chk_idx = window.index("elif not _should_chk and")
        section = window[should_chk_idx:not_should_chk_idx]
        assert "self._checked_fields.add(_flabel_full)" in section

    def test_leave_unchecked_branch_also_adds_to_checked_fields(self):
        """The actual fix -- the branch that decides a checkbox should
        stay unchecked must record that decision, or it gets re-evaluated
        forever every time it's offered as a redirect target again."""
        window = _checkbox_intercept_window()
        not_should_chk_idx = window.index("elif not _should_chk and")
        # bounded to this branch's own body, not spilling into the next
        # top-level statement (`else:` for the non-checkbox case)
        else_idx = window.index("\n                    else:\n", not_should_chk_idx)
        section = window[not_should_chk_idx:else_idx]
        assert "self._checked_fields.add(_flabel_full)" in section

    def test_both_branches_check_membership_using_the_same_key_they_store(self):
        """Real second bug: the membership check used _flabel (bare) while
        the store used _flabel_full (section-qualified) -- a mismatch that
        alone would make self._checked_fields never actually recognize a
        previously-handled field, regardless of the missing add() fix
        above. Both the check and the store must use the same key."""
        window = _checkbox_intercept_window()
        assert window.count("_flabel_full not in self._checked_fields") == 2
        assert "_flabel not in self._checked_fields" not in window

    def test_fix_does_not_touch_the_should_check_branchs_own_behavior(self):
        """No regression -- the should-check branch's actual checking
        mechanism (BM_SETCHECK, BST_CHECKED) must be completely
        untouched by this fix."""
        window = _checkbox_intercept_window()
        assert 'SendMessage(_hw, 0x00F1, 1, 0)  # BM_SETCHECK, BST_CHECKED' in window

    def test_fix_does_not_touch_the_leave_unchecked_branchs_own_uncheck_logic(self):
        """No regression -- the actual BM_GETCHECK/BM_SETCHECK-to-0
        uncheck mechanism must be completely untouched by this fix."""
        window = _checkbox_intercept_window()
        assert "BM_GETCHECK, BM_SETCHECK, BST_CHECKED = 0x00F0, 0x00F1, 1" in window
        assert "SendMessage(_hw, BM_SETCHECK, 0, 0)  # BST_UNCHECKED" in window
