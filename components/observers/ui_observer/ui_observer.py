"""
UIAutomationObserver
====================
Reads the Windows UI Automation element tree of ALL visible windows
simultaneously — not just the foreground.

This lets Intern see both the source data app (e.g. a spreadsheet with
names to copy) and the target app (e.g. a form to fill in) at the same
time.  Every element is tagged with which app and window it belongs to,
and whether that window is the active (focused) one or a background one.

Trace element additions vs OCR
-------------------------------
    app          : str  — process name e.g. "EXCEL.EXE", "chrome.exe"
    window_title : str  — title of the window this element lives in
    window_role  : str  — "active"  → user is currently working here
                          "background" → data source / reference window
    pid          : int  — OS process ID of the owning app
    control_type : str  — raw UIA ControlTypeName (Button, Edit, Text…)
    value        : str  — current value for input/edit controls
    automation_id: str  — UIA AutomationId
    class_name   : str  — Windows class name
    focused      : bool — True for the single focused element

Dependencies
------------
    pip install uiautomation pywin32 psutil
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# ── optional deps ─────────────────────────────────────────────────────────────

# COM must be initialized before uiautomation is imported.
# This is a no-op if COM is already initialized on this thread.
try:
    import ctypes
    ctypes.windll.ole32.CoInitialize(None)
except Exception:
    pass

_UIA_AVAILABLE = False
try:
    import uiautomation as _uia
    _UIA_AVAILABLE = True
except ImportError:
    pass

_WIN32_AVAILABLE = False
try:
    import win32gui
    import win32con
    import win32process
    _WIN32_AVAILABLE = True
except ImportError:
    pass

_PSUTIL_AVAILABLE = False
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    pass

# ── UIA control type → simplified trace type ─────────────────────────────────
_CTRL_TYPE_MAP: Dict[str, str] = {
    "Button":        "button",
    "CheckBox":      "checkbox",
    "ComboBox":      "combobox",
    "Edit":          "input",
    "Hyperlink":     "link",
    "Image":         "image",
    "ListItem":      "listitem",
    "List":          "list",
    "Menu":          "menu",
    "MenuBar":       "menubar",
    "MenuItem":      "menuitem",
    "ProgressBar":   "progressbar",
    "RadioButton":   "radio",
    "ScrollBar":     "scrollbar",
    "Slider":        "slider",
    "Spinner":       "spinner",
    "StatusBar":     "statusbar",
    "Tab":           "tab",
    "TabItem":       "tabitem",
    "Text":          "label",
    "ToolBar":       "toolbar",
    "ToolTip":       "tooltip",
    "Tree":          "tree",
    "TreeItem":      "treeitem",
    "Custom":        "custom",
    "Group":         "group",
    "DataGrid":      "datagrid",
    "DataItem":      "dataitem",
    "Document":      "document",
    "SplitButton":   "splitbutton",
    "Window":        "window",
    "Pane":          "pane",
    "Header":        "header",
    "HeaderItem":    "headeritem",
    "Table":         "table",
    "TitleBar":      "titlebar",
    "Separator":     "separator",
    "Thumb":         "thumb",
}

_ALWAYS_INCLUDE = {"button", "input", "checkbox", "radio", "combobox",
                   "listitem", "menuitem", "tabitem", "tabitemcontrol",
                   "link", "dataitem", "splitbutton"}

# Decorative container types — wxPython, WPF, and similar UI frameworks wrap
# their actual interactive controls inside layers of named panes ("tab_policy",
# "DesktopWindowXamlSource", divider banners, etc.). These containers are NOT
# clickable targets but they DO have non-empty text, so the default
# should_add filter would include them in the output and consume per-window
# element slots that the cap then takes away from the real children further
# down the tree.
#
# Diagnostic on 4,485 recorded traces (scripts/diagnose_click_rejections.py)
# showed 95.9% of rejected clicks landing on panecontrol elements that the
# walker DID visit — but whose interactive children were never captured
# because the pane consumed the slot first. We exclude these types from the
# output (but the walker still recurses into them) so the per-window cap is
# spent on real interactive controls.
_DECORATIVE_CONTAINER_TYPES = {
    "pane", "window", "group", "custom", "splitter",
    "document", "titlebar", "header", "headeritem",
}

# Apps to skip — Intern control panel, system shell
# NOTE: python.exe / pythonw.exe are intentionally NOT listed here so that
# target apps running as Python scripts (e.g. the car insurance form) are
# visible to the observer.  The current process is excluded by PID below.
_SKIP_APPS = {
    "Antigravity.exe",                  # Intern control panel (packaged)
    "explorer.exe",                     # Windows shell
    "SearchHost.exe", "StartMenuExperienceHost.exe",
    "ShellExperienceHost.exe", "TextInputHost.exe",
    "LockApp.exe", "SystemSettings.exe",
}

# PID of this process — never observe our own windows
import os as _os
_OWN_PID: int = _os.getpid()


class UIAutomationObserver:
    """
    Captures UI element trees from ALL visible windows simultaneously.
    Elements are tagged with their source app, window title, and role
    (active = focused window, background = reference/data-source window).
    """

    def __init__(
        self,
        max_depth: int = 14,
        max_elements_per_window: int = 250,
        max_total_elements: int = 500,
        min_size: int = 4,
    ):
        self.max_depth               = max_depth
        self.max_elements_per_window = max_elements_per_window
        self.max_total_elements      = max_total_elements
        self.min_size                = min_size

    @property
    def available(self) -> bool:
        return _UIA_AVAILABLE and _WIN32_AVAILABLE

    # ── public ────────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """
        Capture all visible windows and return a unified trace-compatible
        state dict.  Wraps in UIAutomationInitializerInThread so it is safe
        to call from ScreenObserver's background capture thread.
        """
        if not self.available:
            return _empty_state("uiautomation or pywin32 not installed")
        try:
            with _uia.UIAutomationInitializerInThread():
                return self._capture()
        except Exception as exc:
            return _empty_state(str(exc))

    # ── internal ──────────────────────────────────────────────────────────────

    def _capture(self) -> Dict[str, Any]:
        screen_w, screen_h = _screen_size()

        # Foreground window — this is the "active" / target window.
        # If the foreground window belongs to a skipped app or our own process,
        # find the next eligible visible window in Z-order instead.
        fg_hwnd  = win32gui.GetForegroundWindow() if _WIN32_AVAILABLE else None
        if fg_hwnd and (_app_name(fg_hwnd) in _SKIP_APPS or _pid(fg_hwnd) == _OWN_PID):
            # Walk Z-order to find the topmost non-skipped, non-own-process visible window
            candidate = win32gui.GetWindow(fg_hwnd, 2)  # GW_HWNDNEXT
            while candidate:
                if (win32gui.IsWindowVisible(candidate)
                        and win32gui.GetWindowText(candidate)
                        and _app_name(candidate) not in _SKIP_APPS
                        and _pid(candidate) != _OWN_PID
                        and win32gui.GetWindowPlacement(candidate)[1] != win32con.SW_SHOWMINIMIZED):
                    fg_hwnd = candidate
                    break
                candidate = win32gui.GetWindow(candidate, 2)
        fg_title = win32gui.GetWindowText(fg_hwnd) if fg_hwnd else ""
        fg_app   = _app_name(fg_hwnd)
        fg_pid   = _pid(fg_hwnd)

        # Focused element rect for marking focused=True
        focused_rect = None
        try:
            fc = _uia.GetFocusedControl()
            focused_rect = fc.BoundingRectangle if fc else None
        except Exception:
            pass

        # Enumerate all visible non-minimised windows
        visible_windows = _get_visible_windows(fg_hwnd)

        all_elements: List[Dict[str, Any]] = []
        windows_meta: List[Dict[str, Any]] = []

        for hwnd, title, app, pid in visible_windows:
            if len(all_elements) >= self.max_total_elements:
                break

            is_active = (hwnd == fg_hwnd)
            role      = "active" if is_active else "background"
            win_elems: List[Dict[str, Any]] = []

            try:
                ctrl = _uia.ControlFromHandle(hwnd)
                if ctrl:
                    self._walk(
                        ctrl, win_elems,
                        depth=0,
                        screen_w=screen_w, screen_h=screen_h,
                        focused_rect=focused_rect,
                        app=app, window_title=title,
                        pid=pid, window_role=role,
                        elem_offset=len(all_elements),
                    )
            except Exception:
                pass

            # Skip windows that yielded no meaningful elements
            if not win_elems:
                continue

            windows_meta.append({
                "hwnd":          hwnd,
                "title":         title,
                "app":           app,
                "pid":           pid,
                "role":          role,
                "element_count": len(win_elems),
            })
            all_elements.extend(win_elems)

        # Identify the focused element
        focused_id: Optional[str] = None
        for elem in all_elements:
            if elem.get("focused"):
                focused_id = elem["element_id"]
                break

        return {
            "application":        fg_app,
            "window_title":       fg_title,
            "process_id":         fg_pid,
            "screen_resolution":  [screen_w, screen_h],
            "focused_element_id": focused_id,
            "windows":            windows_meta,
            "elements":           all_elements,
            "source":             "uia",
        }

    def _walk(
        self,
        ctrl: Any,
        out: List[Dict[str, Any]],
        depth: int,
        screen_w: int,
        screen_h: int,
        focused_rect: Any,
        app: str,
        window_title: str,
        pid: Optional[int],
        window_role: str,
        elem_offset: int,
    ) -> None:
        if depth > self.max_depth or len(out) >= self.max_elements_per_window:
            return

        try:
            rect = ctrl.BoundingRectangle
        except Exception:
            return

        if rect is None:
            return
        if rect.right <= 0 or rect.bottom <= 0:
            return
        if rect.left >= screen_w or rect.top >= screen_h:
            return
        if (rect.right - rect.left) < self.min_size:
            return
        if (rect.bottom - rect.top) < self.min_size:
            return

        try:
            ctrl_type  = ctrl.ControlTypeName or "Unknown"
            name       = (ctrl.Name or "").strip()
            auto_id    = ctrl.AutomationId or ""
            class_name = ctrl.ClassName or ""
            enabled    = bool(ctrl.IsEnabled)

            value = ""
            try:
                vp = ctrl.GetPattern(_uia.PatternId.ValuePattern)
                if vp:
                    value = (vp.Value or "").strip()
            except Exception:
                pass

            # Fallback: TextPattern — used by Windows 11 Notepad and
            # other document/editor controls that don't expose ValuePattern.
            if not value:
                tp = None
                try:
                    tp = ctrl.GetPattern(_uia.PatternId.TextPattern)
                except Exception:
                    pass

                if tp:
                    # Attempt 1: document range from the beginning of the file.
                    # This ensures the agent sees the top of the document regardless
                    # of where the user has scrolled — same as a human scrolling to top.
                    try:
                        value = (tp.DocumentRange.GetText(-1) or "").strip()
                    except Exception:
                        pass

                    # Attempt 2: visible ranges fallback if document range empty
                    if not value:
                        try:
                            visible = tp.GetVisibleRanges()
                            if visible:
                                parts = []
                                for r in visible:
                                    try:
                                        t = r.GetText(-1)
                                        if t:
                                            parts.append(t)
                                    except Exception:
                                        pass
                                value = " ".join(parts).strip()
                        except Exception:
                            pass

            # Win32 fallback: for controls where UIA returns ≤1 char (e.g. Windows 11
            # Notepad WinUI3, some PDF viewers).  Read directly from the Win32 edit
            # control via WM_GETTEXT / WM_GETTEXTLENGTH, which bypasses UIA entirely.
            if len(value) <= 1 and _WIN32_AVAILABLE:
                _EDIT_CLASSES = {"Edit", "RichEditD2DPT", "RichEdit20W", "RICHEDIT50W",
                                 "RICHEDIT60W", "RichEdit"}
                try:
                    import ctypes as _ct
                    WM_GETTEXT       = 0x000D
                    WM_GETTEXTLENGTH = 0x000E

                    # Try the control's own HWND first
                    _hwnd = None
                    try:
                        _hwnd = ctrl.NativeWindowHandle
                    except Exception:
                        pass

                    _user32 = _ct.windll.user32

                    def _read_hwnd_text(h):
                        try:
                            length = _user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
                            if length > 1:
                                buf = _ct.create_unicode_buffer(length + 2)
                                _user32.SendMessageW(h, WM_GETTEXT, length + 1, buf)
                                return buf.value
                        except Exception:
                            pass
                        return ""

                    if _hwnd:
                        _cls = win32gui.GetClassName(_hwnd)
                        if _cls in _EDIT_CLASSES:
                            value = _read_hwnd_text(_hwnd)

                    # If the top-level HWND didn't work, walk child windows looking
                    # for an edit class — handles Win11 Notepad's nested hierarchy.
                    if len(value) <= 1 and _hwnd:
                        _found_edit = [None]

                        def _find_edit_child(h, _):
                            if _found_edit[0]:
                                return
                            try:
                                if win32gui.GetClassName(h) in _EDIT_CLASSES:
                                    _found_edit[0] = h
                            except Exception:
                                pass

                        win32gui.EnumChildWindows(_hwnd, _find_edit_child, None)

                        # Also walk one level of grandchildren for WinUI3 nesting
                        if not _found_edit[0]:
                            child = win32gui.GetWindow(_hwnd, 5)   # GW_CHILD
                            while child and not _found_edit[0]:
                                win32gui.EnumChildWindows(child, _find_edit_child, None)
                                child = win32gui.GetWindow(child, 2)  # GW_HWNDNEXT

                        if _found_edit[0]:
                            value = _read_hwnd_text(_found_edit[0])
                except Exception:
                    pass

            simple_type = _CTRL_TYPE_MAP.get(ctrl_type, ctrl_type.lower())
            text        = name or value

            is_focused = (
                focused_rect is not None
                and rect.left   == focused_rect.left
                and rect.top    == focused_rect.top
                and rect.right  == focused_rect.right
                and rect.bottom == focused_rect.bottom
            )

            # Exclude decorative containers (panes, windows, groups, etc.)
            # from the output even when they have text — but ALWAYS recurse
            # into them below so their interactive children are still visited.
            # The focused element is the one exception: never drop it.
            is_decorative_container = (
                simple_type in _DECORATIVE_CONTAINER_TYPES and not is_focused
            )

            should_add = (
                (bool(text) or simple_type in _ALWAYS_INCLUDE or is_focused)
                and not is_decorative_container
            )

            if should_add:
                elem_id = f"elem_{elem_offset + len(out)}"
                out.append({
                    "element_id":    elem_id,
                    "type":          simple_type,
                    "control_type":  ctrl_type,
                    "bbox":          [rect.left, rect.top, rect.right, rect.bottom],
                    "text":          text,
                    "value":         value[:2000] if value and len(value) > 2000 else value,
                    "label":         text,
                    "automation_id": auto_id,
                    "class_name":    class_name,
                    "enabled":       enabled,
                    "visible":       True,
                    "focused":       is_focused,
                    "confidence":    1.0,
                    "source":        "uia",
                    # ── multi-window context ──────────────────────────────
                    "app":           app,
                    "window_title":  window_title,
                    "window_role":   window_role,
                    "pid":           pid,
                    # ─────────────────────────────────────────────────────
                    "metadata": {"depth": depth, "ctrl_type": ctrl_type},
                })
        except Exception:
            pass

        try:
            for child in ctrl.GetChildren():
                self._walk(
                    child, out, depth + 1,
                    screen_w, screen_h, focused_rect,
                    app, window_title, pid, window_role,
                    elem_offset,
                )
                if len(out) >= self.max_elements_per_window:
                    break
        except Exception:
            pass


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_visible_windows(fg_hwnd: Optional[int]) -> List[Tuple[int, str, str, Optional[int]]]:
    """
    Return (hwnd, title, app_name, pid) for all visible, non-minimised
    top-level windows.  The foreground window is always first.
    """
    if not _WIN32_AVAILABLE:
        return []

    results: List[Tuple[int, str, str, Optional[int]]] = []
    seen: set = set()

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        # Skip minimised windows
        if win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMINIMIZED:
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        app = _app_name(hwnd)
        if app in _SKIP_APPS:
            return
        pid = _pid(hwnd)
        if pid == _OWN_PID:          # never observe our own process windows
            return
        if hwnd not in seen:
            seen.add(hwnd)
            results.append((hwnd, title, app, pid))

    # Foreground first so it gets priority when total element cap hits
    if fg_hwnd:
        fg_title = win32gui.GetWindowText(fg_hwnd)
        fg_app   = _app_name(fg_hwnd)
        fg_pid   = _pid(fg_hwnd)
        if fg_app not in _SKIP_APPS and fg_pid != _OWN_PID and fg_title:
            results.append((fg_hwnd, fg_title, fg_app, fg_pid))
            seen.add(fg_hwnd)

    win32gui.EnumWindows(_cb, None)
    return results


def _screen_size() -> Tuple[int, int]:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def _app_name(hwnd: Optional[int]) -> str:
    if not hwnd or not _WIN32_AVAILABLE:
        return "Unknown"
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _PSUTIL_AVAILABLE:
            return psutil.Process(pid).name()
    except Exception:
        pass
    return "Unknown"


def _pid(hwnd: Optional[int]) -> Optional[int]:
    if not hwnd or not _WIN32_AVAILABLE:
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def _empty_state(reason: str = "") -> Dict[str, Any]:
    return {
        "application":        "Unknown",
        "window_title":       "",
        "process_id":         None,
        "screen_resolution":  [1920, 1080],
        "focused_element_id": None,
        "windows":            [],
        "elements":           [],
        "source":             "uia_unavailable",
        "error":              reason,
    }
