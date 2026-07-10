"""
vision_live_view.py — live monitor of what the vision observer sees.

Watches VISION_DEBUG_DIR (default scratch/vision_frames) and always displays
the newest annotated frame. Run it BESIDE a `--perception vision` agent run:

    # terminal 1
    $env:VISION_DEBUG_DIR="scratch\\vision_frames"
    python run_task.py --model ... --perception vision

    # terminal 2
    python scripts/vision_live_view.py            # or: make see

Separate process on purpose: a viewer window inside the agent's process could
steal focus/clicks from the form it is driving. Press Q or close the window to
stop; the agent run is unaffected.
"""
from __future__ import annotations

import glob
import os
import sys
import time

import cv2


def main() -> int:
    watch = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "VISION_DEBUG_DIR", os.path.join("scratch", "vision_frames"))
    print(f"watching {watch!r} — Q to quit")
    cv2.namedWindow("what it sees", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("what it sees", 980, 760)
    last = None
    while True:
        frames = glob.glob(os.path.join(watch, "see_*.png"))
        if frames:
            newest = max(frames, key=os.path.getmtime)
            if newest != last:
                img = cv2.imread(newest)          # None while half-written → retry next tick
                if img is not None:
                    last = newest
                    cv2.setWindowTitle("what it sees",
                                       f"what it sees — {os.path.basename(newest)}")
                    cv2.imshow("what it sees", img)
        # waitKey doubles as the poll interval + keeps the window responsive
        if (cv2.waitKey(250) & 0xFF) in (ord("q"), ord("Q")):
            break
        if cv2.getWindowProperty("what it sees", cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
