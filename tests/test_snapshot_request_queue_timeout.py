"""
Regression test for a real, live-reported hang: "Stuck on 3 frames" in the
Electron recorder (2026-08-10), followed by Stop itself never completing
either ("Flushing 58 pending action(s)…" printed, then nothing -- the whole
app went unresponsive).

Root cause, confirmed by reading DemoRecorder._request_snapshot() and
correlating with the actual bridge output (3 real steps recorded with
`[!empty state]` from step 0000 -- the snapshot subprocess was already not
answering from the very start of the session): `_req_q` is a bounded
multiprocessing.Queue (maxsize=4). `_req_q.put(action_type or 1)` had no
timeout, so once the subprocess stops draining it -- for ANY reason, this
test doesn't need to know why -- the 5th and every later request blocks
FOREVER. Not just that one call: the entire worker thread, permanently.
Every future step silently stops committing, and Stop can never finish
either, since its own drain loop calls this same method.

Fixed by giving put() the same timeout get() already had, so the method's
own documented contract ("return {} on any failure") actually holds in
every case.

Verified in a background thread with a hard join timeout so that if this
regresses, the TEST fails cleanly instead of hanging the whole suite.
"""
import multiprocessing as mp
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import recorder.recorder as recorder_module


class TestRequestSnapshotNeverBlocksForever:
    def test_put_times_out_instead_of_hanging_when_queue_is_full(self):
        """Reproduces the exact live deadlock: the request queue is full and
        nothing is draining it (simulating a subprocess that stopped
        responding) -- _request_snapshot must still return, not hang."""
        rec = object.__new__(recorder_module.DemoRecorder)
        rec._use_subprocess = True
        rec._req_q = mp.Queue(maxsize=4)
        rec._res_q = mp.Queue(maxsize=4)

        # Fill the request queue to capacity -- nobody is consuming it, just
        # like a dead/unresponsive subprocess.
        for _ in range(4):
            rec._req_q.put(1)

        result_box = {}

        def _call():
            result_box["value"] = rec._request_snapshot(timeout=0.5)

        t = threading.Thread(target=_call, daemon=True)
        start = time.time()
        t.start()
        t.join(timeout=5.0)
        elapsed = time.time() - start

        assert not t.is_alive(), (
            f"_request_snapshot() did not return within 5s -- the deadlock is back "
            f"(elapsed={elapsed:.2f}s)"
        )
        assert result_box.get("value") == {}

        rec._req_q.close()
        rec._res_q.close()


class TestSnapshotTimeoutHasRealMargin:
    """Found 2026-08-10: a real UIAutomationObserver.snapshot() profiled at
    0.9-1.2s baseline with zero other load (cProfile showed ~13 separate
    UIA/COM property calls per element -- inherent library cost, not a bug
    here). The old 2.0s default left almost no margin -- any real system
    load (this repo syncs live via OneDrive; a concurrent process; anything)
    tips a marginal snapshot over the timeout, and the request silently
    comes back as {} -- recorded as a real, empty-state step. Every step in
    a live session showed exactly this. Raised to 4.0s for real headroom."""

    def test_default_timeout_has_real_margin_above_profiled_baseline(self):
        import inspect
        sig = inspect.signature(recorder_module.DemoRecorder._request_snapshot)
        default_timeout = sig.parameters["timeout"].default
        profiled_baseline_sec = 1.2
        assert default_timeout >= profiled_baseline_sec * 2, (
            f"default timeout ({default_timeout}s) leaves less than 2x margin "
            f"above the profiled baseline ({profiled_baseline_sec}s) -- too thin, "
            f"will spuriously time out under any real load"
        )
