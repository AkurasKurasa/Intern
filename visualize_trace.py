"""
Visualize how the AI sees the image by drawing OCR bounding boxes on the screenshot.
"""
import json
import os
from PIL import Image, ImageDraw, ImageFont

TRACE_PATH = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\trace_sample_trace.json"
IMAGE_PATH = r"C:\Users\paula\OneDrive\Desktop\trace_sample.png"
OUTPUT_PATH = r"C:\Users\paula\OneDrive\Desktop\Intern\data\output\traces\trace_sample_annotated.png"

# Load trace and image
with open(TRACE_PATH, 'r', encoding='utf-8') as f:
    trace = json.load(f)

img = Image.open(IMAGE_PATH).convert("RGBA")
overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

elements = trace['state_before']['elements']

# Color by confidence: green = high, yellow = mid, red = low
def conf_color(conf):
    if conf >= 0.85:
        return (0, 220, 80, 180)    # green
    elif conf >= 0.5:
        return (255, 200, 0, 180)   # yellow
    else:
        return (255, 60, 60, 180)   # red

try:
    font = ImageFont.truetype("arial.ttf", 11)
except:
    font = ImageFont.load_default()

for el in elements:
    x1, y1, x2, y2 = el['bbox']
    conf = el['confidence']
    color = conf_color(conf)
    border_color = color[:3] + (255,)

    # Draw filled semi-transparent box
    draw.rectangle([x1, y1, x2, y2], fill=color, outline=border_color, width=1)

    # Draw label text above the box
    label = el['text'][:20]
    draw.text((x1, max(0, y1 - 13)), label, fill=(255, 255, 255, 230), font=font)

# Composite overlay onto original
result = Image.alpha_composite(img, overlay).convert("RGB")
os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
result.save(OUTPUT_PATH)
print(f"Annotated image saved to: {OUTPUT_PATH}")
print(f"Total elements visualized: {len(elements)}")
