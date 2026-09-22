"""The live decision HUD (executor/hud.js, driven by executor/runner.Hud).

The HUD exists because Scope #2's most defensible claims - that it ABSTAINS on
a decoy column rather than guessing, and that it DERIVES the pass mark rather
than being told it - were only ever visible as grey text in a terminal, while
the browser the audience actually watches said nothing at all.

Presentation code earns tests here for one reason: it runs inside the same page
the experiment is measured on. The load-bearing assertions below are not that
it looks right, they are that it CANNOT change what the experiment measures:

  * no input/select/textarea anywhere in it, so the Page Scanner cannot pick
    any of it up as a field and the label cascade never sees it
  * no stylesheet in the document, so mocksite/shared/styles.css stays the
    constant the instrument says it is
  * identical run results with the HUD on and off
  * a HUD that throws still leaves the run's own rows filled and verified

Run:  python -m pytest tests/scope2/test_hud.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent / "components" / "scope2"
sys.path.insert(0, str(REPO))

from executor.runner import Hud, hud_payload, run  # noqa: E402
from executor.scanner import CHROMIUM, variant_url  # noqa: E402

MAPPING_PATH = REPO / "data" / "mappings" / "v0_handwritten.json"
SHEET = REPO / "data" / "sheets" / "grade_sheet.xlsx"
HUD_JS = (REPO / "executor" / "hud.js").read_text(encoding="utf-8")


# A resolver output carrying one of each thing the HUD renders: an accepted
# assignment, an abstention, an induced rule, and a field with no source.
SAMPLE_MAPPING = {
    "assignments": [
        {"source_header": "PROGRAM", "target_label": "Course",
         "score": 0.99, "margin": 0.81, "status": "auto"},
        {"source_header": "FINAL GRADE", "target_label": "Grade 0-100",
         "score": 0.95, "margin": 0.60, "status": "auto"},
    ],
    "abstained": [
        {"source_header": "FINAL", "target_label": "Grade 0-100",
         "score": 0.00, "margin": 0.04, "status": "abstain"},
    ],
    "derived_rules": [
        {"field": "Remarks", "kind": "threshold", "depends_on_field": "Grade 0-100",
         "operator": ">=", "cutoff": 75, "if_true": "Passed", "if_false": "Failed",
         "observed_interval": [74, 75], "status": "confirmed"},
    ],
    "unmapped_fields": ["Recommendations optional"],
    "unmapped_columns": ["MIDTERM"],
}


# ------------------------------------------------------------------ units


def test_payload_carries_every_decision_the_resolver_made():
    payload = hud_payload(SAMPLE_MAPPING, "v0_base", dry_run=True, total_rows=50)

    assert payload["variant"] == "v0_base"
    assert payload["dry_run"] is True
    assert payload["total_rows"] == 50
    assert [a["source_header"] for a in payload["assignments"]] == ["PROGRAM", "FINAL GRADE"]
    assert [a["source_header"] for a in payload["abstained"]] == ["FINAL"]
    assert payload["derived_rules"][0]["cutoff"] == 75
    assert payload["unmapped_fields"] == ["Recommendations optional"]


def test_payload_survives_a_mapping_missing_every_optional_key():
    """Hand-written mappings predate `abstained`, so the HUD must render a
    mapping that has no abstentions, no rules and no unmapped fields rather
    than raising on a missing key."""
    payload = hud_payload({}, "v0_base", dry_run=False, total_rows=0)

    assert payload["assignments"] == []
    assert payload["abstained"] == []
    assert payload["derived_rules"] == []
    assert payload["unmapped_fields"] == []


def test_a_disabled_hud_never_touches_the_page():
    """The headless path. If this ever calls into the page, a measurement run
    stops being a measurement of the system alone."""

    class Boom:
        def evaluate(self, *a, **k):
            raise AssertionError("a disabled HUD called into the page")

    hud = Hud(Boom(), enabled=False)
    assert hud.enabled is False
    hud.install({})
    hud.stage("x")
    hud.row(0, "S1")
    hud.cell("Course", "BS IS")
    hud.row_done(True)
    hud.finish("Done", "")


def test_a_hud_that_throws_is_swallowed_not_raised():
    """Presentation must never fail a run whose fills and readbacks all
    succeeded. Every method is best-effort."""

    class Broken:
        def evaluate(self, *a, **k):
            raise RuntimeError("page went away")

    hud = Hud(Broken(), enabled=True)
    hud.install({})            # disables itself on a failed inject
    assert hud.enabled is False
    hud.stage("x")
    hud.row(0, "S1")
    hud.cell("Course", "BS IS")
    hud.row_done(False)
    hud.finish("Done", "")


def test_bars_animate_by_transform_not_by_width():
    """Flagged by the design hook 2026-09-23, and a real problem in this
    context rather than a style nit.

    Animating width relayouts on every frame, and a shadow root is NOT a layout
    boundary - that work lands in the same document the executor is driving,
    which is precisely the interference this file's own header claims it does
    not cause. scaleX runs on the compositor and touches no layout.

    Asserted against the source because it is a property of the stylesheet, not
    of any one rendered frame.
    """
    assert "transition:width" not in HUD_JS
    assert "transition:transform" in HUD_JS
    assert "style.width" not in HUD_JS, "a bar is being driven by width again"
    assert HUD_JS.count("transform-origin:left center") == 2


def test_hud_js_declares_no_form_controls():
    """Static guard on the source. A createElement("input") anywhere in here
    would hand the Page Scanner a 251st field."""
    for banned in ('"input"', "'input'", '"select"', "'select'",
                   '"textarea"', "'textarea'"):
        assert f"createElement({banned}" not in HUD_JS


# ------------------------------------------------------ browser behaviour


@pytest.fixture
def hud_page():
    """The real v0_base portal with the real HUD injected, exactly as the
    executor injects it.

    Function-scoped on purpose, for two reasons. A module-scoped fixture holds
    its `with sync_playwright()` open for the whole file, and the end-to-end
    test below starts its own inside run() - nesting the sync API that way
    raises "Playwright Sync API inside the asyncio loop". It also lets one test
    mutate the page another then asserts against: the click-Save test would
    otherwise leave a committed portal behind for whatever ran next.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None,
            headless=True)
        page = browser.new_page()
        page.goto(variant_url("v0_base"), wait_until="load")
        page.wait_for_selector("#records-body tr")
        page.evaluate(HUD_JS)
        page.evaluate("p => window.__agentHUD.init(p)",
                      hud_payload(SAMPLE_MAPPING, "v0_base", True, 50))
        yield page
        browser.close()


def test_the_hud_renders_inside_a_shadow_root(hud_page):
    """The isolation that makes this safe. If the HUD's markup were in the
    light DOM its CSS could reach the portal, and styling is the one thing the
    instrument holds constant across all eight variants."""
    assert hud_page.locator("[data-agent-hud]").count() == 1
    assert hud_page.evaluate(
        "() => !!document.querySelector('[data-agent-hud]').shadowRoot") is True

    # ...and the panel is reachable ONLY through that shadow root. Checked with
    # document.querySelector rather than a Playwright locator: locators pierce
    # open shadow roots on purpose, so a locator here would pass whether the
    # markup were encapsulated or not and prove nothing.
    assert hud_page.evaluate("() => document.querySelectorAll('.hud').length") == 0
    assert hud_page.evaluate(
        "() => document.querySelector('[data-agent-hud]')"
        ".shadowRoot.querySelectorAll('.hud').length") == 1


def test_the_hud_adds_no_stylesheet_and_no_field_to_the_document(hud_page):
    """Two things the instrument cannot tolerate: a style rule that leaks into
    the portal, and a control the scanner could mistake for a field."""
    assert hud_page.evaluate(
        "() => document.querySelectorAll('style, link[rel=stylesheet]').length"
    ) == hud_page.evaluate(
        "() => [...document.querySelectorAll('style, link[rel=stylesheet]')]"
        ".filter(n => !n.closest('[data-agent-hud]')).length")

    assert hud_page.evaluate(
        "() => document.querySelector('[data-agent-hud]')"
        ".shadowRoot.querySelectorAll('input, select, textarea').length") == 0


def test_the_hud_shows_the_abstention_as_a_decision(hud_page):
    """The claim that is currently invisible: it refused FINAL on purpose."""
    text = hud_page.evaluate(
        "() => document.querySelector('[data-agent-hud]').shadowRoot.textContent")
    assert "Refused to guess" in text
    assert "FINAL" in text
    assert "abstained" in text
    assert "margin 0.04" in text
    assert "Recommendations optional" in text


def test_the_hud_states_the_induced_rule_in_words(hud_page):
    """The other invisible claim: the pass mark was read off the data."""
    text = hud_page.evaluate(
        "() => document.querySelector('[data-agent-hud]').shadowRoot.textContent")
    assert "Derived, not copied" in text
    assert "Remarks" in text
    assert "Grade 0-100 ≥ 75" in text
    assert "Passed" in text and "Failed" in text
    assert "between 74 and 75" in text


def test_row_highlight_is_restored_exactly(hud_page):
    """The HUD's only light-DOM write. It must leave the page byte-for-byte as
    it found it, or a later run would inherit a stray outline."""
    before = hud_page.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].getAttribute('style')")

    hud_page.evaluate("() => window.__agentHUD.row(3, 'S-0004')")
    during = hud_page.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].style.outline")
    assert during != ""

    hud_page.evaluate("() => window.__agentHUD.finish({status: 'Done'})")
    after = hud_page.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].getAttribute('style')")
    assert (after or "").strip(" ;") == (before or "").strip(" ;")


def test_the_hud_cannot_intercept_a_click_meant_for_the_portal(hud_page):
    """Regression, found live by the full suite 2026-09-22.

    The first version anchored the HUD with a normal-flow host. Playwright then
    refused to click the portal's own Save button - "<div data-agent-hud>
    intercepts pointer events" - and a `--show --commit` run timed out after
    filling every row correctly. A HUD that can swallow one of the agent's
    clicks changes the outcome of the run it is supposed to be describing.

    The fix is pointer-events:none on the host and on every descendant, with no
    exception: it is a heads-up display, read and never touched. This asserts
    the property directly - what does the browser hand a click at the HUD's own
    top-right corner? - rather than asserting the CSS text that implements it.
    """
    hit = hud_page.evaluate(
        "() => {"
        "  const box = document.querySelector('[data-agent-hud]')"
        "    .shadowRoot.querySelector('.hud').getBoundingClientRect();"
        "  const el = document.elementFromPoint(box.left + 12, box.top + 12);"
        "  return el ? (el.closest('[data-agent-hud]') ? 'hud' : el.tagName) : 'none';"
        "}")
    assert hit != "hud", "the HUD is capturing clicks the portal should receive"

    # And the real target: Save stays clickable with the panel on screen.
    hud_page.click("#submit-btn", timeout=5000)


def test_the_hud_reserves_its_width_rather_than_covering_the_page(hud_page):
    """Caught by screenshotting the first version: the panel sat on top of the
    Remarks column and the Save button - Remarks being the rule-derived field,
    so the demo was hiding the one column it most wanted to show.

    The panel now reserves a gutter on <html> and the centred portal slides
    clear. Asserted geometrically: no part of the roster may end up underneath
    the panel.
    """
    overlap = hud_page.evaluate(
        "() => {"
        "  const hud = document.querySelector('[data-agent-hud]')"
        "    .shadowRoot.querySelector('.hud').getBoundingClientRect();"
        "  const tbl = document.querySelector('#records-body').getBoundingClientRect();"
        "  return tbl.right - hud.left;"
        "}")
    assert overlap <= 0, f"the roster runs {overlap:.0f}px under the HUD panel"


def test_the_gutter_is_given_back_on_destroy(hud_page):
    """The gutter is a layout change to the instrument's own page. It has to
    leave exactly as it arrived, or a later run inherits a stray margin."""
    before = hud_page.evaluate("() => document.documentElement.style.paddingRight")
    assert before != "", "the gutter was never applied"

    hud_page.evaluate("() => window.__agentHUD.destroy()")
    after = hud_page.evaluate("() => document.documentElement.style.paddingRight")
    assert after == ""
    assert hud_page.evaluate("() => document.querySelectorAll('[data-agent-hud]').length") == 0


def test_init_twice_leaves_exactly_one_hud(hud_page):
    """A re-run in the same page must not stack panels."""
    hud_page.evaluate("p => window.__agentHUD.init(p)",
                      hud_payload(SAMPLE_MAPPING, "v0_base", True, 50))
    assert hud_page.locator("[data-agent-hud]").count() == 1


# --------------------------------------------------- end to end, executor


@pytest.mark.slow
def test_show_mode_produces_the_same_results_as_headless(monkeypatch):
    """The one that matters most. If the HUD changed any number the executor
    reports, it would be contaminating the experiment rather than describing
    it. Same mapping, same sheet, same rows - with the HUD and without.
    """
    if not SHEET.exists():
        pytest.skip("run data/sheets/make_sheets.py first")

    import executor.runner as runner_module

    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(runner_module.time, "sleep", lambda s: None)

    quiet = run("v0_base", MAPPING_PATH, dry_run=True, limit=5,
                capture_state=True, show=False)
    shown = run("v0_base", MAPPING_PATH, dry_run=True, limit=5,
                capture_state=True, show=True)

    assert [r.status for r in shown.rows] == [r.status for r in quiet.rows]
    assert [r.filled for r in shown.rows] == [r.filled for r in quiet.rows]
    assert [r.verified for r in shown.rows] == [r.verified for r in quiet.rows]
    assert shown.portal_state == quiet.portal_state
