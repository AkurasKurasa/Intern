"""
trace_translator/trace_translator.py
=====================================
Converts observer state dicts into trace JSON files for training.

Observers (UIAutomationObserver, WebObserver, VisionObserver) handle all
screen reading. TraceTranslator only handles the conversion:

    observer.snapshot()  →  TraceTranslator  →  trace JSON

Single-state trace (snapshot):
    state = observer.snapshot()
    trace = translator.state_to_trace(state)
    translator.save_trace(trace, 'output.json')

Two-state trace (before/after transition):
    state_before = observer.snapshot()
    # ... action happens ...
    state_after = observer.snapshot()
    trace = translator.states_to_trace(state_before, state_after)
    translator.save_trace(trace, 'output.json')

Video trace (screen recording → sequence of traces):
    traces = translator.video_to_traces('recording.mp4', output_dir='data/traces/')
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class TraceTranslator:
    """
    Converts observer state dicts into trace JSON files.

    Does not read the screen itself — that is the observers' job.
    Accepts state dicts from any observer (UIA, Web, Vision) and produces
    trace files in the standard format consumed by the BC trainer.
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def state_to_trace(
        self,
        state: Dict[str, Any],
        trace_id: Optional[str] = None,
        action: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Convert a single observer state to trace format (snapshot).

        For recordings where you need before/after, use states_to_trace().
        """
        if trace_id is None:
            trace_id = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return {
            "trace_id":     trace_id,
            "timestamp":    datetime.now().isoformat(),
            "state":        state,
            "state_before": state,
            "state_after":  None,
            "mouse":        action.get("mouse", {}) if action else {},
            "keyboard":     action.get("keyboard", {}) if action else {},
            "action":       action,
            "metadata": {
                "trace_type":  "snapshot",
                "num_elements": len(state.get("elements", [])),
            },
        }

    def states_to_trace(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        action: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Convert two observer states into a single trace step.

        If no action is provided, infers mouse/keyboard from the state diff.
        """
        if trace_id is None:
            trace_id = f"trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        diff = self._diff_states(state_before, state_after)

        if action is None:
            mouse, keyboard = self._infer_action_from_diff(diff, state_before, state_after)
        else:
            mouse    = action.get("mouse", {})
            keyboard = action.get("keyboard", {})

        return {
            "trace_id":     trace_id,
            "timestamp":    datetime.now().isoformat(),
            "state":        state_before,
            "state_before": state_before,
            "state_after":  state_after,
            "mouse":        mouse,
            "keyboard":     keyboard,
            "action":       action,
            "diff":         diff,
            "metadata": {
                "trace_type":        "transition",
                "num_elements_before": len(state_before.get("elements", [])),
                "num_elements_after":  len(state_after.get("elements", [])),
            },
        }

    def video_to_traces(
        self,
        video_path: str,
        interval_sec: float = 3.0,
        application: Optional[str] = None,
        output_dir: Optional[str] = None,
        verbose: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Extract frames from a video and generate sequential trace steps.

        Uses VisionObserver to convert each frame into a state dict, then
        pairs consecutive states into transition traces.

        Each trace step: frame_N (state_before) + frame_N+1 (state_after)
        """
        try:
            import cv2
        except ImportError:
            raise ImportError("opencv-python required: pip install opencv-python")

        try:
            from PIL import Image
        except ImportError:
            raise ImportError("Pillow required: pip install Pillow")

        try:
            from observers.vision_observer.vision_observer import VisionObserver
        except ImportError:
            from components.observers.vision_observer.vision_observer import VisionObserver

        vision = VisionObserver()

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video: {video_path}")

        fps            = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_interval = max(1, int(fps * interval_sec))

        if verbose:
            print(f"\nVideo: {video_path}")
            print(f"  FPS: {fps:.1f}  Duration: {total_frames / fps:.1f}s")
            print(f"  Sampling every {interval_sec}s (~{total_frames // frame_interval} frames)\n")

        states: List[Dict[str, Any]] = []
        frame_num = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % frame_interval == 0:
                try:
                    import numpy as np
                    rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img   = Image.fromarray(rgb)
                    state = vision._state_from_pil_image(img, application or "video")
                    state["window_title"] = (
                        f"{os.path.basename(video_path)}::frame_{frame_num:06d}"
                    )
                    states.append(state)
                    if verbose:
                        print(f"  frame {frame_num:06d} → {len(state['elements'])} elements")
                except Exception as exc:
                    if verbose:
                        print(f"  frame {frame_num:06d} → skipped ({exc})")

            frame_num += 1

        cap.release()

        if len(states) < 2:
            print("Warning: fewer than 2 frames — cannot build trace steps.")
            return []

        traces: List[Dict[str, Any]] = []
        basename = os.path.splitext(os.path.basename(video_path))[0]

        for i in range(len(states) - 1):
            trace_id = f"{basename}_step_{i:04d}"
            trace    = self.states_to_trace(states[i], states[i + 1], trace_id=trace_id)
            traces.append(trace)

            if output_dir:
                self.save_trace(trace, os.path.join(output_dir, f"{trace_id}.json"))

        if verbose:
            print(f"\nGenerated {len(traces)} trace steps.")

        return traces

    def save_trace(self, trace: Dict[str, Any], output_path: str) -> None:
        """Save a single trace to a JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(trace, f, indent=2, ensure_ascii=False)

    def save_traces(self, traces: List[Dict[str, Any]], output_dir: str) -> None:
        """Save a list of traces to a directory, one JSON per trace."""
        os.makedirs(output_dir, exist_ok=True)
        for trace in traces:
            out_path = os.path.join(output_dir, f"{trace['trace_id']}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(trace, f, indent=2, ensure_ascii=False)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _diff_states(
        self,
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
        iou_threshold: float = 0.3,
        center_threshold: int = 30,
    ) -> Dict[str, Any]:
        """
        Diff two states using position-based element matching (IoU + centre proximity).

        Returns dict with keys: added, removed, changed, summary.
        """
        before_els: List[Dict] = state_before.get("elements", [])
        after_els:  List[Dict] = state_after.get("elements", [])

        def iou(a, b) -> float:
            ax1, ay1, ax2, ay2 = a
            bx1, by1, bx2, by2 = b
            ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if inter == 0:
                return 0.0
            area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
            area_b = max(1, (bx2 - bx1) * (by2 - by1))
            return inter / (area_a + area_b - inter)

        def center_dist(a, b) -> float:
            ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
            bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

        matched_before: set = set()
        matched_after:  set = set()
        matches: List[Tuple[int, int, float]] = []

        for bi, bel in enumerate(before_els):
            best_score, best_ai = -1.0, -1
            bb = bel.get("bbox", [0, 0, 0, 0])

            for ai, ael in enumerate(after_els):
                if ai in matched_after:
                    continue
                ab = ael.get("bbox", [0, 0, 0, 0])
                score = iou(bb, ab)
                if score >= iou_threshold and score > best_score:
                    best_score, best_ai = score, ai

            if best_ai == -1:
                for ai, ael in enumerate(after_els):
                    if ai in matched_after:
                        continue
                    ab  = ael.get("bbox", [0, 0, 0, 0])
                    dist = center_dist(bb, ab)
                    if dist < center_threshold:
                        score = 1.0 - (dist / center_threshold)
                        if score > best_score:
                            best_score, best_ai = score, ai

            if best_ai != -1:
                matches.append((bi, best_ai, best_score))
                matched_before.add(bi)
                matched_after.add(best_ai)

        changed: List[Dict] = []
        for bi, ai, score in matches:
            bel, ael = before_els[bi], after_els[ai]
            delta: Dict = {}
            for field in ("type", "text", "label", "enabled", "visible"):
                bv = (bel.get(field) or "").strip() if isinstance(bel.get(field), str) else bel.get(field)
                av = (ael.get(field) or "").strip() if isinstance(ael.get(field), str) else ael.get(field)
                if bv != av:
                    delta[field] = {"before": bel.get(field), "after": ael.get(field)}
            bb = bel.get("bbox", [0, 0, 0, 0])
            ab = ael.get("bbox", [0, 0, 0, 0])
            if any(abs(bb[i] - ab[i]) > 5 for i in range(4)):
                delta["bbox"] = {"before": bb, "after": ab}
            if delta:
                changed.append({
                    "element_id":       bel["element_id"],
                    "element_id_after": ael["element_id"],
                    "match_score":      round(score, 3),
                    "before": bel, "after": ael,
                    "changes": delta,
                })

        removed = [before_els[bi] for bi in range(len(before_els)) if bi not in matched_before]
        added   = [after_els[ai]  for ai in range(len(after_els))  if ai not in matched_after]

        return {
            "added":   added,
            "removed": removed,
            "changed": changed,
            "summary": {
                "total_before":  len(before_els),
                "total_after":   len(after_els),
                "matched":       len(matches),
                "added_count":   len(added),
                "removed_count": len(removed),
                "changed_count": len(changed),
            },
        }

    def _infer_action_from_diff(
        self,
        diff: Dict[str, Any],
        state_before: Dict[str, Any],
        state_after: Dict[str, Any],
    ) -> Tuple[Dict, Dict]:
        """
        Heuristically infer mouse/keyboard action from a state diff.

        Priority:
          1. Text grew in an element → keyboard (type new characters)
          2. Button/link disappeared → click at its centre
          3. Button/link appeared → click at its centre
          4. No change → no_op
        """
        for change in diff.get("changed", []):
            text_delta = change.get("changes", {}).get("text")
            if text_delta is None:
                continue
            before_text = text_delta.get("before") or ""
            after_text  = text_delta.get("after")  or ""
            if len(after_text) > len(before_text):
                typed = after_text[len(before_text):]
                bbox  = change["after"].get("bbox", [0, 0, 0, 0])
                cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
                return ({"action": "click", "x": cx, "y": cy},
                        {"text": typed, "key_count": len(typed)})

        for elem in diff.get("removed", []):
            if elem.get("type") in ("button", "link"):
                bbox = elem.get("bbox", [0, 0, 0, 0])
                cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
                return ({"action": "click", "x": cx, "y": cy}, {})

        for elem in diff.get("added", []):
            if elem.get("type") in ("button", "link", "menu_item"):
                bbox = elem.get("bbox", [0, 0, 0, 0])
                cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
                return ({"action": "click", "x": cx, "y": cy}, {})

        return ({}, {})


__all__ = ["TraceTranslator"]
