"""Scope #2's decision feed, and what is left of its in-page script.

The panel used to render into the portal page inside a shadow root. It moved to
the Electron app's floating Agent window, so Scope #1 and Scope #2 report
through one surface instead of each having its own. What stayed in the page is
the row highlighter: outlining the row being filled and scrolling it into view,
which is the one part that cannot live outside the page.

So this file now pins two separate things:

  * the SCOPE2HUD lines the executor emits, which main.js parses
  * the row highlighter, which still runs inside the page the experiment is
    measured on and therefore still has to be provably inert

The load-bearing assertion is unchanged and is the last test here: a run with
the HUD on and a run with it off produce identical rows, values and portal
state. If that ever fails, the HUD is contaminating the experiment rather than
describing it.

Run:  python -m pytest tests/scope2/test_hud.py -q
"""

import io
import json
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

SAMPLE_MAPPING = {
    "assignments": [
        {"source_header": "PROGRAM", "target_label": "Course",
         "score": None, "margin": None, "status": "auto", "via": "llm"},
        {"source_header": "FINAL GRADE", "target_label": "Grade 0-100",
         "score": None, "margin": None, "status": "auto", "via": "source"},
    ],
    # a field the LLM refused, with its reason
    "abstained": [
        {"source_header": None, "target_label": "Recommendations optional",
         "reason": "LLM answered NONE"},
    ],
    "derived_rules": [
        {"field": "Remarks", "kind": "threshold", "depends_on_field": "Grade 0-100",
         "operator": ">=", "cutoff": 75, "if_true": "Passed", "if_false": "Failed",
         "observed_interval": [74, 75], "status": "confirmed"},
    ],
    "unmapped_fields": ["Recommendations optional"],
    "unmapped_columns": ["MIDTERM"],
}


def emitted(fn):
    """Run fn with stdout captured, and return the SCOPE2HUD payloads."""
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        fn()
    finally:
        sys.stdout = original
    return [json.loads(line[len("SCOPE2HUD "):])
            for line in buffer.getvalue().splitlines()
            if line.startswith("SCOPE2HUD ")]


# ------------------------------------------------------------ the payload


def test_payload_carries_every_decision_the_resolver_made():
    payload = hud_payload(SAMPLE_MAPPING, "v0_base", dry_run=True, total_rows=50)

    assert payload["variant"] == "v0_base"
    assert payload["total_rows"] == 50
    assert [a["source_header"] for a in payload["assignments"]] == ["PROGRAM", "FINAL GRADE"]
    assert payload["abstained"] == [{"target_label": "Recommendations optional",
                                     "reason": "LLM answered NONE"}]
    assert payload["derived_rules"][0]["cutoff"] == 75
    assert payload["unmapped_fields"] == ["Recommendations optional"]


def test_every_mapping_says_which_tier_decided_it():
    """Source or LLM -- the same two words Scope #1's HUD uses. A hand-written
    mapping with no tag names its columns itself, which is what source means."""
    payload = hud_payload(SAMPLE_MAPPING, "v0_base", dry_run=True, total_rows=50)
    assert [a["via"] for a in payload["assignments"]] == ["llm", "source"]
    bare = hud_payload({"assignments": [{"source_header": "A", "target_label": "B"}]},
                       "v0_base", dry_run=True, total_rows=1)
    assert bare["assignments"][0]["via"] == "source"


def test_payload_survives_a_mapping_missing_every_optional_key():
    """Hand-written mappings predate `abstained`, so a mapping with no
    abstentions, no rules and no unmapped fields must not raise."""
    payload = hud_payload({}, "v0_base", dry_run=False, total_rows=0)
    assert payload["assignments"] == []
    assert payload["abstained"] == []
    assert payload["derived_rules"] == []
    assert payload["unmapped_fields"] == []


# --------------------------------------------------------- the feed lines


def test_every_stage_of_a_run_emits_a_parseable_line():
    hud = Hud(None, enabled=True)
    payload = hud_payload(SAMPLE_MAPPING, "v0_base", False, 50)

    lines = emitted(lambda: (
        hud.install(payload),
        hud.stage("Filling rows"),
        hud.row(3, "2021-10022"),
        hud.cell("Course", "BS Computer Science"),
        hud.row_done(True),
        hud.finish("Done", "50 rows filled and verified"),
    ))

    assert [l["kind"] for l in lines] == [
        "init", "stage", "row", "cell", "rowDone", "finish"]

    init = lines[0]
    assert init["variant"] == "v0_base"
    assert init["total_rows"] == 50
    assert init["abstained"][0]["target_label"] == "Recommendations optional"
    assert init["derived_rules"][0]["cutoff"] == 75

    assert lines[2]["index"] == 3 and lines[2]["student_id"] == "2021-10022"
    assert lines[3]["label"] == "Course"
    assert lines[4]["ok"] is True
    assert lines[5]["status"] == "Done"


def test_each_line_is_a_single_line():
    """main.js matches on a whole line. A payload that wrapped would parse as
    several broken ones."""
    hud = Hud(None, enabled=True)
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        hud.install(hud_payload(SAMPLE_MAPPING, "v0_base", False, 50))
    finally:
        sys.stdout = original
    assert len(buffer.getvalue().strip().splitlines()) == 1


def test_the_prefix_is_what_main_js_slices():
    """main.js does raw.slice(10) after matching "SCOPE2HUD ". If the prefix
    ever changes length, that slice silently truncates the JSON."""
    assert len("SCOPE2HUD ") == 10


# ------------------------------------------------- it must never break a run


def test_a_disabled_hud_emits_nothing_and_never_touches_the_page():
    """The headless path. If this emits or calls into the page, a measurement
    run stops being a measurement of the system alone."""

    class Boom:
        def evaluate(self, *a, **k):
            raise AssertionError("a disabled HUD called into the page")

    hud = Hud(Boom(), enabled=False)
    assert hud.enabled is False

    lines = emitted(lambda: (
        hud.install({}), hud.stage("x"), hud.row(0, "S1"),
        hud.cell("Course", "BS IS"), hud.row_done(True), hud.finish("Done", ""),
    ))
    assert lines == []


def test_a_page_that_throws_does_not_stop_the_feed():
    """The row highlight is best-effort. A dead page must not take the run or
    the rest of the feed down with it."""

    class Broken:
        def evaluate(self, *a, **k):
            raise RuntimeError("page went away")

    hud = Hud(Broken(), enabled=True)
    lines = emitted(lambda: (
        hud.install(hud_payload(SAMPLE_MAPPING, "v0_base", False, 5)),
        hud.row(0, "S1"),
        hud.finish("Done", ""),
    ))
    assert [l["kind"] for l in lines] == ["init", "row", "finish"]


def test_an_oserror_on_flush_is_survived(monkeypatch):
    """Spawned with no console (the Electron Play button uses windowsHide), an
    explicit flush can raise OSError on Windows even though the write landed.
    The same guard run_task.py's print_countdown() needed."""
    class Exploding(io.StringIO):
        def flush(self):
            raise OSError(22, "Invalid argument")

    buffer = Exploding()
    monkeypatch.setattr(sys, "stdout", buffer)
    Hud(None, enabled=True).stage("x")        # must not raise
    assert "SCOPE2HUD " in buffer.getvalue()


# ------------------------------------------- what is left inside the page


def test_the_page_script_declares_no_form_controls():
    """Static guard. A createElement("input") here would hand the Page Scanner
    a 251st field."""
    for banned in ('"input"', "'input'", '"select"', "'select'",
                   '"textarea"', "'textarea'"):
        assert f"createElement({banned}" not in HUD_JS


def test_the_page_script_adds_no_element_at_all():
    """The panel needed a shadow root; a row outline needs nothing. Nothing in
    here may create or append a node."""
    for banned in ("createElement(", "appendChild(", "attachShadow("):
        assert banned not in HUD_JS, f"the row highlighter is building DOM again: {banned}"


@pytest.fixture
def portal():
    """The real v0_base portal with the row highlighter injected, exactly as
    the executor injects it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None,
            headless=True)
        page = browser.new_page()
        page.goto(variant_url("v0_base"), wait_until="load")
        page.wait_for_selector("#records-body tr")
        page.evaluate(HUD_JS)
        yield page
        browser.close()


def test_the_highlight_marks_the_row_and_restores_it_exactly(portal):
    """The only light-DOM write. It must leave the page as it found it, or a
    later run inherits a stray outline."""
    before = portal.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].getAttribute('style')")

    portal.evaluate("() => window.__agentHUD.row(3)")
    during = portal.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].style.outline")
    assert during != ""

    portal.evaluate("() => window.__agentHUD.release()")
    after = portal.evaluate(
        "() => document.querySelectorAll('#records-body tr')[3].getAttribute('style')")
    assert (after or "").strip(" ;") == (before or "").strip(" ;")


def test_moving_to_another_row_releases_the_previous_one(portal):
    """Otherwise a 50-row run leaves 50 outlined rows behind it."""
    portal.evaluate("() => window.__agentHUD.row(2)")
    portal.evaluate("() => window.__agentHUD.row(5)")
    outlined = portal.evaluate(
        "() => [...document.querySelectorAll('#records-body tr')]"
        ".filter(r => r.style.outline).length")
    assert outlined == 1


def test_it_adds_nothing_to_the_document(portal):
    """Counted for real, not asserted from the source: the highlighter must
    leave the element count and the stylesheet count untouched."""
    counts = portal.evaluate(
        "() => ({ els: document.querySelectorAll('*').length,"
        "         sheets: document.querySelectorAll('style, link[rel=stylesheet]').length,"
        "         fields: document.querySelectorAll('input, select, textarea').length })")
    portal.evaluate("() => window.__agentHUD.row(1)")
    after = portal.evaluate(
        "() => ({ els: document.querySelectorAll('*').length,"
        "         sheets: document.querySelectorAll('style, link[rel=stylesheet]').length,"
        "         fields: document.querySelectorAll('input, select, textarea').length })")
    assert counts == after


def test_the_save_button_stays_clickable(portal):
    """The old panel was found intercepting this exact click. Nothing in the
    page should be able to now, but it is cheap to keep proving."""
    portal.evaluate("() => window.__agentHUD.row(0)")
    portal.click("#submit-btn", timeout=5000)


# --------------------------------------------------- end to end, executor


@pytest.mark.slow
def test_show_mode_produces_the_same_results_as_headless(monkeypatch):
    """The one that matters most. If the HUD changed any number the executor
    reports, it would be contaminating the experiment rather than describing
    it. Same mapping, same sheet, same rows -- with the HUD and without."""
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
