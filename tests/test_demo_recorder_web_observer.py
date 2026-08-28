import sys
import threading
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


class _FakeWebObserverThatLaunchesThenFailsToConnect(_FakeWebObserver):
    """Models connect() failing AFTER a real browser was already launched
    (e.g. page.goto() throwing) -- the realistic failure case, distinct
    from failing before anything was launched."""
    _last_instance = None

    def __init__(self, headless=False):
        super().__init__(headless=headless)
        _FakeWebObserverThatLaunchesThenFailsToConnect._last_instance = self

    def connect(self, url=None):
        self.connected_to = url
        self.launched = True
        return False


class _ThreadRecordingWebObserver(_FakeWebObserver):
    """Records which OS thread each call arrived on. Playwright's sync API is
    bound to the thread that started it -- so 'which thread' is not a detail
    here, it is the entire correctness condition."""
    _last_instance = None

    def __init__(self, headless=False):
        super().__init__(headless=headless)
        self.call_threads = {}
        _ThreadRecordingWebObserver._last_instance = self

    def connect(self, url=None):
        self.call_threads["connect"] = threading.current_thread().ident
        return super().connect(url=url)

    def snapshot(self):
        self.call_threads.setdefault("snapshot", threading.current_thread().ident)
        return super().snapshot()

    def disconnect(self):
        self.call_threads["disconnect"] = threading.current_thread().ident
        return super().disconnect()


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

    def test_web_trace_type_disconnects_on_partial_connect_failure(self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _FakeWebObserverThatLaunchesThenFailsToConnect)

        with pytest.raises(RuntimeError):
            recorder_module.DemoRecorder(
                output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")

        # Verify disconnect() was called even though connection failed
        # This proves the cleanup happens on the partial-failure path (launched, then failed)
        assert _FakeWebObserverThatLaunchesThenFailsToConnect._last_instance is not None
        assert _FakeWebObserverThatLaunchesThenFailsToConnect._last_instance.disconnected is True

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


class TestPlaywrightThreadAffinity:
    """C2 regression.

    Playwright's sync API runs on a greenlet dispatcher owned by whichever OS
    thread started it; any call from a different thread raises greenlet.error.
    The recorder used to build+connect the WebObserver in __init__ (the
    caller's thread) while every snapshot ran on self._worker_thread. Result:
    every real snapshot raised, WebObserver.snapshot() swallowed it
    (`except Exception: return _empty_state()`), _request_snapshot() swallowed
    it again (`except Exception: return {}`), and every recorded step held an
    empty state with nothing logged anywhere.

    Nothing about that is visible from return values -- only from which thread
    the calls land on. Hence this test.
    """

    def test_connect_snapshot_and_disconnect_all_run_on_one_non_caller_thread(
            self, tmp_path, monkeypatch):
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _ThreadRecordingWebObserver)

        caller_ident = threading.current_thread().ident

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")
        rec._quit_event.set()
        rec.run(auto_start=False)     # run() also executes on the caller's thread

        obs = rec._observer
        assert set(obs.call_threads) == {"connect", "snapshot", "disconnect"}, (
            f"not every lifecycle call happened: {obs.call_threads}")

        idents = set(obs.call_threads.values())
        assert len(idents) == 1, (
            f"Playwright touched from more than one thread: {obs.call_threads}")

        used_ident = idents.pop()
        assert used_ident == rec._worker_thread.ident, (
            "observer must live on the worker thread -- the one that snapshots")
        assert used_ident != caller_ident, (
            "observer was used on the thread that constructed/ran the recorder; "
            "that is exactly the greenlet.error case")

    def test_run_does_not_return_before_the_worker_has_disconnected(
            self, tmp_path, monkeypatch):
        """The disconnect moved onto the worker thread, so run() must join it.
        Without the join this assertion is a race, not a guarantee."""
        _patch_pynput(monkeypatch)
        monkeypatch.setattr(recorder_module, "_WEB_OBSERVER_AVAILABLE", True)
        monkeypatch.setattr(recorder_module, "_WebObserver", _ThreadRecordingWebObserver)

        rec = recorder_module.DemoRecorder(
            output_dir=str(tmp_path), trace_type="web", url="http://localhost:8765/")
        rec._quit_event.set()
        rec.run(auto_start=False)

        assert rec._observer.disconnected is True
        assert not rec._worker_thread.is_alive()
