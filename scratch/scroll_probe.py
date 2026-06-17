#!/usr/bin/env python
"""
scroll_probe.py — ISOLATED test: can anything move the Car Insurance form's
ScrolledPanel? No agent, no transformer, no LLM. Just: find a below-fold field,
try each scroll method, report which moved the panel.

Run with the form open:
    python scratch/scroll_probe.py
"""
import time
import uiautomation as uia
import win32gui
import win32con
import win32api

FORM_TITLE_SUB = "Car Insurance"
# A field that is below the fold on the Policyholder tab (so its Y should change
# if the panel scrolls). Pick a few in case names differ.
TARGETS = ["ZIP Code", "City", "Street Address 1", "County", "Drivers License"]


def find_form_hwnd():
    found = []
    def cb(h, _):
        t = win32gui.GetWindowText(h)
        if FORM_TITLE_SUB.lower() in (t or "").lower() and win32gui.IsWindowVisible(h):
            found.append((h, t))
    win32gui.EnumWindows(cb, None)
    return found[0] if found else (None, None)


def find_ctrl(root, name):
    for finder in (root.EditControl, root.ComboBoxControl, root.TextControl):
        c = finder(searchDepth=25, Name=name)
        if c.Exists(maxSearchSeconds=0.4):
            return c
    return None


def y_of(ctrl):
    try:
        r = ctrl.BoundingRectangle
        return r.top
    except Exception:
        return None


def main():
    hwnd, title = find_form_hwnd()
    if not hwnd:
        print("FORM NOT FOUND — open the Car Insurance form first.")
        return
    print(f"Form: {title!r}  hwnd={hwnd}")
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    root = uia.ControlFromHandle(hwnd)

    # Make sure we're on the Policyholder tab (index 1).
    tabs = [c for c in root.GetChildren()]  # not relied on; user should be on Policyholder
    # Find a target field that exists.
    target = None
    for name in TARGETS:
        c = find_ctrl(root, name)
        if c is not None:
            target = (name, c)
            break
    if target is None:
        print(f"None of the target fields {TARGETS} found in the UIA tree.")
        print("→ That itself is the answer: below-fold fields are NOT in the tree until scrolled.")
        return
    name, ctrl = target
    print(f"Target field: {name!r}  initial top-Y = {y_of(ctrl)}")

    def report(method):
        time.sleep(0.4)
        print(f"  after {method}: top-Y = {y_of(ctrl)}")

    # --- Method A: UIA ScrollItemPattern.ScrollIntoView ---
    print("\n[A] ScrollItemPattern.ScrollIntoView()")
    try:
        sip = ctrl.GetScrollItemPattern()
        if sip is None:
            print("  ScrollItemPattern: NOT SUPPORTED (None)")
        else:
            sip.ScrollIntoView()
            report("ScrollIntoView")
    except Exception as e:
        print(f"  ScrollItemPattern error: {e}")

    # --- Method B: SetFocus (wx auto-scroll) ---
    print("\n[B] SetFocus()")
    try:
        ctrl.SetFocus()
        report("SetFocus")
    except Exception as e:
        print(f"  SetFocus error: {e}")

    # --- Method C: ScrollPattern on the scrollable pane ---
    print("\n[C] ScrollPattern.Scroll(LargeIncrement) on ancestor pane")
    try:
        node = ctrl
        sp = None
        for _ in range(12):
            node = node.GetParentControl()
            if node is None:
                break
            try:
                sp = node.GetScrollPattern()
                if sp is not None and sp.VerticallyScrollable:
                    print(f"  scrollable ancestor: {node.ControlTypeName} {node.Name!r}")
                    break
                sp = None
            except Exception:
                sp = None
        if sp is not None:
            sp.Scroll(uia.ScrollAmount.NoAmount, uia.ScrollAmount.LargeIncrement)
            report("ScrollPattern.Scroll")
        else:
            print("  no scrollable-ancestor ScrollPattern found")
    except Exception as e:
        print(f"  ScrollPattern error: {e}")

    # --- Method D: WM_MOUSEWHEEL to the panel hwnd under the field ---
    print("\n[D] WM_MOUSEWHEEL to child window under the field")
    try:
        r = ctrl.BoundingRectangle
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        child = win32gui.WindowFromPoint((cx, cy))
        wparam = (-120) << 16  # one notch down
        lparam = (cy << 16) | (cx & 0xFFFF)
        win32api.SendMessage(child, win32con.WM_MOUSEWHEEL, wparam, lparam)
        report("WM_MOUSEWHEEL")
    except Exception as e:
        print(f"  WM_MOUSEWHEEL error: {e}")

    # --- Method E: WM_VSCROLL SB_PAGEDOWN to the panel hwnd ---
    print("\n[E] WM_VSCROLL SB_PAGEDOWN to child window")
    try:
        r = ctrl.BoundingRectangle
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        child = win32gui.WindowFromPoint((cx, cy))
        win32api.SendMessage(child, win32con.WM_VSCROLL, win32con.SB_PAGEDOWN, 0)
        report("WM_VSCROLL")
    except Exception as e:
        print(f"  WM_VSCROLL error: {e}")

    print("\nDONE. Any method where top-Y CHANGED = that one moves the panel.")


if __name__ == "__main__":
    main()
