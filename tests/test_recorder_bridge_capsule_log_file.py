"""
Regression tests for app/recorder_bridge.py's persisted capsule activity
log -- direct user request after a run that looked totally silent in the
Play panel ("add a log feature... so you could actually read them").
logs/capsule_activity.log now holds a full, real transcript of everything
the Activity log received, truncated fresh at the start of each run.

Also covers emit()'s own flush-safety: this bridge is spawned by main.js
with windowsHide:true (no console window), the exact ancestry that made
run_task.py's own print(text, flush=True) raise OSError: [Errno 22]
Invalid argument on Windows -- emit() is called for every single event
this bridge ever sends, so it had the identical unguarded shape and
needed the identical fix.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


class _FakeProc:
    def __init__(self, lines=None, exit_code=0):
        self.stdout = iter((lines or []))
        self._exit_code = exit_code
        self._polled_running = True

    def poll(self):
        return None if self._polled_running else self._exit_code

    def wait(self):
        self._polled_running = False
        return self._exit_code


class TestEmitSurvivesFlushOSError:
    def test_emit_does_not_raise_when_flush_fails(self, monkeypatch):
        class _FlushRaisesStream:
            def write(self, s):
                pass
            def flush(self):
                raise OSError(22, "Invalid argument")

        monkeypatch.setattr(rb.sys, "stdout", _FlushRaisesStream())

        rb.emit("log", message="hello")  # must not raise

    def test_emit_still_writes_json_on_a_normal_stream(self, monkeypatch, capsys):
        rb.emit("log", message="hello")
        out = capsys.readouterr().out
        assert '"event": "log"' in out
        assert '"message": "hello"' in out


class TestCapsuleActivityLogFile:
    def test_run_capsule_truncates_a_fresh_log_file(self, monkeypatch, tmp_path):
        log_path = tmp_path / "capsule_activity.log"
        monkeypatch.setattr(rb, "_CAPSULE_LOG_PATH", str(log_path))
        monkeypatch.setattr(rb, "emit", MagicMock())
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc(lines=[]))
        log_path.write_text("stale content from a previous run\n")

        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        bridge.run_capsule(str(checkpoint))

        content = log_path.read_text()
        assert "stale content" not in content
        assert "Capsule run starting" in content

    def test_progress_lines_are_appended_to_the_log_file(self, monkeypatch, tmp_path):
        log_path = tmp_path / "capsule_activity.log"
        monkeypatch.setattr(rb, "_CAPSULE_LOG_PATH", str(log_path))
        monkeypatch.setattr(rb, "emit", MagicMock())
        fake_proc = _FakeProc(lines=["COUNTDOWN_BEGIN\n", "step 1 done\n"])
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: fake_proc)

        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        bridge.run_capsule(str(checkpoint))

        import time as _time
        _time.sleep(0.3)  # _pump runs on its own daemon thread -- give it a moment

        content = log_path.read_text()
        assert "COUNTDOWN_BEGIN" in content
        assert "step 1 done" in content
        assert "Run ended (exit code 0)" in content

    def test_stop_capsule_logs_the_stop_request(self, monkeypatch, tmp_path):
        log_path = tmp_path / "capsule_activity.log"
        log_path.write_text("Capsule run starting\n")
        monkeypatch.setattr(rb, "_CAPSULE_LOG_PATH", str(log_path))
        monkeypatch.setattr(rb, "emit", MagicMock())

        bridge = rb.Bridge()
        fake_proc = _FakeProc(lines=[])
        bridge._capsule_proc = fake_proc
        fake_proc.send_signal = MagicMock()

        bridge.stop_capsule()

        assert "Stop requested" in log_path.read_text()

    def test_a_broken_log_directory_does_not_break_the_run(self, monkeypatch, tmp_path):
        """Logging is a nice-to-have layered on top of the real run -- it
        must never be able to prevent run_capsule() from actually working."""
        # An unwritable-looking path (a file where a directory is expected)
        # so os.makedirs()/open() inside _log_capsule_line() raise.
        blocked = tmp_path / "not_a_dir"
        blocked.write_text("x")
        monkeypatch.setattr(rb, "_CAPSULE_LOG_PATH", str(blocked / "capsule_activity.log"))
        events = []
        monkeypatch.setattr(rb, "emit", lambda event, **fields: events.append({"event": event, **fields}))
        monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc(lines=[]))

        bridge = rb.Bridge()
        checkpoint = tmp_path / "model.pt"
        checkpoint.write_bytes(b"fake")
        bridge.run_capsule(str(checkpoint))  # must not raise

        assert any(e["event"] == "capsule_started" for e in events)
