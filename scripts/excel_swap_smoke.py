"""
Excel perception swap-proof (observe-only).

Proves the seam end-to-end on a REAL second app: connect ExcelObserver, take ONE
snapshot (capture -> normalize -> validate), and confirm the output conforms to
the canonical schema — i.e. the agent could read these cells exactly like UIA
fields, with ZERO agent edits.

Usage:
    1. Open Excel with any sheet that has a few rows of data.
    2. python scripts/excel_swap_smoke.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "components")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from observers.excel_observer.excel_observer import ExcelObserver
from observers.schema import validate_state


def main() -> int:
    obs = ExcelObserver()
    if not obs.connect():
        print("FAIL: could not connect to Excel — is a workbook open?")
        return 1

    state = obs.snapshot()          # capture -> normalize -> validate (logs)
    elems = state.get("elements", [])
    issues = validate_state(state)
    errors = [i for i in issues if i.startswith("ERROR")]
    warns  = [i for i in issues if i.startswith("WARN")]

    print("=" * 60)
    print(f"  source           : {state.get('source')}")
    print(f"  workbook/sheet   : {state.get('excel_context', {}).get('workbook')} "
          f"/ {state.get('excel_context', {}).get('sheet')}")
    print(f"  elements         : {len(elems)}")
    print(f"  schema ERRORs    : {len(errors)}")
    print(f"  schema WARNs     : {len(warns)}")
    print("=" * 60)

    # show a few normalized cells — should look like canonical fields
    print("  sample normalized elements (type | label | value):")
    for e in elems[:8]:
        print(f"    {e.get('type'):<14} | {str(e.get('label'))[:20]:<20} | "
              f"{str(e.get('value'))[:24]}")
    print("=" * 60)

    if errors:
        print("RESULT: [FAIL] swap NOT proven -- schema ERRORs present:")
        for e in errors:
            print(f"   {e}")
        return 1
    print("RESULT: [PASS] SWAP PROVEN -- Excel snapshot conforms to the canonical")
    print("        schema. The agent reads these cells like UIA fields, no edits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
