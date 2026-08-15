"""
Quick script to generate a trace from trace_sample.png
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components', 'trace_translator'))

from trace_translator import TraceTranslator

IMAGE_PATH = r"C:\Users\paula\OneDrive\Desktop\trace_sample.png"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'data', 'output', 'traces', 'trace_sample_trace.json')

translator = TraceTranslator(use_cv=True)

print(f"\nAnalyzing: {IMAGE_PATH}")
state = translator.image_to_state(IMAGE_PATH, application='YouTube (Chrome)')

print(f"\n--- State Summary ---")
print(f"  Application : {state['application']}")
print(f"  Resolution  : {state['screen_resolution']}")
print(f"  Elements    : {len(state['elements'])}")

if state['elements']:
    print(f"\n  First 10 detected text elements:")
    for i, el in enumerate(state['elements'][:10], 1):
        print(f"    {i:2}. [{el['bbox']}]  '{el['text']}'  (conf={el['confidence']:.2f})")

trace = translator.state_to_trace(state, trace_id='trace_sample_snapshot')

os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
translator.save_trace(trace, OUTPUT_PATH)

print(f"\n✓ Trace saved to: {OUTPUT_PATH}")
