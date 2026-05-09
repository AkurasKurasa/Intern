"""
run_agent.py
============
Entry point for running the LLMAgent on a live task.

Run from the repo root in your own terminal (NOT via Claude Code):
    python run_agent.py
"""

from __future__ import annotations

# ── COM init — must happen before uiautomation is imported ────────────────────
import ctypes
try:
    ctypes.windll.ole32.CoInitialize(None)
except Exception:
    pass

import logging
import os
import sys

# ── path setup ────────────────────────────────────────────────────────────────
_ROOT     = os.path.dirname(os.path.abspath(__file__))
_COMP_DIR = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── load .env ─────────────────────────────────────────────────────────────────
_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("run_agent")

# ── config ────────────────────────────────────────────────────────────────────
GOAL             = "Fill the car insurance form using data from the open text file"
PROVIDER         = "lmstudio"
API_KEY          = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
MODEL_PATH       = os.path.join(_ROOT, "data", "models", "transformer_bc.pt")
DRY_RUN          = False
MAX_STEPS        = 150   # agent stops itself via "done" action when complete
STEP_DELAY       = 1.5
TASK_NAME        = "fill_insurance"
RECORD_START     = 1    # first record to fill (1-based)
RECORD_END       = 1    # last record to fill (inclusive); set > 1 to loop
SOURCE_WINDOW    = "data_entry_intake"   # fragment of source window title (matches "data_entry_intake.txt - Notepad")
USE_VLM          = True   # set True to enable Groq/Gemini vision; live scan per tab
USE_VLM_PRESCAN  = False  # set True to also do upfront full-document pre-scan
                          # (heavy on rate limits — only useful when scan_tab is unreliable)

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _needs_key = PROVIDER not in ("lmstudio", "none", "gemini")  # gemini key checked later by agent
    if _needs_key and not API_KEY:
        logger.error("API key not set for provider %r. Add it to .env", PROVIDER)
        sys.exit(1)

    import time
    from agent.agent import LLMAgent
    from agent.task_plugins import FormFillerPlugin
    from data_sources import NotepadDataSource

    # ── VLM setup ─────────────────────────────────────────────────────────────
    # Reader instance is created so the plugin can do live per-tab scans
    # (scan_tab) — those fire only when the agent enters a tab, ~5 VLM calls
    # per record. The optional upfront pre_scan walks the entire document and
    # is heavy on rate limits — kept gated behind USE_VLM_PRESCAN.
    visual_cache: dict = {}                       # flat cache (backward-compat)
    visual_record_cache: dict = {}                # per-record cache: {record_num: {field: value}}
    _reader = None
    if USE_VLM:
        from observers.vlm.visual_data_reader import VisualDataReader
        if GROQ_API_KEY:
            logger.info("VLM enabled — Groq backend (live scan_tab on tab switch)")
            _reader = VisualDataReader(api_key=GEMINI_API_KEY, groq_api_key=GROQ_API_KEY)
        elif GEMINI_API_KEY:
            logger.info("VLM enabled — Gemini backend (live scan_tab on tab switch)")
            _reader = VisualDataReader(api_key=GEMINI_API_KEY)
        else:
            logger.warning("USE_VLM=True but no vision API key set — falling back to Win32 text read")

        if _reader and USE_VLM_PRESCAN:
            logger.info("Pre-scanning source window %r (USE_VLM_PRESCAN=True)...", SOURCE_WINDOW)
            visual_cache = _reader.pre_scan(SOURCE_WINDOW)
            visual_record_cache = _reader.get_record_cache()
            logger.info("Pre-scan complete: %d total field(s) across %d record(s)",
                        len(visual_cache), len(visual_record_cache))
        elif _reader:
            logger.info("Skipping upfront pre-scan (USE_VLM_PRESCAN=False) — "
                        "relying on live scan_tab per tab switch")
    else:
        logger.info("USE_VLM=False — using Win32 text read (_peek_notepad) as sole data source")

    print("\nClick on the car insurance form window NOW.")
    for i in range(5, 0, -1):
        print(f"  Starting in {i}...", end="\r")
        time.sleep(1)
    print("  GO!                 ")

    import pyautogui

    total_records = RECORD_END - RECORD_START + 1
    logger.info("Starting LLMAgent  goal=%r  provider=%s  records=%d-%d  dry_run=%s",
                GOAL, PROVIDER, RECORD_START, RECORD_END, DRY_RUN)

    all_results = []
    for record_num in range(RECORD_START, RECORD_END + 1):
        logger.info("=" * 60)
        logger.info("RECORD %d / %d", record_num, RECORD_END)
        logger.info("=" * 60)

        # Build the data source and plugin for this record
        data_src = NotepadDataSource(SOURCE_WINDOW)
        # Do NOT pre-seed data_src._cache from the flat visual_cache —
        # the cache is keyed only by field name, so records 2+ would read
        # record 1's values silently. Plugin._refresh_record_cache fills it
        # via Win32 record-aware parsing per record_num.

        plugin = FormFillerPlugin(
            executor      = None,        # executor is wired in by LLMAgent.__init__
            data_source   = data_src,
            visual_reader = _reader if USE_VLM else None,
            record_num    = record_num,
            step_delay    = STEP_DELAY,
        )
        # Per-record VLM cache becomes primary data for this record.
        # Falls back to flat cache if VLM didn't bucket this record (unscrolled
        # screens, missed header). Win32 text remains as final fallback inside
        # _refresh_record_cache.
        per_record_vlm = visual_record_cache.get(record_num, {})
        if per_record_vlm:
            plugin._visual_cache = dict(per_record_vlm)
            logger.info("VLM record-cache for record %d: %d field(s)", record_num, len(per_record_vlm))
        elif visual_cache:
            plugin._visual_cache = dict(visual_cache)
            logger.warning("No VLM record-cache for record %d — using flat cache (%d fields, "
                           "values may be from another record)",
                           record_num, len(visual_cache))

        agent = LLMAgent(
            goal          = GOAL,
            provider      = PROVIDER,
            api_key       = API_KEY,
            model_path    = MODEL_PATH,
            dry_run       = DRY_RUN,
            max_steps     = MAX_STEPS,
            step_delay    = STEP_DELAY,
            record_num    = record_num,
            use_ocr       = True,
            visual_cache  = visual_cache,   # kept for backward compat / _state_to_text
            visual_reader = _reader if USE_VLM else None,
            source_window = SOURCE_WINDOW,
            task_plugin   = plugin,
        )
        results = agent.run(max_steps=MAX_STEPS, task_name=TASK_NAME)
        all_results.extend(results)

        # After the agent is done, click "Submit & New" to save + clear the form
        if record_num < RECORD_END:
            logger.info("Clicking 'Submit & New' to advance to record %d...", record_num + 1)
            _clicked_submit_new = False
            try:
                import uiautomation as auto
                for _btn_name in ("Submit & New", "Submit  New"):
                    _btn = auto.ButtonControl(Name=_btn_name, searchDepth=10)
                    if _btn.Exists(maxSearchSeconds=2):
                        _btn.Click()
                        logger.info("Submit & New clicked via UIA (%r).", _btn_name)
                        _clicked_submit_new = True
                        break
                if not _clicked_submit_new:
                    # Fallback: find any ButtonControl whose name contains "Submit" and "New"
                    _root = auto.GetRootControl()
                    for _c in _root.GetChildren():
                        for _b in _c.GetChildren():
                            _n = getattr(_b, "Name", "") or ""
                            if "submit" in _n.lower() and "new" in _n.lower():
                                _b.Click()
                                logger.info("Submit & New clicked via tree walk (%r).", _n)
                                _clicked_submit_new = True
                                break
                        if _clicked_submit_new:
                            break
                if not _clicked_submit_new:
                    logger.error("Could not find Submit & New button — stopping loop.")
                    break
            except Exception as e:
                logger.error("Submit & New click failed: %s", e)
                break
            time.sleep(2.0)   # wait for form to clear before next record

    logger.info("=" * 60)
    logger.info("All done — %d record(s), %d total steps", total_records, len(all_results))
    for r in all_results:
        step = r.get("step", "?")
        act  = r.get("action", {}).get("action_type", "?")
        val  = r.get("validation", "?")
        logger.info("  step %02d: %-10s  validation=%s", step, act, val)
