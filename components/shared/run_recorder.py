"""
Shared, reliability-hardened run-result recorder used by both Scope #1
(components/agent/, via run_task.py) and Scope #2 (components/scope2/,
via automate.py). One appended JSONL row per run, in one shared trend
log, so a fix to "why didn't this run get recorded" benefits both scopes
at once instead of needing to be found and fixed twice.

Each scope keeps its own rich, scope-specific result computation --
Scope #1's eval_metrics.evaluate_run(), Scope #2's per-run JSON log via
automate.py's log.write() -- this module only unifies the reliability of
the *persistence* step, not what a "result" means for each scope. See
docs/superpowers/specs/2026-08-21-scope-unification-design.md, Piece 2.

Confirmed root cause of the pre-unification gap: the old inline persist
block in run_task.py caught its own write failures at logger.debug(),
a level nothing in this project actually reads -- so a real failure
there was indistinguishable from "nothing went wrong." This module fixes
that by logging at ERROR with a full traceback, and by returning a
plain bool so callers/tests can assert on success without needing to
inspect logs at all.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "output" / "run_metrics.jsonl"


def record_run_result(scope: str, row: Mapping[str, Any], path: Optional[Path] = None) -> bool:
    """Append one row to the shared trend log. Never raises -- recording a
    run's result must never fail the run it's recording. Returns True on
    success, False on failure."""
    try:
        target = Path(path) if path is not None else DEFAULT_PATH
        full_row = {
            "scope": scope,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **row,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(full_row) + "\n")
        return True
    except Exception:
        logger.error(
            "record_run_result: failed to persist a %r run's result to %s",
            scope, path if path is not None else DEFAULT_PATH, exc_info=True,
        )
        return False
