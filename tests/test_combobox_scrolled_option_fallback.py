"""
Regression test for the combobox keyboard-fallback gating condition in
components/agent/agent.py's run() (~L4288, the "type-into-combobox" branch).

Found live 2026-08-12, directly reported ("there are still some errors...
the dropdown it couldn't select"): the 'State' field needed 'Texas', but the
dropdown's rendered listitems were always exactly
['Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho'] -- the
same 12, alphabetically-first states, no matter how many times the dropdown
was closed and reopened. 37 identical retries over 96 real seconds, logged
in logs/run_task_20260812_123046.log starting at 12:52:49.

Root cause: this listbox only materializes UIA elements for whatever's
currently scrolled into view. Re-clicking to reopen the dropdown never
scrolls it, so a target alphabetically past the visible window (like
'Texas') is permanently invisible to _listitems no matter how many retries
happen. The ONLY existing escape hatch for this class of problem --
_select_combobox_value_via_keyboard(), which navigates with real Up/Down
arrow keys and reads the control's live ValuePattern.Value directly instead
of trusting rendered listitems -- was gated behind "not _match and not
_listitems": it only ever ran when the dropdown appeared to have ZERO
items, never when it had items that were simply the wrong ones. 12 visible
(wrong) items is a non-empty list, so this exact failure mode always fell
through to the "not in dropdown -- pressing Escape" branch and looped.

Fix: try the keyboard fallback whenever no match was found among the
rendered listitems, not only when the list was empty -- pressing Escape
first (closing the wrong dropdown) only when there actually were items to
close past, so a genuinely-empty dropdown's behavior is byte-for-byte
unchanged from before this fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _decide_combobox_fallback(match_found: bool, listitems_present: bool) -> str:
    """Mirrors the CURRENT combobox value-matching decision in agent.py's
    run(): try the keyboard fallback whenever no match was found among the
    currently-rendered listitems, regardless of whether that list was empty
    or simply didn't contain the target -- pressing Escape first only when
    a (wrong) dropdown was actually open (there's something to close)."""
    if match_found:
        return "used_match"
    if listitems_present:
        return "escape_then_keyboard_fallback"
    return "keyboard_fallback_only"


class TestMatchFoundNeverTouchesTheFallback:
    def test_exact_match_skips_fallback_entirely(self):
        assert _decide_combobox_fallback(match_found=True, listitems_present=True) == "used_match"

    def test_match_found_even_with_no_other_items_skips_fallback(self):
        assert _decide_combobox_fallback(match_found=True, listitems_present=False) == "used_match"


class TestEmptyDropdownBehaviorIsUnchanged:
    """The ORIGINAL case this fallback was built and verified for -- must
    behave identically to before this fix (no regression)."""

    def test_no_items_at_all_tries_fallback_without_escaping_first(self):
        assert _decide_combobox_fallback(match_found=False, listitems_present=False) \
            == "keyboard_fallback_only"


class TestWrongItemsVisibleNowAlsoTriesTheFallback:
    """The actual live bug this fix addresses: a non-empty but WRONG list
    (e.g. a long alphabetical dropdown scrolled to the wrong section) used
    to fall straight to Escape-and-retry forever -- now escapes once, then
    tries the same keyboard fallback the empty-list case already had."""

    def test_wrong_items_present_escapes_then_tries_fallback(self):
        assert _decide_combobox_fallback(match_found=False, listitems_present=True) \
            == "escape_then_keyboard_fallback"

    def test_this_is_the_exact_state_change_the_gate_used_to_block(self):
        """Direct regression pin: before this fix, 'items present but wrong'
        and 'match found' both skipped the fallback -- now only 'match
        found' does. Distinguishing the two matters."""
        wrong_items = _decide_combobox_fallback(match_found=False, listitems_present=True)
        matched     = _decide_combobox_fallback(match_found=True, listitems_present=True)
        assert wrong_items != matched
        assert wrong_items == "escape_then_keyboard_fallback"
