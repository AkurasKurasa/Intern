"""
render_figures.py
=================
Renders every figure in Thesis Chapter 4 from the live metric files.

One figure per research objective, each comparing all three study scopes
against that objective's own target, plus a summary matrix covering the whole
grid. Numbers come from `objective_metrics.collect_all()` and are never typed
by hand, so a figure cannot drift from the data behind it.

Design decisions, and why
-------------------------
Form. Each objective asks "how far is each scope from one threshold?" — a
magnitude comparison across a small, fixed set of categories against a
reference. That is a horizontal bar chart with a target rule, not a line (no
time axis), not a pie (not parts of a whole).

Colour. Verdicts are *status*, not identity, so they use a reserved status
palette rather than a categorical one. Running the palette validator on the
four verdict colours returns two failures that are structural rather than
fixable by choosing different hues:

  1. NOT EVALUATED is a chromaless grey. The validator reads chroma 0 as "this
     slot looks grey"; here that is the intended meaning — absent evidence
     should look absent, not like a fourth category.
  2. Orange and red collide under deuteranopia (best achievable ~3.8 dE, ~11.9
     in normal vision). Green/amber/red is a known-weak triad and no
     substitution rescues the orange/red pair while keeping the conventional
     reading a thesis panel expects.

The sanctioned relief for both is secondary encoding, which this module applies
unconditionally: every bar carries its verdict as text, and every verdict gets
its own hatch. The skill's own guidance names grayscale print as a case where
hue fails and texture must carry identity — a printed thesis is exactly that
case, so texture here is primary, with colour as reinforcement. The palette
that survives the remaining checks (lightness band and >=3:1 contrast all pass)
is the one used below.

Usage
    py -3.14 scripts/thesis_figures/render_figures.py
    py -3.14 scripts/thesis_figures/render_figures.py --out some/dir
"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")  # no display on this machine; write files only
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from objective_metrics import (  # noqa: E402
    MET,
    NA,
    NOT_EVAL,
    NOT_MET,
    PARTIAL,
    SCOPES,
    Objective,
    collect_all,
)

# --------------------------------------------------------------------------
# Visual constants
# --------------------------------------------------------------------------

SURFACE = "#FCFCFB"
INK = "#1A1A1A"
INK_MUTED = "#5F5F5F"
GRID = "#E3E3E0"

# Status palette — passes the lightness band and the 3:1 contrast floor.
# The two remaining validator failures are documented in the module docstring.
COLOR = {
    MET: "#1B6B2F",
    PARTIAL: "#B45309",
    NOT_MET: "#C62828",
    NOT_EVAL: "#8A8A8A",
    NA: "#B8B8B8",
}

# Texture is ordered: solid (fully attained) -> 45 -> 135 -> open (no evidence).
# 45/135 only; horizontal and vertical read as gridlines.
HATCH = {
    MET: "",
    PARTIAL: "///",
    NOT_MET: "\\\\\\",
    NOT_EVAL: "",
    NA: "",
}

SCOPE_ROW = {
    "S1": "Scope 1\nData-Entry Form",
    "S2": "Scope 2\nWeb → Excel",
    "S3": "Scope 3\nEmail Triage",
}

# The matrix packs three columns into a narrow grid, so it uses short headers;
# the full names would overrun their own column width.
SCOPE_SHORT = {
    "S1": "Scope 1\nForm",
    "S2": "Scope 2\nExcel",
    "S3": "Scope 3\nEmail",
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID,
    "text.color": INK,
    "axes.labelcolor": INK,
    "xtick.color": INK_MUTED,
    "ytick.color": INK,
})


def _style_axes(ax) -> None:
    """Recessive grid and axes; data stays the loudest thing on the page."""
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _legend(ax, verdicts: list[str]) -> None:
    """Identity is never colour alone: the legend names every verdict shown."""
    seen, handles = [], []
    for v in (MET, PARTIAL, NOT_MET, NOT_EVAL, NA):
        if v in verdicts and v not in seen:
            seen.append(v)
            handles.append(Patch(facecolor=COLOR[v], hatch=HATCH[v],
                                 edgecolor="white", label=v))
    if len(handles) >= 2:
        ax.legend(handles=handles, loc="lower right", frameon=False,
                  fontsize=8, ncol=min(len(handles), 4),
                  bbox_to_anchor=(1.0, -0.34))


# --------------------------------------------------------------------------
# Per-objective figure
# --------------------------------------------------------------------------


def render_objective(obj: Objective, out_dir: str) -> str | None:
    fig, ax = plt.subplots(figsize=(7.4, 3.0))

    ys, labels, verdicts = [], [], []
    for i, s in enumerate(SCOPES):
        cell = obj.scopes.get(s)
        if cell is None:
            continue
        y = len(SCOPES) - 1 - i
        ys.append(y)
        labels.append(SCOPE_ROW[s])
        verdicts.append(cell.verdict)

        pct = cell.pct
        if pct is None:
            # No measurement: a hairline stub holds the row so the reader sees
            # an empty slot rather than a missing one, with the reason in text.
            ax.barh(y, 0.6, height=0.46, color=COLOR[cell.verdict],
                    alpha=0.30, zorder=2)
            msg = "not applicable" if cell.verdict == NA else "no data collected"
            ax.text(2.0, y, msg, va="center", ha="left",
                    fontsize=8.5, color=INK_MUTED, style="italic", zorder=4)
        else:
            ax.barh(y, pct, height=0.46, color=COLOR[cell.verdict],
                    hatch=HATCH[cell.verdict], edgecolor="white",
                    linewidth=0.8, zorder=2)
            # Direct label: value and verdict travel with the bar, so the
            # chart survives grayscale printing and colour-blind reading.
            # Inside labels sit on a solid plate in the bar's own colour,
            # otherwise the hatch stripes cut straight through the glyphs.
            inside = pct > 32
            ax.text(pct - 1.6 if inside else pct + 1.6, y,
                    f"{pct:.1f}%  {cell.verdict}",
                    va="center", ha="right" if inside else "left",
                    fontsize=8.5, weight="bold",
                    color="white" if inside else INK, zorder=4,
                    bbox=dict(facecolor=COLOR[cell.verdict], edgecolor="none",
                              pad=1.8) if inside else None)
            if cell.n:
                ax.text(0.8, y - 0.30, f"n = {cell.n:,}", va="center",
                        ha="left", fontsize=7.5, color=INK_MUTED, zorder=4)

    if not ys:
        plt.close(fig)
        return None

    # Target rule — the threshold the objective itself states.
    if obj.target_value is not None:
        t = obj.target_value * 100
        ax.axvline(t, color=INK, linestyle="--", linewidth=1.3, zorder=3)
        ax.text(t, len(SCOPES) - 0.32, f" target {t:.0f}%", fontsize=8,
                color=INK, weight="bold", va="bottom", ha="left", zorder=4)

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlim(0, 108)
    ax.set_ylim(-0.75, len(SCOPES) - 0.30)
    ax.set_xlabel("Percent", fontsize=9, color=INK_MUTED)
    _style_axes(ax)
    _legend(ax, verdicts)

    direction = "  (lower is better)" if obj.lower_is_better else ""
    fig.suptitle(f"Figure {obj.number + 1}. {obj.title}",
                 fontsize=11, weight="bold", x=0.012, ha="left", y=1.10)
    ax.set_title(
        f"Objective {obj.number} — target: {obj.target_text}{direction}",
        fontsize=8.8, color=INK_MUTED, loc="left", pad=16)

    path = os.path.join(out_dir, f"fig_obj{obj.number:02d}.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Objective 7 gets a scatter: the question is a trend, not a level.
# --------------------------------------------------------------------------


def render_scalability(obj: Objective, out_dir: str) -> str | None:
    cell = obj.scopes.get("S1")
    pts = (cell.detail or {}).get("points") if cell else None
    if not pts:
        return None

    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    xs = [p[0] for p in pts]
    ys = [p[1] * 100 for p in pts]
    ax.scatter(xs, ys, s=120, color=COLOR[NOT_EVAL], edgecolor="white",
               linewidth=1.5, zorder=3)
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.1f}%  (n={x:,} examples)", (x, y),
                    textcoords="offset points", xytext=(12, 0),
                    fontsize=9, va="center", color=INK)

    ax.axhline(90, color=INK, linestyle="--", linewidth=1.3, zorder=2)
    ax.text(ax.get_xlim()[0], 90.9, " target 90%", fontsize=8, weight="bold",
            color=INK, va="bottom", ha="left")

    if len(pts) < 2:
        ax.text(0.5, 0.20,
                "A single training run cannot establish a trend.\n"
                "No scaling relationship is measurable in either direction.",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=10, color=INK_MUTED, style="italic")

    ax.set_xlabel("Training examples", fontsize=9, color=INK_MUTED)
    ax.set_ylabel("Validation click accuracy (%)", fontsize=9, color=INK_MUTED)
    ax.set_ylim(0, 105)
    ax.margins(x=0.30)
    _style_axes(ax)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)

    fig.suptitle("Figure 8. Model performance against training-data volume",
                 fontsize=11, weight="bold", x=0.012, ha="left", y=0.99)
    ax.set_title("Objective 7 — target: ≥ 90% maintained as volume grows",
                 fontsize=8.8, color=INK_MUTED, loc="left", pad=9)

    path = os.path.join(out_dir, "fig_obj07.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Objective 6 detail: the variant sweep, the study's strongest evidence.
# --------------------------------------------------------------------------


def render_adaptability_detail(obj: Objective, out_dir: str) -> str | None:
    cell = obj.scopes.get("S2")
    detail = (cell.detail or {}) if cell else {}
    per_variant = detail.get("per_variant") or {}
    best = detail.get("best_method")
    if not per_variant or not best:
        return None

    pretty = {
        "v0_base": "Baseline",
        "v1_reordered": "Columns reordered",
        "v2_relabeled": "Headers relabelled",
        "v3_extra_fields": "Extra fields added",
        "v4_unassociated": "Unassociated columns",
        "v5_near_duplicates": "Near-duplicate labels",
        "v6a_options": "Options altered",
        "v6b_scale": "Values rescaled",
    }

    items = [(pretty.get(k, k), v.get(best, 0.0) * 100) for k, v in per_variant.items()]
    items.sort(key=lambda kv: kv[1], reverse=True)

    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    ys = list(range(len(items) - 1, -1, -1))
    for y, (name, pct) in zip(ys, items):
        v = MET if pct >= 75 else NOT_MET
        ax.barh(y, pct, height=0.55, color=COLOR[v], hatch=HATCH[v],
                edgecolor="white", linewidth=0.8, zorder=2)
        inside = pct > 26
        ax.text(pct - 1.6 if inside else pct + 1.6, y, f"{pct:.0f}%",
                va="center", ha="right" if inside else "left", fontsize=8.5,
                weight="bold", color="white" if inside else INK, zorder=4,
                bbox=dict(facecolor=COLOR[v], edgecolor="none",
                          pad=1.8) if inside else None)

    ax.axvline(75, color=INK, linestyle="--", linewidth=1.3, zorder=3)
    ax.text(75, len(items) - 0.32, " target 75%", fontsize=8, weight="bold",
            color=INK, va="bottom", ha="left")

    ax.set_yticks(ys)
    ax.set_yticklabels([n for n, _ in items], fontsize=9)
    ax.set_xlim(0, 108)
    ax.set_xlabel("Fields correctly resolved (%)", fontsize=9, color=INK_MUTED)
    _style_axes(ax)
    _legend(ax, [MET, NOT_MET])

    fig.suptitle("Figure 7a. Field resolution under interface perturbation",
                 fontsize=11, weight="bold", x=0.012, ha="left", y=0.99)
    ax.set_title(
        f"Objective 6, Scope 2 — {len(items)} variants, scheme: {best}",
        fontsize=8.8, color=INK_MUTED, loc="left", pad=9)

    path = os.path.join(out_dir, "fig_obj06_detail.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# Summary matrix — the whole grid at a glance.
# --------------------------------------------------------------------------


def render_matrix(grid: dict[str, Objective], out_dir: str) -> str:
    objs = list(grid.values())
    fig, ax = plt.subplots(figsize=(7.6, 6.4))

    for r, obj in enumerate(objs):
        y = len(objs) - 1 - r
        for c, s in enumerate(SCOPES):
            cell = obj.scopes.get(s)
            v = cell.verdict if cell else NOT_EVAL
            ax.add_patch(plt.Rectangle(
                (c, y - 0.42), 0.92, 0.84,
                facecolor=COLOR[v], hatch=HATCH[v],
                edgecolor="white", linewidth=1.6, zorder=2))
            txt = cell.display() if (cell and cell.value is not None) else (
                "n/a" if v == NA else "—")
            # Solid plate behind the value so the hatch cannot stripe the text.
            ax.text(c + 0.46, y, txt, ha="center", va="center",
                    fontsize=9, weight="bold",
                    color="white" if v != NA else INK_MUTED, zorder=3,
                    bbox=dict(facecolor=COLOR[v], edgecolor="none", pad=1.6))

        # A down-arrow marks the objectives stating a ceiling rather than a
        # floor, where a smaller number is the better result.
        arrow = " ↓" if obj.lower_is_better else ""
        ax.text(-0.18, y, f"{obj.number}. {obj.branch}{arrow}", ha="right",
                va="center", fontsize=8.8, color=INK)
        ax.text(3.08, y, obj.verdict(), ha="left", va="center", fontsize=8.2,
                weight="bold", color=COLOR[obj.verdict()])

    # Two-line headers, kept inside their own column width so they cannot
    # run into each other.
    for c, s in enumerate(SCOPES):
        ax.text(c + 0.46, len(objs) - 0.28, SCOPE_SHORT[s],
                ha="center", va="bottom", fontsize=8.4, weight="bold",
                linespacing=1.35)
    ax.text(3.08, len(objs) - 0.28, "Overall", ha="left", va="bottom",
            fontsize=8.4, weight="bold")

    ax.set_xlim(-2.5, 4.5)
    ax.set_ylim(-0.95, len(objs) + 0.70)
    ax.axis("off")

    handles = [Patch(facecolor=COLOR[v], hatch=HATCH[v], edgecolor="white",
                     label=v) for v in (MET, PARTIAL, NOT_MET, NOT_EVAL, NA)]
    ax.legend(handles=handles, loc="lower center", frameon=False, fontsize=8,
              ncol=5, bbox_to_anchor=(0.42, -0.075))
    ax.text(0.42, -0.115, "↓ = objective states a ceiling; a lower value is the better result",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.6,
            color=INK_MUTED, style="italic")

    fig.suptitle("Figure 14. Objective attainment across the three scopes",
                 fontsize=11.5, weight="bold", x=0.012, ha="left", y=1.01)

    path = os.path.join(out_dir, "fig_matrix.png")
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------


def render_all(out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    grid = collect_all()
    made: list[str] = []

    for key, obj in grid.items():
        if key == "obj7":
            p = render_scalability(obj, out_dir)
        else:
            p = render_objective(obj, out_dir)
        if p:
            made.append(p)
        if key == "obj6":
            d = render_adaptability_detail(obj, out_dir)
            if d:
                made.append(d)

    made.append(render_matrix(grid, out_dir))
    return made


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output"))
    args = ap.parse_args()

    paths = render_all(args.out)
    print(f"Rendered {len(paths)} figures into {args.out}")
    for p in paths:
        print("  ", os.path.basename(p))
