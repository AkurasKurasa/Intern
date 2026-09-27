"""The floating Agent window, fed a real Scope #2 run.

Found by the user: the Agent window showed NOTHING during a Scope #2 run,
though the run printed all 400 SCOPE2HUD lines and Electron forwarded them.
s2Init() threw "abst is not defined" on the very first (init) message -- a
variable removed when refusals were merged into one list, still read by one
line further down. A syntax check cannot see that; only running the page
can. So these tests run it.

The payloads are built by executor/runner.py's own hud_payload() and Hud, the
exact code that prints them during a run.

Run:  python -m pytest tests/test_agent_hud_page.py -q
"""

import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HUD = REPO / "app_electron" / "renderer" / "agent-hud.html"
sys.path.insert(0, str(REPO / "components" / "scope2"))

from executor.runner import Hud, hud_payload  # noqa: E402

FAKE_BRIDGE = """
window.__h = {};
window.agentHud = {
  onDecision: cb => window.__h.decision = cb,
  onScope2:   cb => window.__h.scope2 = cb,
  onReset:    cb => window.__h.reset = cb,
  onFinish:   cb => window.__h.finish = cb,
};
"""

# The shape automate.py sends after the source -> LLM change: tagged
# assignments, and a refusal as a field plus the LLM's reason.
MAPPING = {
    "assignments": [
        {"source_header": "YEAR LEVEL", "target_label": "Year 1-5", "via": "source"},
        {"source_header": "FINAL GRADE", "target_label": "Grade 0-100", "via": "source"},
        {"source_header": "PROGRAM", "target_label": "Course", "via": "llm"},
    ],
    "abstained": [{"source_header": None, "target_label": "Recommendations optional",
                   "reason": "refused: PROGRAM went to Course"}],
    "derived_rules": [{"field": "Remarks", "kind": "threshold",
                       "depends_on_field": "Grade 0-100", "operator": ">=", "cutoff": 75,
                       "if_true": "Passed", "if_false": "Failed",
                       "observed_interval": [74, 85], "status": "confirmed"}],
    "unmapped_fields": ["Recommendations optional"],
}


def run_lines(rows=3):
    """What the executor prints for a small run, parsed like main.js does."""
    buf, original = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        hud = Hud(None, enabled=True)
        hud.install(hud_payload(MAPPING, "v0_base", False, rows))
        hud.stage("Filling rows")
        for i in range(rows):
            hud.row(i, f"2021-1000{i}")
            hud.cell("Course", "BS Computer Science")
            hud.row_done(True)
        hud.finish("Done", f"Saved {rows} rows.")
    finally:
        sys.stdout = original
    return [json.loads(l[len("SCOPE2HUD "):]) for l in buf.getvalue().splitlines()
            if l.startswith("SCOPE2HUD ")]


@pytest.fixture()
def hud_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 268, "height": 600})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(FAKE_BRIDGE)
        page.goto(HUD.as_uri())
        page.errors = errors
        yield page
        browser.close()


def feed(page, lines):
    for d in lines:
        page.evaluate("d => window.__h.scope2(d)", d)


def test_a_scope2_run_renders_without_an_error(hud_page):
    feed(hud_page, run_lines())
    assert hud_page.errors == []


def test_every_mapping_is_shown_with_the_tier_that_decided_it(hud_page):
    feed(hud_page, run_lines())
    tags = hud_page.eval_on_selector_all("#s2MapRows .via", "els => els.map(e => e.textContent)")
    assert tags == ["source", "source", "LLM"]


def test_a_refused_field_is_listed_once_with_the_llm_reason(hud_page):
    feed(hud_page, run_lines())
    text = hud_page.inner_text("#s2RefRows")
    assert text.count("Recommendations optional") == 1
    assert "PROGRAM went to Course" in text


def test_overflowing_content_scrolls_with_the_wheel_and_the_header_stays(hud_page):
    """Reported: content past the bottom edge could not be read. `.hud` said
    overflow-y: auto and then overflow: hidden in the same rule -- the later
    one won, so it clipped. Overfill the 600 px window, then wheel it."""
    long = dict(MAPPING, unmapped_fields=[f"Extra field {i}" for i in range(25)])
    buf, original = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        Hud(None, enabled=True).install(hud_payload(long, "v3_extra_fields", False, 50))
    finally:
        sys.stdout = original
    init = json.loads(buf.getvalue().split("SCOPE2HUD ", 1)[1].splitlines()[0])
    feed(hud_page, [init])

    sizes = hud_page.evaluate("() => { const h = document.querySelector('.hud');"
                              " return [h.scrollHeight, h.clientHeight]; }")
    assert sizes[0] > sizes[1], "the test must actually overflow the window"

    hud_page.mouse.move(130, 400)
    hud_page.mouse.wheel(0, 600)
    hud_page.wait_for_timeout(300)
    assert hud_page.evaluate("() => document.querySelector('.hud').scrollTop") > 0
    # the header is pinned: still at the top of the panel after scrolling
    top = hud_page.evaluate("() => document.querySelector('.hd').getBoundingClientRect().top")
    assert top < 10
    assert hud_page.errors == []


def test_the_rule_and_the_row_count_are_shown(hud_page):
    feed(hud_page, run_lines(rows=3))
    assert "Remarks" in hud_page.inner_text("#s2RuleRows")
    assert hud_page.inner_text("#s2Ok") == "3"
    assert hud_page.inner_text("#s2Total") == "3"
