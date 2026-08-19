"""
Regression test for LLMAgent._reassert_form_window()'s streak-breaker --
added 2026-08-10 after a real, severe incident.

_reassert_form_window() runs unconditionally at the top of every step
(~1-2s cadence) and used to fight for foreground no matter what, including
a fake Alt-keypress specifically to defeat Windows' own anti-focus-
stealing protection. A user tried to click over to the Electron Stop
button and could not -- focus kept getting yanked back before they could
act -- and had to hard-shutdown their laptop to regain control of their
own mouse.

Fix: if the SAME foreign window is still foreground on the very next call
despite having just been reclaimed, that's not drift, it's the user
deliberately returning to it -- back off and stop fighting for that
window for the rest of the run. A single stray drift-and-recover (e.g. a
combobox dropdown transiently taking foreground) still gets handled
normally on its first occurrence, since that's real, legitimate anti-
drift behavior this method is also there to provide.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))
from agent.agent import LLMAgent

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_AGENT_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._locked_hwnd = 111  # pretend the form was locked to this hwnd
    return agent


def _install_fake_win32(monkeypatch, foreground_hwnd, form_pid=1234, fg_pid=9999,
                         fg_title="Some Other Window", fg_class="SomeClass"):
    fake_win32gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: foreground_hwnd,
        IsWindow=lambda hwnd: True,
        SetForegroundWindow=MagicMock(),
        GetWindowText=lambda hwnd: fg_title,
        GetClassName=lambda hwnd: fg_class,
    )

    def _get_thread_pid(hwnd):
        return (0, form_pid if hwnd == 111 else fg_pid)

    fake_win32process = types.SimpleNamespace(GetWindowThreadProcessId=_get_thread_pid)
    fake_pyautogui = types.SimpleNamespace(press=MagicMock())
    fake_win32com_client = types.SimpleNamespace(Dispatch=lambda name: MagicMock())
    fake_win32com = types.SimpleNamespace(client=fake_win32com_client)

    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com_client)
    return fake_win32gui


class TestFirstDriftStillGetsReclaimed:
    def test_a_single_occurrence_of_a_foreign_window_is_still_reclaimed(self, monkeypatch):
        """The streak-breaker must not weaken normal, legitimate anti-drift
        recovery on its first occurrence -- only repeated recurrence."""
        fake_win32gui = _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent = _make_agent()

        agent._reassert_form_window()

        fake_win32gui.SetForegroundWindow.assert_called_once_with(111)


class TestStreakBreaker:
    def test_the_same_foreign_window_recurring_stops_being_fought(self, monkeypatch):
        """The exact live scenario: user clicks the Stop button (hwnd 222),
        step N reclaims focus, user immediately clicks Stop again (still
        222) -- this second call must NOT steal it back again."""
        fake_win32gui = _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent = _make_agent()

        agent._reassert_form_window()  # 1st occurrence -- reclaimed
        agent._reassert_form_window()  # 2nd occurrence, same window -- must back off

        assert fake_win32gui.SetForegroundWindow.call_count == 1

    def test_backing_off_persists_across_further_calls_for_that_window(self, monkeypatch):
        """Once backed off, must not resume fighting a few steps later while
        the user is still on that same window."""
        fake_win32gui = _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent = _make_agent()

        for _ in range(5):
            agent._reassert_form_window()

        assert fake_win32gui.SetForegroundWindow.call_count == 1

    def test_returning_to_the_form_resets_the_streak(self, monkeypatch):
        """If the user's own click (or the agent's next action) brings the
        form back to foreground, a LATER unrelated drift must be treated as
        fresh -- not permanently poisoned by an earlier, different window."""
        agent = _make_agent()

        # Step 1: foreign window 222 -- reclaimed.
        fake_win32gui_1 = _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent._reassert_form_window()
        assert fake_win32gui_1.SetForegroundWindow.call_count == 1

        # Step 2: form is foreground again (as if the reclaim worked and
        # nothing has drifted since) -- streak state should reset.
        fake_win32gui_2 = _install_fake_win32(monkeypatch, foreground_hwnd=111)
        agent._reassert_form_window()
        assert fake_win32gui_2.SetForegroundWindow.call_count == 0  # already foreground, no-op

        # Step 3: a DIFFERENT foreign window (333) drifts in -- must be
        # treated as a fresh first occurrence, not blocked by the earlier
        # streak against window 222.
        fake_win32gui_3 = _install_fake_win32(monkeypatch, foreground_hwnd=333)
        agent._reassert_form_window()
        assert fake_win32gui_3.SetForegroundWindow.call_count == 1

    def test_a_new_foreign_window_after_a_backed_off_one_is_still_reclaimed(self, monkeypatch):
        """Backing off from window 222 must not also block reclaiming a
        DIFFERENT window 333 that shows up right after."""
        agent = _make_agent()

        fake_win32gui_222 = _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent._reassert_form_window()  # reclaimed
        agent._reassert_form_window()  # backed off
        assert fake_win32gui_222.SetForegroundWindow.call_count == 1

        fake_win32gui_333 = _install_fake_win32(monkeypatch, foreground_hwnd=333)
        agent._reassert_form_window()  # fresh window -- reclaimed once

        assert fake_win32gui_333.SetForegroundWindow.call_count == 1


class TestBackoffDiagnosticLogging:
    """Added 2026-08-10, direct live report: 'clicked the form, literally
    nothing happened' -- the streak-breaker's own warning used to log only
    a bare hwnd NUMBER, useless after the fact (the window may not even
    exist anymore by the time anyone reads the log). Now looks up and logs
    the foreign window's title, class, and whether it's owned by the SAME
    process as the locked form -- the concrete evidence needed to tell
    apart 'the user genuinely clicked away' from 'some other window (the
    ghost overlay, an unrelated popup, or a same-process wx dialog our own
    modal-dismiss logic never got a chance to run on) is silently sitting
    on top'."""

    def test_backoff_warning_includes_title_class_and_same_process_flag(self, monkeypatch, caplog):
        import logging
        _install_fake_win32(monkeypatch, foreground_hwnd=222, form_pid=1234, fg_pid=9999,
                             fg_title="Mystery Window", fg_class="MysteryClass")
        agent = _make_agent()

        with caplog.at_level(logging.WARNING):
            agent._reassert_form_window()  # reclaimed
            agent._reassert_form_window()  # backs off -- this is the one that logs

        backoff_lines = [r.message for r in caplog.records if "NOT re-stealing foreground" in r.message]
        assert len(backoff_lines) == 1
        assert "Mystery Window" in backoff_lines[0]
        assert "MysteryClass" in backoff_lines[0]
        assert "same_process_as_form=False" in backoff_lines[0]

    def test_backoff_warning_flags_same_process_windows_true(self, monkeypatch, caplog):
        """The case that matters most: a same-process window (e.g. a wx
        dialog) should be clearly distinguishable in the log from an
        unrelated third-party window."""
        import logging
        _install_fake_win32(monkeypatch, foreground_hwnd=222, form_pid=1234, fg_pid=1234,
                             fg_title="Missing Required Fields", fg_class="wxDialogClassNR")
        agent = _make_agent()

        with caplog.at_level(logging.WARNING):
            agent._reassert_form_window()
            agent._reassert_form_window()

        backoff_lines = [r.message for r in caplog.records if "NOT re-stealing foreground" in r.message]
        assert len(backoff_lines) == 1
        assert "same_process_as_form=True" in backoff_lines[0]

    def test_a_lookup_failure_does_not_prevent_backing_off(self, monkeypatch):
        """The diagnostic lookup is best-effort -- if GetWindowText/
        GetClassName raise (window closed by the time we ask, etc.), the
        actual safety-critical behavior (backing off, not fighting the
        user for foreground) must still happen."""
        fake_win32gui = types.SimpleNamespace(
            GetForegroundWindow=lambda: 222,
            IsWindow=lambda hwnd: True,
            SetForegroundWindow=MagicMock(),
            GetWindowText=MagicMock(side_effect=RuntimeError("window gone")),
            GetClassName=MagicMock(side_effect=RuntimeError("window gone")),
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (0, 1234 if hwnd == 111 else 9999))
        monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
        monkeypatch.setitem(sys.modules, "win32process", fake_win32process)
        monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(press=MagicMock()))
        agent = _make_agent()

        agent._reassert_form_window()  # must not raise
        agent._reassert_form_window()  # must not raise, must still back off

        assert fake_win32gui.SetForegroundWindow.call_count == 1


class TestUserReachingForStopFlag:
    """New finding, direct live report: "the Stop button... never actually
    reached the backend... the agent's own log shows it clearly saw the
    user trying [to reach it]." Backing off from re-stealing foreground
    only ever stopped ONE action (SetForegroundWindow) -- the main
    per-step loop (agent.py's run(), not this method) kept moving the
    mouse/typing on the form the whole time regardless, still physically
    fighting the user for the OS mouse cursor even though window focus
    correctly stayed on Electron. This flag is the signal the main loop
    needs in order to actually pause taking real actions while the user
    is reaching for Stop -- not just stop re-stealing window focus."""

    def test_flag_starts_false(self):
        agent = _make_agent()
        assert agent._user_reaching_for_stop is False

    def test_flag_set_true_once_backing_off(self, monkeypatch):
        _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent = _make_agent()

        agent._reassert_form_window()  # 1st occurrence -- reclaimed, not backing off yet
        assert agent._user_reaching_for_stop is False
        agent._reassert_form_window()  # 2nd occurrence -- backs off

        assert agent._user_reaching_for_stop is True

    def test_flag_resets_false_once_form_regains_focus(self, monkeypatch):
        agent = _make_agent()
        _install_fake_win32(monkeypatch, foreground_hwnd=222)
        agent._reassert_form_window()
        agent._reassert_form_window()
        assert agent._user_reaching_for_stop is True

        _install_fake_win32(monkeypatch, foreground_hwnd=111)  # form is foreground again
        agent._reassert_form_window()

        assert agent._user_reaching_for_stop is False

    def test_main_loop_pauses_real_actions_while_the_flag_is_set(self):
        """The flag alone does nothing unless the main step loop actually
        checks it. Source-level regression test, same pattern used
        throughout tests/test_ask_llm_deep_reasoning.py -- the real loop
        is one giant method, too costly to drive end-to-end just to check
        this. Confirms the check sits directly after _reassert_form_window()
        and before self._observe(), so a step where the user is reaching
        for Stop never even gets as far as predicting or acting."""
        anchor = "self._reassert_form_window()"
        idx = _AGENT_SOURCE.index(anchor)
        window = _AGENT_SOURCE[idx:idx + 1300]
        assert "self._user_reaching_for_stop" in window, (
            "the main loop must check the flag right after calling _reassert_form_window()"
        )
        assert "continue" in window, "must skip the rest of this step's action-taking, not just log"
        observe_idx = window.index("self._observe()")
        flag_idx = window.index("self._user_reaching_for_stop")
        assert flag_idx < observe_idx, "the flag check must come BEFORE observing/acting, not after"
