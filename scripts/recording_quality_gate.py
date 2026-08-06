"""
scripts/recording_quality_gate.py
====================================
The thing to run WHILE recording the ~50k-step end-to-end dataset, not just
after. Checks the questions that actually determine whether the time spent
recording pays off:

  1. Transition mapping   — does each recorded action visibly do something?
                             (reuses validate_transitions.py)
  2. Encoding ambiguity   — can every click target be uniquely identified?
                             (reuses encoding_ambiguity.py)
  3. Below-fold reach     — NOT a scroll-action count. Scrolling is the
                             system's job (Tab-triggered auto-reveal), not
                             something the recorder needs to demonstrate with
                             the mouse wheel — corrected 2026-08-06 after this
                             script wrongly flagged "NO SCROLL" on every
                             session. What actually matters is whether Tab
                             navigation kept going far enough to reach every
                             field in a section, scrolled-into-view or not —
                             measured here as per-tab field coverage.
  4. Tab-order consistency— sessions visiting tabs in a consistent left-to-
                             right order, vs. jumping/revisiting (the exact
                             documented cause of the model's tab-order gap).
  5. End-to-end coverage  — did the session actually reach Submit, and how
                             many of the 8 tabs did it touch? A session that
                             stops halfway teaches half a task.

Run this every few sessions, not once at the end — catching "you're not
recording scroll" after 5,000 steps costs a lot less than after 50,000.

Usage
-----
  python scripts/recording_quality_gate.py                  # all sessions
  python scripts/recording_quality_gate.py --since-minutes 60  # just-recorded batch
  python scripts/recording_quality_gate.py --log             # append trend to jsonl
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_transitions import validate_session, _load_session_steps          # noqa: E402
from encoding_ambiguity import state_ambiguity                                  # noqa: E402
from bc_fidelity import _LABEL_TO_KEY, _tab_of                                  # noqa: E402
from collections import Counter, defaultdict                                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = ROOT / "tasks" / "form_filling" / "traces"
LOG_PATH   = ROOT / "data" / "output" / "recording_quality_log.jsonl"

# The form's fixed tab layout (Tab 1..8 per data_entry_intake.txt) — this is
# describing the FORM for a QA report, not a decision the agent makes, so it's
# not the kind of hardcoding CLAUDE.md's "let the Transformer/Agent work"
# rule is about.
CANONICAL_TAB_ORDER = [
    "Policy", "Policyholder", "Vehicle", "Coverage",
    "Driver 1", "Driver 2", "Driver 3", "Claims", "Payment",
]

# Per-tab known field counts, derived from bc_fidelity's own label→key map —
# the only tabs it covers are Policy/Policyholder/Vehicle. Used as the
# denominator for below-fold reach: what fraction of a tab's known fields did
# the session actually touch? A session that stops at the fields visible
# without scrolling will show low coverage on the longer tabs (Policyholder,
# Vehicle) even though it never needed to scroll the mouse wheel to prove it.
_TAB_FIELD_COUNTS: Counter = Counter(_tab_of(k) for k in _LABEL_TO_KEY.values())


def _tab_of_state(state: dict) -> str:
    """
    NOTE: tabitemcontrol elements in these captures are NOT the form's own tab
    strip — they're unrelated UI (e.g. Notepad/toolbar icons picked up from a
    background window: labels like "fork-and-knife.svg. Unmodified."). There's
    also no reliable "selected" flag on them. The agent itself doesn't use a
    selected-tab flag either — it infers the active tab from pane geometry.
    Cheaper and more reliable here: which known FIELD LABELS are visible right
    now (bc_fidelity._LABEL_TO_KEY is the same mapping already trusted to
    score submissions) — majority vote across all non-background elements.
    """
    votes: Counter = Counter()
    for e in state.get("elements", []):
        if e.get("window_role") == "background":
            continue
        label = (e.get("label") or e.get("text") or "").strip().lower()
        key = _LABEL_TO_KEY.get(label)
        if key:
            votes[_tab_of(key)] += 1
    if not votes:
        return ""
    return votes.most_common(1)[0][0]


def _canonical_index(tab_name: str) -> int | None:
    tl = tab_name.lower()
    for i, t in enumerate(CANONICAL_TAB_ORDER):
        if t.lower() in tl or tl in t.lower():
            return i
    return None


def _clicked_element(state: dict, pos) -> dict | None:
    """
    Smallest-area bbox containing the click point, not the first match in
    array order. Elements overlap (window > panel > button), and the window
    is typically listed first — taking the first match silently attributed
    every click inside the form to "Car Insurance ... Data Entry Form"
    instead of the actual button clicked, so submit_reached never fired even
    when the user demonstrably clicked Submit. Found 2026-08-06.
    """
    if not pos or len(pos) < 2:
        return None
    x, y = pos
    best, best_area = None, None
    for e in state.get("elements", []):
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
            if best_area is None or area < best_area:
                best, best_area = e, area
    return best


def check_session(session_dir: Path) -> dict:
    steps = _load_session_steps(session_dir)
    n = len(steps)

    submit_reached = False
    tabs_visited_order: list[str] = []
    seen_tabs: set[str] = set()
    fields_touched: dict[str, set[str]] = defaultdict(set)

    for i, step in enumerate(steps):
        action = step.get("action", {})
        a_type = action.get("action_type", "noop")
        state = step.get("state", {})
        elems = state.get("elements", [])

        tab = _tab_of_state(state)
        if tab and tab not in seen_tabs:
            seen_tabs.add(tab)
            tabs_visited_order.append(tab)

        if a_type in ("click", "keyboard", "type"):
            fid = state.get("focused_element_id", "")
            label = next((
                (e.get("label") or e.get("text") or "").strip().lower()
                for e in elems if e.get("element_id") == fid
            ), "")
            key = _LABEL_TO_KEY.get(label)
            if key:
                fields_touched[_tab_of(key)].add(key)

        if a_type == "click":
            el = _clicked_element(state, action.get("click_position"))
            label = ((el or {}).get("label") or (el or {}).get("text") or "")
            if "submit" in label.lower():
                submit_reached = True

    # tab-order consistency: is the first-visit sequence non-decreasing in
    # canonical index (skips allowed, backward jumps/revisits are not)?
    indices = [i for i in (_canonical_index(t) for t in tabs_visited_order) if i is not None]
    order_consistent = all(b >= a for a, b in zip(indices, indices[1:])) if len(indices) > 1 else True

    # Below-fold reach: for tabs bc_fidelity actually has a known field count
    # for (Policy/Policyholder/Vehicle), what fraction of that tab's fields
    # got touched? Low coverage on a visited tab means navigation stopped
    # short of fields that would only become visible further down.
    tab_coverage: dict[str, float] = {}
    for tab in seen_tabs:
        total = _TAB_FIELD_COUNTS.get(tab, 0)
        if total:
            tab_coverage[tab] = len(fields_touched.get(tab, set())) / total
    low_coverage_tabs = [t for t, c in tab_coverage.items() if c < 0.5]

    return {
        "session": session_dir.name,
        "total_steps": n,
        "tabs_visited": tabs_visited_order,
        "tabs_covered": len(seen_tabs),
        "tab_order_consistent": order_consistent,
        "submit_reached": submit_reached,
        "tab_coverage": tab_coverage,
        "low_coverage_tabs": low_coverage_tabs,
    }


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Quality gate for the recording campaign — run periodically, not just at the end.")
    ap.add_argument("--dir", default=None,
                     help="Directory to scan for session_* folders (default: tasks/form_filling/traces, "
                          "the curated training location). Point this at data/demos/<name>/ to check "
                          "freshly recorded sessions before they've been copied into the training dir.")
    ap.add_argument("--since-minutes", type=float, default=None,
                     help="Only check sessions modified in the last N minutes (the batch you just recorded).")
    ap.add_argument("--log", action="store_true", help="Append the aggregate result to a trend log.")
    args = ap.parse_args()

    scan_dir = Path(args.dir) if args.dir else TRACES_DIR
    if not scan_dir.is_absolute():
        scan_dir = ROOT / scan_dir
    sessions = sorted(d for d in scan_dir.glob("*/") if d.is_dir() and d.name.startswith("session_"))
    if args.since_minutes is not None:
        cutoff = time.time() - args.since_minutes * 60
        sessions = [s for s in sessions if s.stat().st_mtime >= cutoff]

    if not sessions:
        print("No sessions found" + (f" in the last {args.since_minutes} min." if args.since_minutes else "."))
        sys.exit(1)

    print(f"\n{'='*100}")
    print(f"  RECORDING QUALITY GATE — {len(sessions)} session(s)")
    print(f"{'='*100}")

    coverage_rows = []
    total_steps = 0
    sessions_order_ok = 0
    sessions_submitted = 0
    sessions_full_field_coverage = 0
    tab_coverage_sum = 0

    for sd in sessions:
        c = check_session(sd)
        coverage_rows.append(c)
        total_steps += c["total_steps"]
        sessions_order_ok += c["tab_order_consistent"]
        sessions_submitted += c["submit_reached"]
        tab_coverage_sum += c["tabs_covered"]
        sessions_full_field_coverage += (not c["low_coverage_tabs"])

        flags = []
        if not c["tab_order_consistent"]:
            flags.append("TAB ORDER JUMPS")
        if not c["submit_reached"]:
            flags.append("DIDN'T SUBMIT")
        if c["low_coverage_tabs"]:
            flags.append("BELOW-FOLD FIELDS MISSED: " + ", ".join(c["low_coverage_tabs"]))
        flag_str = ("  [" + ", ".join(flags) + "]") if flags else "  [ok]"
        cov_str = ", ".join(f"{t}={c['tab_coverage'][t]*100:.0f}%" for t in c["tab_coverage"]) or "n/a"
        print(f"  {c['session']:<28} {c['total_steps']:>5} steps  "
              f"{c['tabs_covered']}/8 tabs  field-coverage: {cov_str}{flag_str}")

    n_sessions = len(sessions)
    print(f"  {'-'*96}")
    print(f"  Total recorded steps this batch: {total_steps}")
    print(f"  Sessions with consistent tab order: {sessions_order_ok}/{n_sessions} "
          f"({sessions_order_ok/n_sessions*100:.0f}%)  ← the documented cause of tab-jumping")
    print(f"  Sessions that reached Submit:      {sessions_submitted}/{n_sessions} "
          f"({sessions_submitted/n_sessions*100:.0f}%)  ← incomplete passes teach incomplete behavior")
    print(f"  Sessions with full below-fold reach: {sessions_full_field_coverage}/{n_sessions} "
          f"({sessions_full_field_coverage/n_sessions*100:.0f}%)  ← Tab nav reached every known field on "
          f"Policy/Policyholder/Vehicle, not just what's visible without scrolling")
    print(f"  Avg tabs covered per session:      {tab_coverage_sum/n_sessions:.1f}/8")

    # Reuse the existing per-session checks for mapping correctness + ambiguity
    print(f"\n  Transition mapping & encoding checks (this batch):")
    total_actionable = total_correct = total_amb = total_interactive = 0
    for sd in sessions:
        tv = validate_session(sd)
        total_actionable += tv["actionable_transitions"]
        total_correct    += tv["correct_transitions"]
    steps_data = None
    for sd in sessions:
        for f in sorted(sd.glob("*.json")):
            if f.name == "session_manifest.json":
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            amb, tot = state_ambiguity(d.get("state", d))
            total_amb += amb
            total_interactive += tot

    map_acc = total_correct / total_actionable if total_actionable else 1.0
    amb_rate = total_amb / total_interactive if total_interactive else 0.0
    print(f"    Transition mapping accuracy: {map_acc*100:.1f}%  (target >=90%)")
    print(f"    Encoding ambiguity rate:     {amb_rate*100:.2f}%  (target <5%)")

    print(f"\n  VERDICT: ", end="")
    problems = []
    if sessions_full_field_coverage / n_sessions < 0.5:
        problems.append("some tabs are being under-filled — keep tabbing until every field in a "
                         "section is reached, even the ones off-screen (the app scrolls for you)")
    if sessions_order_ok / n_sessions < 0.8:
        problems.append("keep tab order strictly left-to-right")
    if sessions_submitted / n_sessions < 0.8:
        problems.append("finish each pass through Submit, don't stop partway")
    if map_acc < 0.90:
        problems.append("some recorded actions have no visible effect (junk clicks?)")
    if amb_rate > 0.05:
        problems.append("too many fields share the same label+type — check the form/labels")
    if problems:
        print("keep going, but fix these before recording much more:")
        for p in problems:
            print(f"    - {p}")
    else:
        print("this batch looks good — keep recording the same way.")
    print(f"{'='*100}\n")

    if args.log:
        import datetime as _dt
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _dt.datetime.now().isoformat(),
            "sessions_checked": n_sessions,
            "total_steps": total_steps,
            "below_fold_field_coverage": sessions_full_field_coverage / n_sessions,
            "tab_order_consistency": sessions_order_ok / n_sessions,
            "submit_rate": sessions_submitted / n_sessions,
            "transition_mapping_accuracy": round(map_acc, 4),
            "encoding_ambiguity_rate": round(amb_rate, 4),
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"Logged to {LOG_PATH} — trend visible across your recording session.")


if __name__ == "__main__":
    main()
