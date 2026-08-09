"""
Regression test for agent/executor.py's ActionExecutor._press_key -- a
"modifier+key" hotkey string is now only executed if it's on an explicit
allowlist of known-safe combos; anything else is refused.

Found 2026-08-09, live, direct user report ("It closed the fucking
Terminal holy shit"). The process died silently mid-run -- no traceback,
no "interrupted by user" line -- consistent with the TERMINAL window
itself receiving an OS-level close signal, not the target form (confirmed
directly: "It closed the fucking Terminal" -- the form was unaffected).

Traced the mechanism end-to-end, not guessed: agent.py's own LLM system
prompt explicitly lists `["alt+f4"] closes` as example hotkey vocabulary
(components/agent/agent.py, the "hotkey" action-rule text) -- and the
"hotkey" action-type handler takes `llm_action.get("keys", [])`
completely unfiltered and returns it straight through as the executor's
own `keystrokes` list:

    elif action_type == "hotkey":
        keys = llm_action.get("keys", [])
        ...
        return {"action_type": "keyboard", "key_count": len(keys),
                "keystrokes": keys}

_press_key's hotkey branch then called `pyautogui.hotkey(*key.split("+"))`
on whatever string arrived, with zero filtering -- so an LLM response
following its OWN prompt's example vocabulary (e.g. in a confusing or
stuck situation) could have executed alt+f4 verbatim, closing whatever
window had focus at that instant.

Fixed with an ALLOWLIST, not a blocklist -- the same lesson
_find_destructive_button_at's own history already taught this project
once (a keyword/blocklist approach is a whack-a-mole trap; refusing
everything not explicitly known-safe is far more robust). Also fixed the
prompt itself, which was actively teaching the LLM that alt+f4 was valid
vocabulary.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))


def _live_executor(monkeypatch):
    """A non-dry-run ActionExecutor with pyautogui mocked out, so no real
    OS input ever fires while still exercising the real _press_key logic."""
    import agent.executor as mod
    fake_pyautogui = MagicMock()
    monkeypatch.setattr(mod, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(mod, "_PYAUTOGUI_AVAILABLE", True)
    executor = mod.ActionExecutor(dry_run=False)
    return executor, fake_pyautogui


class TestDestructiveHotkeysAreRefused:
    def test_alt_f4_is_never_executed(self, monkeypatch):
        """The actual live regression's exact mechanism -- must be
        refused outright, not passed to pyautogui.hotkey at all."""
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("alt+f4")

        fake_pyautogui.hotkey.assert_not_called()

    def test_ctrl_w_is_never_executed(self, monkeypatch):
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("ctrl+w")

        fake_pyautogui.hotkey.assert_not_called()

    def test_arbitrary_unknown_combo_is_refused_by_default(self, monkeypatch):
        """Allowlist, not a blocklist -- anything not explicitly known-safe
        must be refused, not just a hand-picked set of scary names."""
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("win+d")

        fake_pyautogui.hotkey.assert_not_called()

    def test_full_execute_path_with_an_llm_style_hotkey_prediction_is_blocked(self, monkeypatch):
        """End-to-end: a prediction shaped exactly like what the 'hotkey'
        action-type handler produces from a raw LLM response must be
        blocked at execution time, not just at the unit level."""
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor.execute({"action_type": "keyboard", "key_count": 1, "keystrokes": ["alt+f4"]})

        fake_pyautogui.hotkey.assert_not_called()


class TestKnownSafeHotkeysStillWork:
    def test_ctrl_a_still_executes_normally(self, monkeypatch):
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("ctrl+a")

        fake_pyautogui.hotkey.assert_called_once_with("ctrl", "a")

    def test_ctrl_v_still_executes_normally(self, monkeypatch):
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("ctrl+v")

        fake_pyautogui.hotkey.assert_called_once_with("ctrl", "v")

    def test_plain_tab_is_unaffected_not_even_a_hotkey_combo(self, monkeypatch):
        """Sanity: single non-modifier keys never go through the allowlist
        check at all -- must still work exactly as before."""
        executor, fake_pyautogui = _live_executor(monkeypatch)

        executor._press_key("tab")

        fake_pyautogui.press.assert_called_once_with("tab")
        fake_pyautogui.hotkey.assert_not_called()
