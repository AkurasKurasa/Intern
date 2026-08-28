import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


class _FakeWebObserver:
    def __init__(self, headless=False):
        self.headless = headless
        self.connected_to = None
        self.disconnected = False

    def connect(self, url=None):
        self.connected_to = url
        return True

    def snapshot(self):
        return {"application": "browser", "elements": []}

    def disconnect(self):
        self.disconnected = True


class _FakeWebObserverThatFailsToConnect(_FakeWebObserver):
    def connect(self, url=None):
        self.connected_to = url
        return False


def _patch_pynput(monkeypatch):
    monkeypatch.setattr(recorder_module, "_pynput_mouse", MagicMock(Listener=_FakeMouseKeyboard))
    monkeypatch.setattr(recorder_module, "_pynput_keyboard", MagicMock(Listener=_FakeMouseKeyboard))


class TestWebTraceType:
    def test_web_trace_type_constructs_web_observer(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

        assert isinstance(rec._observer, _FakeWebObserver)
        assert rec._observer.connected_to == "http://localhost:8765/"

    def test_web_trace_type_without_url_raises(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        with pytest.raises(ValueError):
            recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="web", url="")

    def test_web_trace_type_when_unavailable_raises_import_error(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", False)

        with pytest.raises(ImportError):
            recorder_module.DemoRecorder(
                output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

    def test_web_trace_type_when_connect_fails_raises_runtime_error_not_fallback(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserverThatFailsToConnect)

        with pytest.raises(RuntimeError):
            recorder_module.DemoRecorder(
                output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

    def test_non_web_trace_type_is_unaffected(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(output_dir=str(tmp_path), trace_type="form_filling")

        assert not isinstance(rec._observer, _FakeWebObserver)
        assert hasattr(rec._observer, "snapshot")


class TestWebObserverCleanup:
    def test_run_disconnects_web_observer_on_stop(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserver)

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")
        rec._quit_event.set()  # run() exits its wait() immediately
        rec.run(auto_start=False)

        assert rec._observer.disconnected is True
