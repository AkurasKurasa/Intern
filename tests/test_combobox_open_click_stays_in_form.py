"""
Regression test for agent.py's 'CLICK on empty combobox -> treat as FILL'
branch -- the click must be refused if the target position falls outside the
locked form window, not just if it lands on a known button inside the form.

Found 2026-08-09, live, direct user report ("It closed the fucking Terminal
holy shit"), TWICE -- and deterministically reproducible: three separate
live runs (logs/run_task_20260809_182304.log, .../183242.log which crashed
before it could even open a log file, and .../183727.log == latest.log) all
died at the EXACT same point: 'Payment Frequency' combobox, click @
(1458, 900), and then NOTHING -- no further log line at all, ever, in any of
the three runs. No exception, no traceback, consistent with the OS process
itself receiving a close signal mid-call (Windows sends CTRL_CLOSE_EVENT to
a console process when its terminal window is closed; with no handler
registered, the default action is immediate termination with nothing more
flushed -- exactly matching every run's tail).

The FIRST fix (tests/test_executor_hotkey_allowlist.py) addressed a real
vulnerability -- the LLM's own prompt taught it "alt+f4" as example hotkey
vocabulary, unfiltered all the way to pyautogui.hotkey. But that mechanism
requires a KEYBOARD hotkey action. Every one of these three crashes has no
keyboard action anywhere nearby -- it's a bare, unconditional CLICK. That
fix was real but did not address THIS mechanism, which is why the exact same
symptom recurred immediately after it shipped.

Traced end to end this time: the combobox-open branch already got a guard
against clicking a known BUTTON element at its target position
(_find_destructive_button_at, see test_destructive_button_guard.py's
TestComboboxOpenBranchAlsoRefusesAnyButtonClick, added 2026-08-09 for the
earlier 'Clear All modal' incident) -- but that check only scans
state['elements'], which only contains elements from the FORM's own
accessibility tree. It has no way to know a point lands in a completely
DIFFERENT window (like the terminal), because that window's controls were
never scanned into state['elements'] at all.

The sibling navigate-click branch (a few hundred lines below in agent.py)
already had exactly this protection -- self._point_in_form(_snap2, state),
checked against the LIVE win32gui.GetWindowRect of the locked form window --
added for a previous, different incident ("[GUARD] target ... OUTSIDE form
window"). The combobox-open branch reused the identical raw _snap2 target
but never got this specific check wired in, only the button one. Same class
of gap _find_destructive_button_at itself was already generalized for once
(from keyword-list to blanket-button-refusal) -- one more copy of the same
missing guard, not a new mechanism, and not evidence the first fix was
wrong -- just incomplete for a DIFFERENT failure mode sharing the same input.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import _find_destructive_button_at


def _point_in_form(pos, form_rect, margin=6):
    """Mirrors agent.py's AutomationAgent._point_in_form -- same formula,
    with the live win32gui.GetWindowRect() result passed in directly instead
    of fetched via self._form_rect(state), so this can be tested without a
    real window or a real AutomationAgent instance."""
    if not pos or len(pos) < 2:
        return False
    l, t, r, b = form_rect
    return (l - margin) <= pos[0] <= (r + margin) and (t - margin) <= pos[1] <= (b + margin)


def _run_combobox_open_click_guard(elements, click_pos, form_rect, executor):
    """Mirrors the CURRENT (fixed) combobox-open branch in agent.py: checks
    self._point_in_form FIRST (catches drift into another window entirely),
    then _find_destructive_button_at (catches known buttons inside the
    form) -- same order as the sibling navigate branch."""
    if not _point_in_form(click_pos, form_rect):
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return False
    btn = _find_destructive_button_at(elements, click_pos)
    if btn is not None:
        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]})
        return False
    executor.execute({"action_type": "click", "click_position": click_pos})
    return True


class TestComboboxOpenClickRefusesToLeaveTheFormWindow:
    def test_refuses_a_click_far_outside_the_form_the_exact_live_mechanism(self):
        """The actual live regression: a combobox's observed bbox center
        lands well outside the form's real, current window rect -- must be
        refused before ever reaching pyautogui, exactly like the terminal
        window sitting at that same screen position must never receive it."""
        executor = MagicMock()
        combo = {"type": "comboboxcontrol", "text": "Payment Frequency",
                 "label": "Payment Frequency", "bbox": [1300, 820, 1616, 980]}
        form_rect = (0, 0, 1280, 800)   # form window doesn't reach (1458, 900) at all

        clicked = _run_combobox_open_click_guard([combo], [1458, 900], form_rect, executor)

        assert clicked is False
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]
        executor.execute.assert_called_once()  # never a second call that could be the click

    def test_still_opens_a_normal_combobox_dropdown_inside_the_form(self):
        executor = MagicMock()
        combo = {"type": "comboboxcontrol", "text": "Payment Frequency",
                 "label": "Payment Frequency", "bbox": [1300, 820, 1600, 870]}
        form_rect = (0, 0, 1920, 1080)

        clicked = _run_combobox_open_click_guard([combo], [1458, 850], form_rect, executor)

        assert clicked is True
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "click", "click_position": [1458, 850]}]

    def test_point_in_form_check_runs_before_the_button_check(self):
        """Both guards must fire correctly regardless of order, but the
        out-of-form case is checked first -- a point outside the form can't
        be meaningfully compared against the form's OWN button elements."""
        executor = MagicMock()
        btn = {"type": "buttoncontrol", "text": "Clear All", "label": "Clear All",
               "bbox": [1400, 830, 1520, 870]}
        form_rect = (0, 0, 1280, 800)

        clicked = _run_combobox_open_click_guard([btn], [1459, 848], form_rect, executor)

        assert clicked is False
        calls = [c.args[0] for c in executor.execute.call_args_list]
        assert calls == [{"action_type": "keyboard", "key_count": 1, "keystrokes": ["tab"]}]

    def test_margin_allows_a_click_just_barely_at_the_form_edge(self):
        """_point_in_form has a small margin (6px) for edge rounding -- a
        target just outside the strict rect but within the margin must
        still be allowed, matching the sibling navigate branch's behavior."""
        executor = MagicMock()
        combo = {"type": "comboboxcontrol", "text": "Payment Frequency",
                 "label": "Payment Frequency", "bbox": [1300, 820, 1600, 870]}
        form_rect = (0, 0, 1280, 800)

        clicked = _run_combobox_open_click_guard([combo], [1284, 800], form_rect, executor)

        assert clicked is True

    def test_click_well_beyond_the_margin_is_still_refused(self):
        executor = MagicMock()
        combo = {"type": "comboboxcontrol", "text": "Payment Frequency",
                 "label": "Payment Frequency", "bbox": [1300, 820, 1616, 980]}
        form_rect = (0, 0, 1280, 800)

        clicked = _run_combobox_open_click_guard([combo], [1458, 900], form_rect, executor)

        assert clicked is False


class TestPointInFormMirror:
    def test_true_for_a_point_inside_the_rect(self):
        assert _point_in_form([500, 400], (0, 0, 1280, 800)) is True

    def test_false_for_a_point_below_the_form_the_live_incidents_shape(self):
        """(1458, 900) against a form that ends at y=800 -- the exact shape
        of the reproduced live crash (a field near the bottom of a tab,
        close to or past the window's real bottom edge)."""
        assert _point_in_form([1458, 900], (0, 0, 1280, 800)) is False

    def test_false_for_missing_or_short_position(self):
        assert _point_in_form(None, (0, 0, 1280, 800)) is False
        assert _point_in_form([500], (0, 0, 1280, 800)) is False
