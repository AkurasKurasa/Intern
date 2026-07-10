"""
cv_detector.py
==============
Pure computer-vision UI element detector. The "seeing" half of vision
perception: a screenshot (numpy/PIL image) in, a list of canonical element
dicts out — boxes via OpenCV, text/values via Tesseract OCR.

This module does NO screen I/O and has NO repo dependencies, so it is fully
unit-testable on any static image. The CVVisionObserver wraps it with screen
capture and schema assembly.

Why detector + OCR (not a vision-LLM):
    The downstream model CLICKS by exact pixel bbox. A box detector gives
    pixel-tight rectangles; OCR reads the real text (so `value` / is_filled
    work). Local vision-LLMs that fit a 6 GB GPU are imprecise at coordinates,
    which is the one thing perception cannot get wrong here.

Output element dict (a subset of observers/schema.py — the observer fills the
rest):
    type        canonical CONTROL_TYPES string (editcontrol / buttoncontrol / …)
    bbox        [x1, y1, x2, y2] pixel rect on the ORIGINAL image
    label       nearest caption text (field identity)
    value       text found INSIDE the box ("" = empty)  → drives is_filled
    text        same as label (kept for the encoder's text+value embedding)
    confidence  0..1 detector confidence
"""
from __future__ import annotations

import logging
import shutil
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── optional deps (import lazily / degrade loud) ────────────────────────────────
try:
    import cv2
    import numpy as np
    _CV_OK = True
except Exception:                                     # pragma: no cover
    _CV_OK = False

try:
    import pytesseract
    # On Windows pytesseract needs the exe; if it's on PATH this finds it.
    _exe = shutil.which("tesseract")
    if _exe:
        pytesseract.pytesseract.tesseract_cmd = _exe
    _OCR_OK = True
except Exception:                                     # pragma: no cover
    _OCR_OK = False


# ── detection tuning (documented; see DETECTION_METHODOLOGY.md for the why) ──────
class CVConfig:
    # Box detection uses ADAPTIVE THRESHOLD (local contrast), not global Canny:
    # form fields have faint gray borders on white that Canny misses but local
    # thresholding catches. block must be odd; C is subtracted from the local mean.
    adaptive_block:   int   = 15
    adaptive_C:       int   = 8
    gaussian_kernel:  int   = 3
    # Canny fallback thresholds (kept for edge-only images; not the default path).
    canny_low:        int   = 30
    canny_high:       int   = 100
    # Element size filters (pixels). Drop slivers and full-window rectangles.
    # min_w must stay <= a checkbox's width (~13px) or checkboxes get dropped;
    # min_h still blocks 1–9px noise so lowering min_w is safe.
    min_w:            int   = 12
    min_h:            int   = 10
    max_w_frac:       float = 0.96     # reject boxes ≥96% of screen width (the window itself)
    max_h_frac:       float = 0.96
    # Aspect ratio guard — reject only true hairlines. Input fields are very wide
    # and short (e.g. 1750×25 → 0.014), so this must be small; min_h blocks the
    # 1–3px dividers that the aspect guard used to (0.12 wrongly killed fields).
    min_aspect:       float = 0.008    # h/w and w/h must both exceed this
    # Non-max suppression: drop boxes overlapping an existing one above this IoU.
    nms_iou:          float = 0.45
    # A near-square box this small is treated as a checkbox.
    checkbox_max:     int   = 34
    # Dedicated checkbox pass: a CHECKED box is a filled coloured square that
    # adaptive threshold can't outline — catch it with a saturation mask instead.
    cb_sat:           int   = 50      # min HSV saturation to count as "filled"
    cb_min:           int   = 10      # checkbox side length range (px)
    cb_max:           int   = 26
    cb_square:        float = 0.65    # min short/long side ratio (square-ness)
    # Decorative-bar filter: real fields/buttons have LIGHT interiors; coloured
    # section/title/status bars don't. Boxes darker than this mean gray are
    # dropped (general light-theme cue, set to 0 to disable).
    min_brightness:   int   = 150
    # OCR: ignore words below this confidence (Tesseract reports 0..100).
    ocr_min_conf:     int   = 40
    # Label search: how far (px) left/above a box to look for its caption.
    label_gap_x:      int   = 320
    label_gap_y:      int   = 40


# ── geometry helpers ────────────────────────────────────────────────────────────
def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / (area_a + area_b - inter)


def _center_in(box: Tuple[int, int, int, int], pt: Tuple[float, float]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= pt[0] <= x2 and y1 <= pt[1] <= y2


def _to_bgr(image: Any) -> "np.ndarray":
    """Accept a PIL.Image, RGB ndarray, or BGR ndarray → return BGR ndarray."""
    arr = image
    # PIL.Image → ndarray (RGB)
    if hasattr(image, "convert") and hasattr(image, "size"):
        arr = np.array(image.convert("RGB"))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    arr = np.asarray(arr)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.ndim == 3 and arr.shape[2] == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    # Assume already 3-channel. Caller is responsible for RGB-vs-BGR; OCR and
    # box detection are colour-agnostic (we grayscale), so this is harmless.
    return arr


# ── box detection ───────────────────────────────────────────────────────────────
def _detect_boxes(bgr: "np.ndarray", cfg: CVConfig) -> List[Tuple[int, int, int, int]]:
    """Find candidate UI element rectangles via adaptive threshold → contours →
    filter → NMS. Adaptive (local) thresholding catches faint field borders that
    global Canny edge detection misses on form UIs."""
    H, W = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    binimg = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        cfg.adaptive_block, cfg.adaptive_C,
    )
    # Close small gaps so a thin border becomes one closed contour.
    binimg = cv2.morphologyEx(
        binimg, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1,
    )

    contours, _ = cv2.findContours(binimg, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw: List[Tuple[int, int, int, int]] = []
    max_w = cfg.max_w_frac * W
    max_h = cfg.max_h_frac * H
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < cfg.min_w or h < cfg.min_h:
            continue
        if w > max_w or h > max_h:
            continue
        if min(w / h, h / w) < cfg.min_aspect:        # hairline / divider
            continue
        raw.append((x, y, x + w, y + h))

    # Non-max suppression: larger boxes first, drop heavy overlaps.
    raw.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    kept: List[Tuple[int, int, int, int]] = []
    for box in raw:
        if all(_iou(box, k2) < cfg.nms_iou for k2 in kept):
            kept.append(box)
    return kept


def _detect_filled_checkboxes(bgr: "np.ndarray", cfg: CVConfig
                              ) -> List[Tuple[int, int, int, int]]:
    """Find CHECKED checkboxes — small filled coloured squares that adaptive
    threshold can't outline (it sees a fragmented fill, not a border). A
    saturation mask isolates the solid colour; we keep square, mostly-filled
    blobs in checkbox-size range."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] > cfg.cb_sat) & (hsv[:, :, 2] > 60)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: List[Tuple[int, int, int, int]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (cfg.cb_min <= w <= cfg.cb_max and cfg.cb_min <= h <= cfg.cb_max):
            continue
        if min(w, h) / max(w, h) < cfg.cb_square:
            continue
        if cv2.contourArea(c) / max(1, w * h) < 0.6:        # square, not an L/checkmark stroke
            continue
        out.append((x, y, x + w, y + h))
    return out


# ── OCR ─────────────────────────────────────────────────────────────────────────
def _ocr_words(bgr: "np.ndarray", cfg: CVConfig) -> List[Dict[str, Any]]:
    """Return word tokens: {text, conf, cx, cy, box}. Empty if OCR unavailable."""
    if not _OCR_OK:
        return []
    try:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
    except Exception as exc:                           # pragma: no cover
        logger.warning("cv_detector: OCR failed — %s", exc)
        return []

    words: List[Dict[str, Any]] = []
    n = len(data["text"])
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (ValueError, TypeError):
            conf = -1
        if conf < cfg.ocr_min_conf:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append({
            "text": txt, "conf": conf,
            "cx": x + w / 2.0, "cy": y + h / 2.0,
            "box": (x, y, x + w, y + h),
            # Tesseract's own layout: words sharing this id are ONE text line.
            # Label assembly must use it — geometric same-row guessing splits
            # multi-word labels ('Agent ID' → 'ID'), and a fragmented label
            # poisons value lookup, focus verify and fill read-back downstream.
            "line": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
        })
    return words


def _is_text_glyph(box: Tuple[int, int, int, int],
                   words: List[Dict[str, Any]]) -> bool:
    """True if `box` is just the outline of text (a caption), not a container.

    Edge detection turns letters into contours too. A real input/button is a
    container whose text covers only part of its area; a caption's box is almost
    entirely text and only one line tall. Dropping these keeps captions as
    labels instead of phantom fields."""
    bx_area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    inside = [w for w in words if _center_in(box, (w["cx"], w["cy"]))]
    if not inside:
        return False
    cov = sum((w["box"][2] - w["box"][0]) * (w["box"][3] - w["box"][1]) for w in inside)
    heights = sorted(w["box"][3] - w["box"][1] for w in inside)
    med_wh = heights[len(heights) // 2]
    box_h = box[3] - box[1]
    return (cov / bx_area) > 0.55 and box_h < 1.8 * med_wh


def _checkbox_with_label(square: Tuple[int, int, int, int],
                         words: List[Dict[str, Any]],
                         used: set,
                         cfg: CVConfig) -> Tuple[Tuple[int, int, int, int], str]:
    """Extend a checkbox SQUARE rightward to cover its caption, returning the
    full clickable (square + label) box and the label text. UIA reports a
    checkbox as this whole region, and clicking the label toggles the box too —
    so this both matches the accessibility tree and stays a valid click target.
    Stops at a large horizontal gap (the next column's checkbox)."""
    x1, y1, x2, y2 = square
    cands = []
    for idx, wd in enumerate(words):
        # search geometrically (ignore `used`): a checkbox's caption is whatever
        # sits to its right on the same row, even if a stray box claimed it.
        if y1 - 5 <= wd["cy"] <= y2 + 5 and wd["box"][0] >= x2 - 2 \
                and (wd["box"][0] - x2) <= cfg.label_gap_x:
            cands.append((wd["box"][0], idx, wd))
    cands.sort()
    taken, prev_right = [], x2
    for left, idx, wd in cands:
        if left - prev_right > 60:            # column break → stop
            break
        taken.append((idx, wd))
        prev_right = wd["box"][2]
    if not taken:
        return square, ""
    for idx, _ in taken:
        used.add(idx)
    rx = max(wd["box"][2] for _, wd in taken)
    ry1 = min(y1, min(wd["box"][1] for _, wd in taken))
    ry2 = max(y2, max(wd["box"][3] for _, wd in taken))
    label = _clean_label(" ".join(wd["text"] for _, wd in taken))
    return (x1, ry1, rx, ry2), label


def _has_dropdown_arrow(bgr: "np.ndarray", box: Tuple[int, int, int, int]) -> bool:
    """True if the box's right-edge strip contains a compact dark glyph — the
    dropdown arrow that visually distinguishes a combobox from a text field.
    Pure geometry/contrast, no theme colours."""
    x1, y1, x2, y2 = box
    h = y2 - y1
    if h < 12 or (x2 - x1) < 3 * h:
        return False
    gh, gw = bgr.shape[:2]
    sx1, sx2 = max(0, x2 - h), min(gw, x2 - 2)
    sy1, sy2 = max(0, y1 + 2), min(gh, y2 - 2)
    if sx2 <= sx1 or sy2 <= sy1:
        return False
    strip = cv2.cvtColor(bgr[sy1:sy2, sx1:sx2], cv2.COLOR_BGR2GRAY)
    dark = (strip < 128).mean()
    # an arrow glyph is a small dark cluster: some dark pixels, but the strip
    # is not mostly dark (that would be a border/scrollbar)
    return 0.02 < dark < 0.45


def _classify(box: Tuple[int, int, int, int], inside_text: str, cfg: CVConfig,
              bgr: Optional["np.ndarray"] = None) -> str:
    """Heuristic control-type from box geometry + interior text + arrow glyph."""
    w, h = box[2] - box[0], box[3] - box[1]
    if max(w, h) <= cfg.checkbox_max and min(w, h) / max(w, h) > 0.7:
        return "checkboxcontrol"
    # A short, filled, roughly button-shaped box.
    if inside_text and w < 240 and 0.25 < h / max(w, 1) < 1.2 and len(inside_text) <= 20:
        return "buttoncontrol"
    # Dropdown arrow at the right edge → combobox (0 were detected before this,
    # so combobox fill mechanics never triggered under vision).
    if bgr is not None and _has_dropdown_arrow(bgr, box):
        return "comboboxcontrol"
    return "editcontrol"


def _nearest_label(box: Tuple[int, int, int, int],
                   words: List[Dict[str, Any]],
                   used: set,
                   cfg: CVConfig,
                   ctype: str = "editcontrol") -> str:
    """Find the caption for a box: nearest word group to its left (same row),
    else above. Checkboxes/radios are captioned on the RIGHT, so search there
    first for them. Words already consumed as a box's interior value are skipped
    via `used`."""
    x1, y1, x2, y2 = box
    left_cands, above_cands, right_cands = [], [], []
    for idx, wd in enumerate(words):
        if idx in used:
            continue
        wcx, wcy = wd["cx"], wd["cy"]
        # same row, to the left
        if y1 - 4 <= wcy <= y2 + 4 and wcx < x1 and (x1 - wcx) <= cfg.label_gap_x:
            left_cands.append((x1 - wcx, wd))
        # same row, to the right (checkbox/radio captions)
        elif y1 - 4 <= wcy <= y2 + 4 and wcx > x2 and (wcx - x2) <= cfg.label_gap_x:
            right_cands.append((wcx - x2, wd))
        # directly above
        elif abs(wcx - (x1 + x2) / 2.0) <= (x2 - x1) and 0 < (y1 - wcy) <= cfg.label_gap_y:
            above_cands.append((y1 - wcy, wd))
    if ctype in ("checkboxcontrol", "radiobuttoncontrol"):
        pool, side = (right_cands or left_cands or above_cands), "right"
    else:
        pool, side = (left_cands or above_cands or right_cands), "left"
    if not pool:
        return ""
    # WHOLE-LINE assembly: take the nearest word as anchor, then join EVERY
    # word that shares its Tesseract line id AND sits on the label's side of
    # the box. The old distance-anchored subset split multi-word labels
    # ('Agent ID' → 'ID', 'Policy Number' → 'Number') — and a fragmented label
    # mis-binds to boxes, breaking lookup/verify/read-back (live 2026-07-10).
    pool.sort(key=lambda t: t[0])
    anchor = pool[0][1]
    a_line = anchor.get("line")
    def _same_line(w):
        if a_line is not None and w.get("line") is not None:
            return w["line"] == a_line
        return abs(w["cy"] - anchor["cy"]) <= (anchor["box"][3] - anchor["box"][1])
    if side == "left":
        line = [w for w in words if _same_line(w) and w["cx"] < x1]
    else:
        line = [w for w in words if _same_line(w) and w["cx"] > x2]
    if not line:
        line = [anchor]
    line.sort(key=lambda w: w["cx"])
    return _clean_label(" ".join(w["text"] for w in line))


def _clean_label(s: str) -> str:
    """Strip OCR punctuation noise from a label's edges ('| (Renewal Policy ('
    → 'Renewal Policy'); collapse inner whitespace. Keeps interior slashes and
    hyphens ('Paperless / e-Delivery') intact."""
    s = " ".join(s.split())
    return s.strip(" |()[]{}—-–·:;,.'\"`").strip()


# ── public API ──────────────────────────────────────────────────────────────────
def detect_elements(image: Any, cfg: Optional[CVConfig] = None) -> List[Dict[str, Any]]:
    """
    Detect UI elements in an image.

    Parameters
    ----------
    image : PIL.Image, RGB ndarray, or BGR ndarray.
    cfg   : optional CVConfig override.

    Returns
    -------
    List of element dicts: {element_id, type, bbox, label, text, value, confidence}.
    Boxes are pixel coords on the ORIGINAL image. Empty list if OpenCV missing.
    """
    if not _CV_OK:
        logger.error("cv_detector: OpenCV/numpy unavailable — cannot detect.")
        return []

    cfg = cfg or CVConfig()
    bgr = _to_bgr(image)
    boxes = _detect_boxes(bgr, cfg)
    words = _ocr_words(bgr, cfg)

    # Drop text-glyph contours (captions) so they stay labels, not phantom fields.
    boxes = [b for b in boxes if not _is_text_glyph(b, words)]

    # Drop CONTAINER boxes (section panels / group frames). A box that wraps the
    # centers of >=2 other boxes is a container, not a field — the agent clicks
    # the fields inside it, never the panel. Critically, we must NOT let such a
    # container suppress its children (that deletes whole sections of fields).
    def _wraps_center(outer, inner) -> bool:
        icx, icy = (inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0
        return (outer[0] <= icx <= outer[2] and outer[1] <= icy <= outer[3]
                and outer != inner)

    containers = set()
    for i, a in enumerate(boxes):
        if sum(1 for j, b in enumerate(boxes) if i != j and _wraps_center(a, b)) >= 2:
            containers.add(i)
    boxes = [b for i, b in enumerate(boxes) if i not in containers]

    # Drop a box mostly inside a FIELD-SIZED box (e.g. a value's text contour
    # inside its input). Height guard keeps panels from eating their children.
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    keep: List[Tuple[int, int, int, int]] = []
    for b in boxes:
        b_area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        nested = False
        for outer in keep:
            if (outer[3] - outer[1]) >= 60:        # outer is a panel, not a field
                continue
            ix1, iy1 = max(b[0], outer[0]), max(b[1], outer[1])
            ix2, iy2 = min(b[2], outer[2]), min(b[3], outer[3])
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter / b_area > 0.80:
                nested = True
                break
        if not nested:
            keep.append(b)
    boxes = keep

    # Drop decorative COLOURED bars (section headers, title/status bars). Real
    # input fields and buttons have light interiors; coloured bars don't.
    if cfg.min_brightness > 0:
        gray_full = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gh, gw = gray_full.shape[:2]

        def _interior_brightness(b) -> float:
            x1, y1 = max(0, b[0]), max(0, b[1])
            x2, y2 = min(gw, b[2]), min(gh, b[3])
            if x2 <= x1 or y2 <= y1:
                return 255.0
            return float(gray_full[y1:y2, x1:x2].mean())

        boxes = [b for b in boxes if _interior_brightness(b) >= cfg.min_brightness]

    elements: List[Dict[str, Any]] = []
    used_words: set = set()

    # Pass 1: assign interior words (the box's current value / caption).
    interior: List[str] = []
    for box in boxes:
        inside = [(idx, wd) for idx, wd in enumerate(words)
                  if _center_in(box, (wd["cx"], wd["cy"]))]
        inside.sort(key=lambda t: (t[1]["cy"], t[1]["cx"]))
        for idx, _ in inside:
            used_words.add(idx)
        interior.append(" ".join(wd["text"] for _, wd in inside).strip())

    # Pass 2: build elements. CHECKBOXES FIRST so they claim their captions;
    # then suppress any text fragment that falls inside a checkbox row (those
    # are the stray label boxes that would otherwise be false-positive fields).
    ctypes = [_classify(box, interior[i], cfg, bgr) for i, box in enumerate(boxes)]

    cb_rows: List[Tuple[int, int, int, int]] = []
    for i, box in enumerate(boxes):
        if ctypes[i] != "checkboxcontrol":
            continue
        ext, label = _checkbox_with_label(box, words, used_words, cfg)
        cb_rows.append(ext)
        elements.append({
            "element_id": f"cv{i:04d}",
            "type":       "checkboxcontrol",
            "bbox":       [int(ext[0]), int(ext[1]), int(ext[2]), int(ext[3])],
            "label":      label,
            "text":       label,
            "value":      "",
            "confidence": 0.6,
        })

    for i, box in enumerate(boxes):
        if ctypes[i] == "checkboxcontrol":
            continue
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        if any(_center_in(r, (cx, cy)) for r in cb_rows):
            continue                       # text fragment inside a checkbox row
        ctype, inside_text = ctypes[i], interior[i]
        label = _nearest_label(box, words, used_words, cfg, ctype)
        if ctype == "buttoncontrol":
            value = ""
            if not label:
                label = inside_text
        else:
            value = inside_text
        elements.append({
            "element_id": f"cv{i:04d}",
            "type":       ctype,
            "bbox":       [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
            "label":      label,
            "text":       label,
            "value":      value,
            "confidence": 0.6,
        })

    # Pass 2b: add CHECKED checkboxes (filled coloured squares the adaptive
    # detector misses). Skip any whose square is already inside a detected box.
    def _center(b):
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    for j, cb in enumerate(_detect_filled_checkboxes(bgr, cfg)):
        c = _center(cb)
        if any(_center_in(e["bbox"], c) for e in elements
               if e["type"] == "checkboxcontrol"):
            continue
        box, label = _checkbox_with_label(cb, words, used_words, cfg)
        elements.append({
            "element_id": f"cvcb{j:04d}",
            "type":       "checkboxcontrol",
            "bbox":       [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
            "label":      label,
            "text":       label,
            "value":      "checked",     # colour-filled = currently checked
            "confidence": 0.6,
        })

    # Pass 3: free-floating text not inside or labeling any box → textcontrol
    # (captions / static labels — passive, the agent ignores them but a real
    # accessibility tree always lists them, so parity scoring is fair).
    for idx, wd in enumerate(words):
        if idx in used_words:
            continue
        bx = wd["box"]
        elements.append({
            "element_id": f"cvt{idx:04d}",
            "type":       "textcontrol",
            "bbox":       [int(bx[0]), int(bx[1]), int(bx[2]), int(bx[3])],
            "label":      wd["text"],
            "text":       wd["text"],
            "value":      "",
            "confidence": round(wd["conf"] / 100.0, 2),
        })

    logger.info("cv_detector: %d boxes, %d OCR words → %d elements",
                len(boxes), len(words), len(elements))
    return elements


def deps_status() -> Dict[str, bool]:
    """Report which optional backends are live (for the observer / tests)."""
    return {"opencv": _CV_OK, "tesseract": _OCR_OK}
