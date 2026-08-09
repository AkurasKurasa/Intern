"""
app/recorder_bridge.py
=======================
Stdio JSON bridge between the Electron UI (app_electron/) and the existing
Python DemoRecorder backend. Electron's main process spawns this script and
talks to it over stdin/stdout — one JSON object per line each direction.
Nothing about DemoRecorder itself changes; this is purely a thin protocol
adapter so a Chromium-rendered frontend can drive it.

Commands (stdin, one per line)
-------------------------------
  {"cmd": "start", "output_dir": "data/demos/eight_Tabs"}
  {"cmd": "stop"}
  {"cmd": "replay", "n": 10}
  {"cmd": "shutdown"}

Events (stdout, one per line)
-------------------------------
  {"event": "ready"}
  {"event": "started", "output_dir": "..."}
  {"event": "frame_count", "value": 42, "pending": true}
  {"event": "saved", "steps": 42, "session_dir": "..."}
  {"event": "replay_progress", "current": 3, "total": 10}
  {"event": "replay_done", "made": 10, "steps_each": 42, "dest": "..."}
  {"event": "log", "message": "...", "level": "ok" | "err" | "dim"}
  {"event": "error", "message": "..."}
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT    = os.path.dirname(_APP_DIR)
_COMP    = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recorder.recorder import DemoRecorder


def emit(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


class Bridge:
    def __init__(self) -> None:
        self._recorder: DemoRecorder | None = None
        self._running = False
        self._out_dir = os.path.join(_ROOT, "data", "demos", "eight_Tabs")
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()

    # ── start / stop ─────────────────────────────────────────────────────────
    def start(self, output_dir: str | None = None) -> None:
        if self._running:
            emit("error", message="Already recording.")
            return
        if output_dir:
            self._out_dir = output_dir if os.path.isabs(output_dir) else os.path.join(_ROOT, output_dir)

        try:
            self._recorder = DemoRecorder(output_dir=self._out_dir, trace_type="form_filling")
        except Exception as exc:
            emit("error", message=f"Failed to start recorder: {exc}")
            return

        self._running = True
        self._poll_stop.clear()

        def _run():
            try:
                self._recorder.run()
            except Exception as exc:
                emit("error", message=f"Recorder crashed: {exc}")
            finally:
                steps = len(self._recorder._steps) if self._recorder else 0
                session_dir = getattr(self._recorder, "output_dir", "")
                self._running = False
                self._poll_stop.set()
                emit("saved", steps=steps, session_dir=session_dir)

        threading.Thread(target=_run, daemon=True).start()
        self._poll_thread = threading.Thread(target=self._poll, daemon=True)
        self._poll_thread.start()
        emit("started", output_dir=self._out_dir)
        emit("log", message="Demo recorder started.", level="ok")

    def _poll(self) -> None:
        while not self._poll_stop.is_set() and self._running:
            if self._recorder is not None:
                with self._recorder._lock:
                    n = len(self._recorder._steps)
                # A frame only commits when a field is left (Tab/Enter/click
                # elsewhere) -- individual keystrokes accumulate silently in
                # _pending_text/_pending_keys with no queue push (see
                # DemoRecorder._on_key_press). That's intentional: one step
                # per committed field, not one per keystroke, is what every
                # trained checkpoint's data has always assumed. But it made
                # the counter look frozen while actively typing -- reporting
                # whether there's live pending input lets the UI show real
                # activity without changing what actually gets saved.
                pending = bool(self._recorder._pending_text or self._recorder._pending_keys)
                emit("frame_count", value=n, pending=pending)
            time.sleep(0.3)

    def stop(self) -> None:
        if not self._running or self._recorder is None:
            emit("error", message="Not currently recording.")
            return
        self._recorder._quit_event.set()

    # ── replay (pure file duplication, same as app/main.py's _do_replay) ─────
    def replay(self, n: int) -> None:
        if self._running:
            emit("error", message="Stop recording before replaying.")
            return
        cands = [d for d in glob.glob(os.path.join(self._out_dir, "*")) if os.path.isdir(d)]
        if not cands:
            emit("error", message=f"No sessions to duplicate in {self._out_dir}")
            return
        src = max(cands, key=os.path.getmtime)
        step_files = sorted(glob.glob(os.path.join(src, "live_step_*.json")))
        if not step_files:
            emit("error", message=f"No steps in {os.path.basename(src)} to copy.")
            return

        out_base = os.path.join(_ROOT, "data", "demos", "human")
        os.makedirs(out_base, exist_ok=True)
        made = 0
        for i in range(n):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dst = os.path.join(out_base, f"session_copy_{ts}_{i:03d}")
            try:
                shutil.copytree(src, dst)
                made += 1
                emit("replay_progress", current=i + 1, total=n)
            except Exception as exc:
                emit("log", message=f"Copy {i+1} failed: {exc}", level="err")
        emit("replay_done", made=made, steps_each=len(step_files), dest=out_base)
        emit("log", message=f"Replay = {made} copies of '{os.path.basename(src)}' "
                             f"({len(step_files)} steps each) -> data/demos/human", level="ok")

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self) -> None:
        emit("ready")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                emit("error", message=f"Bad JSON: {line!r}")
                continue

            cmd = msg.get("cmd")
            if cmd == "start":
                self.start(msg.get("output_dir"))
            elif cmd == "stop":
                self.stop()
            elif cmd == "replay":
                self.replay(int(msg.get("n", 10)))
            elif cmd == "shutdown":
                if self._running and self._recorder is not None:
                    self._recorder._quit_event.set()
                break
            else:
                emit("error", message=f"Unknown command: {cmd!r}")


if __name__ == "__main__":
    Bridge().run()
