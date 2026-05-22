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

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("run_task")

# ── config ────────────────────────────────────────────────────────────────────
GOAL          = "Fill the car insurance form using data from the open text file"
PROVIDER      = "lmstudio"    # anthropic | groq | gemini | lmstudio | none
API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
SOURCE_WINDOW = "Notepad"     # title fragment of the data source window
MAX_STEPS     = 400
STEP_DELAY    = 1.5

# ── run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time
    from agent.agent import LLMAgent
    from observers.vlm.visual_data_reader.visual_data_reader import VisualDataReader

    if PROVIDER not in ("lmstudio", "none") and not API_KEY:
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

    agent = LLMAgent(
        goal             = GOAL,
        provider         = PROVIDER,
        api_key          = API_KEY,
        task_plugin      = None,
        pure_transformer = True,
        visual_reader    = visual_reader,
        visual_cache     = visual_cache,
        source_window    = SOURCE_WINDOW,
        max_steps        = MAX_STEPS,
        step_delay       = STEP_DELAY,
    )

    logger.info("Starting — goal=%r  provider=%s  no plugin", GOAL, PROVIDER)
    results = agent.run(max_steps=MAX_STEPS, task_name="form_filling")

    logger.info("Done — %d steps", len(results))
    for r in results:
        step = r.get("step", "?")
        act  = r.get("action", {}).get("action_type", "?")
        val  = r.get("validation", "?")
        logger.info("  step %02d: %-10s  validation=%s", step, act, val)

    # Extract generalized rules from the completed run
    if PROVIDER != "none":
        from intelligence.rule_extractor import RuleExtractor
        extractor = RuleExtractor(
            provider   = PROVIDER,
            api_key    = API_KEY or os.environ.get("GROQ_API_KEY", ""),
            output_dir = "data/output/rulesets",
        )
        extractor.extract(results, goal=GOAL, task_name="form_filling")
