"""
scripts/build_presentation_pdf.py
=================================
Builds docs/presentation_flow.pdf -- a printable review copy of the defense
plan in docs/presentation_flow.md, laid out by IMRaD.

The Markdown file stays the single source of truth: edit it, re-run this,
and the PDF follows. The results chart is embedded from
scripts/thesis_figures/output/fig_matrix.png (regenerate it first with
render_figures.py so the chart and the table agree).

Rendered by the Chromium that Playwright already installs (HTML -> PDF), so
no extra PDF library is needed.

    python scripts/build_presentation_pdf.py
"""
from __future__ import annotations

import base64
import html
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "presentation_flow.md"
OUTPUT = ROOT / "docs" / "presentation_flow.pdf"
FIGURE = ROOT / "scripts" / "thesis_figures" / "output" / "fig_matrix.png"

# One colour per IMRaD block; the letter always travels with the colour, so
# the document still reads when printed in greyscale.
BLOCKS = {
    "open": ("", "Opening", "#5A6475"),
    "I": ("I", "Introduction", "#1D5FD1"),
    "M": ("M", "Methods", "#7445C9"),
    "break": ("", "Break", "#9AA3B0"),
    "R": ("R", "Results", "#1E7A46"),
    "D": ("D", "Discussion", "#9A5B00"),
    "qa": ("", "Q&A", "#5A6475"),
}


def block_of(heading: str) -> str | None:
    h = heading.strip()
    for key, prefix in (("I", "I —"), ("M", "M —"), ("R", "R —"), ("D", "D —")):
        if h.startswith(prefix):
            return key
    if h.startswith("0.") or h.lower().startswith("opening"):
        return "open"
    if h.lower().startswith("break"):
        return "break"
    if h.lower().startswith("q&a"):
        return "qa"
    return None


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![*\w])\*([^*]+)\*(?![*\w])", r"<em>\1</em>", t)
    return t


def table_html(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head, body = cells[0], [r for r in cells[2:]]
    out = ["<table><thead><tr>"] + [f"<th>{inline(c)}</th>" for c in head] + ["</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def time_bar(rows: list[str]) -> str:
    """The time-budget table drawn as one proportional bar."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows[2:]]
    parts = []
    for c in cells:
        name = re.sub(r"[*_]", "", c[0])
        try:
            minutes = int(c[1])
        except ValueError:
            continue
        key = block_of(name.replace("Opening", "0.").replace("Open Q&A", "Q&A")) or "qa"
        letter, label, color = BLOCKS[key]
        short = f"{letter} · {label}" if letter else label
        parts.append(
            f'<div class="seg" style="flex:{minutes};background:{color}">'
            f'<span class="seg-label">{html.escape(short)}</span>'
            f'<span class="seg-min">{minutes} min</span></div>')
    return '<div class="timebar">' + "".join(parts) + "</div>"


def figure_html() -> str:
    if not FIGURE.exists():
        return ""
    data = base64.b64encode(FIGURE.read_bytes()).decode("ascii")
    return (f'<figure><img src="data:image/png;base64,{data}" alt="Objective by scope results matrix">'
            '<figcaption>Slide 28: the objective × scope matrix, regenerated from the metric '
            'files on the date above.</figcaption></figure>')


def convert(md: str) -> tuple[str, str]:
    """Returns (title, body html)."""
    lines = md.splitlines()
    title = ""
    out: list[str] = []
    i = 0
    open_section = False
    current_block = None

    def close_section():
        nonlocal open_section
        if open_section:
            out.append("</section>")
            open_section = False

    while i < len(lines):
        line = lines[i]
        s = line.strip()

        if s.startswith("# ") and not title:
            title = s[2:].strip()
            i += 1
            continue
        if s == "---" or not s:
            i += 1
            continue

        if s.startswith("## "):
            close_section()
            heading = s[3:].strip()
            current_block = block_of(heading)
            letter, _, color = BLOCKS.get(current_block, ("", "", "#5A6475"))
            badge = f'<span class="badge" style="background:{color}">{letter}</span>' if letter else ""
            klass = "section block" if letter else "section"
            out.append(f'<section class="{klass}" style="--accent:{color}">'
                       f'<h2>{badge}{inline(heading)}</h2>')
            open_section = True
            i += 1
            continue

        if s.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f'<aside class="note">{inline(" ".join(quote))}</aside>')
            continue

        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            if rows and rows[0].lower().startswith("| block"):
                out.append(time_bar(rows))
            out.append(table_html(rows))
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$", s)
        if m and not line.startswith(" "):
            num, first = m.group(1), m.group(2)
            text, subs, tail = [first], [], []
            sub_indent = None
            i += 1
            while i < len(lines) and lines[i].startswith(" ") and lines[i].strip():
                raw = lines[i]
                indent = len(raw) - len(raw.lstrip())
                t = raw.strip()
                if t.startswith("- "):
                    subs.append(t[2:])
                    sub_indent = indent
                elif subs and indent > sub_indent:
                    subs[-1] += " " + t          # wrapped line of a bullet
                elif subs:
                    tail.append(t)               # item text after its bullets
                else:
                    text.append(t)
                i += 1
            sub_html = ("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in subs) + "</ul>") if subs else ""
            tail_html = f"<p>{inline(' '.join(tail))}</p>" if tail else ""
            out.append(f'<div class="slide"><div class="num">{num}</div>'
                       f'<div class="body"><p>{inline(" ".join(text))}</p>{sub_html}{tail_html}</div></div>')
            if num == "28" and current_block == "R":
                out.append(figure_html())
            continue

        if s.startswith("- "):
            items = []
            while i < len(lines) and (lines[i].strip().startswith("- ") or
                                      (lines[i].startswith("  ") and lines[i].strip() and items)):
                t = lines[i].strip()
                if t.startswith("- "):
                    items.append(t[2:])
                else:
                    items[-1] += " " + t
                i += 1
            out.append("<ul class='plain'>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        # paragraph (possibly multi-line)
        para = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|>|\||-|\d+\.|---)", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        text = " ".join(para)
        if re.fullmatch(r"\*\*[^*]+\*\*", text):
            out.append(f"<h3>{inline(text[2:-2])}</h3>")
        else:
            out.append(f"<p>{inline(text)}</p>")

    close_section()
    return title, "\n".join(out)


CSS = """
@page { size: Letter; margin: 18mm 16mm 18mm 16mm; }
:root { --ink:#18212F; --muted:#5A6475; --rule:#DCE1E7; --paper:#FFFFFF; }
* { box-sizing: border-box; }
body { margin:0; color:var(--ink); background:var(--paper);
  font-family:"Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size:10.5pt; line-height:1.45; }
header.cover { border-bottom:2px solid var(--ink); padding-bottom:10px; margin-bottom:14px; }
header.cover .kicker { color:var(--muted); font-size:9.5pt; }
header.cover h1 { margin:2px 0 4px; font-size:22pt; line-height:1.15; letter-spacing:-0.01em; }
header.cover .meta { color:var(--muted); font-size:9.5pt; }
.imrad { display:flex; gap:6px; margin:10px 0 2px; }
.imrad span { display:flex; align-items:center; gap:6px; font-size:9pt; color:var(--muted); }
.imrad b { display:inline-grid; place-items:center; width:18px; height:18px; border-radius:3px; color:#fff; font-size:9pt; }
p { margin:0 0 6px; }
code { font-family:Consolas, "Courier New", monospace; font-size:9pt; background:#F1F3F6; padding:0 3px; border-radius:2px; }
.section { margin:16px 0 0; }
.section.block { border-left:4px solid var(--accent); padding-left:12px; }
h2 { display:flex; align-items:center; gap:8px; font-size:14pt; margin:0 0 8px; break-after:avoid; }
h3 { font-size:10.5pt; margin:12px 0 6px; color:var(--accent, var(--ink)); break-after:avoid; }
.badge { display:inline-grid; place-items:center; width:24px; height:24px; border-radius:4px; color:#fff; font-size:11pt; }
.slide { display:grid; grid-template-columns:28px 1fr; gap:6px; padding:5px 0; border-top:1px solid var(--rule); break-inside:avoid; }
.slide .num { font-weight:700; color:var(--accent, var(--muted)); font-variant-numeric:tabular-nums; }
.slide ul, ul.plain { margin:2px 0 4px; padding-left:16px; }
.slide li, ul.plain li { margin:1px 0; }
.note { border:1px solid var(--rule); background:#F6F8FA; border-radius:4px; padding:8px 10px; margin:8px 0; font-size:9.5pt; break-inside:avoid; }
table { width:100%; border-collapse:collapse; margin:6px 0 10px; font-size:9pt; break-inside:avoid; }
th, td { text-align:left; padding:4px 6px; border-bottom:1px solid var(--rule); vertical-align:top; }
th { border-bottom:1.5px solid var(--ink); }
.timebar { display:flex; height:40px; border-radius:4px; overflow:hidden; margin:6px 0 8px; }
.seg { color:#fff; padding:4px 5px; display:flex; flex-direction:column; justify-content:center; min-width:0; }
.seg-label { font-size:7.5pt; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.seg-min { font-size:7.5pt; opacity:.9; }
figure { margin:8px 0 10px; break-inside:avoid; text-align:center; }
figure img { max-width:78%; border:1px solid var(--rule); }
figcaption { color:var(--muted); font-size:8.5pt; margin-top:3px; }
"""


def build(output: Path = OUTPUT) -> Path:
    md = SOURCE.read_text(encoding="utf-8")
    title, body = convert(md)
    measured = re.search(r"as run on (\d{4}-\d{2}-\d{2})", md)
    legend = "".join(f'<span><b style="background:{BLOCKS[k][2]}">{BLOCKS[k][0]}</b>{BLOCKS[k][1]}</span>'
                     for k in ("I", "M", "R", "D"))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<header class="cover">
  <div class="kicker">Thesis defense — review copy</div>
  <h1>{inline(title)}</h1>
  <div class="meta">Built {date.today().isoformat()} from docs/presentation_flow.md
  {('· results measured ' + measured.group(1)) if measured else ''}</div>
  <div class="imrad">{legend}</div>
</header>
{body}
</body></html>"""

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.set_content(page, wait_until="load")
        pg.pdf(path=str(output), format="Letter", print_background=True,
               display_header_footer=True,
               header_template="<span></span>",
               footer_template=('<div style="font-size:8px;color:#5A6475;width:100%;'
                                'text-align:center;">Flow of Presentation (IMRaD) — page '
                                '<span class="pageNumber"></span> of <span class="totalPages"></span></div>'),
               margin={"top": "16mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
        browser.close()
    return output


if __name__ == "__main__":
    print(build())
