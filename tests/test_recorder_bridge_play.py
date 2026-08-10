"""
Regression tests for app/recorder_bridge.py's "play" command -- the
Workflows-tab feature that actually executes a recorded session live on
the form via DemoRecorder.replay() (pyautogui), distinct from the pure
file-duplication replay() the bridge already had.

Only the safe, non-executing parts are covered here (guards, path
resolution, event ordering) -- DemoRecorder is monkeypatched out entirely
so no test ever touches pyautogui or the real screen. Actually running a
live play is a real GUI-automation action and is the user's call to make
from the app, the same as every other live run in this project.
"""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


class TestPlayGuards:
    def test_refuses_while_recording(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._running = True

        bridge.play("data/demos/eight_Tabs/session_x", count=1)

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "recording" in errors[0]["message"].lower()

    def test_refuses_when_session_directory_does_not_exist(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.play(str(tmp_path / "nope"), count=1)

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not found" in errors[0]["message"].lower()

    def test_relative_session_path_resolves_against_root(self, monkeypatch, tmp_path):
        """A relative path (as sent by the renderer, e.g.
        'data/demos/eight_Tabs/session_x') must resolve against the repo
        root, not the bridge process's cwd at call time."""
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.play("this/does/not/exist/anywhere", count=1)

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert rb._ROOT in errors[0]["message"]


class TestPlayStartsAndDelegatesCorrectly:
    def test_valid_session_emits_play_started_and_calls_replay(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        session_dir = tmp_path / "session_20260101_000000"
        session_dir.mkdir()

        fake_recorder = MagicMock()
        fake_recorder.replay.return_value = 42
        monkeypatch.setattr(rb, "DemoRecorder", lambda **kwargs: fake_recorder)

        bridge.play(str(session_dir), count=3)

        # play_started must fire synchronously, before the background
        # thread's work -- the UI needs immediate feedback.
        started = [e for e in events if e["event"] == "play_started"]
        assert len(started) == 1
        assert started[0]["session"] == session_dir.name

        # give the daemon thread a moment to run the mocked replay
        for _ in range(50):
            if fake_recorder.replay.called:
                break
            time.sleep(0.05)

        assert fake_recorder.replay.called
        call_kwargs = fake_recorder.replay.call_args
        assert call_kwargs.args[0] == str(session_dir)
        assert call_kwargs.kwargs["count"] == 3
        assert call_kwargs.kwargs["submit_between"] is True

    def test_worker_thread_calls_init_com_before_replay(self, monkeypatch, tmp_path):
        """Same lesson as _worker()'s live segfault -- any thread that calls
        into _request_snapshot()/self._observer.snapshot() must initialize
        COM on itself first."""
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        session_dir = tmp_path / "session_20260101_000000"
        session_dir.mkdir()

        call_order = []
        fake_recorder = MagicMock()
        fake_recorder._init_com.side_effect = lambda: call_order.append("init_com")
        fake_recorder.replay.side_effect = lambda *a, **k: call_order.append("replay") or 1
        monkeypatch.setattr(rb, "DemoRecorder", lambda **kwargs: fake_recorder)

        bridge.play(str(session_dir))

        for _ in range(50):
            if call_order == ["init_com", "replay"]:
                break
            time.sleep(0.05)

        assert call_order == ["init_com", "replay"]

    def test_play_done_emitted_with_total_steps_on_success(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        session_dir = tmp_path / "session_20260101_000000"
        session_dir.mkdir()

        fake_recorder = MagicMock()
        fake_recorder.replay.return_value = 17
        monkeypatch.setattr(rb, "DemoRecorder", lambda **kwargs: fake_recorder)

        bridge.play(str(session_dir))

        done = []
        for _ in range(50):
            done = [e for e in events if e["event"] == "play_done"]
            if done:
                break
            time.sleep(0.05)

        assert len(done) == 1
        assert done[0]["steps"] == 17

    def test_replay_exception_emits_error_not_a_crash(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        session_dir = tmp_path / "session_20260101_000000"
        session_dir.mkdir()

        fake_recorder = MagicMock()
        fake_recorder.replay.side_effect = RuntimeError("form window not found")
        monkeypatch.setattr(rb, "DemoRecorder", lambda **kwargs: fake_recorder)

        bridge.play(str(session_dir))

        errors = []
        for _ in range(50):
            errors = [e for e in events if e["event"] == "error"]
            if errors:
                break
            time.sleep(0.05)

        assert len(errors) == 1
        assert "form window not found" in errors[0]["message"]
