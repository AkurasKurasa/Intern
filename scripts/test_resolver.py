"""
test_resolver.py — isolation test for value inference.

Snapshots the live UIA state (form + Notepad must be open) and asks the
_TextResolver what value it would type for each Policy field, by matching the
field label against the Notepad content. Proves the content-inference half
works independently of the transformer.
"""
import sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "components")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from observers.ui_observer.ui_observer import UIAutomationObserver
from agent.executor import _TextResolver

POLICY_FIELDS = [
    "Policy Number", "Policy Status", "Policy Type", "Policy Term",
    "Effective Date", "Expiration Date", "Agent ID", "Agent Name",
    "Agency Name", "Underwriter",
]

def main():
    obs = UIAutomationObserver(background_apps={"notepad", ".txt"})
    state = obs.snapshot()
    elems = state.get("elements", [])
    bg = [e for e in elems if e.get("window_role") == "background"]
    active = [e for e in elems if e.get("window_role") == "active"]
    print(f"State: {len(elems)} elems  ({len(active)} active, {len(bg)} background)")
    print(f"Active app: {state.get('application')}")
    print(f"Background windows: {sorted({e.get('window_title','')[:30] for e in bg})}")
    print("=" * 60)

    resolver = _TextResolver()
    hits = 0
    for field in POLICY_FIELDS:
        # find the form field element by label, mark it focused, resolve
        fld = next((e for e in active
                    if field.lower() in (e.get("label") or e.get("text") or "").lower()
                    and e.get("type") in ("input", "combobox", "editcontrol", "comboboxcontrol")),
                   None)
        if fld:
            test_state = dict(state)
            test_state["focused_element_id"] = fld.get("element_id")
            resolver._used_texts = set()  # reset dedupe per field
            val = resolver.resolve(test_state, source_elem_idx=-1)
        else:
            # field element not found on screen — match value directly from Notepad
            resolver._used_texts = set()
            val = _TextResolver._match_value(field, bg)
        mark = "OK " if val else "-- "
        if val:
            hits += 1
        print(f"  {mark} {field:<22} -> {val!r}")

    print("=" * 60)
    print(f"Resolved {hits}/{len(POLICY_FIELDS)} Policy field values from Notepad")

if __name__ == "__main__":
    main()
