# CV Detection Guide

This guide explains how to use and understand the computer vision (CV) detection pipeline in Intern. By the end you should be able to run it, read its output, and tune it for your own use case.

---

## What is CV Detection?

CV detection analyses a **screenshot image** and extracts UI elements from it using:

1. **Tesseract OCR** — finds all text on screen and maps each word/phrase to a bounding box
2. *(Planned)* **YOLOv8** — object detection for non-text UI elements (buttons, icons, inputs)
3. *(Planned)* **SAM** — pixel-accurate segmentation masks for detected elements

It is the fallback method when you don't have access to the page's HTML — for example, desktop apps, PDFs, or any non-web UI.

**Compare with HTML detection:**

| | CV Detection | HTML Detection |
|---|---|---|
| Works on | Any screenshot | Web pages only |
| Accuracy | Medium (OCR-dependent) | 100% |
| Element types | Text regions (`label`) | `button`, `input`, `link`, etc. |
| Requires browser | No | Yes (Playwright) |
| Use case | Desktop apps, images, video | Web automation |

---

## Prerequisites

### 1. Install Python dependencies

```bash
cd components/trace_translator
pip install -r requirements.txt
```

### 2. Install Tesseract OCR (system dependency)

Tesseract must be installed at the OS level — `pip` alone is not enough.

**Windows:**
```bash
# Download installer from: https://github.com/UB-Mannheim/tesseract/wiki
# During install, note the path (default: C:\Program Files\Tesseract-OCR\tesseract.exe)
```

Then add Tesseract to your PATH, or set it explicitly in your script:

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

**macOS:**
```bash
brew install tesseract
```

**Linux:**
```bash
sudo apt install tesseract-ocr
```

### 3. Verify installation

```python
import pytesseract
print(pytesseract.get_tesseract_version())  # Should print a version number
```

---

## Quick Start

### Analyse a single screenshot

```python
from components.trace_translator.trace_translator.trace_translator import TraceTranslator

translator = TraceTranslator(use_cv=True)
state = translator.image_to_state('my_screenshot.png', application='MyApp')

print(f"Detected {len(state['elements'])} elements")
for el in state['elements']:
    print(f"  {el['element_id']}: '{el['text']}' @ {el['bbox']}")
```

### Compare two screenshots (before/after an action)

```python
translator = TraceTranslator(use_cv=True)

state_before = translator.image_to_state('before.png', application='MyApp')
state_after  = translator.image_to_state('after.png',  application='MyApp')

trace = translator.states_to_trace(
    state_before,
    state_after,
    action={'type': 'click', 'description': 'clicked Submit'},
    trace_id='my_trace_001'
)

translator.save_trace(trace, 'output/trace.json')

diff = trace['diff']
print(f"Added:   {diff['summary']['added_count']} elements")
print(f"Removed: {diff['summary']['removed_count']} elements")
print(f"Changed: {diff['summary']['changed_count']} elements")
```

### Process a screen recording

```python
translator = TraceTranslator(use_cv=True)

traces = translator.video_to_traces(
    'recording.mp4',
    interval_sec=2.0,         # Analyse one frame every 2 seconds
    application='Excel',
    output_dir='data/output/traces/',
    verbose=True
)

print(f"Generated {len(traces)} trace steps from video")
```

---

## How the Pipeline Works

Here is the full data flow from an image file to a saved trace:

```
PNG / JPG / MP4
      │
      ▼
 Image.open()  ──►  PIL Image
      │
      ▼
 CVDetector.detect_ui_elements()
      │
      ├── pytesseract.image_to_data(image, config='--psm 11')
      │       Returns: list of (text, bbox, confidence) per word
      │
      └── Filters out: empty strings, confidence < 30
      │
      ▼
 UIElementExtractor.extract_elements()
      │
      ├── Assigns sequential IDs:  label_0, label_1, label_2 ...
      ├── Wraps each OCR result in the standard element schema
      └── Calls merge_overlapping_elements()  [stub — returns as-is]
      │
      ▼
 TraceTranslator._state_from_pil()
      │
      └── Packages elements into a UI state dict:
          { application, window_title, screen_resolution, elements, metadata }
      │
      ▼
 TraceTranslator.states_to_trace()   (if comparing two states)
      │
      ├── Calls _diff_states() to match elements across frames
      │       Matching by IoU (≥ 0.3) then center proximity (< 30 px)
      │       Detects: added / removed / changed elements
      │
      └── Returns full trace dict with state_before, state_after, diff
      │
      ▼
 save_trace()  ──►  trace.json
```

---

## Classes and Methods

### `TraceTranslator`

The main entry point. Orchestrates all detection and trace generation.

```python
TraceTranslator(
    trace_format_path=None,  # Path to trace_format.json (uses default if None)
    use_cv=True,             # Enable CV detection
    use_html=False           # Enable HTML detection
)
```

**Key methods:**

| Method | What it does |
|--------|-------------|
| `image_to_state(image_path, application=None)` | Loads a PNG/JPG and returns a UI state dict |
| `url_to_state(url, application='Chrome')` | HTML-only: loads a URL and returns a UI state dict |
| `states_to_trace(state_before, state_after, action=None, trace_id=None)` | Compares two states and returns a trace with a diff |
| `state_to_trace(state, trace_id=None, action=None)` | Single-state snapshot trace (no diff) |
| `video_to_traces(video_path, interval_sec=3.0, ...)` | Processes a video file into a list of traces |
| `save_trace(trace, output_path)` | Writes a trace dict to a JSON file |
| `save_traces(traces, output_dir)` | Writes a list of traces to a directory |

---

### `CVDetector`

Runs OCR on a PIL image and returns raw text regions.

```python
CVDetector()  # No parameters needed
```

**`detect_ui_elements(image, use_ocr=True)`**

- `image` — a `PIL.Image.Image` object
- `use_ocr` — set to `False` to skip OCR and return an empty result
- Returns:
  ```python
  {
      "ocr_results": [
          {
              "text":       "Submit",       # Recognised text
              "bbox":       [x1, y1, x2, y2],
              "confidence": 0.94            # Normalised 0.0–1.0
          },
          ...
      ]
  }
  ```

**`_extract_text_with_ocr(image)`** (internal)

Calls Tesseract with `--psm 11` (sparse text mode). Filters out results where:
- `text` is empty or whitespace
- Tesseract confidence is below 30 (on its internal 0–100 scale)

---

### `UIElementExtractor`

Converts raw OCR results into the standard element schema used throughout the system.

```python
UIElementExtractor()
```

**`extract_elements(image, ocr_results)`**

Turns each OCR result into:
```python
{
    "element_id": "label_0",     # Sequential, e.g. label_0, label_1, ...
    "type":       "label",       # Always "label" for OCR results
    "bbox":       [x1, y1, x2, y2],
    "text":       "Submit",
    "label":      "Submit",      # Same as text for OCR
    "enabled":    True,
    "visible":    True,
    "confidence": 0.94,
    "metadata":   { "source": "ocr" }
}
```

**`merge_overlapping_elements(elements)`**

Currently a stub — returns elements unchanged. This is where YOLO/SAM post-processing will be added to de-duplicate overlapping detections.

---

### `HTMLDetector`

Playwright-based DOM extraction. Only relevant when `use_html=True`.

```python
HTMLDetector(
    headless=True,    # Run browser without a visible window
    timeout=30000     # Page load timeout in milliseconds
)
```

**`extract_ui_elements(url)`**

Opens the URL in Chromium, runs JavaScript to query the DOM, and returns a dict with `elements`, `screenshot`, and `page_info`. Elements have `confidence: 1.0` and include real types (`button`, `input`, `link`, `checkbox`, etc.).

---

## Tunable Parameters

### Tesseract Page Segmentation Mode (`--psm`)

The most impactful setting. Controls how Tesseract interprets the layout.

| Mode | Name | When to use |
|------|------|------------|
| `--psm 3` | Fully automatic (default) | Documents, PDFs |
| `--psm 6` | Assume single uniform block | Dense text areas |
| **`--psm 11`** | **Sparse text** | **UI screenshots ← current setting** |
| `--psm 12` | Sparse text with OSD | Mixed orientation |

Change it in `CVDetector._extract_text_with_ocr()`:
```python
# Current:
ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 11')

# For denser layouts (e.g. forms with lots of labels):
ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config='--psm 6')
```

---

### Confidence threshold (default: `30`)

Tesseract returns a confidence value from 0–100 for each word. Results below the threshold are discarded.

```python
# Current (in CVDetector._extract_text_with_ocr):
if not text or conf < 30:
    continue
```

**Raise it** (e.g. to `60`) if you are getting too many false positives — garbled text or image noise recognised as words.

**Lower it** (e.g. to `10`) if you are missing real text — low-contrast labels, small font, or unusual colours.

---

### IoU threshold (default: `0.3`)

Used in `_diff_states()` to decide whether an element in `state_before` is the "same" element in `state_after`. IoU = how much the bounding boxes overlap (0 = no overlap, 1 = identical).

```python
# In TraceTranslator._diff_states():
iou_threshold: float = 0.3
```

**Raise it** (e.g. to `0.5`) if unrelated elements are being matched together — happens when elements are densely packed.

**Lower it** (e.g. to `0.1`) if the same element is being reported as removed+added on every frame — happens when OCR bounding boxes shift slightly between screenshots.

---

### Center proximity threshold (default: `30` px)

Fallback matching in `_diff_states()`. If two elements don't meet the IoU threshold but their centre points are within this many pixels, they are treated as the same element.

```python
center_threshold: int = 30
```

**Lower it** for high-resolution screens where elements are close together and you want stricter matching.

**Raise it** for low-resolution or zoomed-in captures where elements shift more between frames.

---

### Bounding box change threshold (default: `5` px)

A change in an element's bounding box is only recorded in the diff if at least one coordinate shifts by more than this many pixels. Prevents noise from sub-pixel OCR jitter being reported as changes.

```python
# In _diff_states():
if any(abs(bb[i] - ab[i]) > 5 for i in range(4)):
    delta['bbox'] = {'before': bb, 'after': ab}
```

**Lower it** to `1–2 px` if you need to catch very small layout shifts. **Raise it** to `10–15 px` to ignore minor OCR repositioning noise.

---

### Video sampling interval (default: `3.0` seconds)

In `video_to_traces()`, one frame is extracted every `interval_sec` seconds.

```python
translator.video_to_traces('recording.mp4', interval_sec=2.0)
```

**Lower it** to capture fast interactions (e.g. `0.5` for rapid clicking).
**Raise it** to reduce the number of traces generated from long recordings.

---

## Output Format

### UI State

```json
{
    "application": "MyApp",
    "window_title": "screenshot.png",
    "screen_resolution": [1920, 1080],
    "focused_element_id": null,
    "elements": [
        {
            "element_id": "label_0",
            "type": "label",
            "bbox": [120, 340, 200, 360],
            "text": "Submit",
            "label": "Submit",
            "enabled": true,
            "visible": true,
            "confidence": 0.94,
            "metadata": { "source": "ocr" }
        }
    ],
    "metadata": {
        "source": "screenshot.png",
        "detection_method": "cv",
        "detection_timestamp": "2026-03-28T10:00:00.000Z",
        "num_elements_detected": 12
    }
}
```

### Trace (two-state)

```json
{
    "trace_id": "my_trace_001",
    "timestamp": "2026-03-28T10:00:00.000Z",
    "state_before": { "...": "..." },
    "state_after":  { "...": "..." },
    "action": { "type": "click", "description": "clicked Submit" },
    "diff": {
        "added":   [],
        "removed": [],
        "changed": [
            {
                "element_id":       "label_3",
                "element_id_after": "label_3",
                "match_score":      0.91,
                "changes": {
                    "text": { "before": "", "after": "John" }
                }
            }
        ],
        "summary": {
            "total_before":   12,
            "total_after":    12,
            "matched":        11,
            "added_count":     0,
            "removed_count":   0,
            "changed_count":   1,
            "diff_method": "position_iou"
        }
    },
    "metadata": {
        "trace_type": "transition",
        "detection_method_before": "cv",
        "detection_method_after":  "cv",
        "num_elements_before": 12,
        "num_elements_after":  12
    }
}
```

---

## Extending with YOLO and SAM

YOLOv8 and SAM are listed in `requirements.txt` but not yet connected to the pipeline. Here is where to hook them in:

### Where to add YOLOv8

In `CVDetector.detect_ui_elements()`, after the OCR step, add YOLO inference to detect non-text elements:

```python
# In CVDetector.detect_ui_elements():
def detect_ui_elements(self, image, use_ocr=True):
    results = {}
    if use_ocr:
        results['ocr_results'] = self._extract_text_with_ocr(image)

    # ── Add here ──────────────────────────────────────────────────
    # from ultralytics import YOLO
    # model = YOLO('yolov8n.pt')
    # yolo_results = model(image)
    # results['yolo_results'] = _parse_yolo_output(yolo_results)
    # ──────────────────────────────────────────────────────────────

    return results
```

### Where to add SAM

In `UIElementExtractor.merge_overlapping_elements()` — currently a stub — add SAM to refine bounding boxes using segmentation masks:

```python
# In UIElementExtractor.merge_overlapping_elements():
def merge_overlapping_elements(self, elements):
    # ── Add here ──────────────────────────────────────────────────
    # from segment_anything import SamPredictor, build_sam
    # predictor = SamPredictor(build_sam(checkpoint='sam_vit_h.pth'))
    # For each element bbox, run predictor.predict() to get a mask
    # Use the mask to produce a tighter bounding box
    # ──────────────────────────────────────────────────────────────
    return elements  # Remove this once SAM is wired up
```

---

## Troubleshooting

**No elements detected at all**
- Verify Tesseract is installed: `pytesseract.get_tesseract_version()`
- Check the image is not blank or all-black
- Try lowering the confidence threshold from `30` to `10`
- Try `--psm 3` instead of `--psm 11`

**Too many garbled / nonsense elements**
- Raise the confidence threshold from `30` to `50` or `60`
- Pre-process the image: increase contrast, convert to greyscale, upscale if small

**Same element appears as removed + added on every frame**
- Lower the IoU threshold from `0.3` to `0.1`
- Raise the center proximity threshold from `30` to `50`

**Diff reports hundreds of changes between similar frames**
- The OCR is producing different element IDs on each run (expected — IDs are sequential, not stable)
- The diff algorithm handles this via IoU/center matching, not ID matching — check your `iou_threshold`
- Consider raising the bounding box change threshold from `5` to `15` to suppress jitter

**`TesseractNotFoundError`**
- Tesseract is not on your PATH
- Set it explicitly: `pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'`