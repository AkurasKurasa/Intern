"""
cv_vision_observer.py
=====================
Vision perception adapter: screenshot → cv_detector → canonical state dict.

This is the real "see the screen" observer the milestone calls for — it reads
pixels (screenshot) instead of the accessibility tree, so it works on apps where
UIA returns junk. It plugs into the SAME seam as UIAutomationObserver: it
subclasses observers/base.Observer, so snapshot() runs the shared
capture → normalize → validate template and its output conforms to
observers/schema.py.

Usage
-----
    obs = CVVisionObserver()                 # live screen
    state = obs.snapshot()                   # schema-conforming dict

    obs = CVVisionObserver(image=pil_image)  # offline: detect on a given image
    state = obs.snapshot()

CLI (eyeball it on a real screenshot):
    py -3.14 components/observers/vlm/vision_observer/cv_vision_observer.py shot.png
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── path setup so `from observers...` works whether run as module or script ─────
_HERE = os.path.dirname(os.path.abspath(__file__))   # vision_observer/
_VLM  = os.path.dirname(_HERE)                        # vlm/
_OBS  = os.path.dirname(_VLM)                         # observers/
_COMP = os.path.dirname(_OBS)                         # components/
_ROOT = os.path.dirname(_COMP)                        # Intern/
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from observers.base import Observer
except ImportError:                                   # pragma: no cover
    from components.observers.base import Observer

try:
    from cv_detector import detect_elements, deps_status, CVConfig
except ImportError:                                   # pragma: no cover
    from components.observers.vlm.vision_observer.cv_detector import (
        detect_elements, deps_status, CVConfig,
    )

DEFAULT_W, DEFAULT_H = 1920, 1080


class CVVisionObserver(Observer):
    """Screenshot-based perception via classical CV + OCR.

    UIA is already canonical, so its TYPE_MAP/KEY_MAP are empty (identity).
    cv_detector emits canonical types/keys directly too, so this observer only
    needs to capture and assemble — the base class validates.
    """

    source_name = "cv_vision"

    def __init__(
        self,
        image:  Optional[Any] = None,                 # offline: detect on this image
        region: Optional[Tuple[int, int, int, int]] = None,   # (left, top, w, h)
        cfg:    Optional[CVConfig] = None,
        origin: Optional[Tuple[int, int]] = None,     # add (x,y) to every bbox
    ):
        self._image  = image
        self._region = region
        self._cfg    = cfg
        # When capturing a sub-region, detector boxes are region-relative. Shift
        # them by the region's top-left so coords are absolute screen pixels
        # (matching UIA / what the agent clicks). Defaults to the region origin.
        if origin is None and region is not None:
            origin = (region[0], region[1])
        self._origin = origin
        self._screen_res: Optional[Tuple[int, int]] = None

    # ── Observer template hook ──────────────────────────────────────────────────
    def _raw_snapshot(self) -> Dict[str, Any]:
        img, W, H = self._load_image()
        elements = detect_elements(img, self._cfg)
        # DEBUG FLIPBOOK: VISION_DEBUG_DIR=<dir> → save an annotated frame for
        # EVERY observation (one per agent step) — boxes + OCR labels drawn on
        # the exact pixels the model decided on. "What did it see?" made literal.
        _dbg = os.environ.get("VISION_DEBUG_DIR")
        if _dbg:
            try:
                # Trigger only when the SIGHT CHANGED: identical element sets
                # (same boxes, labels, values) mean an identical frame — a stall
                # loop would otherwise spam dozens of duplicate PNGs. One frame
                # per distinct observation carries all the information.
                _sig = hash(tuple(sorted(
                    (e.get("label") or e.get("text") or "", tuple(e.get("bbox") or ()),
                     e.get("value") or "", e.get("type") or "")
                    for e in elements)))
                if _sig != getattr(self, "_last_sig", None):
                    self._last_sig = _sig
                    self._dump_debug_frame(img, elements, _dbg)
            except Exception as exc:                   # never break perception
                logger.debug("vision debug dump failed: %s", exc)
        ox, oy = self._origin if self._origin else (0, 0)
        for e in elements:
            # Vision is single-window: everything seen is the active foreground.
            e.setdefault("window_role", "active")
            if ox or oy:
                x1, y1, x2, y2 = e["bbox"]
                e["bbox"] = [x1 + ox, y1 + oy, x2 + ox, y2 + oy]
        return {
            "elements":           elements,
            "screen_resolution":  [W, H],
            "focused_element_id": None,    # pixels don't expose focus; agent infers
            "source":             self.source_name,
        }

    _TYPE_TINT = {
        "editcontrol": (80, 200, 255), "comboboxcontrol": (255, 200, 90),
        "checkboxcontrol": (100, 255, 150), "buttoncontrol": (120, 120, 255),
        "tabitemcontrol": (255, 120, 255), "textcontrol": (150, 150, 150),
    }

    def _dump_debug_frame(self, img, elements, out_dir: str) -> None:
        """Annotated copy of this observation (region-relative coords)."""
        import numpy as _np
        import cv2 as _cv2
        os.makedirs(out_dir, exist_ok=True)
        frame = _np.array(img)[:, :, ::-1].copy()      # PIL RGB → BGR
        for e in elements:
            x1, y1, x2, y2 = [int(v) for v in e["bbox"]]
            c = self._TYPE_TINT.get((e.get("type") or ""), (128, 128, 128))
            _cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
            lab = (e.get("label") or e.get("text") or e.get("value") or "")[:24]
            if lab:
                _cv2.putText(frame, lab, (x1, max(10, y1 - 4)),
                             _cv2.FONT_HERSHEY_SIMPLEX, 0.38, c, 1, _cv2.LINE_AA)
        self._frame_no = getattr(self, "_frame_no", 0) + 1
        _cv2.imwrite(os.path.join(out_dir, f"see_{self._frame_no:04d}.png"), frame)

    # ── image acquisition ───────────────────────────────────────────────────────
    def _load_image(self) -> Tuple[Any, int, int]:
        """Return (image, width, height). Injected image wins; else grab screen."""
        if self._image is not None:
            img = self._image
            if hasattr(img, "size") and hasattr(img, "convert"):     # PIL
                W, H = img.size
            else:                                                    # ndarray
                H, W = img.shape[:2]
            return img, int(W), int(H)
        return self._capture(self._region)

    def _capture(self, region=None) -> Tuple[Any, int, int]:
        """Grab the screen (or a sub-region) → (PIL.Image, W, H)."""
        try:
            import mss
            from PIL import Image
        except ImportError as exc:                    # pragma: no cover
            raise RuntimeError(
                "CVVisionObserver screen capture needs mss + Pillow "
                "(pip install mss Pillow)."
            ) from exc

        with mss.mss() as sct:
            mon = (
                {"left": region[0], "top": region[1],
                 "width": region[2], "height": region[3]}
                if region else sct.monitors[1]        # primary monitor
            )
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        W, H = img.size
        return img, int(W), int(H)

    # ── diagnostics ─────────────────────────────────────────────────────────────
    @staticmethod
    def backend_status() -> Dict[str, bool]:
        return deps_status()


# ── CLI: dump elements + an annotated overlay for a screenshot ──────────────────
def _annotate(image_path: str, state: Dict[str, Any], out_path: str) -> None:
    from PIL import Image, ImageDraw
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    colors = {
        "editcontrol": (0, 160, 0), "buttoncontrol": (0, 90, 220),
        "checkboxcontrol": (200, 0, 0), "textcontrol": (150, 150, 150),
    }
    for e in state["elements"]:
        x1, y1, x2, y2 = e["bbox"]
        c = colors.get(e["type"], (255, 140, 0))
        draw.rectangle([x1, y1, x2, y2], outline=c, width=2)
        tag = e["label"] or e["value"] or e["type"]
        draw.text((x1 + 2, max(0, y1 - 11)), tag[:24], fill=c)
    img.save(out_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    print("backends:", CVVisionObserver.backend_status())

    if len(sys.argv) > 1:
        from PIL import Image
        path = sys.argv[1]
        pil  = Image.open(path).convert("RGB")
        obs  = CVVisionObserver(image=pil)
        st   = obs.snapshot()
        from collections import Counter
        print(f"\n{path}: {len(st['elements'])} elements")
        print("by type:", dict(Counter(e["type"] for e in st["elements"])))
        for e in st["elements"]:
            if e["type"] != "textcontrol":
                print(f"  {e['type']:16s} {e['bbox']}  "
                      f"label={e['label']!r}  value={e['value']!r}")
        out = os.path.splitext(path)[0] + "_cv_overlay.png"
        _annotate(path, st, out)
        print(f"\nannotated overlay → {out}")
    else:
        print("\nNo image given. Capturing live screen…")
        st = CVVisionObserver().snapshot()
        print(f"{len(st['elements'])} elements at {st['screen_resolution']}")
