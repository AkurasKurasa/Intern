"""
objective_metrics.py
====================
The single source of truth for every number printed in Thesis Chapter 4.

Chapter 4 is organised as a grid: twelve research objectives down, three study
scopes across. This module computes every cell of that grid by reading the
*live* metric files the system writes during real runs. Nothing here is typed
by hand, so a figure can never drift from the data that produced it.

Scopes
    S1  Data-entry form filling  (car insurance form, UIA perception)
    S2  Web form -> Excel        (cross-application transfer)
    S3  Email / ticket triage    (inbox router)

Every cell resolves to a `Cell`, which carries the measured value, the sample
size, the method that produced it, and a verdict against the objective's own
target. A cell with no collected data is NOT_EVALUATED, which is deliberately
distinct from NOT_MET: one means the evidence is absent, the other means the
evidence is present and falls short.

Usage
    from objective_metrics import collect_all
    grid = collect_all()
    print(grid["obj6"].scopes["S2"].value)

    # or, from the command line, dump the whole grid as JSON:
    py -3.14 scripts/thesis_figures/objective_metrics.py
"""
from __future__ import annotations

import glob
import json
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

# --------------------------------------------------------------------------
# Paths — resolved relative to the repo root so this runs from anywhere.
# --------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))

P_BC_PROGRESS = os.path.join(REPO, "data", "output", "bc_progress.jsonl")
P_RUN_METRICS = os.path.join(REPO, "data", "output", "run_metrics.jsonl")
P_TRAIN_LOG = os.path.join(REPO, "data", "output", "transformer_training_log.jsonl")
P_ENCODING = os.path.join(REPO, "data", "output", "encoding_ambiguity_log.jsonl")
P_TRANSITION = os.path.join(REPO, "data", "output", "transition_validation_log.jsonl")

P_S2_RUNS = os.path.join(REPO, "components", "scope2", "data", "runs", "automate_*.json")
P_S2_EVAL = os.path.join(REPO, "components", "scope2", "data", "runs", "eval.json")
P_S3_INBOX = os.path.join(REPO, "components", "inbox_router", "data", "runs", "automate_inbox_*.json")
P_S3_COLD = os.path.join(REPO, "components", "inbox_router", "data", "runs", "automate_cold_email_*.json")

SCOPES = ("S1", "S2", "S3")

SCOPE_LABELS = {
    "S1": "Scope 1\nData-Entry Form",
    "S2": "Scope 2\nWeb → Excel",
    "S3": "Scope 3\nEmail Triage",
}

# Verdicts ------------------------------------------------------------------

MET = "MET"
PARTIAL = "PARTIALLY MET"
NOT_MET = "NOT MET"
NOT_EVAL = "NOT EVALUATED"
NA = "NOT APPLICABLE"

VERDICT_COLORS = {
    MET: "#2E7D32",
    PARTIAL: "#F9A825",
    NOT_MET: "#C62828",
    NOT_EVAL: "#9E9E9E",
    NA: "#CFD8DC",
}


# --------------------------------------------------------------------------
# Data containers
# --------------------------------------------------------------------------


@dataclass
class Cell:
    """One objective measured within one scope."""

    scope: str
    value: float | None = None          # 0..1 where the metric is a rate
    n: int | None = None                # sample size behind `value`
    unit: str = "rate"                  # "rate" | "count" | "seconds"
    method: str = ""                    # how it was measured, for the table
    verdict: str = NOT_EVAL
    note: str = ""                      # caveat printed under the table
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def pct(self) -> float | None:
        return None if self.value is None else self.value * 100.0

    def display(self) -> str:
        if self.value is None:
            return "—"
        if self.unit == "rate":
            return f"{self.value * 100:.1f}%"
        if self.unit == "count":
            return f"{self.value:,.0f}"
        return f"{self.value:,.2f}"


@dataclass
class Objective:
    """One research objective across all three scopes."""

    key: str
    number: int
    branch: str                 # Task Tree branch name
    title: str
    target_text: str
    target_value: float | None  # 0..1, None when the objective states no number
    scopes: dict[str, Cell] = field(default_factory=dict)
    summary: str = ""
    # Some objectives state a ceiling (ambiguity, error rate) rather than a
    # floor. On those a longer bar is a worse result, which a reader will
    # misread unless the figure says so outright.
    lower_is_better: bool = False

    def verdict(self) -> str:
        """Objective-level verdict across the scopes.

        A rollup that let the best-performing scope stand for the objective
        would report Objective 8 as MET on the strength of one scope while two
        others fall short. Scored cells are therefore combined honestly: the
        objective is MET only when every scope that was actually measured met
        it, and mixed evidence is reported as mixed.
        """
        scored = [c.verdict for c in self.scopes.values() if c.verdict in (MET, NOT_MET, PARTIAL)]
        if not scored:
            return NOT_EVAL
        if all(v == MET for v in scored):
            return MET
        if all(v == NOT_MET for v in scored):
            return NOT_MET
        return PARTIAL

    def measured_scopes(self) -> list[str]:
        """Scopes carrying a scored result, in fixed order."""
        return [s for s in SCOPES if s in self.scopes and self.scopes[s].verdict in (MET, NOT_MET, PARTIAL)]


# --------------------------------------------------------------------------
# Loaders — every one tolerates a missing or half-written file.
# --------------------------------------------------------------------------


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a run killed mid-write leaves a torn last line
    return rows


def _read_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _read_glob(pattern: str) -> list[tuple[str, Any]]:
    out = []
    for p in sorted(glob.glob(pattern)):
        d = _read_json(p)
        if d is not None:
            out.append((p, d))
    return out


def _verdict(value: float | None, target: float | None, higher_is_better: bool = True) -> str:
    if value is None or target is None:
        return NOT_EVAL
    if higher_is_better:
        return MET if value >= target else NOT_MET
    return MET if value <= target else NOT_MET


# --------------------------------------------------------------------------
# Scope 1 measurements
# --------------------------------------------------------------------------


def s1_encoding_ambiguity() -> Cell:
    """Objective 2 — key-collision / ambiguity rate over recorded GUI states."""
    rows = _read_jsonl(P_ENCODING)
    if not rows:
        return Cell("S1", method="encoding_ambiguity.py", note="No encoding audit has been run.")
    last = rows[-1]
    rate = last.get("overall_ambiguity_rate")
    return Cell(
        scope="S1",
        value=rate,
        n=last.get("sessions_checked"),
        method="encoding_ambiguity.py over recorded sessions",
        verdict=_verdict(rate, 0.05, higher_is_better=False),
        note="Target is an upper bound: ambiguity must stay below 5%.",
        detail={"timestamp": last.get("timestamp"), "meets_target": last.get("meets_5pct_target")},
    )


def s1_transition_correctness() -> Cell:
    """Objective 4 — share of interactions resolved to a specific element."""
    rows = _read_jsonl(P_TRANSITION)
    if not rows:
        return Cell("S1", method="validate_transitions.py", note="No transition audit has been run.")
    last = rows[-1]
    acc = last.get("overall_mapping_accuracy")
    return Cell(
        scope="S1",
        value=acc,
        n=last.get("sessions_validated"),
        method="validate_transitions.py over recorded sessions",
        verdict=_verdict(acc, 0.90),
        detail={"timestamp": last.get("timestamp"), "meets_target": last.get("meets_90pct_target")},
    )


def _real_training_runs() -> list[dict]:
    """Training rows from genuine campaigns, not pytest fixtures.

    The training log is shared with the test suite, which writes tiny
    synthetic runs (n_train=4, a temp pytest trace_dir). Those are not
    results and must never reach a figure.
    """
    rows = _read_jsonl(P_TRAIN_LOG)
    real = []
    for r in rows:
        td = str(r.get("trace_dir", ""))
        n_train = r.get("n_train") or 0
        if "pytest" in td.lower() or "temp" in td.lower():
            continue
        if n_train < 50:          # a real demonstration set is never this small
            continue
        real.append(r)
    return real


def s1_model_accuracy() -> Cell:
    """Objectives 3 and 5 — validation click accuracy of the trained policy."""
    real = _real_training_runs()
    if not real:
        return Cell("S1", method="transformer_training_log.jsonl", note="No non-test training run is logged.")
    best = max(real, key=lambda r: r.get("best_val_click_acc") or 0.0)
    acc = best.get("best_val_click_acc")
    return Cell(
        scope="S1",
        value=acc,
        n=best.get("n_train"),
        method="best validation click accuracy, real training runs",
        verdict=_verdict(acc, 0.90),
        note=(
            f"{len(real)} genuine training run(s) logged; "
            f"{len(_read_jsonl(P_TRAIN_LOG)) - len(real)} further rows are "
            "test-suite fixtures and are excluded."
        ),
        detail={
            "runs": [
                {
                    "timestamp": r.get("timestamp"),
                    "n_train": r.get("n_train"),
                    "n_val": r.get("n_val"),
                    "epochs": r.get("epochs"),
                    "val_acc": r.get("best_val_acc"),
                    "val_click_acc": r.get("best_val_click_acc"),
                }
                for r in real
            ]
        },
    )


def s1_scalability() -> Cell:
    """Objective 7 — does accuracy hold up as the demonstration set grows?"""
    real = _real_training_runs()
    pts = [
        (r.get("n_train"), r.get("best_val_click_acc"))
        for r in real
        if r.get("n_train") and r.get("best_val_click_acc") is not None
    ]
    if len(pts) < 2:
        # Deliberately no value: a single point is not a scaling measurement,
        # and printing one invites it to be read as though it were. The point
        # still travels in `detail` so the figure can plot what exists.
        return Cell(
            "S1",
            value=None,
            n=len(pts),
            method="validation accuracy vs. training-set size",
            verdict=NOT_EVAL,
            note=(
                "A scaling trend requires at least two real training runs at "
                f"different data volumes; {len(pts)} is logged, so no trend can "
                "be measured in either direction."
            ),
            detail={"points": pts},
        )
    accs = [a for _, a in pts]
    return Cell(
        "S1",
        value=min(accs),
        n=len(pts),
        method="validation accuracy vs. training-set size",
        verdict=_verdict(min(accs), 0.90),
        note="Objective requires performance to be maintained as volume grows.",
        detail={"points": pts},
    )


def s1_end_to_end() -> Cell:
    """Objective 8 — autonomous task completion on the insurance form."""
    rows = _read_jsonl(P_BC_PROGRESS)
    metrics = _read_jsonl(P_RUN_METRICS)
    if not rows and not metrics:
        return Cell("S1", method="bc_progress.jsonl / run_metrics.jsonl", note="No run data.")
    completed = sum(1 for r in rows if r.get("completed"))
    logged_complete = sum(1 for r in metrics if (r.get("task_completion_rate") or 0) > 0)
    n = len(rows)
    rate = (completed / n) if n else None
    return Cell(
        "S1",
        value=rate,
        n=n,
        method="share of recorded runs reaching submission",
        verdict=_verdict(rate, 0.85),
        note=(
            "Known instrumentation defect (evaluation_metrics_pipeline_gap): the "
            "metrics writer sits in a finally: block that real runs bypass on exit, "
            f"so live successes go unrecorded. Only {logged_complete} of "
            f"{len(metrics)} run_metrics rows carry a non-zero completion. These "
            "figures therefore under-report true performance and are reported as "
            "instrument output, not as system capability."
        ),
        detail={"completed": completed, "run_metrics_rows": len(metrics), "run_metrics_nonzero": logged_complete},
    )


def s1_field_correctness() -> Cell:
    """Supporting evidence for Objective 8 — were the fields actually right?"""
    rows = _read_jsonl(P_BC_PROGRESS)
    vals = [r.get("field_match_rate") for r in rows if isinstance(r.get("field_match_rate"), (int, float))]
    if not vals:
        return Cell("S1", method="bc_fidelity.py field match rate")
    return Cell(
        "S1",
        value=statistics.mean(vals),
        n=len(vals),
        method="mean field match rate across recorded runs",
        verdict=NOT_EVAL,
        note="Reported as context for completion, not scored against a target of its own.",
        detail={"max": max(vals), "min": min(vals)},
    )


def s1_execution_quality() -> Cell:
    """Objective 9 — execution error rate and redundant-step rate."""
    metrics = _read_jsonl(P_RUN_METRICS)
    usable = [r for r in metrics if (r.get("execution_success_rate") or 0) > 0]
    if not usable:
        return Cell(
            "S1",
            n=len(metrics),
            method="run_metrics.jsonl execution_success_rate",
            verdict=NOT_EVAL,
            note=(
                f"All {len(metrics)} recorded rows carry a zero execution-success "
                "rate, a consequence of the same metrics-pipeline defect affecting "
                "Objective 8. No usable execution-error measurement exists."
            ),
        )
    err = 1.0 - statistics.mean([r["execution_success_rate"] for r in usable])
    return Cell(
        "S1",
        value=err,
        n=len(usable),
        method="1 - mean execution success rate",
        verdict=_verdict(err, 0.10, higher_is_better=False),
    )


# --------------------------------------------------------------------------
# Scope 2 measurements
# --------------------------------------------------------------------------


def s2_write_fidelity() -> Cell:
    """Objective 8/9 support — did each written cell read back as written?"""
    runs = _read_glob(P_S2_RUNS)
    if not runs:
        return Cell("S2", method="scope2 automate runs", note="No Scope 2 runs recorded.")
    cells = ok = rows = esc = 0
    for _, d in runs:
        for r in d.get("rows", []):
            rows += 1
            esc += len(r.get("escalations") or [])
            filled = r.get("filled") or {}
            verified = r.get("verified") or {}
            for k, v in filled.items():
                cells += 1
                if str(verified.get(k, "")).strip() == str(v).strip():
                    ok += 1
    rate = (ok / cells) if cells else None
    return Cell(
        "S2",
        value=rate,
        n=cells,
        method="written value vs. read-back value, per cell",
        verdict=_verdict(rate, 0.85),
        note=(
            "This compares what the agent wrote against what it then read back "
            "from the sheet. It measures write fidelity — that an intended value "
            "landed intact — and NOT correctness against the source record. A "
            "value transcribed wrongly but written cleanly still scores as a match."
        ),
        detail={"rows": rows, "runs": len(runs), "escalations": esc, "matched": ok},
    )


def s2_adaptability() -> Cell:
    """Objective 6 — field resolution under eight interface perturbations."""
    ev = _read_json(P_S2_EVAL)
    if not isinstance(ev, dict) or not ev:
        return Cell("S2", method="scope2 eval.json", note="No variant sweep recorded.")
    methods = list(next(iter(ev.values())).keys())
    totals: dict[str, list[int]] = {m: [0, 0] for m in methods}
    per_variant: dict[str, dict[str, float]] = {}
    for variant, by_method in ev.items():
        per_variant[variant] = {}
        for m, s in by_method.items():
            c, t = s.get("mapped_correct", 0), s.get("mapped_total", 0)
            totals[m][0] += c
            totals[m][1] += t
            per_variant[variant][m] = (c / t) if t else 0.0
    best_m = max(totals, key=lambda m: (totals[m][0] / totals[m][1]) if totals[m][1] else 0)
    c, t = totals[best_m]
    rate = (c / t) if t else None

    # Two defensible readings of the same sweep, reported together because
    # they disagree. Pooling every field decision gives the headline; asking
    # instead how many environments were fully handled gives a harsher answer.
    full = sum(1 for v in per_variant.values() if v.get(best_m, 0.0) >= 0.999)
    n_variants = len(per_variant)

    return Cell(
        "S2",
        value=rate,
        n=t,
        method=f"field resolution pooled across {n_variants} interface variants (scheme: {best_m})",
        verdict=_verdict(rate, 0.75),
        note=(
            "Each variant perturbs the target sheet without changing the task: "
            "reordered columns, relabelled headers, extra fields, unassociated "
            "columns, near-duplicate labels, altered options and rescaled values. "
            f"Three readings qualify this result. First, the sample is small: {t} "
            f"field decisions in total, {t // n_variants} per variant. Second, the "
            f"pooled figure lands on the threshold almost exactly ({c}/{t}), so it "
            "carries no margin and a single decision either way would move the "
            f"verdict. Third, pooling flatters the result: only {full} of "
            f"{n_variants} variants were resolved in full, while the remaining "
            f"{n_variants - full} each left at least one field unresolved. The "
            "objective is met on the pooled reading and should be reported with "
            "that qualification rather than as a clean pass."
        ),
        detail={
            "per_variant": per_variant,
            "totals": {m: totals[m] for m in methods},
            "best_method": best_m,
            "variants_fully_resolved": full,
            "n_variants": n_variants,
        },
    )


def s2_end_to_end() -> Cell:
    """Objective 8 — did each queued row get processed to completion?"""
    runs = _read_glob(P_S2_RUNS)
    if not runs:
        return Cell("S2", method="scope2 automate runs")
    rows = filled = 0
    for _, d in runs:
        for r in d.get("rows", []):
            rows += 1
            if r.get("status") == "filled":
                filled += 1
    rate = (filled / rows) if rows else None

    # Completion and correctness are different questions, and reporting a 100%
    # completion figure without saying so would repeat the exact overstatement
    # this chapter sets out to correct. The write-fidelity measure is folded in
    # here, where the headline number appears, rather than left to stand alone.
    fidelity = s2_write_fidelity()
    fid_txt = ""
    if fidelity.value is not None:
        fid_txt = (
            f" Completion records only that a row was processed, not that its "
            f"values were right. Read-back agreement across the same "
            f"{fidelity.n:,} written cells is {fidelity.display()}, but that "
            f"compares what the agent wrote against what it then read back: it "
            f"confirms a value landed intact, and does NOT establish "
            f"correctness against the source record. A value transcribed "
            f"wrongly but written cleanly still counts as a match. No "
            f"independent check against source truth was performed for this "
            f"scope."
        )

    return Cell(
        "S2",
        value=rate,
        n=rows,
        method="share of queued rows reaching 'filled' status",
        verdict=_verdict(rate, 0.85),
        note=f"Across {len(runs)} recorded automation runs." + fid_txt,
        detail={
            "runs": len(runs),
            "filled": filled,
            "write_fidelity": fidelity.value,
            "write_fidelity_cells": fidelity.n,
        },
    )


def s2_execution_quality() -> Cell:
    """Objective 9 — redundant work, proxied by operator escalations."""
    runs = _read_glob(P_S2_RUNS)
    if not runs:
        return Cell("S2", method="scope2 escalations")
    rows = esc = 0
    for _, d in runs:
        for r in d.get("rows", []):
            rows += 1
            esc += len(r.get("escalations") or [])
    rate = (esc / rows) if rows else None
    return Cell(
        "S2",
        value=rate,
        n=rows,
        method="escalations raised per processed row",
        verdict=_verdict(rate, 0.20, higher_is_better=False),
        note=(
            "An escalation is a row the agent declined to complete unaided. It is "
            "a proxy for redundant or failed execution, not the step-level error "
            "rate the objective specifies."
        ),
        detail={"escalations": esc},
    )


# --------------------------------------------------------------------------
# Scope 3 measurements
# --------------------------------------------------------------------------


def _s3_decisions() -> tuple[dict[tuple, list[str]], int]:
    by_email: dict[tuple, list[str]] = defaultdict(list)
    n_runs = 0
    for _, d in _read_glob(P_S3_INBOX):
        n_runs += 1
        for r in d.get("results", []):
            by_email[(r.get("sender"), r.get("subject"))].append(r.get("decision"))
    return by_email, n_runs


def s3_decision_stability() -> Cell:
    """Objectives 2 and 6 — does the same message get the same decision?"""
    by_email, n_runs = _s3_decisions()
    if not by_email:
        return Cell("S3", method="inbox router runs", note="No Scope 3 runs recorded.")
    total = agree = repeated = 0
    for _, ds in by_email.items():
        if len(ds) < 2:
            continue
        repeated += 1
        total += len(ds)
        agree += Counter(ds).most_common(1)[0][1]
    rate = (agree / total) if total else None
    dist = Counter(d for ds in by_email.values() for d in ds)
    return Cell(
        "S3",
        value=rate,
        n=total,
        method=f"modal-decision agreement over {n_runs} runs, {repeated} repeated messages",
        verdict=_verdict(rate, 0.75),
        note=(
            "Stability measures whether the router reaches the SAME decision for a "
            "message seen repeatedly. It is a consistency measure; because the mock "
            "inbox carries no gold-standard labels, it is not decision accuracy."
        ),
        detail={"runs": n_runs, "unique_messages": len(by_email), "distribution": dict(dist)},
    )


def s3_end_to_end() -> Cell:
    """Objective 8 — share of messages carried to a confirmed outcome."""
    outcomes: Counter = Counter()
    n_runs = 0
    for _, d in _read_glob(P_S3_INBOX):
        n_runs += 1
        for r in d.get("results", []):
            outcomes[str(r.get("outcome"))] += 1
    if not outcomes:
        return Cell("S3", method="inbox router outcomes")
    total = sum(outcomes.values())
    confirmed = sum(v for k, v in outcomes.items() if k.startswith("confirmed"))
    rate = confirmed / total
    return Cell(
        "S3",
        value=rate,
        n=total,
        method="share of handled messages reaching a confirmed outcome",
        verdict=_verdict(rate, 0.85),
        note=(
            "The remainder are deliberate handoffs — messages left pending for a "
            "human to complete — rather than failures. The router is built to defer "
            "when a reply needs genuine human content."
        ),
        detail={"runs": n_runs, "outcomes": dict(outcomes), "confirmed": confirmed},
    )


def s3_execution_quality() -> Cell:
    """Objective 9 — share of handled messages needing a human handoff."""
    outcomes: Counter = Counter()
    for _, d in _read_glob(P_S3_INBOX):
        for r in d.get("results", []):
            outcomes[str(r.get("outcome"))] += 1
    if not outcomes:
        return Cell("S3", method="inbox router outcomes")
    total = sum(outcomes.values())
    pending = sum(v for k, v in outcomes.items() if k.startswith("left pending"))
    rate = pending / total
    return Cell(
        "S3",
        value=rate,
        n=total,
        method="share of messages deferred to a human",
        verdict=_verdict(rate, 0.20, higher_is_better=False),
        note="Deferral is by design, so this is reported descriptively.",
        detail={"pending": pending},
    )


# --------------------------------------------------------------------------
# The grid
# --------------------------------------------------------------------------


def _descriptive(cell: Cell, why: str) -> Cell:
    """Re-cast a scored cell as supporting evidence under a different objective.

    Some measurements inform more than one objective but are only scored
    against the target of the objective they were designed for. Scope 3's
    decision stability, for instance, speaks to representational consistency
    (Objective 2) but is scored against Objective 6's 75% threshold. Carrying
    that verdict across would score it against a criterion it never tested, so
    the value travels and the verdict does not.
    """
    return Cell(
        scope=cell.scope,
        value=cell.value,
        n=cell.n,
        unit=cell.unit,
        method=cell.method,
        verdict=NOT_EVAL,
        note=why + " " + cell.note,
        detail=dict(cell.detail),
    )


def _na(scope: str, why: str) -> Cell:
    return Cell(scope, verdict=NA, note=why)


def _blank(scope: str, why: str) -> Cell:
    return Cell(scope, verdict=NOT_EVAL, note=why)


def collect_all() -> dict[str, Objective]:
    """Compute every cell of the objective x scope grid."""
    objs: list[Objective] = []

    objs.append(Objective(
        key="obj1", number=1, branch="Perception",
        title="Vision-based perception of interface elements",
        target_text="≥ 95% detection accuracy across multiple GUI environments",
        target_value=0.95,
        scopes={
            "S1": _blank("S1", "A vision/OCR observer exists in prototype form and "
                               "scripts/perception_eval.py can score it, but the measurement "
                               "requires a live capture the study reserves for manual "
                               "execution. UIA perception proved sufficient for this scope, "
                               "so the vision path was never put on the measuring stick."),
            "S2": _blank("S2", "Perception is supplied by the Excel object model and the "
                               "browser DOM rather than by vision, so the 95% visual "
                               "detection target has no measured counterpart here."),
            "S3": _na("S3", "Scope 3 consumes structured mail data through an API. It has "
                            "no graphical interface to perceive, so this objective does "
                            "not apply."),
        },
        summary="Not evaluated in any scope. The accessibility tree and structured APIs "
                "met each scope's needs, leaving the vision route unmeasured.",
    ))

    objs.append(Objective(
        key="obj2", number=2, branch="Representation",
        title="Consistent, low-ambiguity encoding of GUI states and actions",
        target_text="< 5% encoding ambiguity",
        target_value=0.05,
        lower_is_better=True,
        scopes={
            "S1": s1_encoding_ambiguity(),
            "S2": _blank("S2", "Scope 2 addresses the same collision problem structurally, by "
                               "resolving a header to a column before writing, but no "
                               "ambiguity audit comparable to Scope 1's has been run."),
            "S3": _descriptive(
                s3_decision_stability(),
                "Reported here as supporting evidence of representational consistency "
                "only. It is not scored against this objective's 5% ambiguity "
                "criterion, which it does not test.",
            ),
        },
        summary="Measured directly in Scope 1 and by proxy in Scope 3.",
    ))

    objs.append(Objective(
        key="obj3", number=3, branch="Learning",
        title="Learning pipeline converting demonstrations into a cloned policy",
        target_text="≥ 90% action prediction accuracy",
        target_value=0.90,
        scopes={
            "S1": s1_model_accuracy(),
            "S2": _blank("S2", "Scope 2 resolves fields through the induced-mapping matcher "
                               "rather than a trained policy; no behavioural cloning model "
                               "was trained for this scope."),
            "S3": _blank("S3", "Scope 3 reaches decisions by classification over message "
                               "content, not by a cloned action policy, so no action "
                               "prediction accuracy exists to report."),
        },
        summary="Measurable in Scope 1 only, where a behavioural cloning policy is trained.",
    ))

    objs.append(Objective(
        key="obj4", number=4, branch="Representation",
        title="Translating observed interactions into state-action pairs",
        target_text="≥ 90% correctness in state transition mapping",
        target_value=0.90,
        scopes={
            "S1": s1_transition_correctness(),
            "S2": _blank("S2", "No transition-mapping audit has been run for Scope 2."),
            "S3": _blank("S3", "Scope 3 records decisions rather than interface transitions."),
        },
        summary="Measured in Scope 1 against every recorded state-action pair.",
    ))

    objs.append(Objective(
        key="obj5", number=5, branch="Learning",
        title="Behavioural cloning model trained on structured state-action traces",
        target_text="≥ 90% action prediction accuracy",
        target_value=0.90,
        scopes={
            "S1": s1_model_accuracy(),
            "S2": _blank("S2", "No behavioural cloning model is trained in Scope 2."),
            "S3": _blank("S3", "No behavioural cloning model is trained in Scope 3."),
        },
        summary="This objective restates Objective 3's criterion against the same trained "
                "model and the same validation data; the two are reported together and "
                "share a single measurement.",
    ))

    objs.append(Objective(
        key="obj6", number=6, branch="Adaptability",
        title="Maintaining execution under interface variation",
        target_text="≥ 75% success on varied / unseen interfaces",
        target_value=0.75,
        scopes={
            "S1": _blank("S1", "scripts/adaptation_proxy_eval.py can perturb recorded states "
                               "to probe layout robustness, but its own documentation is "
                               "explicit that this substitutes for, rather than satisfies, "
                               "a held-out environment test. No such test was run."),
            "S2": s2_adaptability(),
            "S3": s3_decision_stability(),
        },
        summary="Scope 2 supplies the study's principal adaptability evidence through a "
                "systematic eight-variant perturbation of the target interface.",
    ))

    objs.append(Objective(
        key="obj7", number=7, branch="Scalability",
        title="Performance maintained as demonstration volume grows",
        target_text="≥ 90% performance maintained at increasing data volume",
        target_value=0.90,
        scopes={
            "S1": s1_scalability(),
            "S2": _blank("S2", "Scope 2 does not train on accumulated demonstrations, so it "
                               "presents no data-volume scaling curve."),
            "S3": _blank("S3", "Scope 3's per-sender profile accumulates passively and was "
                               "not evaluated at varying volumes."),
        },
        summary="A scaling claim requires several real training runs at different volumes.",
    ))

    objs.append(Objective(
        key="obj8", number=8, branch="Execution and Integration",
        title="Agentic orchestration to end-to-end task completion",
        target_text="≥ 85% end-to-end completion without manual intervention",
        target_value=0.85,
        scopes={
            "S1": s1_end_to_end(),
            "S2": s2_end_to_end(),
            "S3": s3_end_to_end(),
        },
        summary="The only objective with collected evidence in all three scopes, and "
                "consequently the chapter's clearest cross-scope comparison.",
    ))

    objs.append(Objective(
        key="obj9", number=9, branch="Execution and Integration",
        title="Execution mechanism translating predictions into input events",
        target_text="≤ 10% execution error, ≤ 20% redundant steps",
        target_value=0.10,
        lower_is_better=True,
        scopes={
            "S1": s1_execution_quality(),
            "S2": s2_execution_quality(),
            "S3": s3_execution_quality(),
        },
        summary="Each scope measures a different proxy for wasted or failed work; the "
                "three are not directly comparable and are read per scope.",
    ))

    objs.append(Objective(
        key="obj10", number=10, branch="Execution and Integration",
        title="Integration of all components into a unified framework",
        target_text="Qualitative — no numeric target stated",
        target_value=None,
        scopes={
            "S1": Cell("S1", verdict=MET, method="qualitative",
                       note="Every Scope 1 measurement above was taken against the system "
                            "running as an integrated whole — perceiving, encoding, "
                            "predicting and executing — not against isolated components."),
            "S2": Cell("S2", verdict=MET, method="qualitative",
                       note="Scope 2 runs a complete source-to-sheet pipeline: read the web "
                            "source, resolve the mapping, write the cell, read it back."),
            "S3": Cell("S3", verdict=MET, method="qualitative",
                       note="Scope 3 runs classification through to a committed action and a "
                            "confirmed outcome, including real calendar and mail effects."),
        },
        summary="Demonstrated in all three scopes by construction: every reported figure "
                "is the output of an integrated pipeline rather than a component harness.",
    ))

    rpa_note = ("No rule-based RPA tool was implemented or run against these tasks, and no "
                "cognitive-workload study was conducted. scripts/compare_baseline.py is "
                "built and verified against synthetic input, so the analysis is ready, but "
                "it requires baseline runs that only a human operator can perform.")

    objs.append(Objective(
        key="obj11", number=11, branch="Evaluation",
        title="Comparison against traditional rule-based RPA tools",
        target_text="> 10–20% improvement per metric",
        target_value=None,
        scopes={s: _blank(s, rpa_note) for s in SCOPES},
        summary="Not evaluated in any scope. This is an uncollected comparison, not a "
                "negative result.",
    ))

    objs.append(Objective(
        key="obj12", number=12, branch="Evaluation",
        title="Benchmarking with statistical significance against baseline tools",
        target_text="Statistically significant improvement",
        target_value=None,
        scopes={s: _blank(s, rpa_note) for s in SCOPES},
        summary="Restates Objective 11's comparison with a significance requirement; both "
                "depend on the same uncollected baseline data.",
    ))

    return {o.key: o for o in objs}


def to_json(grid: dict[str, Objective]) -> dict:
    return {k: asdict(v) for k, v in grid.items()}


def _fmt(o: Objective) -> str:
    head = f"Obj {o.number:>2} [{o.branch}] {o.title}"
    line = f"{head}\n    target: {o.target_text}\n    verdict: {o.verdict()}"
    for s in SCOPES:
        c = o.scopes.get(s)
        if not c:
            continue
        n = f"n={c.n:,}" if c.n else "n=—"
        line += f"\n      {s}: {c.display():>8}  {n:<12} {c.verdict}"
    return line


if __name__ == "__main__":
    import sys

    # Windows consoles default to cp1252 and choke on the target symbols.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    grid = collect_all()
    if "--json" in sys.argv:
        print(json.dumps(to_json(grid), indent=2, default=str))
    else:
        for o in grid.values():
            print(_fmt(o))
            print()
