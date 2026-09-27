"""
render_obj4_paired.py
=====================
Figure 6 for Chapter 4, section 4.1.4 (Objective 4, Adaptability): task
success on the original interface (Condition A) beside task success on
modified interfaces (Condition B), per scope.

Scope 2 is computed from the real variant sweep in
components/scope2/data/runs/eval.json. The unmodified baseline (v0_base) is
Condition A; the seven modified variants are Condition B. A task counts as
successful only when every field in it was resolved (strict task success, as
defined for TSR in the evaluation metrics).

Scope 1 has no Condition A measurement and its Condition B value is a
practice placeholder, so the chart labels both states explicitly rather than
drawing them as measured results. Scope 3 was not evaluated under
Condition B.

Usage
    python scripts/thesis_figures/render_obj4_paired.py
"""
from __future__ import annotations

import json
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
EVAL = os.path.join(REPO, "components", "scope2", "data", "runs", "eval.json")
OUT = os.path.join(HERE, "output", "fig06_obj4_conditions.png")

S1_PRACTICE_TSR_B = 0.903      # practice placeholder, not a measurement
TARGET = 0.75
SCHEME = "matcher-noPos"       # best-performing matching scheme in the sweep
LABEL_X = 104                  # value labels sit in a column right of the axis


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def scope2_conditions() -> dict:
    ev = json.load(open(EVAL, encoding="utf8"))
    full = {v: m[SCHEME]["mapped_correct"] == m[SCHEME]["mapped_total"] for v, m in ev.items()}
    base = [v for v in ev if v == "v0_base"]
    mod = [v for v in ev if v != "v0_base"]
    return {
        "A": (sum(full[v] for v in base), len(base)),
        "B": (sum(full[v] for v in mod), len(mod)),
    }


def main() -> str:
    s2 = scope2_conditions()

    ink, muted, grid = "#1A1A1A", "#5F5F5F", "#DDDDDD"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, ax = plt.subplots(figsize=(8.2, 4.4))

    rows = ["Scope 1\nData-Entry Form", "Scope 2\nWeb → Excel", "Scope 3\nEmail Triage"]
    centres = [2.0, 1.0, 0.0]
    h = 0.30
    style_a = dict(facecolor="#D9D9D9", edgecolor=ink, linewidth=1.0)
    style_b = dict(facecolor="white", edgecolor=ink, linewidth=1.0)

    def bar(y, value, style, label, k=None, n=None):
        ax.barh(y, value * 100, height=h, zorder=2, **style)
        if k is not None and n:
            lo, hi = wilson(k, n)
            ax.errorbar(value * 100, y, xerr=[[value * 100 - lo * 100], [hi * 100 - value * 100]],
                        fmt="none", ecolor=ink, elinewidth=1.0, capsize=3, zorder=3)
        ax.text(LABEL_X, y, label, va="center", ha="left", fontsize=8.8, color=ink, zorder=4)

    def empty(y, label):
        ax.text(1.0, y, label, va="center", ha="left", fontsize=8.8, color=muted,
                style="italic", zorder=4)

    # Scope 1
    empty(centres[0] + h / 2, "Condition A: not measured")
    bar(centres[0] - h / 2, S1_PRACTICE_TSR_B, style_b, "90.3%  MET  (practice value)")

    # Scope 2 (real)
    ka, na = s2["A"]
    kb, nb = s2["B"]
    bar(centres[1] + h / 2, ka / na, style_a, f"{ka / na * 100:.1f}%  (n = {na} task)", ka, na)
    verdict = "MET" if kb / nb >= TARGET else "NOT MET"
    bar(centres[1] - h / 2, kb / nb, dict(style_b, hatch="////"),
        f"{kb / nb * 100:.1f}%  {verdict}  (n = {nb} tasks)", kb, nb)
    gap = (ka / na - kb / nb) * 100
    rows[1] = f"Scope 2\nWeb → Excel\nGG = {gap:.1f} pp"

    # Scope 3
    empty(centres[2] + h / 2, "Condition A: not evaluated")
    empty(centres[2] - h / 2, "Condition B: not evaluated")

    ax.axvline(TARGET * 100, color=ink, linestyle="--", linewidth=1.2, zorder=1)
    ax.text(TARGET * 100, 2.5, " target 75% (Condition B)", fontsize=8.3, weight="bold",
            va="bottom", ha="left", color=ink)

    ax.set_yticks(centres)
    ax.set_yticklabels(rows, fontsize=9.5)
    ax.set_xlim(0, 150)
    ax.set_xticks(range(0, 101, 20))
    ax.set_xticklabels([f"{t}%" for t in range(0, 101, 20)])
    ax.set_ylim(-0.6, 2.75)
    ax.set_xlabel("Task success rate", fontsize=9, color=muted)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_bounds(0, 100)
    ax.xaxis.grid(True, color=grid, linewidth=0.8, zorder=0)
    ax.tick_params(length=0)

    handles = [Patch(label="Condition A: original interface", **style_a),
               Patch(label="Condition B: modified interface", **style_b),
               Patch(label="Condition B, target not met", hatch="////", **style_b)]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.40, -0.26), ncol=3,
              frameon=False, fontsize=8.2)
    ax.text(0.40, -0.34, "Whiskers show 95% Wilson confidence intervals. GG = Generalization Gap (A − B).",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.8, color=muted, style="italic")
    ax.set_title("Task success on original vs. modified interfaces", fontsize=11.5,
                 weight="bold", loc="left", pad=22)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return OUT


if __name__ == "__main__":
    print(main())
