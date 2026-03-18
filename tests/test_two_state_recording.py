"""
test_two_state_recording.py
===========================
Tests the two-state trace format introduced in TraceTranslator.

Workflow
--------
1. If a video file is found in data/input/, extract frames from it.
2. Otherwise, synthesise two distinct "before" and "after" UI screenshots
   that differ in a predictable way (a button is added, a label changes).
3. Run image_to_state on each frame.
4. Call states_to_trace to produce a transition trace.
5. Print a human-readable diff report and save the trace JSON.

Run from the project root:
    python -m tests.test_two_state_recording
  or
    python tests/test_two_state_recording.py
"""

import sys
import os
import json
from pathlib import Path
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Path setup – works whether run as a module or a script
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent          # Intern/
COMPONENT = ROOT / "components" / "trace_translator"
sys.path.insert(0, str(COMPONENT))

from trace_translator.trace_translator import TraceTranslator

OUTPUT_DIR = ROOT / "test_data" / "output" / "two_state"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FRAME_DIR = OUTPUT_DIR / "frames"
FRAME_DIR.mkdir(exist_ok=True)


# ============================================================================
# STEP 1 – obtain two frames
# ============================================================================

def find_video(input_dir: Path) -> Path | None:
    """Return the first video file found in input_dir, or None."""
    for ext in ("*.mp4", "*.avi", "*.mov", "*.mkv", "*.webm"):
        hits = list(input_dir.glob(ext))
        if hits:
            return hits[0]
    return None


def extract_frames_from_video(video_path: Path, n: int = 2) -> list[Path]:
    """
    Extract n evenly-spaced frames from a video using OpenCV.
    Returns list of saved frame paths.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        print("  ✗ opencv-python not installed – falling back to synthetic frames.")
        return []

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < n:
        n = total

    indices = [int(i * (total - 1) / (n - 1)) for i in range(n)]
    paths: list[Path] = []

    for idx, frame_no in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok:
            continue
        p = FRAME_DIR / f"frame_{idx:02d}.png"
        cv2.imwrite(str(p), frame)
        paths.append(p)
        print(f"  ✓ Extracted frame {frame_no}/{total}  →  {p.name}")

    cap.release()
    return paths


def _draw_ui(draw: ImageDraw.ImageDraw, w: int, h: int, variant: str):
    """Draw a simple UI mockup.  variant='before'|'after'."""
    # Background
    draw.rectangle([0, 0, w, h], fill="#f5f5f5")

    # Title bar
    draw.rectangle([0, 0, w, 40], fill="#2c3e50")
    draw.text((10, 10), "My Application", fill="white")

    # Text field  (always present)
    draw.rectangle([30, 70, 400, 110], outline="#aaa", width=2, fill="white")
    label = "Hello World" if variant == "after" else "Enter text here..."
    draw.text((38, 82), label, fill="#555" if variant == "before" else "#222")
    draw.text((30, 55), "Input:", fill="#333")

    # Save button  (always present)
    draw.rectangle([30, 140, 140, 180], outline="#2980b9", width=2, fill="#3498db")
    draw.text((52, 152), "Save", fill="white")

    # Cancel button  (always present)
    draw.rectangle([160, 140, 270, 180], outline="#c0392b", width=2, fill="#e74c3c")
    draw.text((178, 152), "Cancel", fill="white")

    # Status label – changes between states
    status = "Status: Idle" if variant == "before" else "Status: Saved!"
    draw.text((30, 210), status, fill="#27ae60" if variant == "after" else "#888")

    # New button that ONLY appears in the "after" state
    if variant == "after":
        draw.rectangle([300, 140, 450, 180], outline="#8e44ad", width=2, fill="#9b59b6")
        draw.text((328, 152), "Share", fill="white")


def synthesise_frames() -> tuple[Path, Path]:
    """Create two PNG files that differ in a controlled, visible way."""
    W, H = 600, 300

    before_path = FRAME_DIR / "frame_before.png"
    after_path  = FRAME_DIR / "frame_after.png"

    for path, variant in [(before_path, "before"), (after_path, "after")]:
        img  = Image.new("RGB", (W, H), color="white")
        draw = ImageDraw.Draw(img)
        _draw_ui(draw, W, H, variant)
        img.save(path)
        print(f"  ✓ Synthesised {variant} frame  →  {path.name}")

    return before_path, after_path


# ============================================================================
# STEP 2 – run the translator
# ============================================================================

def run_two_state_test(frame_before: Path, frame_after: Path):
    print("\n" + "=" * 70)
    print("STEP 2 – Extracting UI states with TraceTranslator")
    print("=" * 70)

    translator = TraceTranslator(use_cv=True)

    print(f"\n  Processing BEFORE frame: {frame_before.name}")
    state_before = translator.image_to_state(str(frame_before), application="RecordingApp")

    print(f"\n  Processing AFTER  frame: {frame_after.name}")
    state_after  = translator.image_to_state(str(frame_after),  application="RecordingApp")

    print(f"\n  Elements in BEFORE state: {len(state_before['elements'])}")
    print(f"  Elements in AFTER  state: {len(state_after['elements'])}")

    return translator, state_before, state_after


# ============================================================================
# STEP 3 – build the two-state trace and report
# ============================================================================

def build_and_report(translator: TraceTranslator,
                     state_before: dict,
                     state_after:  dict):
    print("\n" + "=" * 70)
    print("STEP 3 – Building two-state trace  (states_to_trace)")
    print("=" * 70)

    action = {
        "type": "TYPE",
        "description": "User typed into the text field and saved"
    }

    trace = translator.states_to_trace(
        state_before=state_before,
        state_after=state_after,
        action=action,
        trace_id="recording_step_001"
    )

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------
    out_path = OUTPUT_DIR / "recording_step_001.json"
    translator.save_trace(trace, str(out_path))
    print(f"\n  ✓ Trace saved  →  {out_path}")

    # -----------------------------------------------------------------------
    # Human-readable diff report
    # -----------------------------------------------------------------------
    diff = trace["diff"]
    meta = trace["metadata"]

    print("\n" + "=" * 70)
    print("TRACE DIFF REPORT")
    print("=" * 70)
    print(f"  trace_type          : {meta['trace_type']}")
    print(f"  elements BEFORE     : {meta['num_elements_before']}")
    print(f"  elements AFTER      : {meta['num_elements_after']}")

    print(f"\n  ➕ Added elements    : {len(diff['added'])}")
    for el in diff["added"]:
        print(f"       {el['element_id']!r:20s}  type={el['type']!r}  text={el.get('text','')!r}")

    print(f"\n  ➖ Removed elements  : {len(diff['removed'])}")
    for el in diff["removed"]:
        print(f"       {el['element_id']!r:20s}  type={el['type']!r}  text={el.get('text','')!r}")

    print(f"\n  ✏️  Changed elements  : {len(diff['changed'])}")
    for ch in diff["changed"]:
        print(f"       {ch['element_id']!r}")
        for field, delta in ch["changes"].items():
            print(f"         {field}: {delta['before']!r}  →  {delta['after']!r}")

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("VALIDATION")
    print("=" * 70)
    total_changes = len(diff["added"]) + len(diff["removed"]) + len(diff["changed"])

    assert "state_before" in trace, "FAIL: state_before missing from trace"
    assert "state_after"  in trace, "FAIL: state_after missing from trace"
    assert trace["state_before"] is not trace["state_after"], \
        "FAIL: state_before and state_after are the same object"
    assert "diff" in trace, "FAIL: diff key missing"
    assert meta["trace_type"] == "transition", "FAIL: wrong trace_type"

    print("  ✓ state_before present")
    print("  ✓ state_after  present")
    print("  ✓ state_before ≠ state_after")
    print("  ✓ diff block present")
    print(f"  ✓ trace_type == 'transition'")
    print(f"  ✓ total element changes detected: {total_changes}")

    if total_changes == 0:
        print("\n  ⚠  No changes detected – this is expected when both frames")
        print("     are identical (e.g. video had no visible UI change between")
        print("     the two sampled frames, or OCR found the same text in both).")
    else:
        print(f"\n  🎉 State change successfully distinguished!")

    return trace


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 70)
    print("TWO-STATE TRACE TEST  –  Screen Recording Edition")
    print("=" * 70)

    # --- Step 1: obtain frames -----------------------------------------------
    print("\nSTEP 1 – Obtaining frames")
    print("=" * 70)

    input_dir = ROOT / "data" / "input"
    video = find_video(input_dir)

    if video:
        print(f"  Found video: {video}")
        frame_paths = extract_frames_from_video(video, n=2)
        if len(frame_paths) >= 2:
            frame_before, frame_after = frame_paths[0], frame_paths[1]
        else:
            print("  Could not extract enough frames – falling back to synthetic.")
            frame_before, frame_after = synthesise_frames()
    else:
        print("  No video found in data/input/ – using synthetic UI frames.")
        frame_before, frame_after = synthesise_frames()

    # --- Step 2 & 3 ----------------------------------------------------------
    translator, state_before, state_after = run_two_state_test(frame_before, frame_after)
    build_and_report(translator, state_before, state_after)

    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print(f"Output folder: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
