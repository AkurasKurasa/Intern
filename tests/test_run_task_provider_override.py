"""
run_task.py's LLM provider can be overridden with INTERN_PROVIDER, so a
machine without LM Studio can run Intern with provider "none" (direct lookup
+ transformer only). Added 2026-11-01 for the Objective 7 comparison runs.
The default must stay "lmstudio" so nothing changes for anyone else.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_task


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("INTERN_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("INTERN_PROVIDER", value)
    return importlib.reload(run_task)


def test_default_provider_is_still_lmstudio(monkeypatch):
    assert _reload(monkeypatch, None).PROVIDER == "lmstudio"


def test_env_var_overrides_provider(monkeypatch):
    assert _reload(monkeypatch, "none").PROVIDER == "none"


def test_none_provider_needs_no_api_key():
    # The startup key check in run_task.py exempts lmstudio and none; this
    # pins that exemption so provider "none" can't start failing on a missing key.
    src = (Path(run_task.__file__)).read_text(encoding="utf-8")
    assert 'if PROVIDER not in ("lmstudio", "none") and not _active_key:' in src
