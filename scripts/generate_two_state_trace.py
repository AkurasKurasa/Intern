"""
Generate a two-state trace from state_1.png -> state_2.png
and produce a diff visualization showing what changed.
"""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components', 'trace_translator'))

from trace_translator import TraceTranslator
from PIL import Image, ImageDraw, ImageFont

STATE1 = r"C:\Users\paula\OneDrive\Desktop\state_1.png"
STATE2 = r"C:\Users\paula\OneDrive\Desktop\state_2.png"
TRACE_OUT  = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\state_transition_trace.json"
VIZ_BEFORE = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\state_1_annotated.png"
VIZ_AFTER  = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\state_2_annotated.png"
VIZ_DIFF   = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\state_diff.png"

os.makedirs(os.path.dirname(TRACE_OUT), exist_ok=True)

# ── 1. Generate two-state trace ──────────────────────────────────────────────
print("="*60)
print("STEP 1: Running OCR on both states...")
print("="*60)
translator = TraceTranslator(use_cv=True)

state_before = translator.image_to_state(STATE1, application='Anvil Web App')
state_after  = translator.image_to_state(STATE2, application='Anvil Web App')

trace = translator.states_to_trace(
    state_before, state_after,
    action={'type': 'unknown', 'description': 'transition from state_1 to state_2'},
    trace_id='state_1_to_state_2'
)
translator.save_trace(trace, TRACE_OUT)

# ── 2. Print diff summary ────────────────────────────────────────────────────
diff = trace['diff']
print("\n" + "="*60)
print("DIFF SUMMARY")
print("="*60)
print(f"  Elements in state_before : {len(state_before['elements'])}")
print(f"  Elements in state_after  : {len(state_after['elements'])}")
print(f"  Added   : {len(diff['added'])}")
print(f"  Removed : {len(diff['removed'])}")
print(f"  Changed : {len(diff['changed'])}")

if diff['added']:
    print("\n  ADDED elements:")
    for el in diff['added']:
        print(f"    + [{el['element_id']}] '{el['text']}' @ {el['bbox']}")

if diff['removed']:
    print("\n  REMOVED elements:")
    for el in diff['removed']:
        print(f"    - [{el['element_id']}] '{el['text']}' @ {el['bbox']}")

if diff['changed']:
    print("\n  CHANGED elements:")
    for ch in diff['changed']:
        print(f"    ~ [{ch['element_id']}]")
        for field, delta in ch['changes'].items():
            print(f"       {field}: '{delta['before']}' -> '{delta['after']}'")

# ── 3. Build diff visualization ───────────────────────────────────────────────
print("\n" + "="*60)
print("STEP 2: Building diff visualization...")
print("="*60)

try:
    font = ImageFont.truetype("arial.ttf", 11)
    font_lg = ImageFont.truetype("arial.ttf", 14)
except:
    font = ImageFont.load_default()
    font_lg = font

def annotate_image(img_path, elements, highlights, label):
    """Draw boxes on image: grey for normal, colored for highlights."""
    img = Image.open(img_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay)

    # Draw all elements lightly
    for el in elements:
        x1,y1,x2,y2 = el['bbox']
        draw.rectangle([x1,y1,x2,y2], fill=(200,200,200,60), outline=(200,200,200,120), width=1)

    # Draw highlighted elements prominently
    for el, color in highlights:
        x1,y1,x2,y2 = el['bbox']
        draw.rectangle([x1,y1,x2,y2], fill=color[:3]+(160,), outline=color[:3]+(255,), width=2)
        draw.text((x1, max(0,y1-14)), el['text'][:25], fill=(255,255,255,230), font=font)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    return result

# Build highlight sets
added_ids   = {el['element_id']: el for el in diff['added']}
removed_ids = {el['element_id']: el for el in diff['removed']}
changed_ids = {ch['element_id']: ch for ch in diff['changed']}

# Annotate state_before: removed=red, changed=orange
before_highlights = []
for el in state_before['elements']:
    eid = el['element_id']
    if eid in removed_ids:
        before_highlights.append((el, (255, 60, 60)))
    elif eid in changed_ids:
        before_highlights.append((el, (255, 160, 0)))

# Annotate state_after: added=green, changed=cyan
after_highlights = []
after_el_map = {el['element_id']: el for el in state_after['elements']}
for el in state_after['elements']:
    eid = el['element_id']
    if eid in added_ids:
        after_highlights.append((el, (0, 220, 80)))
    elif eid in changed_ids:
        after_highlights.append((el, (0, 200, 220)))

img1_ann = annotate_image(STATE1, state_before['elements'], before_highlights, "BEFORE")
img2_ann = annotate_image(STATE2, state_after['elements'],  after_highlights,  "AFTER")

img1_ann.save(VIZ_BEFORE)
img2_ann.save(VIZ_AFTER)

# ── 4. Side-by-side diff image ───────────────────────────────────────────────
W1, H1 = img1_ann.size
W2, H2 = img2_ann.size
GAP = 20
HEADER = 50

total_w = W1 + GAP + W2
total_h = max(H1, H2) + HEADER

canvas = Image.new("RGB", (total_w, total_h), (30, 30, 30))
draw_c = ImageDraw.Draw(canvas)

# Header bar
draw_c.rectangle([0,0,total_w,HEADER], fill=(20,20,20))
draw_c.text((10, 15), "STATE BEFORE (state_1.png)", fill=(255,100,100), font=font_lg)
draw_c.text((W1+GAP+10, 15), "STATE AFTER (state_2.png)", fill=(100,255,150), font=font_lg)

# Legend
legend_x = total_w - 500
draw_c.rectangle([legend_x,    10, legend_x+12, 22], fill=(255,60,60))
draw_c.text((legend_x+16, 10), "removed", fill=(255,200,200), font=font)
draw_c.rectangle([legend_x+80, 10, legend_x+92, 22], fill=(255,160,0))
draw_c.text((legend_x+96, 10), "changed(before)", fill=(255,200,200), font=font)
draw_c.rectangle([legend_x+210,10, legend_x+222,22], fill=(0,220,80))
draw_c.text((legend_x+226, 10), "added", fill=(200,255,200), font=font)
draw_c.rectangle([legend_x+280,10, legend_x+292,22], fill=(0,200,220))
draw_c.text((legend_x+296, 10), "changed(after)", fill=(200,255,255), font=font)

canvas.paste(img1_ann, (0, HEADER))
canvas.paste(img2_ann, (W1+GAP, HEADER))

canvas.save(VIZ_DIFF)

print(f"\nOutputs saved:")
print(f"  Trace      : {TRACE_OUT}")
print(f"  Before viz : {VIZ_BEFORE}")
print(f"  After viz  : {VIZ_AFTER}")
print(f"  Diff image : {VIZ_DIFF}")
