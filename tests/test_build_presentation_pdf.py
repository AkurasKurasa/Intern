"""The IMRaD presentation PDF builder (scripts/build_presentation_pdf.py).

Run:  python -m pytest tests/test_build_presentation_pdf.py -q
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import build_presentation_pdf as b  # noqa: E402

MD = """# Title

| Block | Minutes | Slides | Running clock |
|---|---|---|---|
| **I — Introduction** | 20 | 3–11 | 0:03 – 0:23 |
| **R — Results** | 27 | 28–41 | 1:03 – 1:30 |

## I — Introduction (20 min)

3. **The problem.** Work eats
   hours.
30. **Demo.** Launch, then Play.
    - Scope #1: intake file
      to form.
    - Scope #3: inbox.
    **Have a video ready.**
"""


def test_each_imrad_block_becomes_a_coloured_section():
    title, body = b.convert(MD)
    assert title == "Title"
    assert 'class="badge"' in body and ">I</span>" in body


def test_wrapped_lines_join_their_own_bullet_and_item_text_stays_separate():
    """Found building the first PDF: the sentence after slide 30's bullets
    was glued onto the last bullet."""
    _, body = b.convert(MD)
    assert "<li>Scope #1: intake file to form.</li>" in body
    assert "</ul><p><strong>Have a video ready.</strong></p>" in body
    assert "Work eats hours." in body


def test_time_budget_becomes_a_proportional_bar():
    _, body = b.convert(MD)
    assert 'class="timebar"' in body
    assert "flex:20" in body and "flex:27" in body


def test_real_plan_converts_with_all_four_blocks():
    _, body = b.convert(b.SOURCE.read_text(encoding="utf-8"))
    for letter in ("I", "M", "R", "D"):
        assert f">{letter}</span>" in body
    assert "fig_matrix" not in body or "data:image/png" in body


@pytest.mark.slow
def test_build_writes_a_pdf(tmp_path):
    pytest.importorskip("playwright")
    out = b.build(tmp_path / "flow.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20_000
