"""
Regression test for a real, live-reported bug: "Stop is not working" in the
Electron recorder (2026-08-10, direct user report while testing the app for
the first time -- this UI had never actually been visible/interactive
before tonight, so this race was never caught).

Root cause, confirmed by reading DemoRecorder.run() directly, not guessed:
run() did `self._quit_event = threading.Event()` as its first real
statement -- REPLACING the event object __init__ already created with a
brand-new one. If app/recorder_bridge.py's stop() (or app/main.py's _stop())
calls `self._recorder._quit_event.set()` in the narrow window BEFORE run()
reaches that reassignment, the "stop requested" signal lands on the OLD
event object. run() then waits on the NEW (still-unset) event forever --
the recording thread never returns, Stop silently does nothing.

Every real caller (app/main.py, app/recorder_bridge.py, record_trace.py)
constructs a brand-new DemoRecorder right before calling .run() exactly
once -- nothing ever reuses an instance across multiple runs, so recreating
the event inside run() was never actually necessary. Fixed by removing the
reassignment; __init__'s event is always already fresh.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder.recorder as recorder_module


class _FakeListener:
    """Stands in for pynput's real Listener -- no global input hooks, no
    real thread work, just enough surface for DemoRecorder.run() to use."""
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass
    def stop(self):
        pass
    def is_alive(self):
        return True
    def join(self, timeout=None):
        pass


def _make_recorder(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "_pynput_mouse", MagicMock(Listener=_FakeListener))
    monkeypatch.setattr(recorder_module, "_pynput_keyboard", MagicMock(Listener=_FakeListener))
    return recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="form_filling")


class TestQuitEventSurvivesARaceWithRun:
    def test_set_before_run_still_stops_promptly(self, tmp_path, monkeypatch):
        """The exact live bug: stop() called in the narrow window before
        run() used to reassign _quit_event. Must not hang."""
        rec = _make_recorder(tmp_path, monkeypatch)

        rec._quit_event.set()  # simulates stop() arriving before run() starts

        done = threading.Event()

        def _run():
            rec.run()
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        finished = done.wait(timeout=5.0)

        assert finished, "run() never returned -- the quit signal was lost, same as the live bug"

    def test_quit_event_identity_is_not_reassigned_by_run(self, tmp_path, monkeypatch):
        """Direct check on the actual root cause: run() must not replace the
        object __init__ created, even mid-execution."""
        rec = _make_recorder(tmp_path, monkeypatch)
        original_event = rec._quit_event
        rec._quit_event.set()

        rec.run()

        assert rec._quit_event is original_event

    def test_set_shortly_after_run_starts_also_stops(self, tmp_path, monkeypatch):
        """Sanity check the other direction -- stop() arriving a moment
        after run() has already started must still work (this direction was
        never broken, confirming the fix didn't just narrow the window)."""
        rec = _make_recorder(tmp_path, monkeypatch)
        done = threading.Event()

        def _run():
            rec.run()
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        import time
        time.sleep(0.1)
        rec._quit_event.set()

        finished = done.wait(timeout=5.0)
        assert finished
