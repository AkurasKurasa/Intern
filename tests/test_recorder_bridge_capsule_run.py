"""
Regression tests for app/recorder_bridge.py's "run_capsule"/"stop_capsule"
commands -- this is what "Play" actually means now: running the real
trained agent (transformer decides WHERE, LLM decides WHAT, looping the
whole form live), the same thing run_task.py does when the user runs it
themselves from a terminal, not a replay of one recorded session's raw
actions (that earlier version was explicitly reverted -- direct user
correction: "I don't want to play per session... capsule meaning model").

run_capsule() spawns `python run_task.py --model <checkpoint>` as a real
subprocess rather than re-implementing LLMAgent construction in the bridge,
so a Play run is exactly what the user would get running it themselves --
one real implementation, not two that can quietly drift apart.

Only the safe, non-executing parts are covered here -- subprocess.Popen is
monkeypatched out entirely so no test ever actually launches run_task.py or
touches the real screen. Actually running a live capsule is a real
GUI-automation action and is the user's call to make from the app, the
same as every other live run in this project.
"""
import signal
import sys
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


class _FakeProc:
    """Stands in for subprocess.Popen -- no real process ever spawned."""
    def __init__(self, lines=None, exit_code=0):
        self.stdout = iter((lines or []))
        self._exit_code = exit_code
        self._polled_running = True
        self.sent_signals = []

    def poll(self):
        return None if self._polled_running else self._exit_code

    def wait(self):
        self._polled_running = False
        return self._exit_code

    def send_signal(self, sig):
        self.sent_signals.append(sig)


class TestRunCapsuleGuards:
    def test_refuses_while_recording(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._running = True

        bridge.run_capsule("tasks/form_filling/model.pt")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "recording" in errors[0]["message"].lower()

    def test_refuses_when_a_capsule_is_already_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._capsule_proc = _FakeProc()  # poll() -> None -> still running

        bridge.run_capsule("tasks/form_filling/model.pt")

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "already running" in errors[0]["message"].lower()

    def test_refuses_missing_checkpoint(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.run_capsule(str(tmp_path / "nope.pt"))

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "not found" in errors[0]["message"].lower()


class TestRunCapsuleSpawnsRunTaskCorrectly:
    def test_spawns_run_task_with_model_flag(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")

        popen_calls = []

        def _fake_popen(args, **kwargs):
            popen_calls.append((args, kwargs))
            return _FakeProc(lines=[])

        monkeypatch.setattr(rb.subprocess, "Popen", _fake_popen)

        bridge.run_capsule(str(checkpoint))

        assert len(popen_calls) == 1
        args, kwargs = popen_calls[0]
        assert args[0] == sys.executable
        # "-u" -- unbuffered stdout, same fix already used elsewhere in this
        # project for GUI-launched subprocesses. Without it, a non-tty
        # stdout is block-buffered by default and _pump()'s line-by-line
        # read can sit dead for a while with real output already produced
        # but not yet flushed to the pipe -- found live as part of the same
        # "Play does nothing" report the countdown fix above addresses.
        assert args[1] == "-u"
        assert args[2] == rb.os.path.join(rb._ROOT, "run_task.py")
        assert args[3] == "--model"
        assert args[4] == str(checkpoint)
        assert kwargs["cwd"] == rb._ROOT

    def test_capsule_started_emitted_synchronously(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc(lines=[]))

        bridge.run_capsule(str(checkpoint))

        started = [e for e in events if e["event"] == "capsule_started"]
        assert len(started) == 1
        assert started[0]["model_path"] == str(checkpoint)

    def test_stdout_lines_stream_as_capsule_progress(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        fake_proc = _FakeProc(lines=["step 1: click\n", "step 2: keyboard\n", ""])
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)

        bridge.run_capsule(str(checkpoint))

        for _ in range(50):
            progress = [e for e in events if e["event"] == "capsule_progress"]
            if len(progress) >= 2:
                break
            time.sleep(0.05)

        progress = [e for e in events if e["event"] == "capsule_progress"]
        assert [p["line"] for p in progress] == ["step 1: click", "step 2: keyboard"]

    def test_capsule_done_emitted_with_exit_code(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        fake_proc = _FakeProc(lines=[], exit_code=0)
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)

        bridge.run_capsule(str(checkpoint))

        done = []
        for _ in range(50):
            done = [e for e in events if e["event"] == "capsule_done"]
            if done:
                break
            time.sleep(0.05)

        assert len(done) == 1
        assert done[0]["code"] == 0

    def test_bridge_capsule_proc_cleared_after_done(self, monkeypatch, tmp_path):
        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        fake_proc = _FakeProc(lines=[])
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)
        monkeypatch.setattr(rb, "emit", lambda *a, **k: None)

        bridge.run_capsule(str(checkpoint))

        for _ in range(50):
            if bridge._capsule_proc is None:
                break
            time.sleep(0.05)

        assert bridge._capsule_proc is None


class TestStopCapsule:
    def test_refuses_when_nothing_running(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()

        bridge.stop_capsule()

        errors = [e for e in events if e["event"] == "error"]
        assert len(errors) == 1
        assert "no capsule" in errors[0]["message"].lower()

    def test_sends_ctrl_break_not_a_hard_terminate(self, monkeypatch):
        """run_task.py's own run() catches KeyboardInterrupt to save partial
        results and write metrics cleanly -- a hard terminate() would skip
        all of that, so stop must use CTRL_BREAK_EVENT, not .terminate()."""
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        fake_proc = _FakeProc()
        bridge._capsule_proc = fake_proc

        bridge.stop_capsule()

        assert fake_proc.sent_signals == [signal.CTRL_BREAK_EVENT]
        stopped = [e for e in events if e["event"] == "capsule_stopped"]
        assert len(stopped) == 1
