"""
Bridge.start() threading trace_type/url through to DemoRecorder, and
calling ensure_server_running() first when trace_type="web" -- so the
page actually exists before WebObserver tries to navigate to it.
Same subprocess.Popen-is-never-real approach as
test_recorder_bridge_capsule_run.py.
"""
import sys
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
