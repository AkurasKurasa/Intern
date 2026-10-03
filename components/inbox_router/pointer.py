"""
components/inbox_router/pointer.py
==================================
Drives Scope #3's browser with the real Windows mouse pointer, so a person
watching the demo sees Intern move to each email and each button and click
it, the same way Scope #1 operates its form.

Playwright still reads the page and finds every element. It just no longer
performs the click itself: Pointer asks for the element's box on the page,
converts that to a position on the physical screen, glides the real pointer
there, and clicks.

Getting the conversion right is the whole difficulty. A page reports
positions in CSS pixels measured from the top-left of its viewport, while
the mouse works in physical screen pixels. Two things sit between them:

  1. Where the viewport is on screen. The browser's toolbar, tabs and window
     border push it down and right by an amount that changes with window
     state and browser version, so it is never guessed. Windows itself is
     asked for the rectangle of Chromium's render widget -- the child
     window that holds the page content and nothing else.

  2. Display scaling. At 125% or 150% Windows scaling one CSS pixel is more
     than one screen pixel. The scale is measured, not assumed: the render
     widget's width in screen pixels divided by the page's own innerWidth.

This process also declares itself DPI-aware before anything is measured,
so the window rectangle and the mouse both report physical pixels. Without
that, Windows silently scales one of them and clicks land off-target on any
laptop not running at 100%.

When the render widget cannot be found -- a renamed window, a non-Windows
machine -- the page's own window metrics are used as an estimate instead,
which is close enough to land on a button but noted in the log.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

_dpi_declared = False


def declare_dpi_aware() -> None:
    """Make this process report physical pixels. Must run before the first
    window rectangle is read or the mouse is moved; calling it again is
    harmless."""
    global _dpi_declared
    if _dpi_declared or sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()    # older Windows
        except Exception:
            pass
    _dpi_declared = True


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left


def screen_point(box: dict, viewport: Rect, inner_width: float) -> Tuple[int, int]:
    """Centre of a page element, in physical screen pixels.

    box:         Playwright bounding_box() -- CSS pixels from the viewport's
                 top-left corner.
    viewport:    the page content area on screen, in physical pixels.
    inner_width: the page's window.innerWidth, in CSS pixels.
    """
    scale = viewport.width / inner_width if inner_width else 1.0
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    return round(viewport.left + cx * scale), round(viewport.top + cy * scale)


def find_render_widget(title_hint: str) -> Optional[Rect]:
    """Screen rectangle of the Chromium render widget whose top-level window
    title contains title_hint. Returns None when it cannot be found."""
    if sys.platform != "win32" or not title_hint:
        return None
    try:
        import win32gui
    except ImportError:
        return None

    tops = []

    def _top(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd)
                and win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1"
                and title_hint in win32gui.GetWindowText(hwnd)):
            tops.append(hwnd)
        return True

    win32gui.EnumWindows(_top, None)

    best: Optional[Rect] = None
    for top in tops:
        children = []

        def _child(hwnd, _):
            if win32gui.GetClassName(hwnd) == "Chrome_RenderWidgetHostHWND":
                children.append(hwnd)
            return True

        try:
            win32gui.EnumChildWindows(top, _child, None)
        except Exception:
            continue
        for hwnd in children:
            r = Rect(*win32gui.GetWindowRect(hwnd))
            # The largest visible render widget is the page; small ones are
            # popups or devtools panes.
            if r.width > 0 and (best is None or r.width * (r.bottom - r.top) > best.width * (best.bottom - best.top)):
                best = r
    return best


class Pointer:
    """Clicks page elements with the real mouse pointer.

    enabled=False falls back to Playwright's own click, which is what
    headless runs and tests use: same behaviour, nothing moves on screen."""

    def __init__(self, page, enabled: bool = True, glide_seconds: float = 0.45,
                 settle_ms: int = 120):
        self.page = page
        self.enabled = enabled
        self.glide_seconds = glide_seconds
        self.settle_ms = settle_ms
        self.used_estimate = False
        if enabled:
            declare_dpi_aware()

    # -- geometry ---------------------------------------------------------
    def _viewport(self) -> Tuple[Rect, float]:
        metrics = self.page.evaluate(
            "() => ({iw: innerWidth, ih: innerHeight, ow: outerWidth, oh: outerHeight,"
            " sx: screenX, sy: screenY, dpr: devicePixelRatio, title: document.title})")
        rect = find_render_widget(metrics["title"])
        if rect is not None:
            self.used_estimate = False
            return rect, metrics["iw"]

        # Estimate from the page's own window metrics. Border is assumed
        # symmetric left/right/bottom; everything else above the page is
        # toolbar.
        self.used_estimate = True
        dpr = metrics["dpr"] or 1.0
        border = max(0.0, (metrics["ow"] - metrics["iw"]) / 2)
        left_css = metrics["sx"] + border
        top_css = metrics["sy"] + (metrics["oh"] - metrics["ih"]) - border
        left, top = round(left_css * dpr), round(top_css * dpr)
        rect = Rect(left, top, left + round(metrics["iw"] * dpr), top + round(metrics["ih"] * dpr))
        return rect, metrics["iw"]

    def target(self, locator) -> Tuple[int, int]:
        """Screen position the pointer will click for this element."""
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError("element has no box on screen (hidden or detached)")
        rect, inner_width = self._viewport()
        return screen_point(box, rect, inner_width)

    # -- action -----------------------------------------------------------
    def click(self, target) -> None:
        """Click a Locator or CSS selector."""
        locator = self.page.locator(target) if isinstance(target, str) else target
        if not self.enabled:
            locator.click()
            return

        import pyautogui
        x, y = self.target(locator)
        pyautogui.moveTo(x, y, duration=self.glide_seconds, tween=pyautogui.easeInOutQuad)
        self.page.wait_for_timeout(self.settle_ms)
        pyautogui.click(x, y)
