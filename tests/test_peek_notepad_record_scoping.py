"""
Regression test for LLMAgent._peek_notepad()'s record-scoped field search.

Real bug found live, direct report ("Check most recent logs. I don't think
reasoning was activated again."): _peek_notepad used to search the ENTIRE
multi-record intake file for a field, with no idea which record it was
actually supposed to be reading. The FOREIGN_TEST intake file has a real,
legitimate relabeling in record 1 ("Policy Reference #" instead of "Policy
Number") -- record 1 genuinely has no "Policy Number" key. The whole-file
search fell through to the NEXT matching line anywhere in the file, which
belongs to record 2 -- and silently filled a DIFFERENT customer's policy
number ('PAI-2026-00442') into record 1's form, with no error logged
anywhere. Confirmed directly in logs/run_task_20260819_153010.log.

_peek_notepad itself is heavy Win32-integration code (window enumeration,
EM_LINESCROLL, WM_GETTEXT) not practically unit-testable end to end --
same category as _reassert_form_window()'s own Win32 dependencies. This is
a source-level regression test confirming the fix is actually wired in,
matching the established pattern elsewhere in this test suite (e.g.
test_ask_llm_deep_reasoning.py's TestCallSiteRoutesOnTransformerConfidence).
The new record-scoping function itself (_record_body_and_line_offset) is
directly, fully unit-tested in test_notepad_source.py against this exact
real intake file.
"""
from pathlib import Path

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"
_AGENT_SOURCE = _AGENT_PY.read_text(encoding="utf-8")


def test_peek_notepad_scopes_its_search_to_the_current_record():
    anchor = "def _peek_notepad(self, state: Dict[str, Any], field_name: str) -> None:"
    idx = _AGENT_SOURCE.index(anchor)
    window = _AGENT_SOURCE[idx:idx + 4700]
    assert "_record_body_and_line_offset" in window, (
        "_peek_notepad must scope its field search to the current record, "
        "not search the whole multi-record file"
    )
    assert "self._record_num" in window, (
        "must scope using the agent's own current record number"
    )


def test_peek_notepad_still_falls_back_to_whole_file_search_when_unscoped():
    """Single-record sources (or any text with no 'RECORD N OF M' headers)
    must keep working exactly as before -- _record_body_and_line_offset()
    returns (None, 0) in that case, same fallback contract already proven
    in test_notepad_source.py's own tests for that function."""
    anchor = "def _peek_notepad(self, state: Dict[str, Any], field_name: str) -> None:"
    idx = _AGENT_SOURCE.index(anchor)
    window = _AGENT_SOURCE[idx:idx + 4700]
    assert "_find_field_line(lines, field_name)" in window, (
        "must still fall back to searching the full text when no record-scoped body is found"
    )
