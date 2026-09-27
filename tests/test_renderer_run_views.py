"""The main window's Running / Finished views, driven by real bridge events.

Two bugs found by the user on a Scope #2 run, both fixed in renderer.js:

  * Pressing Stop drew "Stopped -- you have control again" ON TOP of Home's
    "Nothing running / Show Intern the task once" hero. showFinished() showed
    the Tasks section's summary without leaving Home.
  * Duration read "--". The Running view and the elapsed timer were only
    started by COUNTDOWN_BEGIN, and Scope #2 no longer prints a countdown
    (it drives its own browser; the 5 s bought nothing). They now start at
    capsule_started, which every task sends.

The page is loaded in a real Chromium with every preload API faked, and the
same {"event": ...} objects app/recorder_bridge.py emits are replayed into
recorderAPI.onEvent.

Run:  python -m pytest tests/test_renderer_run_views.py -q
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "app_electron" / "renderer" / "index.html"

# Every preload API as a Proxy: any method resolves to an empty result, and
# recorderAPI.onEvent keeps the callback so the test can push events.
FAKE_APIS = """
(() => {
  const fn = () => Promise.resolve([]);
  const api = extra => new Proxy(extra || {}, {
    get: (t, k) => (k in t ? t[k] : fn),
  });
  window.__emit = null;
  window.recorderAPI = api({ onEvent: cb => { window.__emit = cb; },
                             setActiveSection: () => {} });
  window.capsulesAPI = api();
  window.workflowsAPI = api();
  window.settingsAPI = api();
  window.inboxAPI = api({ onEvent: () => {} });
  window.agentHud = api({ onDecision() {}, onScope2() {}, onReset() {}, onFinish() {} });
})();
"""

VIEWS = ["emptyState", "recordingView", "workflowsWrap", "runningView", "finishedView"]


@pytest.fixture()
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script(FAKE_APIS)
        pg.goto(INDEX.as_uri())
        pg.wait_for_function("() => typeof window.__emit === 'function'")
        pg.errors = errors
        yield pg
        browser.close()


def emit(page, **event):
    page.evaluate("e => window.__emit(e)", event)


def visible(page):
    """Which of the main-area views are actually on screen."""
    return [v for v in VIEWS
            if page.evaluate("id => { const el = document.getElementById(id);"
                             " return !!el && getComputedStyle(el).display !== 'none'; }", v)]


def test_home_is_what_shows_first(page):
    assert visible(page) == ["emptyState"]


def test_a_run_without_a_countdown_still_gets_the_running_view(page):
    """Scope #2 prints no COUNTDOWN lines. It must still leave Home."""
    emit(page, event="capsule_started", label="automate.py")
    emit(page, event="capsule_progress", line="  sheet        grade_sheet.xlsx")
    assert visible(page) == ["runningView"]
    assert page.errors == []


def test_stopping_shows_the_summary_alone_never_over_home(page):
    """The reported bug: 'Stopped -- you have control again' drawn over
    'Nothing running / Show Intern the task once'."""
    emit(page, event="capsule_started", label="automate.py")
    for n in range(12):
        emit(page, event="capsule_progress", line=f"line {n}")
    emit(page, event="capsule_stopped")
    emit(page, event="capsule_done", code=3221225786)

    assert visible(page) == ["finishedView"]
    assert page.inner_text("#finishedHeadline").startswith("Stopped")
    assert page.inner_text("#finishedLineCount") == "12"
    assert page.errors == []


def test_the_duration_is_measured_without_a_countdown(page):
    emit(page, event="capsule_started", label="automate.py")
    page.wait_for_timeout(1100)
    emit(page, event="capsule_done", code=0)
    assert page.inner_text("#finishedDuration") != "—"
    assert page.inner_text("#finishedDuration").startswith("00:0")


def test_a_countdown_run_still_shows_its_countdown(page):
    """Scope #1 is unchanged: the countdown overlay still appears, over the
    Running view that capsule_started already opened."""
    emit(page, event="capsule_started", label="run_task.py")
    emit(page, event="capsule_progress", line="COUNTDOWN_BEGIN")
    emit(page, event="capsule_progress", line="Click on the target window NOW.")
    emit(page, event="capsule_progress", line="COUNTDOWN 5")
    assert page.evaluate("() => !document.getElementById('handoverOverlay').hidden")
    assert visible(page) == ["runningView"]
    emit(page, event="capsule_progress", line="COUNTDOWN_END")
    assert page.evaluate("() => document.getElementById('handoverOverlay').hidden")
    assert page.errors == []
