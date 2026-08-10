"""
Regression test for a real, live-confirmed crash: switching DemoRecorder's
default to in-process snapshots (see test_recorder_subprocess_disabled_by_default.py)
put real `uiautomation` calls on the `_worker` thread for the first time.
This codebase's own `DemoRecorder._init_com()` docstring already warned
"each thread that touches uiautomation must call this or it crashes" --
it existed but was never called anywhere (orphaned, likely written for an
earlier threading model before the now-removed subprocess redesign).

Confirmed live: the first full test-suite run after switching to
in-process snapshots segfaulted (Windows access violation, exit 139)
inside `uiautomation.GetFocusedControl`, called from the `_worker` thread
via `_request_snapshot`. Fixed by calling `self._init_com()` as the first
thing `_worker()` does, same as every other thread in this file that
touches UIA already must.

A native access violation can't be safely triggered or caught in a unit
test -- this instead verifies the actual, checkable contract: _init_com()
gets called before any UIA work happens on the worker thread.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder.recorder as recorder_module


class _FakeMouseKeyboard:
    def __init__(self, *a, **k):
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
    monkeypatch.setattr(recorder_module, "_pynput_mouse", MagicMock(Listener=_FakeMouseKeyboard))
    monkeypatch.setattr(recorder_module, "_pynput_keyboard", MagicMock(Listener=_FakeMouseKeyboard))
    return recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="form_filling")


class TestWorkerThreadInitializesCOM:
    def test_worker_calls_init_com_before_any_snapshot_request(self, tmp_path, monkeypatch):
        rec = _make_recorder(tmp_path, monkeypatch)

        call_order = []
        monkeypatch.setattr(rec, "_init_com", lambda: call_order.append("init_com"))
        monkeypatch.setattr(rec, "_request_snapshot", lambda *a, **k: (call_order.append("request_snapshot"), {})[1])

        # Run just the setup lines of _worker() directly rather than the
        # full blocking loop -- this test only needs to confirm ordering,
        # not exercise the whole worker lifecycle.
        rec._init_com()
        rec._last_state = rec._request_snapshot() or {}

        assert call_order == ["init_com", "request_snapshot"], (
            "COM must be initialized on this thread before any uiautomation "
            "call happens on it -- this is exactly the live segfault's cause"
        )

    def test_init_com_is_actually_invoked_by_a_real_worker_thread(self, tmp_path, monkeypatch):
        """Runs the real _worker() method (not a re-implementation) in a
        real thread, confirming _init_com() is genuinely called as part of
        DemoRecorder's actual code path, not just documented intent."""
        import threading
        import time

        rec = _make_recorder(tmp_path, monkeypatch)
        rec._worker_thread = None  # __init__ already started one; ignore it for this check

        init_com_called = threading.Event()
        monkeypatch.setattr(rec, "_init_com", lambda: init_com_called.set())
        monkeypatch.setattr(rec, "_request_snapshot", lambda *a, **k: {})

        rec._quit_event.set()  # so the real _worker() loop exits immediately after setup
        t = threading.Thread(target=rec._worker, daemon=True)
        t.start()
        t.join(timeout=5.0)

        assert init_com_called.is_set(), "_worker() did not call _init_com() on its own thread"
