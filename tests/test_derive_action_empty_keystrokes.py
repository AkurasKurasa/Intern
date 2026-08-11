"""
tests/test_derive_action_empty_keystrokes.py
=============================================
Regression tests for components/recorder/recorder.py's
ScreenObserver._derive_action_from() -- the shared function used by both
the live recorder and scripts/backfill_actions.py to turn raw mouse/
keyboard/clipboard events into a structured action dict.

Bug found 2026-08-11 tracing a real scripts/validate_transitions.py
failure (Objective 4, transition-mapping accuracy) on real recorded data:
session_20260808_144216 step 0 had keystrokes=[""] (an empty/malformed
key value), text="", focus unchanged, no value changed -- yet the
function still returned action_type="keyboard" unconditionally whenever
step_strokes was non-empty, regardless of whether anything meaningful
was actually derived from it. validate_transitions.py's own docstring is
explicit that a no-effect action "would mistrain the model on a
transition that didn't really happen" -- noop steps are already excluded
from its actionable-transitions denominator, so an empty/garbage stroke
group should be classified the same way, not elevated to its own
distinct "keyboard" action.

A first draft of this fix was broader -- suppressing the keyboard
classification for ANY stroke group that produced no typed text,
including a lone Tab/arrow/Escape press. Caught directly before shipping
by testing _derive_action_from({'key': 'tab'}) in isolation: Tab
genuinely does move focus on its own, and validate_transitions.py's
check_transition() already correctly validates empty-text keyboard
actions via a focus/value-changed check -- turning Tab into noop would
have destroyed real, legitimate training signal, not fixed anything.
The real fix is narrower: only suppress when every stroke is either a
blank/malformed key value or a BARE modifier (Shift/Ctrl/Alt/Win/Caps
Lock) with nothing else in the group -- keys with genuinely zero
standalone effect. Navigation keys (Tab, arrows, Escape, Page Up/Down,
Home/End, Insert/Delete, F-keys) still produce a real keyboard action
even with empty text, exactly as before this fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

from recorder.recorder import ScreenObserver


def _derive(strokes, mouse=None, clipboard=None):
    return ScreenObserver._derive_action_from(
        step_mouse=mouse or [], step_strokes=strokes, step_clipboard=clipboard or [])


class TestEmptyOrMalformedStrokesBecomeNoop:
    def test_blank_key_value_is_noop(self):
        """The exact real bug: a stroke whose key value is an empty
        string, found live in session_20260808_144216 step 0."""
        result = _derive([{"key": ""}])
        assert result["action_type"] == "noop"

    def test_multiple_blank_keys_is_noop(self):
        result = _derive([{"key": ""}, {"key": ""}])
        assert result["action_type"] == "noop"

    def test_lone_shift_is_noop(self):
        """A bare modifier with no companion key does nothing to any GUI
        by itself -- genuinely nothing for the model to learn from."""
        for mod in ("shift", "ctrl", "alt", "win", "caps lock"):
            result = _derive([{"key": mod}])
            assert result["action_type"] == "noop", f"{mod!r} should be noop alone"

    def test_multiple_bare_modifiers_together_is_noop(self):
        result = _derive([{"key": "shift"}, {"key": "ctrl"}])
        assert result["action_type"] == "noop"


class TestNavigationKeysStillProduceRealKeyboardActions:
    """The regression this fix must NOT introduce -- these keys have real,
    independent effects (moving focus, closing a dropdown) and were
    already correctly validated by validate_transitions.py's own
    focus/value-changed check for empty-text keyboard actions."""

    def test_lone_tab_is_still_a_keyboard_action(self):
        result = _derive([{"key": "tab"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == ""
        assert result["keystrokes"] == ["tab"]

    def test_lone_arrow_keys_are_still_keyboard_actions(self):
        for arrow in ("up", "down", "left", "right"):
            result = _derive([{"key": arrow}])
            assert result["action_type"] == "keyboard", f"{arrow!r} should stay a keyboard action"

    def test_lone_escape_is_still_a_keyboard_action(self):
        result = _derive([{"key": "esc"}])
        assert result["action_type"] == "keyboard"

    def test_lone_backspace_on_an_already_empty_field_is_still_kept(self):
        """A deliberate Backspace, even producing empty text (nothing left
        to delete), is a real intentional action worth keeping."""
        result = _derive([{"key": "backspace"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == ""


class TestNormalTypingIsUnaffected:
    def test_single_character(self):
        result = _derive([{"key": "a"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == "a"

    def test_shift_plus_letter_combo_produces_the_letter(self):
        result = _derive([{"key": "shift"}, {"key": "A"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == "A"

    def test_multi_character_word(self):
        result = _derive([{"key": k} for k in "hello"])
        assert result["action_type"] == "keyboard"
        assert result["text"] == "hello"

    def test_space_alone_is_a_keyboard_action(self):
        result = _derive([{"key": "space"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == " "

    def test_enter_alone_is_a_keyboard_action(self):
        result = _derive([{"key": "enter"}])
        assert result["action_type"] == "keyboard"
        assert result["text"] == "\n"


class TestFallsThroughToMouseOrNoopCorrectly:
    def test_empty_strokes_with_a_click_falls_through_to_click(self):
        """When the only strokes present are garbage, the function should
        behave exactly as if step_strokes had been empty in the first
        place -- falling through to check for a real mouse action."""
        result = _derive([{"key": ""}], mouse=[{"type": "click", "position": [10, 20]}])
        assert result["action_type"] == "click"
        assert result["click_position"] == [10, 20]

    def test_empty_strokes_with_no_mouse_either_is_noop(self):
        result = _derive([{"key": "shift"}], mouse=[])
        assert result["action_type"] == "noop"

    def test_clipboard_paste_still_takes_priority_over_garbage_strokes(self):
        result = _derive([{"key": ""}], clipboard=[{"text": "pasted value"}])
        assert result["action_type"] == "paste"
        assert result["text"] == "pasted value"
