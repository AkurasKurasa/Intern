"""
Generate a two-state trace from state_1.png -> state_3.png
and produce a diff visualization.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'components', 'trace_translator'))

from trace_translator import TraceTranslator
from PIL import Image, ImageDraw, ImageFont, ImageChops
import numpy as np

STATE1   = r"C:\Users\paula\OneDrive\Desktop\state_1.png"
STATE3   = r"C:\Users\paula\OneDrive\Desktop\state_3.png"
OUT_DIR  = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces"
TRACE_OUT = os.path.join(OUT_DIR, "s1_to_s3_trace.json")
DIFF_OUT  = os.path.join(OUT_DIR, "s1_to_s3_diff.png")
os.makedirs(OUT_DIR, exist_ok=True)

# ── 1. Generate two-state trace ──────────────────────────────────────────────
translator = TraceTranslator(use_cv=True)

print("Running OCR on state_1...")
s1 = translator.image_to_state(STATE1, application='Anvil Web App')
print("Running OCR on state_3...")
s3 = translator.image_to_state(STATE3, application='Anvil Web App')

trace = translator.states_to_trace(
    s1, s3,
    action={'type': 'form_fill', 'description': 'Fields typed into'},
    trace_id='state_1_to_state_3'
)
translator.save_trace(trace, TRACE_OUT)

# ── 2. Print diff summary ────────────────────────────────────────────────────
diff = trace['diff']
print("\n" + "="*55)
print("DIFF SUMMARY")
print("="*55)
print(f"  Before elements : {len(s1['elements'])}")
print(f"  After elements  : {len(s3['elements'])}")
print(f"  Added           : {len(diff['added'])}")
print(f"  Removed         : {len(diff['removed'])}")
print(f"  Changed         : {len(diff['changed'])}")

# Pixel-level diff
img1 = Image.open(STATE1).convert('RGB')
img3 = Image.open(STATE3).convert('RGB')
# Resize to same size for comparison
if img1.size != img3.size:
    img3_r = img3.resize(img1.size, Image.LANCZOS)
else:
    img3_r = img3
pdiff = ImageChops.difference(img1, img3_r)
arr = np.array(pdiff)
print(f"\n--- PIXEL DIFF ---")
print(f"  Max diff  : {arr.max()}")
print(f"  Mean diff : {arr.mean():.4f}")
changed_px = int(np.count_nonzero(arr.sum(axis=2)))
total_px   = arr.shape[0] * arr.shape[1]
print(f"  Changed   : {changed_px} / {total_px} px ({100*changed_px/total_px:.1f}%)")

# ── 3. Diff visualization ────────────────────────────────────────────────────
try:
    font = ImageFont.truetype("arial.ttf", 11)
    font_lg = ImageFont.truetype("arial.ttf", 14)
except Exception:
    font = ImageFont.load_default()
    font_lg = font

changed_ids = {ch['element_id'] for ch in diff['changed']}
added_ids   = {el['element_id'] for el in diff['added']}
removed_ids = {el['element_id'] for el in diff['removed']}

def annotate(img_path, elements, changed, removed, added, side):
    img = Image.open(img_path).convert("RGBA")
    ov  = Image.new("RGBA", img.size, (0,0,0,0))
    draw = ImageDraw.Draw(ov)
    for el in elements:
        x1,y1,x2,y2 = el['bbox']
        eid = el['element_id']
        if side == 'before' and eid in removed:
            color = (255, 60, 60, 170)
        elif side == 'after'  and eid in added:
            color = (0, 220, 80, 170)
        elif eid in changed:
            color = (0, 180, 255, 150) if side == 'after' else (255, 165, 0, 150)
        else:
            color = (180, 180, 180, 50)
        draw.rectangle([x1,y1,x2,y2], fill=color, outline=color[:3]+(255,), width=1)
        if eid in (changed | removed | added):
            draw.text((x1, max(0, y1-13)), el['text'][:22], fill=(255,255,255,220), font=font)
    return Image.alpha_composite(img, ov).convert("RGB")

img1_ann = annotate(STATE1, s1['elements'], changed_ids, removed_ids, added_ids, 'before')
img3_ann = annotate(STATE3, s3['elements'], changed_ids, removed_ids, added_ids, 'after')

# Side-by-side canvas
W1,H1 = img1_ann.size
W3,H3 = img3_ann.size
GAP, HDR = 16, 48
canvas = Image.new("RGB", (W1+GAP+W3, max(H1,H3)+HDR), (20,20,20))
d = ImageDraw.Draw(canvas)
d.rectangle([0,0,W1+GAP+W3,HDR], fill=(15,15,15))
d.text((8, 14), "BEFORE  state_1.png", fill=(255,130,80), font=font_lg)
d.text((W1+GAP+8, 14), "AFTER  state_3.png", fill=(80,220,120), font=font_lg)

# Legend
lx = W1+GAP+W3 - 460
d.rectangle([lx,14,lx+10,26],    fill=(255,165,0));   d.text((lx+14,14), "changed(before)", fill=(220,180,100), font=font)
d.rectangle([lx+135,14,lx+145,26], fill=(0,180,255)); d.text((lx+149,14), "changed(after)",  fill=(100,200,220), font=font)
d.rectangle([lx+265,14,lx+275,26], fill=(255,60,60)); d.text((lx+279,14), "removed",         fill=(220,120,120), font=font)
d.rectangle([lx+335,14,lx+345,26], fill=(0,220,80));  d.text((lx+349,14), "added",           fill=(120,220,140), font=font)

canvas.paste(img1_ann, (0,   HDR))
canvas.paste(img3_ann, (W1+GAP, HDR))
canvas.save(DIFF_OUT)

print(f"\nOutputs saved:")
print(f"  Trace : {TRACE_OUT}")
print(f"  Diff  : {DIFF_OUT}")
