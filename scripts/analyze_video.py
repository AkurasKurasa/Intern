"""
Analyze video_state_sample.mp4 and generate a trace sequence.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components', 'trace_translator'))

import cv2
from trace_translator import TraceTranslator

VIDEO   = r"C:\Users\paula\OneDrive\Desktop\video_state_sample.mp4"
OUT_DIR = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\video_sample"

# ── Inspect video metadata first ─────────────────────────────────────────────
cap = cv2.VideoCapture(VIDEO)
fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
duration_sec = total_frames / fps
cap.release()

print("=" * 55)
print("VIDEO METADATA")
print("=" * 55)
print(f"  File      : {os.path.basename(VIDEO)}")
print(f"  FPS       : {fps:.1f}")
print(f"  Frames    : {total_frames}")
print(f"  Duration  : {duration_sec:.1f}s  ({duration_sec/60:.1f} min)")

# Pick a sensible interval — aim for ~10-20 frames from the video
interval = max(1.0, round(duration_sec / 15, 1))
print(f"  Interval  : {interval}s  (targeting ~15 samples)")
print()

# ── Generate traces ───────────────────────────────────────────────────────────
translator = TraceTranslator(use_cv=True)

traces = translator.video_to_traces(
    VIDEO,
    interval_sec=interval,
    application='Video Recording',
    output_dir=OUT_DIR,
    verbose=True
)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 55)
print("TRACE SUMMARY")
print("=" * 55)
print(f"  Total trace steps : {len(traces)}")

added_total   = sum(len(t['diff']['added'])   for t in traces)
removed_total = sum(len(t['diff']['removed']) for t in traces)
changed_total = sum(len(t['diff']['changed']) for t in traces)

print(f"  Total added       : {added_total}")
print(f"  Total removed     : {removed_total}")
print(f"  Total changed     : {changed_total}")

# Find the most active transition
if traces:
    busiest = max(traces, key=lambda t: len(t['diff']['added']) + len(t['diff']['removed']) + len(t['diff']['changed']))
    b_id    = busiest['trace_id']
    b_score = len(busiest['diff']['added']) + len(busiest['diff']['removed']) + len(busiest['diff']['changed'])
    print(f"\n  Most active step  : {b_id}  ({b_score} element changes)")

    # Step-by-step breakdown
    print(f"\n  Step-by-step changes:")
    print(f"  {'Step':<30} {'Added':>6} {'Removed':>8} {'Changed':>8}")
    print(f"  {'-'*54}")
    for t in traces:
        d = t['diff']
        print(f"  {t['trace_id']:<30} {len(d['added']):>6} {len(d['removed']):>8} {len(d['changed']):>8}")

print(f"\n  Traces saved to: {OUT_DIR}")
