"""
Regression tests for app/recorder_bridge.py's frame-count pending-typing
indicator.

Direct user report, 2026-08-10, testing the live Electron app for the first
time: "the frames are still delayed and not being real time." Root-caused:
DemoRecorder only commits a step (and so the frame count) when a field is
left (Tab/Enter/click elsewhere) -- individual keystrokes accumulate
silently with no queue push at all, by design (one step per committed field
is what every trained checkpoint's data has always assumed; changing that
would degrade training data, not fix a bug). The counter looking frozen
while actively typing was real, though -- _poll() now also reports whether
there's live pending input, so the UI can show real activity without
changing what actually gets saved.

(An F8 replay hotkey and a Replay xN UI button were also built here at
first, then explicitly reverted per direct follow-up instruction -- this
file no longer covers either; only the pending-indicator fix remains.)
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder_bridge as rb


def _events(monkeypatch):
    """Capture every emit() call as a list of dicts instead of printing."""
    captured = []
    monkeypatch.setattr(rb, "emit", lambda event, **fields: captured.append({"event": event, **fields}))
    return captured


class _FakeRecorder:
    """Minimal stand-in for DemoRecorder -- just the attributes _poll() reads."""
    def __init__(self):
        self._lock = threading.Lock()
        self._steps = []
        self._pending_text = ""
        self._pending_keys = []


def _run_poll_briefly(bridge):
    t = threading.Thread(target=bridge._poll, daemon=True)
    t.start()
    time.sleep(0.05)
    bridge._running = False
    bridge._poll_stop.set()
    t.join(timeout=2.0)


class TestPollReportsPendingTypingActivity:
    def test_pending_true_while_text_is_accumulating(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._recorder = _FakeRecorder()
        bridge._recorder._pending_text = "Jam"
        bridge._running = True

        _run_poll_briefly(bridge)

        frame_events = [e for e in events if e["event"] == "frame_count"]
        assert frame_events, "expected at least one frame_count event"
        assert frame_events[0]["pending"] is True

    def test_pending_false_with_no_accumulated_input(self, monkeypatch):
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._recorder = _FakeRecorder()
        bridge._running = True

        _run_poll_briefly(bridge)

        frame_events = [e for e in events if e["event"] == "frame_count"]
        assert frame_events, "expected at least one frame_count event"
        assert frame_events[0]["pending"] is False

    def test_pending_true_for_accumulated_non_char_keys_too(self, monkeypatch):
        """_pending_keys (e.g. arrow keys with no .char) must count as
        pending too, not just _pending_text."""
        events = _events(monkeypatch)
        bridge = rb.Bridge()
        bridge._recorder = _FakeRecorder()
        bridge._recorder._pending_keys = ["shift"]
        bridge._running = True

        _run_poll_briefly(bridge)

        frame_events = [e for e in events if e["event"] == "frame_count"]
        assert frame_events
        assert frame_events[0]["pending"] is True
