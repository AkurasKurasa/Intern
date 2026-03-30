"""
recorder/vision_observer/vision_observer.py
===================================================
Screenshot-based UI observation via a local VLM.

Used as a fallback when Windows UI Automation (UIA) cannot see an
application (browsers, Electron apps, custom-rendered UIs).  Returns
elements in the same dict format as UIAutomationObserver so the rest
of the pipeline — trainer, executor, planner — needs no changes.

Default backend: LM Studio (http://localhost:1234) running any
vision-capable model: LLaVA, Phi-3-vision, Moondream, Qwen-VL, etc.

The observer is completely optional.  If the VLM is not reachable it
returns an empty state and logs a warning rather than crashing.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from io import BytesIO
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))   # vision_observer/
_SO   = os.path.dirname(_HERE)                        # recorder/
_COMP = os.path.dirname(_SO)                          # components/
_ROOT = os.path.dirname(_COMP)                        # Intern/
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── VLM system prompt ──────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are a UI element extractor.
Given a screenshot return ONLY a JSON array of interactive elements.
Each element must have these fields:
  element_id : unique string  (e.g. "e001")
  type        : one of editcontrol | comboboxcontrol | buttoncontrol |
                checkboxcontrol | textcontrol | tabitemcontrol | listcontrol
  text        : visible label or content (empty string if none)
  bbox        : [x1, y1, x2, y2]  pixel coordinates on the ORIGINAL screenshot
  enabled     : true or false
  confidence  : 0.0–1.0

Return ONLY the JSON array. No explanation. No markdown fences."""


class VisionObserver:
    """
    Observes UI state via screenshot + local VLM.

    Parameters
    ----------
    lmstudio_url     : Base URL of an OpenAI-compatible local API.
    model_id         : Model name as shown in LM Studio (or "local-model").
    timeout          : HTTP request timeout in seconds.
    screenshot_scale : Resize factor before sending to VLM (reduces tokens).
                       1.0 = full resolution, 0.5 = half.
    """

    def __init__(
        self,
        lmstudio_url:     str   = "http://localhost:1234",
        model_id:         str   = "local-model",
        timeout:          float = 30.0,
        screenshot_scale: float = 0.75,
    ):
        self.lmstudio_url     = lmstudio_url.rstrip("/")
        self.model_id         = model_id
        self.timeout          = timeout
        self.screenshot_scale = screenshot_scale
        self._available:      Optional[bool] = None   # lazily checked

    # ── Public API ─────────────────────────────────────────────────────────────

    def observe(self, region: Optional[Tuple[int, int, int, int]] = None) -> dict:
        """
        Capture a screenshot and return a state dict with UI elements.

        Parameters
        ----------
        region : Optional (left, top, width, height) to capture a sub-region.
                 None = full primary monitor.

        Returns
        -------
        State dict compatible with encode_state() in the transformer:
        {
            "source":             "vision",
            "screen_resolution":  [W, H],
            "elements":           [...],
            "focused_element_id": None,
        }
        """
        image_b64, W, H = self._capture(region)

        if not self._check_available():
            logger.warning("VisionObserver: VLM not reachable at %s — returning empty state",
                           self.lmstudio_url)
            return self._empty_state(W, H)

        elements = self._query_vlm(image_b64, W, H)
        for elem in elements:
            elem.setdefault("window_role", "active")
            elem.setdefault("value", "")

        return {
            "source":             "vision",
            "screen_resolution":  [W, H],
            "elements":           elements,
            "focused_element_id": None,
        }

    def is_available(self) -> bool:
        """Return True if the VLM backend is reachable."""
        return self._check_available()

    def reset(self):
        """Force re-check of VLM availability on next call."""
        self._available = None

    # ── Screenshot ─────────────────────────────────────────────────────────────

    def _capture(self, region=None) -> Tuple[str, int, int]:
        """Capture screen, return (base64_png, original_W, original_H)."""
        try:
            import mss
            from PIL import Image
        except ImportError:
            raise RuntimeError(
                "VisionObserver requires mss and Pillow.\n"
                "Install with: pip install mss Pillow"
            )

        with mss.mss() as sct:
            mon = (
                {"left": region[0], "top": region[1],
                 "width": region[2], "height": region[3]}
                if region else sct.monitors[1]
            )
            raw = sct.grab(mon)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        W, H = img.size

        if self.screenshot_scale != 1.0:
            img = img.resize((int(W * self.screenshot_scale),
                              int(H * self.screenshot_scale)))

        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode(), W, H

    # ── VLM query ──────────────────────────────────────────────────────────────

    def _query_vlm(self, image_b64: str, W: float, H: float) -> List[dict]:
        import urllib.request

        payload = json.dumps({
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type":      "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                        {
                            "type": "text",
                            "text": "Extract all interactive UI elements from this screenshot.",
                        },
                    ],
                },
            ],
            "max_tokens":  2048,
            "temperature": 0.0,
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.lmstudio_url}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())

            content = data["choices"][0]["message"]["content"].strip()
            elements = self._parse_response(content, W, H)
            logger.info("VisionObserver: extracted %d elements", len(elements))
            return elements

        except Exception as exc:
            logger.warning("VisionObserver: VLM query failed — %s", exc)
            return []

    def _parse_response(self, content: str, W: float, H: float) -> List[dict]:
        # Strip markdown code fences if the model wrapped the JSON
        if content.startswith("```"):
            lines = content.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            content = "\n".join(inner)

        try:
            parsed = json.loads(content.strip())
            elements = parsed if isinstance(parsed, list) else parsed.get("elements", [])
        except json.JSONDecodeError:
            logger.warning("VisionObserver: could not parse VLM response as JSON")
            return []

        # Rescale bbox back to original resolution if screenshot was shrunk
        if self.screenshot_scale != 1.0:
            inv = 1.0 / self.screenshot_scale
            for e in elements:
                if "bbox" in e and len(e["bbox"]) == 4:
                    e["bbox"] = [int(v * inv) for v in e["bbox"]]

        return elements

    # ── Availability ───────────────────────────────────────────────────────────

    def _check_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{self.lmstudio_url}/v1/models",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self._available = (resp.status == 200)
        except Exception:
            self._available = False
        logger.info("VisionObserver: VLM available = %s", self._available)
        return self._available

    @staticmethod
    def _empty_state(W: float, H: float) -> dict:
        return {
            "source":             "vision",
            "screen_resolution":  [W, H],
            "elements":           [],
            "focused_element_id": None,
        }
