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
GOAL          = "Fill the car insurance form using data from the open text file"
PROVIDER      = "lmstudio"
API_KEY       = os.environ.get("GROQ_API_KEY", "")
MODEL_PATH    = os.path.join(_ROOT, "data", "models", "transformer_bc.pt")
DRY_RUN       = False
MAX_STEPS     = 500   # agent stops itself via "done" action when complete
STEP_DELAY    = 1.5
TASK_NAME     = "fill_insurance"
RECORD_START  = 1    # first record to fill (1-based)
RECORD_END    = 5    # last record to fill (inclusive); set > 1 to loop

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _needs_key = PROVIDER not in ("lmstudio", "none")
    if _needs_key and not API_KEY:
        logger.error("API key not set for provider %r. Add it to .env", PROVIDER)
        sys.exit(1)

    import time
    from agent.agent import LLMAgent

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

        agent = LLMAgent(
            goal       = GOAL,
            provider   = PROVIDER,
            api_key    = API_KEY,
            model_path = MODEL_PATH,
            dry_run    = DRY_RUN,
            max_steps  = MAX_STEPS,
            step_delay = STEP_DELAY,
            record_num = record_num,
            use_ocr    = True,   # EXPERIMENTAL: Tesseract OCR fallback for background windows
        )
        results = agent.run(max_steps=MAX_STEPS, task_name=TASK_NAME)
        all_results.extend(results)

        # After the agent is done, click "Submit & New" to save + clear the form
        if record_num < RECORD_END:
            logger.info("Clicking 'Submit & New' to advance to record %d...", record_num + 1)
            try:
                import uiautomation as auto
                btn = auto.ButtonControl(Name="Submit & New", searchDepth=6)
                if btn.Exists(maxSearchSeconds=3):
                    btn.Click()
                    logger.info("Submit & New clicked via UIA.")
                else:
                    logger.warning("Submit & New button not found — trying image search.")
                    loc = pyautogui.locateOnScreen("assets/btn_submit_new.png",
                                                   confidence=0.8)
                    if loc:
                        pyautogui.click(pyautogui.center(loc))
                    else:
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
