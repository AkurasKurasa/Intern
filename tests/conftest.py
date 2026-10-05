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

# Tests must never drive a window the user opened with Launch. Found
# 2026-10-05: with a real Scope #2 portal window open, a show-mode test
# attached to it and filled a row there. workspace_browser.attach() honours
# this switch; the tests that exercise attaching clear it for their own
# private, headless windows (see tests/test_inbox_workspace.py and
# tests/scope2/test_workspace.py).
os.environ["INTERN_NO_WORKSPACE_ATTACH"] = "1"
