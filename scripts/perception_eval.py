"""
perception_eval.py
==================
The measuring stick for vision perception.

Given a CANDIDATE state (from the vision observer) and a REFERENCE state (the
"answer key" — usually UIA on the same screen), score how well the candidate
recovers the reference's elements:

    - precision / recall / F1  — did we find the elements, without inventing junk?
    - mean IoU of matches      — are the boxes pixel-tight (clicks land)?
    - type-match rate          — did we classify control types correctly?
    - label-match / value-match — did OCR read the right text?

Matching is greedy by IoU (an element matches the unused reference box it most
overlaps, above `iou_threshold`). Text is compared loosely (lowercased,
alphanumeric-only) so OCR near-misses like "Palicy"≈"Policy" don't tank the
score for a near-correct read.

Use it two ways:

    # In code — compare two states you already have:
    from perception_eval import score_states
    report = score_states(candidate_state, reference_state)
    print(report["summary"])

    # Live on the real form — capture UIA + vision on the same screen:
    py -3.14 scripts/perception_eval.py --live
    # (UIA = reference, CVVisionObserver = candidate; open the form first.)

    # Offline on a saved screenshot, with a hand-written ground-truth JSON:
    py -3.14 scripts/perception_eval.py --image shot.png --truth truth.json
"""
from __future__ import annotations

import json
import os
import re
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Interactive types we actually care about scoring (passive text/containers are
# noise for a click-targeting agent). Override via score_states(..., types=...).
DEFAULT_SCORED_TYPES = {
    "editcontrol", "comboboxcontrol", "checkboxcontrol",
    "radiobuttoncontrol", "buttoncontrol", "tabitemcontrol",
}


def _iou(a, b) -> float:
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


def _norm_text(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _text_match(a: Optional[str], b: Optional[str]) -> bool:
    """Loose text equality: one normalized string contains the other (and both
    non-empty). Forgives OCR near-misses + label/value substring differences."""
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return na == nb            # both empty counts as a match (empty field)
    return na in nb or nb in na


def _filter(elems: List[Dict[str, Any]], types: Optional[set]) -> List[Dict[str, Any]]:
    if types is None:
        return list(elems)
    return [e for e in elems if (e.get("type") or "").lower() in types]


def score_states(
    candidate: Dict[str, Any],
    reference: Dict[str, Any],
    iou_threshold: float = 0.5,
    types: Optional[set] = DEFAULT_SCORED_TYPES,
    environment: str = "",
    scan_time_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Score candidate elements against reference elements. See module docstring."""
    cand = _filter(candidate.get("elements", []), types)
    ref  = _filter(reference.get("elements", []),  types)

    # Greedy IoU matching: best pairs first.
    pairs: List[Tuple[float, int, int]] = []
    for ci, c in enumerate(cand):
        cb = c.get("bbox", [0, 0, 0, 0])
        for ri, r in enumerate(ref):
            iou = _iou(cb, r.get("bbox", [0, 0, 0, 0]))
            if iou >= iou_threshold:
                pairs.append((iou, ci, ri))
    pairs.sort(reverse=True)

    matched_c: set = set()
    matched_r: set = set()
    matches: List[Tuple[int, int, float]] = []
    for iou, ci, ri in pairs:
        if ci in matched_c or ri in matched_r:
            continue
        matched_c.add(ci)
        matched_r.add(ri)
        matches.append((ci, ri, iou))

    tp = len(matches)
    fp = len(cand) - tp           # candidate boxes with no reference = invented
    fn = len(ref) - tp            # reference boxes we missed
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    iou_sum = type_hits = label_hits = value_hits = 0.0
    detail: List[Dict[str, Any]] = []
    for ci, ri, iou in matches:
        c, r = cand[ci], ref[ri]
        type_ok  = (c.get("type") or "").lower() == (r.get("type") or "").lower()
        label_ok = _text_match(c.get("label") or c.get("text"),
                               r.get("label") or r.get("text"))
        value_ok = _text_match(c.get("value"), r.get("value"))
        iou_sum    += iou
        type_hits  += type_ok
        label_hits += label_ok
        value_hits += value_ok
        detail.append({
            "iou": round(iou, 3), "type_ok": type_ok,
            "label_ok": label_ok, "value_ok": value_ok,
            "cand": {"type": c.get("type"), "label": c.get("label"), "value": c.get("value")},
            "ref":  {"type": r.get("type"), "label": r.get("label"), "value": r.get("value")},
        })

    n = max(1, tp)
    # "Detection accuracy" (objective 1's exact term) = F1: penalizes both missed
    # elements (recall) and hallucinated ones (precision) — a bare hit-rate would
    # let a noisy detector game the number by over-proposing boxes.
    detection_accuracy = f1
    report = {
        "environment":     environment,
        "counts": {"candidate": len(cand), "reference": len(ref),
                   "matched": tp, "false_pos": fp, "false_neg": fn},
        "precision":          round(precision, 3),
        "recall":             round(recall, 3),
        "f1":                 round(f1, 3),
        "detection_accuracy": round(detection_accuracy, 3),
        "meets_95pct_target": detection_accuracy >= 0.95,
        "mean_iou":        round(iou_sum / n, 3),
        "type_match":      round(type_hits / n, 3),
        "label_match":     round(label_hits / n, 3),
        "value_match":     round(value_hits / n, 3),
        "iou_threshold":   iou_threshold,
        "scored_types":    sorted(types) if types else "all",
        "scan_time_ms":    round(scan_time_ms, 1) if scan_time_ms is not None else None,
        "matches":         detail,
    }
    report["summary"] = (
        f"P={report['precision']:.2f} R={report['recall']:.2f} F1={report['f1']:.2f} "
        f"(detection_accuracy={detection_accuracy*100:.1f}%, target 95%: "
        f"{'PASS' if report['meets_95pct_target'] else 'FAIL'}) | "
        f"IoU={report['mean_iou']:.2f} type={report['type_match']:.2f} "
        f"label={report['label_match']:.2f} value={report['value_match']:.2f} | "
        f"matched {tp}/{len(ref)} ref (fp={fp}, fn={fn})"
        + (f" | scan={scan_time_ms:.0f}ms" if scan_time_ms is not None else "")
    )
    return report


# ── CLI ─────────────────────────────────────────────────────────────────────────
def _load_state(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # accept either a bare state or a trace wrapping one
    return data.get("state", data) if isinstance(data, dict) else data


def _foreground_rect():
    """Screen rect (left, top, right, bottom) of the active window, or None."""
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def _restrict_to_rect(state: Dict[str, Any], rect) -> Dict[str, Any]:
    """Keep only elements whose center falls inside `rect` (the form window)."""
    left, top, right, bottom = rect
    kept = []
    for e in state.get("elements", []):
        b = e.get("bbox") or [0, 0, 0, 0]
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if left <= cx <= right and top <= cy <= bottom:
            kept.append(e)
    return dict(state, elements=kept)


def _active_window_only(state: Dict[str, Any], rect) -> Dict[str, Any]:
    """Keep only the foreground form's elements. Prefer window_role=='active'
    (set by the UIA observer); fall back to a rect filter if absent."""
    elems = state.get("elements", [])
    active = [e for e in elems if e.get("window_role") == "active"]
    if active:
        return dict(state, elements=active)
    return _restrict_to_rect(state, rect)


def _save_overlay(shot, candidate, reference, origin, out_path) -> None:
    """Draw UIA (green), vision (red), and matched-vision (yellow) boxes onto the
    captured form screenshot. Coords are absolute → subtract origin to draw."""
    from PIL import ImageDraw
    ox, oy = origin
    img = shot.convert("RGB").copy()
    d = ImageDraw.Draw(img)

    scored = {"editcontrol", "comboboxcontrol", "checkboxcontrol",
              "radiobuttoncontrol", "buttoncontrol", "tabitemcontrol"}
    # UIA reference boxes (the answer key) — green.
    for e in reference.get("elements", []):
        if (e.get("type") or "").lower() not in scored:
            continue
        x1, y1, x2, y2 = e.get("bbox", [0, 0, 0, 0])
        d.rectangle([x1 - ox, y1 - oy, x2 - ox, y2 - oy], outline=(0, 170, 0), width=2)

    # Which vision boxes matched a reference box (for coloring).
    cand_boxes = [e for e in candidate.get("elements", [])
                  if (e.get("type") or "").lower() in scored]
    ref_boxes = [e for e in reference.get("elements", [])
                 if (e.get("type") or "").lower() in scored]
    matched = set()
    used_r = set()
    pairs = []
    for ci, c in enumerate(cand_boxes):
        for ri, r in enumerate(ref_boxes):
            iou = _iou(c.get("bbox", [0, 0, 0, 0]), r.get("bbox", [0, 0, 0, 0]))
            if iou >= 0.5:
                pairs.append((iou, ci, ri))
    for iou, ci, ri in sorted(pairs, reverse=True):
        if ci in matched or ri in used_r:
            continue
        matched.add(ci)
        used_r.add(ri)

    for ci, e in enumerate(cand_boxes):
        x1, y1, x2, y2 = e.get("bbox", [0, 0, 0, 0])
        color = (220, 200, 0) if ci in matched else (220, 0, 0)
        d.rectangle([x1 - ox, y1 - oy, x2 - ox, y2 - oy], outline=color, width=2)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Score vision perception vs a reference.")
    ap.add_argument("--live", action="store_true",
                    help="Capture UIA (reference) + CV vision (candidate) on the live screen.")
    ap.add_argument("--fullscreen", action="store_true",
                    help="With --live: compare the whole screen instead of just the form window.")
    ap.add_argument("--image", help="Screenshot to run the vision observer on (offline).")
    ap.add_argument("--truth", help="Reference state JSON (the answer key) for --image.")
    ap.add_argument("--candidate", help="Candidate state JSON (skip running the observer).")
    ap.add_argument("--reference", help="Reference state JSON.")
    ap.add_argument("--iou", type=float, default=0.5, help="IoU match threshold.")
    ap.add_argument("--tag", default="live",
                    help="Label for saved files, e.g. --tag drivers → perception_drivers_overlay.png")
    ap.add_argument("--environment", default="",
                    help="Name of the GUI/app under test (e.g. 'car_insurance_form', 'excel', "
                         "'notepad'). Tags the log entry so detection accuracy can be broken out "
                         "per environment — objective 1 targets 95% ACROSS MULTIPLE environments, "
                         "not just one.")
    ap.add_argument("--log", action="store_true",
                    help="Append this run's scores to data/output/perception_eval_log.jsonl.")
    args = ap.parse_args(argv)

    from observers.vlm.vision_observer.cv_vision_observer import CVVisionObserver

    scan_time_ms = None
    if args.live:
        import time
        from observers.ui_observer import UIAutomationObserver
        print("Click the form window NOW — capturing in:")
        for i in range(5, 0, -1):
            print(f"  {i}...", end="\r", flush=True)
            time.sleep(1)
        print("  capturing!   ")

        # Focus the comparison on the FORM window only, so vision isn't penalized
        # for seeing VS Code / the desktop behind it. Filter UIA to the ACTIVE
        # window (correct even when the form is maximized — a rectangle can't
        # exclude background windows then, but window_role can).
        from PIL import Image
        import mss
        rect = _foreground_rect() if not args.fullscreen else None
        reference = UIAutomationObserver().snapshot()

        shot = None
        if rect:
            left, top, right, bottom = rect
            with mss.mss() as sct:
                raw = sct.grab({"left": left, "top": top,
                                "width": right - left, "height": bottom - top})
                shot = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            _t0 = time.time()
            candidate = CVVisionObserver(image=shot, origin=(left, top)).snapshot()
            scan_time_ms = (time.time() - _t0) * 1000
            reference = _active_window_only(reference, rect)
            print(f"focused on foreground window {rect} "
                  f"({len(reference['elements'])} UIA form elements)")
        else:
            _t0 = time.time()
            candidate = CVVisionObserver().snapshot()
            scan_time_ms = (time.time() - _t0) * 1000

        # Save a visual so detection can be inspected (what was found vs missed).
        if shot is not None:
            out = os.path.join(_ROOT, "data", "output", f"perception_{args.tag}_overlay.png")
            _save_overlay(shot, candidate, reference, (rect[0], rect[1]), out)
            shot.save(os.path.join(_ROOT, "data", "output", f"perception_{args.tag}_shot.png"))
            print(f"overlay (UIA=green, vision=red, matched=yellow) → {out}")
    elif args.candidate and args.reference:
        candidate = _load_state(args.candidate)
        reference = _load_state(args.reference)
    elif args.image and args.truth:
        import time
        from PIL import Image
        _t0 = time.time()
        candidate = CVVisionObserver(image=Image.open(args.image).convert("RGB")).snapshot()
        scan_time_ms = (time.time() - _t0) * 1000
        reference = _load_state(args.truth)
    else:
        ap.error("need --live, or --image+--truth, or --candidate+--reference")
        return 2

    report = score_states(candidate, reference, iou_threshold=args.iou,
                           environment=args.environment, scan_time_ms=scan_time_ms)
    print("\n" + report["summary"])
    print(json.dumps({k: v for k, v in report.items() if k != "matches"}, indent=2))

    if args.log:
        import datetime as _dt
        log_path = os.path.join(_ROOT, "data", "output", "perception_eval_log.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        row = {k: v for k, v in report.items() if k != "matches"}
        row["timestamp"] = _dt.datetime.now().isoformat()
        row["tag"] = args.tag
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(json.dumps(row) + "\n")
        print(f"\nLogged to {log_path} (environment={args.environment or 'unspecified'!r}) "
              f"— run scripts/objectives_report.py to see the cross-environment rollup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
