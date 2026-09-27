"""
Bridge.start() threading trace_type/url through to DemoRecorder, and
calling ensure_server_running() first when trace_type="web" -- so the
page actually exists before WebObserver tries to navigate to it.
Same subprocess.Popen-is-never-real approach as
test_recorder_bridge_capsule_run.py.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


class _FakeRecorder:
    def __init__(self, output_dir="", trace_type="form_filling", url=""):
        self.output_dir = output_dir
        self.trace_type = trace_type
        self.url = url
        self._steps = []

    def run(self):
        pass


def _wait_until_not_running(bridge, timeout=2.0):
    """start() runs the recorder on a background thread; poll for its
    finally block (which flips _running back to False) to finish instead
    of racing it, same spirit as the pending-indicator tests' own
    thread-join helper."""
    deadline = time.time() + timeout
    while bridge._running and time.time() < deadline:
        time.sleep(0.02)


class _FakeRecorderWithSteps(_FakeRecorder):
    def __init__(self, output_dir="", trace_type="form_filling", url="", n_steps=1):
        super().__init__(output_dir, trace_type, url)
        self._steps = [{} for _ in range(n_steps)]


def test_start_passes_trace_type_and_url_to_demo_recorder(monkeypatch):
    captured = {}

    def fake_demo_recorder(output_dir="", trace_type="form_filling", url=""):
        captured.update(output_dir=output_dir, trace_type=trace_type, url=url)
        return _FakeRecorder(output_dir, trace_type, url)

    monkeypatch.setattr(rb, "DemoRecorder", fake_demo_recorder)
    monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")

    assert captured["trace_type"] == "web"
    assert captured["url"] == "http://localhost:8765/"


def test_start_defaults_match_existing_behavior(monkeypatch):
    captured = {}

    def fake_demo_recorder(output_dir="", trace_type="form_filling", url=""):
        captured.update(output_dir=output_dir, trace_type=trace_type, url=url)
        return _FakeRecorder(output_dir, trace_type, url)

    monkeypatch.setattr(rb, "DemoRecorder", fake_demo_recorder)
    bridge = rb.Bridge()

    bridge.start()

    assert captured["trace_type"] == "form_filling"
    assert captured["url"] == ""


def test_start_with_web_trace_type_ensures_server_running_first(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "ensure_server_running", lambda: calls.append("ensured"))
    monkeypatch.setattr(rb, "DemoRecorder", lambda output_dir="", trace_type="form_filling", url="": _FakeRecorder())
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")

    assert calls == ["ensured"]


def test_start_with_non_web_trace_type_does_not_touch_server(monkeypatch):
    calls = []
    monkeypatch.setattr(rb, "ensure_server_running", lambda: calls.append("ensured"))
    monkeypatch.setattr(rb, "DemoRecorder", lambda output_dir="", trace_type="form_filling", url="": _FakeRecorder())
    bridge = rb.Bridge()

    bridge.start()

    assert calls == []


def test_start_emits_error_and_returns_when_server_fails_to_start(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: events.append({"event": event, **fields}))

    def fake_ensure_server_running():
        raise SystemExit("local_server.py didn't come up within 20s")
    monkeypatch.setattr(rb, "ensure_server_running", fake_ensure_server_running)
    monkeypatch.setattr(rb, "DemoRecorder", lambda output_dir="", trace_type="form_filling", url="": _FakeRecorder())

    bridge = rb.Bridge()
    bridge.start(trace_type="web", url="http://localhost:8765/")

    errors = [e for e in events if e["event"] == "error"]
    assert len(errors) == 1
    assert "Could not start the local server for web recording" in errors[0]["message"]
    assert bridge._recorder is None  # DemoRecorder must never have been constructed


# ── local_server.py lifecycle ────────────────────────────────────────────────
# ensure_server_running() returns a Popen handle ONLY when that call actually
# spawned the server; None means one was already up and belongs to someone
# else. The bridge used to throw the return value away, so every web recording
# that started its own server leaked a Python process that outlived Electron.

class _FakeServerProc:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


class _BlockingFakeRecorder(_FakeRecorder):
    """run() blocks until released, so bridge._running is still True when the
    test calls stop() -- a run() that returns instantly would race it."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._release = threading.Event()
        # Bridge._poll() reads these on its own thread while "recording".
        self._lock = threading.Lock()
        self._pending_text = ""
        self._pending_keys = []

        class _Ev:
            def __init__(self, outer): self._outer = outer
            def set(self): self._outer._release.set()
        self._quit_event = _Ev(self)

    def run(self):
        self._release.wait(timeout=5.0)


def test_web_recording_terminates_the_server_it_started(monkeypatch):
    proc = _FakeServerProc()
    monkeypatch.setattr(rb, "ensure_server_running", lambda: proc)
    monkeypatch.setattr(rb, "DemoRecorder",
                        lambda output_dir="", trace_type="form_filling", url="": _BlockingFakeRecorder())
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")
    assert bridge._local_server_proc is proc   # handle kept, not discarded

    bridge.stop()

    assert proc.terminated is True
    assert bridge._local_server_proc is None


def test_bridge_never_touches_a_server_it_did_not_start(monkeypatch):
    """A None return means another process owns that server -- terminating it
    would kill a server the user (or another window) is relying on."""
    monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
    monkeypatch.setattr(rb, "DemoRecorder",
                        lambda output_dir="", trace_type="form_filling", url="": _BlockingFakeRecorder())
    bridge = rb.Bridge()

    bridge.start(trace_type="web", url="http://localhost:8765/")
    assert bridge._local_server_proc is None

    bridge.stop()   # must not raise

    assert bridge._local_server_proc is None


def test_server_is_cleaned_up_when_the_recorder_fails_to_construct(monkeypatch):
    """The server got started, then DemoRecorder raised -- there will never be
    a stop() for this session, so start() has to clean up its own mess."""
    proc = _FakeServerProc()
    monkeypatch.setattr(rb, "ensure_server_running", lambda: proc)
    monkeypatch.setattr(rb, "emit", lambda event, **fields: None)

    def _boom(output_dir="", trace_type="form_filling", url=""):
        raise RuntimeError("WebObserver could not connect")
    monkeypatch.setattr(rb, "DemoRecorder", _boom)

    bridge = rb.Bridge()
    bridge.start(trace_type="web", url="http://localhost:8765/")

    assert proc.terminated is True
    assert bridge._local_server_proc is None


# ── Auto-translation on stop ─────────────────────────────────────────────────
# A web recording captures a real session on disk, but that's just a raw
# trace -- nothing used to turn it into actual training data on its own,
# leaving Stop Recording as a dead end for Inbox Dispatch. Direct
# instruction: "we need to utilize the Recorder because that's what we
# actually use" -- so the finished session now gets translated into real
# training examples automatically, the moment recording stops.

def _events(monkeypatch):
    captured = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


class TestWebRecordingAutoTranslatesOnStop:
    def test_web_recording_with_steps_calls_translate_session(self, monkeypatch):
        events = _events(monkeypatch)
        monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
        monkeypatch.setattr(rb, "DemoRecorder",
            lambda output_dir="", trace_type="form_filling", url="":
                _FakeRecorderWithSteps(output_dir, trace_type, url, n_steps=3))
        calls = []
        monkeypatch.setattr(rb, "translate_session",
            lambda session_dir, gmail_client: (calls.append((session_dir, gmail_client)), 2)[1])
        monkeypatch.setattr(rb, "get_gmail_client", lambda: "fake-gmail-client")

        bridge = rb.Bridge()
        bridge.start(trace_type="web", url="http://localhost:8765/")
        _wait_until_not_running(bridge)

        assert calls == [(bridge._out_dir, "fake-gmail-client")]
        saved = [e for e in events if e["event"] == "saved"][0]
        assert saved["examples_written"] == 2
        assert saved["trace_type"] == "web"

    def test_form_filling_recording_never_calls_translate_session(self, monkeypatch):
        events = _events(monkeypatch)
        monkeypatch.setattr(rb, "DemoRecorder",
            lambda output_dir="", trace_type="form_filling", url="":
                _FakeRecorderWithSteps(output_dir, trace_type, url, n_steps=3))
        calls = []
        monkeypatch.setattr(rb, "translate_session", lambda session_dir, gmail_client: calls.append(session_dir) or 5)

        bridge = rb.Bridge()
        bridge.start()  # defaults to trace_type="form_filling"
        _wait_until_not_running(bridge)

        assert calls == []
        saved = [e for e in events if e["event"] == "saved"][0]
        assert saved["examples_written"] == 0
        assert saved["trace_type"] == "form_filling"

    def test_web_recording_with_zero_steps_skips_translation(self, monkeypatch):
        events = _events(monkeypatch)
        monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
        monkeypatch.setattr(rb, "DemoRecorder",
            lambda output_dir="", trace_type="form_filling", url="":
                _FakeRecorderWithSteps(output_dir, trace_type, url, n_steps=0))
        calls = []
        monkeypatch.setattr(rb, "translate_session", lambda session_dir, gmail_client: calls.append(session_dir) or 9)

        bridge = rb.Bridge()
        bridge.start(trace_type="web", url="http://localhost:8765/")
        _wait_until_not_running(bridge)

        assert calls == []

    def test_translate_session_failure_does_not_crash_the_bridge(self, monkeypatch):
        events = _events(monkeypatch)
        monkeypatch.setattr(rb, "ensure_server_running", lambda: None)
        monkeypatch.setattr(rb, "DemoRecorder",
            lambda output_dir="", trace_type="form_filling", url="":
                _FakeRecorderWithSteps(output_dir, trace_type, url, n_steps=2))

        def _boom(session_dir, gmail_client):
            raise RuntimeError("gmail auth expired")
        monkeypatch.setattr(rb, "translate_session", _boom)
        monkeypatch.setattr(rb, "get_gmail_client", lambda: "fake-gmail-client")

        bridge = rb.Bridge()
        bridge.start(trace_type="web", url="http://localhost:8765/")
        _wait_until_not_running(bridge)

        error_logs = [e for e in events if e["event"] == "log" and e.get("level") == "err"]
        assert any("Couldn't extract training examples" in e["message"] for e in error_logs)
        saved = [e for e in events if e["event"] == "saved"][0]
        assert saved["examples_written"] == 0  # failed translation -> 0, not a crash
