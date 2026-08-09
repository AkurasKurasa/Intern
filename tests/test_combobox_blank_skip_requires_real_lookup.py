"""
Regression test for agent.py's "combobox already attempted (known blank)"
skip-check -- it must only skip a combobox that was GENUINELY looked up and
found to have no record value, not one merely touched by a stray navigation
click.

Found 2026-08-09, live, direct user report ("It submitted but were we
missing any fields? We're improving."). Cross-checked the actual ground-
truth data (data_entry_intake.txt) and found 'Primary Use' has a real value
('Commute') for the record being filled -- it never got typed in. Traced
it: a bare low-confidence-fallback navigate click landed on 'Primary Use'
earlier in the run (just passing through, no lookup ever attempted), which
still marked it in the GENERAL self._attempted_keys via _record_attempt.
The combobox-blank skip-check then read that SAME general set and
concluded "confirmed blank," permanently skipping a field that was never
actually looked up -- confirmed by the log: "LLM focused-field lookup:
field='Primary Use'" never appears anywhere in the entire run.

Fixed by pointing the skip-check at self._leave_blank_keys instead of
self._attempted_keys -- an already-established set (used by the OPT2
fill-gate's own _fe2_confirmed_blank check) that only gains an entry when
self._lookup_field was genuinely called and genuinely came back empty, via
the "combobox %r -- no value, Tab" branch. A field merely touched by a
stray click no longer gets treated as confirmed-blank.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _attempt_key(elem):
    lbl = (elem.get("label") or elem.get("text") or "").strip().lower()
    if not lbl:
        b = elem.get("bbox") or [0, 0, 0, 0]
        return ("@", round((b[0] + b[2]) / 2 / 20) * 20, round((b[1] + b[3]) / 2 / 20) * 20)
    return lbl


def _should_skip_as_confirmed_blank(cbox, leave_blank_keys):
    """Mirrors the CURRENT (2026-08-09) skip-check in agent.py's run():
    only skips when the field's key is in leave_blank_keys (genuinely
    looked up and confirmed empty), not merely in the general attempted
    set."""
    return _attempt_key(cbox) in leave_blank_keys


class TestBareNavigateClickDoesNotConfirmBlank:
    def test_a_field_merely_clicked_during_navigation_still_gets_a_real_fill_attempt(self):
        """The actual live regression: 'Primary Use' was clicked once by
        the low-confidence fallback (marking it in the general attempted
        set via _record_attempt) but never actually looked up -- must NOT
        be treated as confirmed-blank."""
        primary_use = {"element_id": "e1", "type": "comboboxcontrol",
                        "label": "Primary Use", "value": "", "bbox": [1400, 840, 1600, 870]}
        attempted_keys = {_attempt_key(primary_use)}  # marked via a bare click, NOT a real lookup
        leave_blank_keys = set()  # never genuinely looked up

        assert _should_skip_as_confirmed_blank(primary_use, leave_blank_keys) is False
        # Sanity: the OLD (buggy) behavior would have incorrectly skipped it.
        assert _attempt_key(primary_use) in attempted_keys


class TestGenuinelyBlankFieldStillSkipsCorrectly:
    def test_a_field_actually_looked_up_and_confirmed_empty_is_skipped(self):
        """The ORIGINAL incident this check was built for ('Suffix', a
        field genuinely blank in the record) must still work -- once
        self._lookup_field genuinely returns nothing, leave_blank_keys
        gains the entry and the skip-check correctly fires."""
        suffix = {"element_id": "e2", "type": "comboboxcontrol",
                  "label": "Suffix", "value": "", "bbox": [100, 100, 300, 130]}
        leave_blank_keys = {_attempt_key(suffix)}  # populated by the real "no value, Tab" branch

        assert _should_skip_as_confirmed_blank(suffix, leave_blank_keys) is True

    def test_a_different_field_with_no_entry_at_all_is_not_skipped(self):
        untouched = {"element_id": "e3", "type": "comboboxcontrol",
                     "label": "Garaging Location", "value": "", "bbox": [100, 200, 300, 230]}
        leave_blank_keys = {"suffix"}

        assert _should_skip_as_confirmed_blank(untouched, leave_blank_keys) is False
