"""
Regression test locking down a real live infinite loop: combobox dropdown
item matching only recognized elements typed "listitemcontrol", but
ui_observer.py's _CTRL_TYPE_MAP maps the standard UIA "ListItem" control
type to "listitem" (no "control" suffix) -- only raw, unmapped
ControlTypeName strings fall through as "listitemcontrol".

Found live 2026-08-07: a run looped forever on "Body Type" -- opened the
dropdown, found 0 list items every single time despite 8 new elements
genuinely appearing (StateValidator confirmed it), concluded "Sedan" wasn't
in the dropdown, pressed Escape, and repeated. Fixed in all three places
agent.py filtered list items by type: combobox auto-fix (~L1671), the
click-to-fill combobox handler (~L1950), and the type-into-combobox handler
(~L2333) -- all now accept both "listitem" and "listitemcontrol".

This test can't exercise the full step loop (needs a live GUI), so it
directly verifies the type sets used by each fixed filter accept the
standard, mapped type -- a plain string-membership check on the exact
constant/inline set used at each call site would have caught the original
bug (a set/comparison containing only "listitemcontrol").
"""
import re
import sys
from pathlib import Path

_AGENT_PY = Path(__file__).resolve().parent.parent / "components" / "agent" / "agent.py"


def test_no_call_site_filters_list_items_by_listitemcontrol_alone():
    """The actual regression check: no line in agent.py should compare an
    element's type to the single literal "listitemcontrol" via `==` — every
    such check must accept "listitem" too. A reintroduced single-type check
    would silently break dropdown matching again."""
    src = _AGENT_PY.read_text(encoding="utf-8")
    offending = re.findall(r'get\("type"\)\s*==\s*"listitemcontrol"', src)
    assert not offending, (
        f"found {len(offending)} single-type 'listitemcontrol' check(s) -- "
        "must accept 'listitem' too, since ui_observer.py maps the standard "
        "UIA ListItem control type to 'listitem' (no 'control' suffix)"
    )


def test_listitem_types_constant_covers_both_forms():
    sys.path.insert(0, str(_AGENT_PY.parent.parent))
    src = _AGENT_PY.read_text(encoding="utf-8")
    assert '_LISTITEM_TYPES = {"listitem", "listitemcontrol"}' in src


def test_dropdown_polling_waits_at_least_2_seconds_total():
    """The type-name fix alone wasn't enough: re-tested live and 'Sedan' (a
    genuinely correct, exact-match option) STILL came up with 0 items in the
    same run where other dropdowns succeeded moments apart -- the popup
    just hadn't finished rendering within the old 4x0.35s=1.4s poll window
    every time. Both combobox dropdown-polling loops were widened to
    _POLL_TRIES/_POLL_INTERVAL giving at least 2s of total patience --
    this locks in that neither silently regresses back to a shorter window."""
    src = _AGENT_PY.read_text(encoding="utf-8")
    matches = re.findall(r"_POLL_TRIES,\s*_POLL_INTERVAL\s*=\s*(\d+),\s*([\d.]+)", src)
    assert len(matches) == 2, f"expected 2 poll-config declarations, found {len(matches)}"
    for tries, interval in matches:
        total = int(tries) * float(interval)
        assert total >= 2.0, f"poll window {tries}x{interval}s = {total}s is under the 2s floor"
