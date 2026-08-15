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
  {"cmd": "run_capsule", "capsule_name": "form_filling"}
  {"cmd": "stop_capsule"}
  {"cmd": "shutdown"}

Events (stdout, one per line)
-------------------------------
  {"event": "ready"}
  {"event": "started", "output_dir": "..."}
  {"event": "frame_count", "value": 42, "pending": true}
  {"event": "saved", "steps": 42, "session_dir": "..."}
  {"event": "replay_progress", "current": 3, "total": 10}
  {"event": "replay_done", "made": 10, "steps_each": 42, "dest": "..."}
  {"event": "capsule_started", "label": "..."}
  {"event": "capsule_progress", "line": "..."}
  {"event": "capsule_done", "code": 0}
  {"event": "capsule_stopped"}
  {"event": "log", "message": "...", "level": "ok" | "err" | "dim"}
  {"event": "error", "message": "..."}
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
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
from agent.capsule import CapsuleRegistry

# Full, persisted transcript of everything the Play panel's Activity log
# receives -- direct user request ("add a log feature... so you could
# actually read them") after a capsule run that looked totally silent in
# the UI even though it was genuinely running underneath. Truncated fresh
# at the start of each capsule run (see run_capsule() below) so it always
# holds exactly the most recent run, mirroring run_task.py's own
# logs/latest.log convention.
_CAPSULE_LOG_PATH = os.path.join(_ROOT, "logs", "capsule_activity.log")


def _log_capsule_line(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(_CAPSULE_LOG_PATH), exist_ok=True)
        with open(_CAPSULE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {text}\n")
    except Exception:
        pass  # never let logging itself break a capsule run


def emit(event: str, **fields) -> None:
    # write() then a separately-guarded flush() -- not print(..., flush=True)
    # directly. This exact bridge process is spawned by main.js with
    # windowsHide:true (no console window); an explicit stdout.flush() can
    # raise OSError: [Errno 22] Invalid argument on Windows under that
    # no-console chain even though the write itself already succeeds --
    # found live in run_task.py (same windowsHide ancestry) and fixed there
    # first (see run_task.py's _flush_safe_print) before realizing this
    # bridge's own emit() -- called for EVERY single event -- had the exact
    # same unguarded shape.
    print(json.dumps({"event": event, **fields}))
    try:
        sys.stdout.flush()
    except OSError:
        pass


class Bridge:
    # registry is injectable so tests can hand in a temp registry.json
    # instead of monkeypatching the real project file -- same pattern as
    # agent.py's injectable uia_mod/win32gui_mod/user32 params.
    def __init__(self, registry: CapsuleRegistry | None = None) -> None:
        self._recorder: DemoRecorder | None = None
        self._running = False
        self._out_dir = os.path.join(_ROOT, "data", "demos", "eight_Tabs")
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._capsule_proc: subprocess.Popen | None = None
        self._registry = registry if registry is not None else CapsuleRegistry()

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

    # ── capsule run (either the real autonomous agent, same as run_task.py,
    # or a standalone script capsule like components/scope2/automate.py) ────
    # "Play" means running the capsule's real, live process -- not replaying
    # one recorded session's raw actions. For kind="agent" this reuses
    # run_task.py's own entry point as a subprocess rather than
    # re-implementing LLMAgent construction here, so a Play run is
    # byte-for-byte the same thing the user gets running it themselves from
    # a terminal -- one real implementation, not two that can quietly drift
    # apart. kind="script" capsules (e.g. Scope #2) get the same treatment:
    # WorkflowCapsule.launch_command() is the one place that decides the
    # actual argv, so both kinds share this exact Popen call.
    def run_capsule(self, capsule_name: str) -> None:
        if self._running:
            emit("error", message="Stop recording before running a capsule.")
            return
        if self._capsule_proc is not None and self._capsule_proc.poll() is None:
            emit("error", message="A capsule is already running.")
            return

        capsule = next((c for c in self._registry.list_capsules() if c.name == capsule_name), None)
        if capsule is None:
            emit("error", message=f"Capsule not found: {capsule_name}")
            return
        try:
            argv, cwd = capsule.launch_command(_ROOT)
        except FileNotFoundError as exc:
            emit("error", message=str(exc))
            return
        # Show the checkpoint filename for an agent capsule (what the old
        # model_path-keyed label showed) or the entrypoint filename for a
        # script capsule -- either way, a quick "what's actually running".
        target = capsule.model_path if capsule.kind != "script" else capsule.entrypoint
        label = f"{capsule.name} — {os.path.basename(target)}"

        # Fresh transcript for this run -- truncate, don't append, so it
        # never grows unbounded and always matches "the run I just did."
        try:
            os.makedirs(os.path.dirname(_CAPSULE_LOG_PATH), exist_ok=True)
            with open(_CAPSULE_LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%H:%M:%S}] Capsule run starting — {label}\n")
        except Exception:
            pass

        try:
            self._capsule_proc = subprocess.Popen(
                # "-u" -- unbuffered stdout/stderr, same fix already applied
                # elsewhere in this project for GUI-launched subprocesses
                # (car_insurance_form_wx.py). Without it, a non-tty stdout
                # is block-buffered by default -- print() output can sit in
                # the child's own buffer well past when it's produced,
                # arriving at _pump()'s `for line in proc.stdout` in
                # delayed bursts instead of as it actually happens.
                argv,
                cwd=cwd,
                # stdin=DEVNULL, explicit -- found live 2026-08-10: this
                # Popen call never set stdin at all, so run_task.py
                # inherited THIS bridge process's own stdin handle, which
                # is itself a pipe Electron's main.js writes JSON commands
                # into. A grandchild silently holding a handle onto a pipe
                # two processes up, with no console of its own, is a
                # known way to get a process that's genuinely blocked (not
                # crashed, not busy) on standard-handle setup -- confirmed
                # directly against real OS process state: two run_task.py
                # processes launched via the real bridge chain before this
                # fix stayed Responding=True with near-zero CPU for
                # minutes and created no log file at all, meaning they
                # never even reached logging.basicConfig(), the first real
                # statement in the script. A direct terminal launch never
                # has this problem because its stdin is a real console
                # handle, not an inherited pipe. DEVNULL gives the child a
                # clean, unambiguous stdin with no upstream process in the
                # chain to block on.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1,
                # CREATE_NEW_PROCESS_GROUP -- back to this (not
                # CREATE_NEW_CONSOLE) as of 2026-08-10. CREATE_NEW_CONSOLE
                # was tried as a fix for the hang below and, in the one
                # live trial run through the real Electron bridge, the
                # hang WAS gone -- but it also pops a real, visible
                # console window on screen for every Play click, which is
                # not acceptable for a GUI app and the user does not want.
                # Reverted. Whether CREATE_NEW_CONSOLE was actually the
                # ingredient that fixed the hang is still unknown: that
                # same trial also (a) added stdin=DEVNULL below and (b)
                # ran through a freshly relaunched Electron process tree,
                # so more than one variable changed between the last
                # confirmed hang and the first confirmed success -- sloppy,
                # not the "one variable at a time" standard this project
                # otherwise holds to. CREATE_NEW_PROCESS_GROUP is the
                # value this flag had throughout the whole rest of this
                # session, including every prior confirmed-working
                # CTRL_BREAK_EVENT/Stop-button test -- keeping it is the
                # smaller, better-understood change; stdin=DEVNULL below
                # is the one new ingredient being kept and needs to be
                # reverified live on its own before this can be called
                # fixed.
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as exc:
            # Found live 2026-08-10: a capsule launch attempt left
            # "Capsule run starting" as the ONLY line in
            # logs/capsule_activity.log, no run_task.py process ever
            # appeared, and this exact same invocation worked fine when
            # reproduced directly moments later -- an intermittent failure
            # in the long-lived bridge process itself, not the checkpoint
            # or the command. This branch was the one place in run_capsule()
            # that DIDN'T write to the persisted log, so there was no
            # record of what Popen actually raised. Fixed so next time this
            # happens, the real exception is captured, not just guessed at.
            _log_capsule_line(f"Failed to start capsule run: {exc}")
            emit("error", message=f"Failed to start capsule run: {exc}")
            return

        def _pump() -> None:
            proc = self._capsule_proc
            try:
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    if line:
                        _log_capsule_line(line)
                        emit("capsule_progress", line=line)
            except Exception:
                pass
            code = proc.wait()
            self._capsule_proc = None
            _log_capsule_line(f"Run ended (exit code {code}).")
            emit("capsule_done", code=code)

        threading.Thread(target=_pump, daemon=True).start()
        emit("capsule_started", label=label)
        emit("log", message=f"Capsule run started — {label}", level="ok")

    def stop_capsule(self) -> None:
        if self._capsule_proc is None or self._capsule_proc.poll() is not None:
            emit("error", message="No capsule run in progress.")
            return
        try:
            # CTRL_BREAK_EVENT, not terminate() -- run_task.py's own run()
            # already catches KeyboardInterrupt to save partial results and
            # write metrics/logs cleanly, and a hard terminate() would skip
            # all of that. A script-kind capsule (e.g. automate.py) has no
            # such handler, so CTRL_BREAK_EVENT there just stops the process
            # via Python's default SIGBREAK behavior -- still a clean stop,
            # just without a documented "save partial results" guarantee.
            self._capsule_proc.send_signal(signal.CTRL_BREAK_EVENT)
            _log_capsule_line("Stop requested — CTRL_BREAK_EVENT sent.")
            emit("capsule_stopped")
            emit("log", message="Capsule run interrupted…", level="dim")
        except Exception as exc:
            emit("error", message=f"Failed to stop capsule run: {exc}")

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
            elif cmd == "run_capsule":
                self.run_capsule(msg.get("capsule_name", ""))
            elif cmd == "stop_capsule":
                self.stop_capsule()
            elif cmd == "shutdown":
                if self._running and self._recorder is not None:
                    self._recorder._quit_event.set()
                if self._capsule_proc is not None and self._capsule_proc.poll() is None:
                    try:
                        self._capsule_proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except Exception:
                        pass
                break
            else:
                emit("error", message=f"Unknown command: {cmd!r}")


if __name__ == "__main__":
    Bridge().run()
