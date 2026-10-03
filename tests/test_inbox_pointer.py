"""Tests for components/inbox_router/pointer.py and its use by Scope #3.

unit         screen-position maths at different display scales; the
             enabled and disabled click paths; the estimate fallback
integration  every Scope #3 entry point routes its clicks through Pointer
e2e          a real (headless) Chromium page: a disabled Pointer clicks a
             button, and an enabled Pointer computes a target that lies
             inside the page area on screen
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(REPO, "components", "inbox_router")
sys.path.insert(0, INBOX)

import pointer as ptr  # noqa: E402


# ==========================================================================
# unit — geometry
# ==========================================================================

BOX = {"x": 100, "y": 50, "width": 40, "height": 20}   # centre (120, 60) in CSS px


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_screen_point_scales_with_display_setting(scale):
    inner_width = 1000
    rect = ptr.Rect(left=8, top=120, right=8 + round(inner_width * scale), bottom=900)
    x, y = ptr.screen_point(BOX, rect, inner_width)
    assert x == round(8 + 120 * scale)
    assert y == round(120 + 60 * scale)


def test_screen_point_uses_element_centre_not_corner():
    rect = ptr.Rect(0, 0, 1000, 800)
    assert ptr.screen_point({"x": 0, "y": 0, "width": 200, "height": 100}, rect, 1000) == (100, 50)


def test_screen_point_survives_zero_inner_width():
    assert ptr.screen_point(BOX, ptr.Rect(0, 0, 500, 500), 0) == (120, 60)


# ==========================================================================
# unit — click paths, with a fake page and a fake pyautogui
# ==========================================================================

class FakeLocator:
    def __init__(self, box=None):
        self.box = box or dict(BOX)
        self.clicked = 0
        self.scrolled = 0

    def click(self):
        self.clicked += 1

    def scroll_into_view_if_needed(self):
        self.scrolled += 1

    def bounding_box(self):
        return self.box


class FakePage:
    def __init__(self, metrics=None):
        self.locators = {}
        self.waits = []
        self.metrics = metrics or {"iw": 1000, "ih": 700, "ow": 1016, "oh": 830,
                                   "sx": 0, "sy": 0, "dpr": 1.0, "title": "Inbox"}

    def locator(self, selector):
        return self.locators.setdefault(selector, FakeLocator())

    def evaluate(self, _js):
        return dict(self.metrics)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


@pytest.fixture
def fake_pyautogui(monkeypatch):
    calls = []
    mod = types.SimpleNamespace(
        easeInOutQuad=lambda t: t,
        moveTo=lambda x, y, duration=0, tween=None: calls.append(("move", x, y)),
        click=lambda x, y: calls.append(("click", x, y)),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", mod)
    return calls


def test_disabled_pointer_uses_playwright_click_and_never_moves_mouse(fake_pyautogui):
    page = FakePage()
    p = ptr.Pointer(page, enabled=False)
    p.click("#sendBtn")
    assert page.locators["#sendBtn"].clicked == 1
    assert fake_pyautogui == []


def test_enabled_pointer_glides_then_clicks_at_mapped_position(monkeypatch, fake_pyautogui):
    page = FakePage()
    monkeypatch.setattr(ptr, "find_render_widget", lambda title: ptr.Rect(10, 100, 1260, 975))
    p = ptr.Pointer(page, enabled=True)
    loc = FakeLocator()
    p.click(loc)

    expected = ptr.screen_point(BOX, ptr.Rect(10, 100, 1260, 975), 1000)
    assert fake_pyautogui == [("move", *expected), ("click", *expected)]
    assert loc.clicked == 0, "the real pointer clicks, not Playwright"
    assert loc.scrolled == 1, "off-screen targets are scrolled into view first"
    assert p.used_estimate is False


def test_enabled_pointer_accepts_a_selector_string(monkeypatch, fake_pyautogui):
    page = FakePage()
    monkeypatch.setattr(ptr, "find_render_widget", lambda title: ptr.Rect(0, 0, 1000, 700))
    ptr.Pointer(page, enabled=True).click("#backBtn")
    assert [c[0] for c in fake_pyautogui] == ["move", "click"]


def test_falls_back_to_window_metrics_when_render_widget_missing(monkeypatch, fake_pyautogui):
    page = FakePage({"iw": 1000, "ih": 700, "ow": 1016, "oh": 830,
                     "sx": 50, "sy": 40, "dpr": 1.25, "title": "Inbox"})
    monkeypatch.setattr(ptr, "find_render_widget", lambda title: None)
    p = ptr.Pointer(page, enabled=True)
    x, y = p.target(FakeLocator())
    assert p.used_estimate is True
    # border 8, toolbar = 830 - 700 - 8 = 122 -> viewport origin (58, 162) CSS.
    # The viewport corner is rounded to a whole pixel before the element
    # offset is added, so allow one pixel of rounding either way.
    assert abs(x - (58 + 120) * 1.25) <= 1
    assert abs(y - (162 + 60) * 1.25) <= 1


def test_hidden_element_raises_rather_than_clicking_nowhere(monkeypatch, fake_pyautogui):
    page = FakePage()
    monkeypatch.setattr(ptr, "find_render_widget", lambda title: ptr.Rect(0, 0, 1000, 700))
    loc = FakeLocator()
    loc.box = None
    loc.bounding_box = lambda: None
    with pytest.raises(RuntimeError):
        ptr.Pointer(page, enabled=True).click(loc)
    assert fake_pyautogui == []


# ==========================================================================
# integration — every Scope #3 entry point uses the pointer
# ==========================================================================

SCRIPTS = ["automate_inbox.py", "automate_cold_email.py", "run_boss_task_list.py"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_script_clicks_around_the_pointer(name):
    src = open(os.path.join(INBOX, name), encoding="utf8").read()
    stray = re.findall(r"\b(?:page|row)\.click\(", src)
    assert not stray, f"{name} still clicks invisibly: {stray}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_offer_a_no_pointer_escape_hatch(name):
    src = open(os.path.join(INBOX, name), encoding="utf8").read()
    assert '"--no-pointer"' in src
    assert "args.headless or args.no_pointer" in src


def test_process_one_functions_accept_a_pointer():
    import automate_cold_email
    import automate_inbox
    assert "pointer" in inspect.signature(automate_inbox.process_one).parameters
    assert "pointer" in inspect.signature(automate_cold_email.process_one).parameters


# ==========================================================================
# e2e — a real headless browser
# ==========================================================================

HTML = """<!doctype html><title>Pointer Test</title>
<body style="margin:0;height:3000px">
  <button id="near" style="position:absolute;left:40px;top:30px">near</button>
  <button id="far" style="position:absolute;left:300px;top:2400px"
          onclick="document.title='clicked-far'">far</button>
</body>"""


@pytest.fixture
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # chromium not installed on this machine
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        page.set_content(HTML)
        yield page
        browser.close()


def test_e2e_disabled_pointer_really_clicks_an_offscreen_button(browser_page):
    ptr.Pointer(browser_page, enabled=False).click("#far")
    assert browser_page.title() == "clicked-far"


def test_e2e_target_lands_inside_the_page_area(browser_page, monkeypatch):
    monkeypatch.setattr(ptr, "find_render_widget", lambda title: None)   # headless: no window
    p = ptr.Pointer(browser_page, enabled=False)
    m = browser_page.evaluate("() => ({iw: innerWidth, ih: innerHeight, dpr: devicePixelRatio})")
    x, y = p.target(browser_page.locator("#far"))       # scrolls it into view first
    rect, _ = p._viewport()
    assert rect.left <= x <= rect.right and rect.top <= y <= rect.bottom, (x, y, rect, m)
