"""
Regression test for agent.py's checkbox-check merge branch -- after checking
a checkbox via the raw BM_SETCHECK Win32 message (which never moves real
keyboard focus itself), the follow-up action must redirect to a KNOWN
visible target instead of a blind Tab.

Found 2026-08-09, live, direct user report ("Marital Status was not even
filled up until Cell Phone but we navigated away"): the blind Tab used here
handed movement to Windows' OWN native tab order -- which, independent of
anything this code decided, triggered a SECOND, uncontrolled auto-scroll
(element count dropped 163->157 in the log at this exact point). Stacked on
top of the redirect-to-deepest-cluster fix from earlier the same night, that
extra scroll left several shallower fields (Marital Status, Occupation,
Education Level, Credit Score) scrolled past -- and since this module only
ever scrolls DOWN, never back up, they were permanently unreachable for the
rest of the run.

Fixed by redirecting to a known, currently-visible empty target (a click,
not a guess) instead of gambling on native tab order -- the same principle
already applied to every other Tab-as-navigation removal this session.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.navigation_protocol import find_visible_empty_target

VIEWPORT_BOTTOM = 1000.0


def _field(label, value="", bbox=(100, 100, 300, 130), ftype="editcontrol"):
    return {"element_id": label, "type": ftype, "label": label, "value": value,
            "bbox": list(bbox), "window_role": "active"}


def _next_action_after_checkbox_check(state):
    """Mirrors the CURRENT (2026-08-09) post-checkbox-check branch in
    agent.py's run(): redirect to a known visible target via click instead
    of a blind Tab, falling back to Tab only if nothing is visible."""
    target = find_visible_empty_target(state, VIEWPORT_BOTTOM)
    if target and target.get("bbox"):
        b = target["bbox"]
        return {"action_type": "click", "click_position": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]}
    return {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}


class TestNoBlindTabAfterCheckboxCheck:
    def test_redirects_to_a_known_visible_target_instead_of_tab(self):
        """The actual live regression: fields like 'Marital Status' were
        still visible and empty right after a checkbox check -- must be
        clicked directly, not left to native Tab order."""
        state = {"elements": [
            _field("Homeowner", value="Checked", bbox=(100, 100, 300, 130), ftype="checkboxcontrol"),
            _field("Marital Status", value="", bbox=(100, 140, 300, 170), ftype="comboboxcontrol"),
        ]}
        action = _next_action_after_checkbox_check(state)
        assert action == {"action_type": "click", "click_position": [200.0, 155.0]}

    def test_falls_back_to_tab_when_nothing_else_is_visible(self):
        """No genuinely empty target exists yet -- still safe to fall back
        to a plain Tab rather than clicking nothing."""
        state = {"elements": [
            _field("Homeowner", value="Checked", bbox=(100, 100, 300, 130), ftype="checkboxcontrol"),
        ]}
        action = _next_action_after_checkbox_check(state)
        assert action == {"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}


class TestMarkAttemptedPreventsSameStepSelfRedirectLoop:
    """Found 2026-08-09, live, direct user report ("Another loop error"):
    just-checked 'Homeowner' kept getting offered back as the redirect
    target across MANY steps -- ui_observer.py's checkbox value read was the
    root cause (fixed separately, see test_ui_observer_checkbox_toggle_state.py),
    but even with that fixed, the redirect search inside the SAME step that
    performed the check still runs against the pre-check state snapshot,
    where the checkbox's value hasn't caught up yet. agent.py now calls
    self._mark_attempted() immediately after BM_SETCHECK succeeds, so the
    attempted-keys layer excludes it even before the next observation cycle
    would. This test proves find_visible_empty_target respects that,
    independent of a stale 'value' read."""

    def test_attempted_checkbox_is_skipped_even_if_its_value_still_reads_empty(self):
        from agent.navigation_protocol import find_visible_empty_target

        state = {"elements": [
            _field("Homeowner", value="", bbox=(100, 100, 300, 130), ftype="checkboxcontrol"),
            _field("Marital Status", value="", bbox=(100, 140, 300, 170), ftype="comboboxcontrol"),
        ]}

        def attempt_key_fn(elem, elements):
            return (elem.get("label") or "").strip().lower()

        attempted_keys = {"homeowner"}
        target = find_visible_empty_target(
            state, VIEWPORT_BOTTOM, attempted_keys=attempted_keys, attempt_key_fn=attempt_key_fn)
        assert target is not None
        assert target["label"] == "Marital Status"
