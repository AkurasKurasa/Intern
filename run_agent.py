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
GOAL       = "Fill the car insurance form using data from the open text file"
PROVIDER   = "groq"
API_KEY    = os.environ.get("GROQ_API_KEY", "")
MODEL_PATH = "data/models/transformer_bc.pt"
DRY_RUN    = False
MAX_STEPS  = 60
STEP_DELAY = 1.5
TASK_NAME  = "fill_insurance"

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not API_KEY:
        logger.error("GROQ_API_KEY not set. Add it to .env")
        sys.exit(1)

    import time
    from agent.agent import LLMAgent

    print("\nClick on the car insurance form window NOW.")
    for i in range(5, 0, -1):
        print(f"  Starting in {i}...", end="\r")
        time.sleep(1)
    print("  GO!                 ")

    logger.info("Starting LLMAgent  goal=%r  provider=%s  dry_run=%s", GOAL, PROVIDER, DRY_RUN)
    agent = LLMAgent(
        goal       = GOAL,
        provider   = PROVIDER,
        api_key    = API_KEY,
        model_path = MODEL_PATH,
        dry_run    = DRY_RUN,
        max_steps  = MAX_STEPS,
        step_delay = STEP_DELAY,
    )
    results = agent.run(max_steps=MAX_STEPS, task_name=TASK_NAME)

    logger.info("=" * 60)
    logger.info("Run complete — %d steps", len(results))
    for r in results:
        step = r.get("step", "?")
        act  = r.get("action", {}).get("action_type", "?")
        val  = r.get("validation", "?")
        logger.info("  step %02d: %-10s  validation=%s", step, act, val)
