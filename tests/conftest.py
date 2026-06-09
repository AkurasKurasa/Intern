"""
pytest bootstrap — put the repo root and components/ on sys.path so tests can
import the agent the same way the app does (`from agent.agent import LLMAgent`).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# keep heavy/optional deps from blocking import-time (mirrors run_task.py env)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
