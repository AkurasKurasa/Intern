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

import sys as _sys_enc
# Windows console is cp1252 by default → printing the metrics summary (which has
# Unicode arrows ←/→) crashes with UnicodeEncodeError. Force UTF-8 stdout/stderr.
for _stream in (_sys_enc.stdout, _sys_enc.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import json
import logging
import sys

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

# Enable ANSI colour on the Windows console (VT processing), so logs aren't the
# default red-on-stderr. Then send logs to STDOUT (PowerShell paints stderr red):
# white text, with a green divider line before every "── Step N/M" header so
# steps read as visually separate blocks.
try:
    import ctypes as _ct
    _k = _ct.windll.kernel32
    _k.SetConsoleMode(_k.GetStdHandle(-11), 7)   # STD_OUTPUT, ENABLE_VIRTUAL_TERMINAL_PROCESSING|...
except Exception:
    pass


class _RunFormatter(logging.Formatter):
    _DIV = "\x1b[32m" + "─" * 78 + "\x1b[0m"

    def format(self, record):
        line = "\x1b[97m" + super().format(record) + "\x1b[0m"
        try:
            if "── Step " in record.getMessage():
                line = f"{self._DIV}\n{line}"
        except Exception:
            pass
        return line


logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    stream=_sys_enc.stdout,      # stdout, not stderr → no red
)
for _h in logging.getLogger().handlers:
    _h.setFormatter(_RunFormatter("[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
                                  datefmt="%H:%M:%S"))
logger = logging.getLogger("run_task")

# ── config ────────────────────────────────────────────────────────────────────
GOAL          = "Fill the car insurance form using data from the open text file"
PROVIDER      = "lmstudio"    # anthropic | groq | gemini | lmstudio | none
API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
SOURCE_WINDOW = "Notepad"     # title fragment of the data source window
MAX_STEPS     = 400   # 8 full tabs (~176 fields) + scrolls/tab-switches need headroom to reach Submit
STEP_DELAY    = 1.5

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import time
    from agent.agent import LLMAgent
    from observers.vlm.visual_data_reader.visual_data_reader import VisualDataReader

    _parser = argparse.ArgumentParser()
    _parser.add_argument("--start_tab", type=int, default=0,
                         help="Tab index to start from (0=Policy … 4=Drivers). "
                              "Manually click that tab in the form before running.")
    _parser.add_argument("--model", default="tasks/form_filling/model.pt",
                         help="Transformer checkpoint to load (default = Policy model). "
                              "e.g. tasks/form_filling/model_three_tabs.pt")
    _parser.add_argument("--perception", choices=["uia", "vision"], default="uia",
                         help="Perception source: 'uia' (accessibility tree, default) or "
                              "'vision' (screenshot + CV/OCR — sees the form from pixels).")
    _args = _parser.parse_args()

    _active_key = API_KEY if PROVIDER == "anthropic" else GROQ_API_KEY if PROVIDER == "groq" else API_KEY
    if PROVIDER not in ("lmstudio", "none") and not _active_key:
        logger.error("API key not set for provider %r. Add to .env", PROVIDER)
        sys.exit(1)

    # VLM pre-scan disabled — using Win32 Notepad read instead
    visual_reader = None
    visual_cache  = {}
    logger.info("VLM pre-scan skipped — using Win32 Notepad read")

    print("\nClick on the car insurance form window NOW.")
    for i in range(5, 0, -1):
        print(f"  Starting in {i}...", end="\r")
        time.sleep(1)
    print("  GO!                 ")

    # ── Perception source ──────────────────────────────────────────────────────
    # UIA (default) reads the accessibility tree. VISION sees the form from pixels:
    # a screenshot + CV/OCR observer, locked to the form window the user just
    # clicked (captured here, while it is the foreground window). Coords are
    # offset to absolute screen pixels so clicks land. Drop-in via the observer
    # seam — the agent calls snapshot() identically either way.
    _observer = None
    if _args.perception == "vision":
        import win32gui
        from observers.vlm.vision_observer.cv_vision_observer import CVVisionObserver
        _hwnd = win32gui.GetForegroundWindow()
        # Capture the CLIENT AREA, not the window rect: the rect includes the
        # title bar/frame, and the CV detector boxed the caption buttons as
        # "checkboxes" — the agent clicked 2px from CLOSE (live, 2026-07-10).
        # Client-area capture is generic (no pixel constants) and excludes all
        # window chrome by definition.
        _cl, _ct = win32gui.ClientToScreen(_hwnd, (0, 0))
        _, _, _cw, _ch = win32gui.GetClientRect(_hwnd)
        _observer = CVVisionObserver(region=(_cl, _ct, _cw, _ch), origin=(_cl, _ct))
        logger.info("Perception: VISION (CV+OCR) — window %r client=%s",
                    win32gui.GetWindowText(_hwnd), (_cl, _ct, _cw, _ch))
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
        scope            = INSURANCE_SCOPE,   # the only place insurance-specifics live
        model_path       = _args.model,
        route_capsule    = False,             # honor --model; don't let the capsule router override
    )
    logger.info("Model checkpoint: %s", _args.model)
    if _args.start_tab:
        logger.info("Drill mode: starting at tab index %d — manually click that tab first.", _args.start_tab)

    logger.info("Starting — goal=%r  provider=%s  no plugin", GOAL, PROVIDER)
    results = []
    try:
        results = agent.run(max_steps=MAX_STEPS, task_name="form_filling")
    except KeyboardInterrupt:
        results = list(agent._results)
        logger.info("Run interrupted by user at step %d.", len(results))
    except Exception as exc:
        results = list(agent._results)
        logger.error("Run crashed at step %d: %s", len(results), exc)
    finally:
        logger.info("Run ended — %d steps", len(results))

        # ── Evaluation metrics (always runs, even on early stop or crash) ──────
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        from eval_metrics import evaluate_run
        _metrics = evaluate_run(results, goal=GOAL, heuristic_steps=agent._heuristic_steps)

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
            score_run(results, goal=GOAL)
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
