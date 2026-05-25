"""
run_transformer.py — Transformer-driven agent (no LLM)
=======================================================
Uses the trained BC checkpoint to fill forms autonomously.
No API calls, no rate limits — runs fully locally.

Usage:
    python run_transformer.py
    python run_transformer.py --steps 80 --delay 1.0 --dry_run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_transformer")

_ROOT = os.path.dirname(os.path.abspath(__file__))
_COMP = os.path.join(_ROOT, "components")
_MODEL_DIR = os.path.join(_COMP, "intelligence", "model")
for _p in (_ROOT, _COMP, _MODEL_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_record(data_file: str, record_num: int) -> dict:
    """
    Parse a multi-record intake .txt file and return {field: value} for
    the requested record number.  Lines must follow 'Field : Value' format.
    Records are delimited by 'RECORD N OF' headers.
    """
    import re
    with open(data_file, encoding="utf-8") as f:
        text = f.read()

    # Split on record headers like "RECORD 1 OF 10"
    parts = re.split(r"={3,}[\s\S]*?RECORD\s+(\d+)\s+OF\s+\d+[\s\S]*?={3,}", text)
    # parts[0] = preamble, then alternating: record_num_str, record_body
    records = {}
    i = 1
    while i + 1 < len(parts):
        rn   = int(parts[i])
        body = parts[i + 1]
        data = {}
        for line in body.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().strip("[]").strip()
                val = val.strip()
                # Strip trailing [VERIFY], [NOTE ...], ← annotations
                val = re.sub(r"\s*\[.*", "", val)
                val = re.sub(r"\s*←.*", "", val)
                val = val.strip()
                if key and val and val.lower() not in {"(none)", "none", ""}:
                    data[key.lower()] = val
        records[rn] = data
        i += 2

    if record_num not in records:
        available = sorted(records.keys())
        raise ValueError(f"Record {record_num} not found. Available: {available}")

    logger.info("Loaded record %d — %d fields", record_num, len(records[record_num]))
    return records[record_num]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", default="tasks/form_filling/model.pt")
    parser.add_argument("--steps",      type=int,   default=60)
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--countdown",  type=int,   default=5)
    parser.add_argument("--dry_run",    action="store_true")
    parser.add_argument("--verbose",    action="store_true", help="Show transformer reasoning")
    parser.add_argument("--data_file",  default=None,        help="Path to intake .txt file")
    parser.add_argument("--record",     type=int, default=1, help="Which record number to fill (default: 1)")
    args = parser.parse_args()

    model_path = args.model_path if os.path.isabs(args.model_path) \
        else os.path.join(_ROOT, args.model_path)

    if not os.path.exists(model_path):
        logger.error("Model checkpoint not found: %s", model_path)
        logger.error("Run train.py first to generate a checkpoint.")
        sys.exit(1)

    from observers.ui_observer.ui_observer import UIAutomationObserver
    from intelligence.training.bc.behavioral_cloning import BCTrainer
    from agent.executor import ActionExecutor, _TextResolver, _snap_to_element

    observer    = UIAutomationObserver()
    trainer     = BCTrainer(save_path=model_path)
    executor    = ActionExecutor(dry_run=args.dry_run)
    resolver    = _TextResolver()
    record_data = {}

    if args.data_file:
        data_path = args.data_file if os.path.isabs(args.data_file) \
            else os.path.join(_ROOT, args.data_file)
        record_data = _load_record(data_path, args.record)
        logger.info("Record %d loaded — fields: %s",
                    args.record, list(record_data.keys())[:6])

    logger.info("Transformer agent ready — model: %s", model_path)
    logger.info("Steps: %d  |  Delay: %.1fs  |  Dry-run: %s",
                args.steps, args.delay, args.dry_run)

    # Countdown
    print(f"\nClick the target window now — starting in {args.countdown}s ...")
    for i in range(args.countdown, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)
    print("  GO\n")

    history      = []
    noop_run     = 0
    stuck_state  = None   # (focused_element_id, value)
    stuck_count  = 0
    _STUCK_LIMIT = 4      # Tab-escape after this many steps on same element+value
    blocked_zone = None   # (cx, cy) of a recently escaped element
    blocked_steps = 0     # how many more steps to block that zone
    _BLOCK_RADIUS = 80    # px — clicks within this radius of a stuck element are skipped
    _BLOCK_DURATION = 8   # steps to keep the zone blocked after Tab-escape

    for step in range(1, args.steps + 1):
        logger.info("─── Step %d / %d ───", step, args.steps)

        # 1. Observe
        state = observer.snapshot()
        win   = state.get("window_title", "?")
        elems = len(state.get("elements", []))
        logger.info("Screen: %r  |  elements: %d", win, elems)

        # 2. Predict
        prediction  = trainer.predict(state, history)
        action_type = prediction.get("action_type", "no_op")
        scores      = prediction.get("_scores", {})

        # Show reasoning
        score_str = "  ".join(f"{k}={v:.3f}" for k, v in scores.items())
        logger.info("Transformer → %-10s  [%s]", action_type.upper(), score_str)

        if args.verbose:
            elements  = state.get("elements", [])
            focused_id = state.get("focused_element_id")
            focused   = next((e for e in elements if e.get("element_id") == focused_id), None)
            if focused:
                logger.info("  Focused: [%s] %r  value=%r",
                            focused.get("type","?"),
                            focused.get("label") or focused.get("text",""),
                            (focused.get("value") or "")[:40])
            if action_type == "click" and prediction.get("click_position"):
                logger.info("  Click target: (%.0f, %.0f)",
                            *prediction["click_position"])
            if action_type == "keyboard":
                src_idx  = prediction.get("source_elem_idx", -1)
                src_conf = prediction.get("_source_conf", 0.0)
                src_elem = elements[src_idx] if 0 <= src_idx < len(elements) else None
                src_text = (src_elem.get("value") or src_elem.get("text",""))[:60] if src_elem else "?"
                src_role = src_elem.get("window_role","?") if src_elem else "?"
                logger.info("  Source ptr: elem[%d] conf=%.3f  role=%s  text=%r",
                            src_idx, src_conf, src_role, src_text)

        # 3. Resolve text for keyboard actions
        if action_type == "keyboard":
            src_idx = prediction.get("source_elem_idx", -1)
            text = resolver.resolve(state, src_idx)

            # --data_file override: if UIA match failed, try the loaded record dict
            if not text and record_data:
                elements  = state.get("elements", [])
                focused_id = state.get("focused_element_id")
                focused   = next((e for e in elements if e.get("element_id") == focused_id), None)
                if focused:
                    # Try label, text, automation_id as key candidates
                    for key_attr in ("label", "text", "automation_id"):
                        candidate = (focused.get(key_attr) or "").strip().lower()
                        if candidate and candidate in record_data:
                            rv = record_data[candidate]
                            if rv not in resolver._used_texts:
                                text = rv
                                resolver._used_texts.add(text)
                                logger.info("RecordData → field=%r  value=%r", candidate, text[:60])
                            break
                    # Fuzzy: check if any record key is contained in the field label
                    if not text and focused:
                        field_label = (focused.get("label") or focused.get("text") or "").strip().lower()
                        for rk, rv in record_data.items():
                            if rk in field_label or field_label in rk:
                                if rv not in resolver._used_texts:
                                    text = rv
                                    logger.info("RecordData fuzzy → field=%r key=%r value=%r",
                                                field_label, rk, text[:60])
                                    resolver._used_texts.add(text)
                                    break

            if text:
                prediction["text"] = text
                logger.info("TextResolver → %r", text[:60])
            else:
                logger.info("TextResolver → no match, skipping keyboard")
                prediction["action_type"] = "no_op"

        # 4. Snap clicks to nearest UI element
        if action_type == "click" and prediction.get("click_position"):
            snapped = _snap_to_element(prediction["click_position"], state)
            if snapped:
                prediction["click_position"] = snapped

        # 4b. Block clicks back into the recently-escaped zone
        if blocked_steps > 0:
            blocked_steps -= 1
        if action_type == "click" and blocked_zone and prediction.get("click_position"):
            cx, cy   = blocked_zone
            px, py   = prediction["click_position"]
            dist     = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
            if dist <= _BLOCK_RADIUS:
                logger.warning("Blocked click @ (%.0f,%.0f) — within %dpx of escaped zone (%.0f,%.0f)",
                               px, py, _BLOCK_RADIUS, cx, cy)
                prediction["action_type"] = "no_op"
            elif blocked_steps <= 0:
                blocked_zone = None   # zone expired

        # 5. Execute
        result = executor.execute(prediction)
        logger.info("Result: %s", result)

        # 5b. Stuck-loop detection — if same focused element+value for too long, Tab out
        _focused_id  = state.get("focused_element_id")
        _focused_val = ""
        _focused_bbox = None
        for _e in state.get("elements", []):
            if _e.get("element_id") == _focused_id:
                _focused_val  = (_e.get("value") or "").strip()
                _focused_bbox = _e.get("bbox")
                break
        _cur_state = (_focused_id, _focused_val)
        if _cur_state == stuck_state:
            stuck_count += 1
            if stuck_count >= _STUCK_LIMIT:
                logger.warning("Stuck on %r for %d steps — pressing Tab to advance",
                               _focused_val or _focused_id, stuck_count)
                if not args.dry_run:
                    import pyautogui as _pag
                    _pag.press("tab")
                # Block zone = centroid of recent clicks (model's actual click target,
                # not the UIA bbox which may be on the other side of the form)
                recent_clicks = [
                    h["click_xy"] for h in history[-_STUCK_LIMIT:]
                    if h.get("action_type") == "click" and h.get("click_xy")
                ]
                if recent_clicks:
                    bx = sum(c[0] for c in recent_clicks) / len(recent_clicks)
                    by = sum(c[1] for c in recent_clicks) / len(recent_clicks)
                    blocked_zone  = (bx, by)
                    blocked_steps = _BLOCK_DURATION
                    logger.info("Blocking click zone @ (%.0f, %.0f) for %d steps",
                                bx, by, _BLOCK_DURATION)
                stuck_count = 0
        else:
            stuck_state = _cur_state
            stuck_count = 0

        # 6. Track history
        history.append({
            "state":       state,
            "action_type": action_type,
            "click_xy":    prediction.get("click_position", [0.0, 0.0]),
            "key_count":   prediction.get("key_count", 0),
        })

        # 7. Noop guard — stop if model keeps doing nothing
        if action_type == "no_op":
            noop_run += 1
            if noop_run >= 5:
                logger.warning("5 consecutive no_ops — task likely complete or model stuck.")
                break
        else:
            noop_run = 0

        time.sleep(args.delay)

    logger.info("Transformer agent finished.")


if __name__ == "__main__":
    main()
