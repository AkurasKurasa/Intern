"""Open the portal window the agent will work in (the Launch button).

    python open_workspace.py                 # v0_base
    python open_workspace.py --variant v2_relabeled

Exits as soon as the browser is started; the browser itself is detached and
stays open. Play (automate.py --show) then fills this same window -- see
executor/workspace.py.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from executor.workspace import open_workspace  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", default="v0_base")
    args = ap.parse_args()
    status, _ = open_workspace(args.variant)
    print(f"portal workspace {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
