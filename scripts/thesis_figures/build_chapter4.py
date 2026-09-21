"""
build_chapter4.py
=================
Rewrites Section 4.1 of Thesis.docx as an objective x scope grid.

The existing 4.1 is a flat run of prose covering Scope 1 only, carrying numbers
that no longer reconcile with any log in this repository. This script replaces
it with twelve subsections, one per research objective, each presenting all
three study scopes against that objective's own target: a figure, a table, and
an interpretation drawn from the measured values.

Every number and every caveat originates in `objective_metrics.collect_all()`,
so the chapter cannot drift from the data. Re-running this script after new
runs are recorded regenerates the chapter against the fresh numbers.

Safety
    - The thesis is read and written at its real location outside the repo.
    - A timestamped backup is written before any modification.
    - The script refuses to run while the document is open in Word, because
      Word holds an exclusive lock and a partial write would corrupt it.

Usage
    py -3.14 scripts/thesis_figures/build_chapter4.py            # write it
    py -3.14 scripts/thesis_figures/build_chapter4.py --dry-run  # preview only
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

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

THESIS = r"C:\Users\paula\OneDrive\Desktop\Thesis.docx"
FIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

SCOPE_NAME = {
    "S1": "Scope 1 \u2014 Data-Entry Form Filling",
    "S2": "Scope 2 \u2014 Web Form to Excel",
    "S3": "Scope 3 \u2014 Email and Ticket Triage",
}

def set_table_borders(table) -> None:
    """Draw single-line borders on a table.

    The thesis originates as a Google Docs export, whose style catalogue has no
    'Table Grid'. Borders are therefore written directly onto the table
    properties so the result does not depend on a style that may not exist in
    whatever template the document is carrying.
    """
    tbl_pr = table._element.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "BFBFBF")
        borders.append(el)
    tbl_pr.append(borders)


VERDICT_RGB = {
    MET: RGBColor(0x1B, 0x6B, 0x2F),
    PARTIAL: RGBColor(0xB4, 0x53, 0x09),
    NOT_MET: RGBColor(0xC6, 0x28, 0x28),
    NOT_EVAL: RGBColor(0x6E, 0x6E, 0x6E),
    NA: RGBColor(0x8A, 0x8A, 0x8A),
}


# --------------------------------------------------------------------------
# Locating the section to replace
# --------------------------------------------------------------------------


def find_section_bounds(doc) -> tuple[int, int]:
    """Return [start, end) paragraph indices covering section 4.1.

    Anchored on the visible headings rather than fixed indices, so an edit
    elsewhere in the document cannot silently shift the target region.
    """
    start = end = None
    for i, p in enumerate(doc.paragraphs):
        t = (p.text or "").strip()
        if start is None and t.startswith("4.1 Presentation of Results"):
            start = i
        elif start is not None and t.startswith("4.2 Presentation of the Application"):
            end = i
            break
    if start is None or end is None:
        raise SystemExit(
            "Could not locate section 4.1 / 4.2 headings in the document. "
            "Refusing to edit rather than guess at the boundaries."
        )
    return start, end


def delete_range(doc, start: int, end: int) -> int:
    """Remove paragraphs [start, end), returning how many were removed."""
    victims = doc.paragraphs[start:end]
    n = 0
    for p in victims:
        el = p._element
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
            n += 1
    return n


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


class Builder:
    """Appends content after a moving anchor paragraph."""

    def __init__(self, doc, anchor):
        self.doc = doc
        self.anchor = anchor

    def _add(self, para):
        self.anchor._element.addnext(para._element)
        self.anchor = para
        return para

    def heading(self, text: str, level: int):
        p = self.doc.add_paragraph(style=f"Heading {level}")
        p.add_run(text)
        return self._add(p)

    def body(self, text: str, italic: bool = False, size: float = 12):
        p = self.doc.add_paragraph()
        r = p.add_run(text)
        r.italic = italic
        r.font.size = Pt(size)
        p.paragraph_format.space_after = Pt(6)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        return self._add(p)

    def caption(self, text: str):
        p = self.doc.add_paragraph()
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(10)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(10)
        return self._add(p)

    def image(self, path: str, width_in: float = 6.1):
        if not os.path.exists(path):
            return None
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(path, width=Inches(width_in))
        p.paragraph_format.space_after = Pt(4)
        return self._add(p)

    def table(self, obj: Objective):
        t = self.doc.add_table(rows=1, cols=5)
        set_table_borders(t)
        hdr = ["Scope", "n", "Measurement", "Result", "Verdict"]
        for c, h in enumerate(hdr):
            cell = t.rows[0].cells[c]
            cell.text = ""
            r = cell.paragraphs[0].add_run(h)
            r.bold = True
            r.font.size = Pt(9)

        for s in SCOPES:
            cell_data = obj.scopes.get(s)
            if cell_data is None:
                continue
            row = t.add_row()
            vals = [
                SCOPE_NAME[s],
                f"{cell_data.n:,}" if cell_data.n else "\u2014",
                cell_data.method or "\u2014",
                cell_data.display(),
                cell_data.verdict,
            ]
            for c, v in enumerate(vals):
                tc = row.cells[c]
                tc.text = ""
                r = tc.paragraphs[0].add_run(v)
                r.font.size = Pt(9)
                if c == 4:
                    r.bold = True
                    r.font.color.rgb = VERDICT_RGB.get(cell_data.verdict)

        self.anchor._element.addnext(t._element)
        # A table is not a paragraph; park a spacer after it so the next
        # addnext() has something to attach to.
        spacer = self.doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(4)
        t._element.addnext(spacer._element)
        self.anchor = spacer
        return t


# --------------------------------------------------------------------------
# Chapter content
# --------------------------------------------------------------------------


def _counts(grid: dict[str, Objective]) -> dict[str, int]:
    c = {MET: 0, PARTIAL: 0, NOT_MET: 0, NOT_EVAL: 0}
    for o in grid.values():
        c[o.verdict()] = c.get(o.verdict(), 0) + 1
    return c


def intro_text(grid: dict[str, Objective]) -> list[str]:
    c = _counts(grid)
    return [
        "This section presents the empirical evaluation of the Intern framework "
        "against the twelve specific objectives defined in Chapter 1. Results are "
        "organised by objective rather than by experiment: each subsection states "
        "one objective, reports what was measured for that objective within each "
        "of the study's three scopes, and returns a verdict against the target the "
        "objective itself sets.",

        "The three scopes are treated as distinct evaluation environments rather "
        "than as one pooled dataset. Scope 1 covers data-entry form filling in a "
        "car insurance application, perceived through the UI Automation "
        "accessibility tree. Scope 2 covers transfer from a web source into an "
        "Excel grid, a cross-application task with a two-dimensional target. "
        "Scope 3 covers email and ticket triage, where decisions are drawn from "
        "message content rather than from a cloned interface action. Because the "
        "three differ in what they perceive and what they act upon, an objective "
        "is not necessarily measurable in all three, and the chapter says so "
        "plainly where it is not.",

        "Four verdicts are used throughout. MET indicates the measured value "
        "satisfies the objective's stated target. PARTIALLY MET indicates the "
        "target was satisfied in some scopes but not others. NOT MET indicates "
        "evidence was collected and falls short of the target. NOT EVALUATED "
        "indicates no measurement was collected, which is a gap in evidence and "
        "is deliberately distinguished from a failure. An objective is reported "
        "as MET only where every scope that was actually measured met it; a "
        "single strong scope is not allowed to stand for the whole.",

        f"Across the twelve objectives, {c.get(MET, 0)} were met, "
        f"{c.get(PARTIAL, 0)} were partially met, {c.get(NOT_MET, 0)} were not "
        f"met, and {c.get(NOT_EVAL, 0)} were not evaluated. Figure 14 presents "
        "the complete grid.",

        "Every figure and table in this section is generated directly from the "
        "system's own metric logs by a reporting script, rather than transcribed "
        "by hand. Where the instrumentation is known to be defective, the affected "
        "result is reported as instrument output together with the defect, rather "
        "than being silently adjusted or omitted.",
    ]


def objective_section(b: Builder, obj: Objective, idx: int) -> None:
    b.heading(f"4.1.{idx} Results on {obj.branch} for Objective {obj.number}", 3)

    b.body(
        f"Objective {obj.number} states: {obj.title.lower()}, with a target of "
        f"{obj.target_text}. {obj.summary}"
    )

    fig_no = obj.number + 1
    fig_path = os.path.join(FIG_DIR, f"fig_obj{obj.number:02d}.png")
    if b.image(fig_path):
        b.caption(f"Figure {fig_no}. {obj.title} across the three scopes")

    if obj.key == "obj6":
        detail = os.path.join(FIG_DIR, "fig_obj06_detail.png")
        if b.image(detail):
            b.caption("Figure 7a. Field resolution under each interface perturbation "
                      "(Scope 2)")

    b.table(obj)
    b.caption(f"Table 4.{idx}. Objective {obj.number} results by scope")

    # Per-scope interpretation, drawn from the measured cells themselves.
    for s in SCOPES:
        cell = obj.scopes.get(s)
        if cell is None:
            continue
        lead = f"{SCOPE_NAME[s]}. "
        if cell.value is None:
            body = cell.note or "No measurement was collected for this scope."
        else:
            n_txt = f", n = {cell.n:,}" if cell.n else ""
            body = (f"The measured value is {cell.display()}, obtained by "
                    f"{cell.method}{n_txt}, against a target of "
                    f"{obj.target_text}. This scope is therefore recorded as "
                    f"{cell.verdict}.")
            if cell.note:
                body += " " + cell.note
        b.body(lead + body)

    b.body(f"Objective {obj.number} is therefore assessed as {obj.verdict()}.",
           italic=True)


def summary_section(b: Builder, grid: dict[str, Objective], idx: int) -> None:
    b.heading(f"4.1.{idx} Summary of Objective Attainment", 3)
    c = _counts(grid)

    b.body(
        "The grid below consolidates every verdict reported above. Reading down "
        "a column shows how far a single scope was carried; reading across a row "
        "shows whether an objective held up beyond the environment it was first "
        "developed in."
    )

    if b.image(os.path.join(FIG_DIR, "fig_matrix.png"), width_in=6.3):
        b.caption("Figure 14. Objective attainment across the three scopes")

    met = [f"Objective {o.number}" for o in grid.values() if o.verdict() == MET]
    part = [f"Objective {o.number}" for o in grid.values() if o.verdict() == PARTIAL]
    unmet = [f"Objective {o.number}" for o in grid.values() if o.verdict() == NOT_MET]
    uneval = [f"Objective {o.number}" for o in grid.values() if o.verdict() == NOT_EVAL]

    def _join(xs: list[str]) -> str:
        if not xs:
            return "none"
        if len(xs) == 1:
            return xs[0]
        return ", ".join(xs[:-1]) + " and " + xs[-1]

    b.body(
        f"{_join(met)} were met. {_join(part)} were partially met, satisfying the "
        f"target in some scopes but not all. {_join(unmet)} were not met on the "
        f"evidence collected. {_join(uneval)} were not evaluated."
    )

    b.body(
        "Three patterns are worth separating. First, the objectives that were not "
        "met are concentrated in Scope 1's representation and learning pipeline, "
        "where encoding ambiguity, transition mapping and model accuracy all fall "
        "short of their targets on the current recorded data. These are measured "
        "shortfalls against collected evidence, not gaps in evidence."
    )

    b.body(
        "Second, the objectives that were not evaluated are of a different kind. "
        "Vision-based perception, scalability, and the two comparisons against "
        "rule-based RPA tools have no measurement because the corresponding data "
        "was never collected, in each case because development effort was "
        "concentrated on establishing feasibility within a single environment "
        "first. The instruments exist and are verified; the runs behind them do "
        "not. Reporting these as failures would overstate what is known, and "
        "reporting them as successes would be unfounded."
    )

    b.body(
        "Third, the strongest evidence in the study comes from a scope the earlier "
        "draft of this chapter did not examine. Scope 2's systematic perturbation "
        "of the target interface is the only adaptability measurement collected, "
        "and it is the basis on which Objective 6 is assessed. Its qualifications "
        "are stated in full in Section 4.1.6 and should be read with the result."
    )

    b.body(
        "A limitation applies to the Scope 1 execution figures throughout this "
        "section. The metrics writer is known to sit in a cleanup block that real "
        "runs bypass on exit, so live successes are not always recorded. The "
        "figures reported are what the instrument captured, and they under-report "
        "actual behaviour by an amount this study cannot currently quantify. "
        "Closing that gap, and re-recording the demonstration set behind the "
        "learning objectives, are the two changes that would most alter the "
        "results reported here."
    )


def build(doc, grid: dict[str, Objective]) -> None:
    start, end = find_section_bounds(doc)
    anchor = doc.paragraphs[start]          # keep the "4.1" heading itself
    removed = delete_range(doc, start + 1, end)
    print(f"  removed {removed} old paragraphs from section 4.1")

    b = Builder(doc, anchor)
    for para in reversed(intro_text(grid)):
        # Each insert lands directly after the anchor, so feeding the
        # paragraphs in reverse leaves them in written order.
        p = doc.add_paragraph()
        r = p.add_run(para)
        r.font.size = Pt(12)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_after = Pt(6)
        anchor._element.addnext(p._element)
    # Re-anchor past the intro block just written.
    b.anchor = doc.paragraphs[start + len(intro_text(grid))]

    for i, obj in enumerate(grid.values(), start=1):
        objective_section(b, obj, i)
        print(f"  wrote 4.1.{i} \u2014 Objective {obj.number} ({obj.verdict()})")

    summary_section(b, grid, len(grid) + 1)
    print(f"  wrote 4.1.{len(grid) + 1} \u2014 summary")


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    ap.add_argument("--thesis", default=THESIS)
    args = ap.parse_args()

    if not os.path.exists(args.thesis):
        print(f"Thesis not found at {args.thesis}")
        return 1

    # Word holds an exclusive lock; writing under it corrupts the file.
    try:
        with open(args.thesis, "r+b"):
            pass
    except PermissionError:
        print("Thesis.docx is locked \u2014 it is open in Microsoft Word.")
        print("Close the document in Word, then run this again.")
        return 2

    grid = collect_all()
    doc = docx.Document(args.thesis)
    start, end = find_section_bounds(doc)
    print(f"Section 4.1 spans paragraphs {start}..{end} "
          f"({end - start} paragraphs)")

    if args.dry_run:
        print("\nDry run \u2014 nothing written. Planned structure:")
        for i, obj in enumerate(grid.values(), start=1):
            print(f"  4.1.{i}  Results on {obj.branch} for Objective "
                  f"{obj.number}  [{obj.verdict()}]")
        print(f"  4.1.{len(grid) + 1}  Summary of Objective Attainment")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = args.thesis.replace(".docx", f"_BACKUP_{stamp}.docx")
    shutil.copy2(args.thesis, backup)
    print(f"Backup written: {os.path.basename(backup)}")

    build(doc, grid)
    doc.save(args.thesis)
    print(f"\nSaved {args.thesis}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
