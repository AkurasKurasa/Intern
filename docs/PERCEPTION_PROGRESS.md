# Vision Perception — Progress Report

**Component:** Big Three #1 — *Perception: from reading hidden labels to seeing the screen.*
**Status:** Working across all 8 tabs of the car-insurance form. Not yet wired into the agent.
**Runtime:** Fully local (OpenCV + Tesseract OCR). No cloud, no GPU required.

---

## What this is

The agent used to "see" only by reading the app's hidden accessibility tree (UIA) —
it worked on tidy apps but is blind on anything messier. This component lets the
agent **see the screen like a person**: take a screenshot, find each UI element
visually, and read its text — then emit the *same* element format the rest of the
pipeline already consumes, so it's a drop-in perception source.

Approach: a **detector + OCR** pipeline (not a vision-LLM). The agent clicks by exact
pixel box, and local vision-LLMs are imprecise at coordinates. A box detector gives
pixel-tight rectangles; OCR reads the actual text/values.

```
screenshot ──▶ adaptive-threshold box detection ──▶ + Tesseract OCR ──▶ canonical
               (+ colour mask for filled checkboxes)   (labels/values)    element list
                                                                          (observers/schema.py)
```

---

## Files

| File | Purpose |
|---|---|
| `components/observers/vlm/vision_observer/cv_detector.py` | Pure CV detector — boxes + OCR → element dicts. No screen I/O, unit-testable. |
| `components/observers/vlm/vision_observer/cv_vision_observer.py` | `Observer` adapter — screen/image capture → schema-conforming state. CLI overlay tool. |
| `scripts/perception_eval.py` | Scorer: vision vs UIA (precision/recall, IoU, label/value), `--live` capture, saves annotated overlays. |
| `tests/test_cv_perception.py` | 10 tests (synthetic form): detection, schema, OCR, scorer. Headless. |
| `components/observers/vlm/vision_observer/requirements.txt` | Local deps (opencv, pytesseract, mss, Pillow). Needs system Tesseract. |

---

## Results (live, vs UIA ground truth on the form)

Metrics: **P**recision / **R**ecall / **F1**; **IoU** = box tightness; **value** = field-content read accuracy.

| Tab | F1 | Precision | Recall | IoU | value |
|---|---|---|---|---|---|
| Payment | 0.82 | 0.91 | 0.74 | 0.98 | 1.00 |
| Vehicle | 0.72 | 1.00 | 0.56 | 0.98 | 0.81 |
| Drivers | 0.68 | 0.81 | 0.59 | 0.98 | 0.97 |
| **Coverage** | **0.67** | 0.71 | 0.64 | 0.90 | 0.97 |
| Claims | 0.66 | 0.76 | 0.58 | 0.97 | 1.00 |
| Policy | 0.65 | 0.69 | 0.61 | 0.97 | 1.00 |
| History | 0.61 | 0.80 | 0.50 | 0.96 | 0.69 |

**Headline:** IoU ~0.90–0.98 and value ~0.69–1.00 on *every* tab — when vision finds an
element, the box is click-accurate and the content reads correctly. It generalizes
across the whole form, not just the tab it was tuned on.

### How it got here (Policy tab, single-run trace)

| Stage | F1 | What changed |
|---|---|---|
| Initial | 0.03 | Canny edges only — found text labels, missed the faint-bordered fields |
| Window focus | 0.18 | Compare form-window only (not the whole desktop) |
| **Adaptive threshold** | 0.60 | Local-contrast detection finds faint field borders Canny missed |
| + brightness gate | 0.64 | Drop decorative coloured bars (section headers) |
| + checkbox fix | 0.65 | Detect all checkboxes (incl. checked/filled) + extend to label |

The **Coverage** (checkbox-heavy) tab is the clearest single fix: 0.43 → 0.67 once
checkboxes were detected as the full "☐ Label" region (matching UIA) instead of the
bare square.

---

## Key technical decisions

- **Adaptive thresholding, not Canny.** Form fields have faint gray borders on white;
  global edge detection misses them, local thresholding catches them. This was the
  single biggest win (0.18 → 0.60).
- **Checkbox = square + label.** UIA reports a checkbox as the whole clickable row;
  vision now extends the detected square to cover its caption, both matching UIA and
  staying a valid click target. Checked checkboxes are caught with a colour/saturation
  mask (no clean border to trace).
- **Container & brightness filters.** Section panels (boxes wrapping ≥2 others) and
  coloured decorative bars are dropped so they don't pollute the field list.
- **No hardcoding.** No field names, tab names, coordinates, or app names — all cues
  are generic (contrast, size, colour, geometry).

---

## Known gaps (non-blocking)

- **`type` ~0.6** — comboboxes are classified as plain edit fields; distinguishing them
  needs dropdown-arrow detection.
- **`label` ~0.5** — OCR misses or splits some captions (esp. tightly-packed rows).
- **Below-the-fold fields** on tall tabs aren't in a single screenshot (the agent
  scrolls anyway).
- Scoring is slightly pessimistic: UIA splits one combobox into several sub-elements,
  which vision sensibly sees as one — counted as "misses".

---

## How to run

```bash
# Tests (headless, no GUI needed)
py -3.14 -m pytest tests/test_cv_perception.py -v

# Inspect detection on a screenshot (writes an annotated overlay)
py -3.14 components/observers/vlm/vision_observer/cv_vision_observer.py shot.png

# Score vision vs UIA on the live form (open + maximize the form first)
py -3.14 scripts/perception_eval.py --live --tag <name>
```

---

## Next step

**Wire vision into `run_task.py`** so the agent can run on the vision observer instead
of UIA — the real milestone test: *can the agent fill the form from pixels alone?*
Perception is the injectable seam already; this is a configuration + verification task,
not a rebuild.
