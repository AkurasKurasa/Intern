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


def _make_agent():
    agent = LLMAgent(goal="test goal", dry_run=True, max_steps=1)
    agent._locked_hwnd = 111  # pretend the form was locked to this hwnd
    return agent


def _install_fake_win32(monkeypatch, foreground_hwnd, form_pid=1234, fg_pid=9999):
    fake_win32gui = types.SimpleNamespace(
        GetForegroundWindow=lambda: foreground_hwnd,
        IsWindow=lambda hwnd: True,
        SetForegroundWindow=MagicMock(),
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
