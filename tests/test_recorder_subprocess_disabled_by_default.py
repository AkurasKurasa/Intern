"""
Regression test: DemoRecorder's snapshot subprocess is disabled by default.

Found 2026-08-10, live: under the Electron-hosted recorder (Electron ->
Node child_process.spawn -> Python bridge -> multiprocessing.Process
snapshot subprocess), the subprocess consistently failed to return real
data -- not intermittently, but from the very first request of every
session, before any user action at all ("[SNAP-DIAG] get() timed out:
Empty()"). Once the bounded request queue filled, it stayed permanently
wedged ("put() Full()") for the rest of the session -- every subsequent
step recorded empty state. CPU-delta sampling confirmed the subprocess
process itself was genuinely frozen (0% CPU), not just slow. The identical
code path tested standalone, outside this specific process tree, worked
fine -- the problem is specific to this multi-layer spawn chain and wasn't
fully root-caused before switching it off.

self._observer (constructed unconditionally in __init__ either way) is
documented in its own comment as "~10x faster" than the general-purpose
snapshot approach (foreground-window + Notepad only, not every visible
window). Verified live: 0.03-0.17s per call via the in-process path vs
0.8-2.1s via the subprocess, with real (non-empty) data every time.

Uses `_use_subprocess = False`, routing every _request_snapshot() call
through self._observer.snapshot() directly -- no multiprocessing, no
queues, no cross-process IPC to fail.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder.recorder as recorder_module


class _FakeMouseKeyboard:
    """Stands in for pynput's real Listener -- no global input hooks."""
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


class TestSubprocessDisabledByDefault:
    def test_use_subprocess_defaults_to_false(self, tmp_path, monkeypatch):
        rec = _make_recorder(tmp_path, monkeypatch)
        assert rec._use_subprocess is False

    def test_snap_proc_is_never_started(self, tmp_path, monkeypatch):
        rec = _make_recorder(tmp_path, monkeypatch)
        assert rec._snap_proc is None

    def test_observer_still_constructed_for_the_in_process_path(self, tmp_path, monkeypatch):
        """The whole fix depends on self._observer already existing --
        confirm __init__ still builds it regardless of subprocess mode."""
        rec = _make_recorder(tmp_path, monkeypatch)
        assert rec._observer is not None
        assert hasattr(rec._observer, "snapshot")

    def test_request_snapshot_routes_through_observer_not_queues(self, tmp_path, monkeypatch):
        """Confirms the actual routing -- no _req_q/_res_q access at all
        when _use_subprocess is False, matching the live fix."""
        rec = _make_recorder(tmp_path, monkeypatch)
        fake_state = {"application": "TestApp", "window_title": "Test", "elements": [{"x": 1}]}
        monkeypatch.setattr(rec._observer, "snapshot", lambda: fake_state)

        result = rec._request_snapshot()

        assert result == fake_state
        assert not hasattr(rec, "_req_q")
        assert not hasattr(rec, "_res_q")

    def test_request_snapshot_returns_empty_dict_on_observer_failure(self, tmp_path, monkeypatch):
        rec = _make_recorder(tmp_path, monkeypatch)

        def _boom():
            raise RuntimeError("UIA failed")
        monkeypatch.setattr(rec._observer, "snapshot", _boom)

        result = rec._request_snapshot()

        assert result == {}
