"""
run_task.py
===========
Runs LLMAgent with NO task plugin — pure transformer + LLM.
Capsule router auto-selects model based on goal and window title.

Run from repo root:
    python run_task.py
"""

from __future__ import annotations

import os
os.environ.setdefault("TQDM_DISABLE", "1")           # prevent tqdm bars from crashing Windows terminal
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # silence tokenizer fork warning
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")  # suppress unauthenticated warning

import ctypes
try:
    ctypes.windll.ole32.CoInitialize(None)
except Exception:
    pass

import json
import logging
import sys
import time

_ROOT     = os.path.dirname(os.path.abspath(__file__))
_COMP_DIR = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

import datetime as _datetime

_LOG_DIR = os.path.join(_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, f"run_task_{_datetime.datetime.now():%Y%m%d_%H%M%S}.log")
_LATEST_LOG = os.path.join(_LOG_DIR, "latest.log")  # always overwritten — tail this for the current run

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.FileHandler(_LATEST_LOG, mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_task")
logger.info("Logging to %s (also mirrored to %s)", _LOG_FILE, _LATEST_LOG)

# Any exception that reaches the top of the script without being caught
# (e.g. during LLMAgent(...) construction, which sits outside the run()
# try/except below) used to print only to the console and never reach the
# log file -- the log would just stop mid-stream with no record of why.
# Found 2026-08-08: a live run's log cut off right after "Perception: UIA",
# before agent construction's own try/except even starts, with nothing
# telling you what killed it. sys.excepthook fires for ANY uncaught
# exception anywhere in the script, so this guarantees the log always has
# the real traceback regardless of where the crash happens.
def _log_uncaught_exception(exc_type, exc_value, exc_tb):
    logger.error("Unhandled exception — process is crashing:", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _log_uncaught_exception

# recorder_bridge.py's stop_capsule() (Electron "Stop") sends
# CTRL_BREAK_EVENT to this process, specifically because the run() loop
# below already catches KeyboardInterrupt to save partial results and
# write metrics/BC-fidelity/rule-extraction -- the same graceful path a
# real terminal Ctrl+C would take. But on Windows, CTRL_BREAK_EVENT maps
# to SIGBREAK, and unlike SIGINT (Ctrl+C), Python installs NO default
# handler for SIGBREAK -- without one, the OS's default action just hard-
# kills the process (exit code 0xC000013A / 3221225786) before a single
# line of Python ever runs, silently skipping the entire finally: block
# this was built for. Found live, directly from the user's own capsule
# run: "Run ended (exit code 3221225786)" after clicking Stop -- verified
# the mechanism, not guessed, with a standalone child-process test before
# writing this (no SIGBREAK handler: hard kill, exit 3221225786; with one:
# KeyboardInterrupt caught, exit 0). Installing this handler makes
# CTRL_BREAK_EVENT behave exactly like Ctrl+C from here on.
import signal as _signal
if hasattr(_signal, "SIGBREAK"):
    def _handle_ctrl_break(signum, frame):
        raise KeyboardInterrupt
    _signal.signal(_signal.SIGBREAK, _handle_ctrl_break)


def print_countdown(seconds: int = 5, sleep_fn=None, print_fn=None) -> None:
    """Prints the pre-run 'switch to the target window' countdown.

    Each line ends with a real '\\n' and is flushed immediately -- the
    previous version used `end="\\r"` (a terminal-only cursor trick) with
    no newline at all between the 5 countdown lines. That's invisible to
    anything reading this process's stdout line-by-line (e.g.
    recorder_bridge.py's `for line in proc.stdout:`, used when "Play" in
    the Electron app launches this exact script as a subprocess) --
    without a '\\n', the line iterator can't yield ANYTHING until the
    next real newline shows up, so the entire countdown window rendered
    as total silence in the Play panel's Activity log. Found live,
    directly from the user: "I pressed Play and it's not running at all"
    -- during exactly this window, the process WAS running (or about to),
    it just had nothing to show for it in the one place the user was
    watching. Lines are also machine-parseable (COUNTDOWN_BEGIN /
    COUNTDOWN N / COUNTDOWN_END) so the Electron UI can render an actual
    big countdown indicator instead of scrolling log text -- the second,
    separate request ("add a countdown too").

    print_fn defaults to `_flush_safe_print`, not the bare builtin --
    found live, directly from a real user run: `print(text, flush=True)`
    raised `OSError: [Errno 22] Invalid argument` on the very first
    countdown line, crashing the run before anything ever displayed.
    recorder_bridge.py is spawned by main.js with `windowsHide: true` (no
    console window), and an explicit `.flush()` on a piped stdout further
    down that no-console process chain can fail on Windows even though the
    write itself succeeds. This exact process's own `logger.info(...)`
    calls never hit this -- not because they're immune, but because
    Python's `logging.Handler.emit()` already catches and swallows
    emission errors internally (`handleError()`); the raw `print()` call
    had no equivalent protection."""
    sleep_fn = sleep_fn or time.sleep
    print_fn = print_fn or _flush_safe_print
    print_fn("COUNTDOWN_BEGIN")
    print_fn("Click on the target window NOW.")
    for i in range(seconds, 0, -1):
        print_fn(f"COUNTDOWN {i}")
        sleep_fn(1)
    print_fn("COUNTDOWN_END")


def _flush_safe_print(text: str) -> None:
    """Write, then attempt-and-ignore the flush -- see print_countdown's
    docstring. Deliberately NOT `print(text, flush=True)` a second time
    on failure: that would duplicate the line if the write half already
    succeeded and only the flush half raised."""
    print(text)
    try:
        sys.stdout.flush()
    except OSError:
        pass

# ── config ────────────────────────────────────────────────────────────────────
GOAL          = "Fill the car insurance form using data from the open text file"
PROVIDER      = "lmstudio"    # anthropic | groq | gemini | lmstudio | none
API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
SOURCE_WINDOW = "Notepad"     # title fragment of the data source window
MAX_STEPS     = 1000  # was 200 -- hard ceiling that guaranteed incompletion regardless of
                      # bug fixes. The form has 176 fields; even the best live run tonight
                      # (after 4 real bug fixes) needed ~5-6 raw steps/field, meaning full
                      # completion needs on the order of 800-1000+ steps, not 200. At the
                      # old cap, running out of steps was mathematically certain no matter
                      # how many more loop bugs got fixed. Sized with headroom, not tightly
                      # tuned -- first real end-to-end attempt will show the real number.
STEP_DELAY    = 1.0  # was 1.5 -- tonight's live runs showed ~2-3s/step dominated by this
                      # fixed pause, not LLM waits (fast-path lookup already made those
                      # near-free). Some settle time is genuinely load-bearing (a prior
                      # bug came from checking the screen too soon after a tab switch),
                      # so this is a moderate cut to test live, not a blind zero-out.
CORRECTION_WATCH_SECONDS = 0.5  # DAgger correction-capture window per failed step (0 = off).
                                 # Default was 4.0s (agent.py's own default) — that's a real-time
                                 # block for a human to physically step in and correct; on a run
                                 # nobody's actively supervising to correct in real time, 5 failures
                                 # in a row cost 20s of pure dead wait for zero benefit. Bump this
                                 # back up only for a deliberate live DAgger-collection session.

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    # emergency_stop imported and armed BEFORE `from agent.agent import
    # LLMAgent` -- deliberately, and this order matters. The previous
    # version imported LLMAgent first, then armed the hotkey -- and a
    # `from X import Y` statement fully executes (runs X's own module-
    # level code top to bottom) before the NEXT line of THIS script ever
    # runs, regardless of where start_emergency_stop_listener() is
    # *called*. agent.agent pulls in torch/transformers -- found live,
    # directly, in logs/capsule_activity.log: a 23-SECOND silent gap
    # between the process starting and anything else appearing, with the
    # emergency-stop hotkey genuinely not armed for any of it despite this
    # exact file's own earlier comment claiming "armed before anything
    # else, even before the countdown." That comment was true of the code
    # ORDER on the page, not the actual EXECUTION order -- an import
    # listed first always finishes first, no matter what runs after it in
    # the same block. Real safety gap (not just UX): if something needed
    # the failsafe during those 23s, it flatly did not exist yet.
    from agent.emergency_stop import start_emergency_stop_listener, HOTKEY_LABEL
    start_emergency_stop_listener()
    # _flush_safe_print, not print(..., flush=True) directly -- this exact
    # call shape is what OSError: [Errno 22] Invalid argument was coming
    # from, in this exact windowsHide subprocess chain (see print_countdown).
    _flush_safe_print(f"[EMERGENCY STOP] Press {HOTKEY_LABEL} at any time to force-kill this run.")
    # Also the first thing the user sees, period -- previously nothing
    # printed at all until the heavy imports below finished, which is
    # exactly what read as "stuck"/needing "shenanigans" to get going.
    _flush_safe_print("Loading model and dependencies — this can take up to 20-30s the first time...")

    _parser = argparse.ArgumentParser()
    _parser.add_argument("--start_tab", type=int, default=0,
                         help="Tab index to start from, for drilling one tab in "
                              "isolation instead of re-running the whole form each "
                              "time: 0=Policy 1=Policyholder 2=Vehicle 3=Coverage "
                              "4=Drivers 5=History 6=Claims 7=Payment. "
                              "Manually click that tab in the form before running.")
    _parser.add_argument("--model", default="tasks/form_filling/model.pt",
                         help="Transformer checkpoint to load (default = Policy model). "
                              "e.g. tasks/form_filling/model_three_tabs.pt")
    _parser.add_argument("--perception", choices=["uia", "vision"], default="uia",
                         help="Perception source: 'uia' (accessibility tree, default) or "
                              "'vision' (screenshot + CV/OCR — sees the form from pixels).")
    _parser.add_argument("--start_record", type=int, default=1,
                         help="Record number to start from in the intake text (default: 1). "
                              "After each successful Submit, the agent automatically advances "
                              "to the next record and keeps going — see --end_record to cap "
                              "how far.")
    _parser.add_argument("--end_record", type=int, default=None,
                         help="Last record number to attempt (inclusive). Default: none — "
                              "advance through every record the intake text actually has. "
                              "e.g. --start_record 2 --end_record 3 attempts only records 2-3 "
                              "of a 5-record file.")
    _args = _parser.parse_args()
    if _args.end_record is not None and _args.end_record < _args.start_record:
        logger.warning("--end_record (%d) is before --start_record (%d) — the run will "
                       "attempt record %d, submit it, then immediately end.",
                       _args.end_record, _args.start_record, _args.start_record)

    _active_key = API_KEY if PROVIDER == "anthropic" else GROQ_API_KEY if PROVIDER == "groq" else API_KEY
    if PROVIDER not in ("lmstudio", "none") and not _active_key:
        logger.error("API key not set for provider %r. Add to .env", PROVIDER)
        sys.exit(1)

    # VLM pre-scan disabled — using Win32 Notepad read instead
    visual_reader = None
    visual_cache  = {}
    logger.info("VLM pre-scan skipped — using Win32 Notepad read")

    # Everything below that can meaningfully be interrupted -- the
    # countdown, LLMAgent(...) construction (can take a while: checkpoint
    # load, LM Studio connection), and the run itself -- now lives inside
    # ONE try/except/finally. It used to be that only agent.run() was
    # covered; a Stop (CTRL_BREAK_EVENT) during the countdown or during
    # construction hit nothing and hard-killed the process before a single
    # line of this cleanup ran -- reproduced live, directly: stopping
    # during the countdown still exited 3221225786 even after the SIGBREAK
    # handler fix above, because that fix only made CTRL_BREAK_EVENT
    # *catchable* -- it still needed a try/except actually wrapping the
    # code that could be interrupted. `agent` starts as None specifically
    # so the except/finally blocks below can tell "never got that far"
    # apart from "got partway through a run" and handle both without
    # crashing on a NameError over an agent that was never constructed.
    agent = None
    results = []
    try:
        # LLMAgent's own import pulls in torch/transformers -- 20-30s on a
        # cold start (confirmed live: logs/capsule_activity.log showed a
        # real 23-second gap here). Deliberately inside the try now, not
        # before it: found live, directly, that interrupting DURING this
        # exact import still hard-killed (exit 3221225786) even after the
        # try/except was widened to cover print_countdown()+construction+
        # run() -- because the import itself was still one step further
        # out, above the try entirely. Nothing meaningful exists to save
        # at this point (agent is still None either way), but the process
        # should still exit cleanly via the same KeyboardInterrupt path as
        # everywhere else, not hard-kill, if interrupted here.
        from agent.agent import LLMAgent
        from observers.vlm.visual_data_reader.visual_data_reader import VisualDataReader

        print_countdown(5)

        # ── Perception source ────────────────────────────────────────────────
        # UIA (default) reads the accessibility tree. VISION sees the form from
        # pixels: a screenshot + CV/OCR observer, locked to the form window the
        # user just clicked (captured here, while it is the foreground window).
        # Coords are offset to absolute screen pixels so clicks land. Drop-in
        # via the observer seam — the agent calls snapshot() identically either
        # way.
        _observer = None
        if _args.perception == "vision":
            import win32gui
            from observers.vlm.vision_observer.cv_vision_observer import CVVisionObserver
            _hwnd = win32gui.GetForegroundWindow()
            _l, _t, _r, _b = win32gui.GetWindowRect(_hwnd)
            _observer = CVVisionObserver(region=(_l, _t, _r - _l, _b - _t), origin=(_l, _t))
            logger.info("Perception: VISION (CV+OCR) — window %r rect=%s",
                        win32gui.GetWindowText(_hwnd), (_l, _t, _r, _b))
            logger.info("Backends: %s", CVVisionObserver.backend_status())
        else:
            logger.info("Perception: UIA (accessibility tree)")

        from agent.scope import INSURANCE_SCOPE   # app-specific tabs/sections/records

        agent = LLMAgent(
            goal             = GOAL,
            provider         = PROVIDER,
            api_key          = _active_key,
            task_plugin      = None,
            pure_transformer = False,
            disable_auto_handlers = True,   # kill legacy heuristics — transformer(WHERE)+LLM(WHAT) merge drives
            observer         = _observer,   # None → agent defaults to UIA; else vision
            visual_reader    = visual_reader,
            visual_cache     = visual_cache,
            source_window    = SOURCE_WINDOW,
            max_steps        = MAX_STEPS,
            step_delay       = STEP_DELAY,
            start_tab_idx    = _args.start_tab,
            record_num       = _args.start_record,
            end_record       = _args.end_record,
            scope            = INSURANCE_SCOPE,   # the only place insurance-specifics live
            model_path       = _args.model,
            route_capsule    = False,             # honor --model; don't let the capsule router override
            correction_watch_seconds = CORRECTION_WATCH_SECONDS,
        )
        logger.info("Model checkpoint: %s", _args.model)
        if _args.start_tab:
            logger.info("Drill mode: starting at tab index %d — manually click that tab first.", _args.start_tab)

        logger.info("Starting — goal=%r  provider=%s  no plugin", GOAL, PROVIDER)
        results = agent.run(max_steps=MAX_STEPS, task_name="form_filling")
    except KeyboardInterrupt:
        results = list(agent._results) if agent is not None else []
        logger.info("Run interrupted by user at step %d.", len(results))
    except Exception:
        results = list(agent._results) if agent is not None else []
        # exc_info=True logs the full traceback, not just the message -- a
        # crash step number and "IndexError" alone weren't enough to
        # actually diagnose anything without knowing WHERE it happened.
        logger.error("Run crashed at step %d:", len(results), exc_info=True)
    finally:
        logger.info("Run ended — %d steps", len(results))

        # ── Evaluation metrics (always runs, even on early stop or crash) ──────
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        from eval_metrics import evaluate_run
        _metrics = evaluate_run(
            results, goal=GOAL, heuristic_steps=getattr(agent, "_heuristic_steps", []),
            run_duration_sec=getattr(agent, "_run_duration_sec", None),
            time_to_first_action_sec=getattr(agent, "_time_to_first_action_sec", None),
            manual_interventions=getattr(agent, "_manual_interventions", 0),
        )

        # ── Persist metrics to JSONL for trend tracking ───────────────────────
        try:
            import datetime as _dt
            _metrics_path = os.path.join(_ROOT, "data", "output", "run_metrics.jsonl")
            _metrics_row  = {
                "timestamp": _dt.datetime.now().isoformat(),
                "goal":      GOAL,
                "provider":  PROVIDER,
                **{k: v for k, v in _metrics.items() if k != "summary"},
            }
            with open(_metrics_path, "a", encoding="utf-8") as _mf:
                _mf.write(json.dumps(_metrics_row) + "\n")
        except Exception as _me:
            logger.debug("Metrics persist failed: %s", _me)

        # ── BC fidelity score vs gold standard ───────────────────────────────
        try:
            from bc_fidelity import score_run
            score_run(
                results, goal=GOAL,
                duration_sec=getattr(agent, "_run_duration_sec", None),
                run_start_ts=getattr(agent, "_run_start_ts", None),
            )
        except Exception as _fe:
            logger.debug("BC fidelity scorer skipped: %s", _fe)

        # ── Per-step log ──────────────────────────────────────────────────────
        for r in results:
            step = r.get("step", "?")
            act  = r.get("action", {}).get("action_type", "?")
            val  = r.get("validation", "?")
            logger.info("  step %02d: %-10s  validation=%s", step, act, val)

        # ── Extract generalized rules from the completed run ───────────────────
        if PROVIDER != "none" and results:
            from intelligence.rule_extractor import RuleExtractor
            extractor = RuleExtractor(
                provider   = PROVIDER,
                api_key    = API_KEY or os.environ.get("GROQ_API_KEY", ""),
                output_dir = "tasks/form_filling",
            )
            extractor.extract(results, goal=GOAL, task_name="form_filling")
