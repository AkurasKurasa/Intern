"""
tests/test_ghost_overlay.py
============================
Regression tests for agent/ghost_overlay.py -- the "Intern"-branded ghost
cursor/caret overlay, direct user request ("We need a ghost cursor and
ghost caret real bad") to replace hijacking the real OS mouse cursor
during a live run with a decoupled, purely visual indicator.

Exactly ONE test in this file spawns a real Tk window/thread, and it runs
in a fully isolated subprocess rather than in-process -- confirmed the
hard way why this matters: an in-process version of this same test
(destroying a real tk.Tk() root created on a background thread, then
letting the test process continue) reliably reproduced Tcl's own
"Tcl_AsyncDelete: async handler deleted by the wrong thread" -- a real,
known Tcl/Tk limitation (an interpreter's teardown expects to happen on
the thread that created it; Python's own interpreter shutdown doesn't
guarantee that for a background-thread-owned Tk root). In isolation this
just prints a scary-looking but harmless line during exit; left running
inside the shared pytest process, later, UNRELATED garbage collection
(observed: plain MagicMock creation in a completely different test class)
hit corrupted state and hard-crashed with "Windows fatal exception: code
0x80000003" -- taking the entire ~500-test suite down with it, not just
this one test. Isolating it into its own subprocess confines any Tcl/Tk
teardown fallout to that throwaway child process, which the OS fully
reaps on exit -- the parent pytest process's memory/GC state is never
touched. (Production is not exposed to this: a live run's GhostOverlay
lives for the process's entire lifetime and is torn down by the same
process exit that would print this same harmless noise on its way out,
never followed by further work that could crash on it -- see
DEVELOPERS.md for the equivalent risk analysis.) Everything else in this
file (window setup logic, tkinter-missing fallback, click-through window
style) is tested through pure mocks that never touch a real Tk root.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import agent.ghost_overlay as go

_LIFECYCLE_SCRIPT = r"""
import sys, time
sys.path.insert(0, {components_dir!r})
from agent.ghost_overlay import GhostOverlay

overlay = GhostOverlay()
overlay.start()
assert overlay._ready.is_set(), "never became ready"

t0 = time.time()
overlay.show_cursor(100, 200)
overlay.hide_cursor()
overlay.show_caret(50, 60, 18)
overlay.hide_caret()
assert time.time() - t0 < 0.5, "show/hide blocked"

first_thread = overlay._thread
overlay.start()  # must not spawn a second root/thread
assert overlay._thread is first_thread, "start() was not idempotent"

overlay.stop()
assert overlay._thread is None, "stop() did not clear the thread"
overlay.stop()  # stopping an already-stopped overlay must not raise

print("LIFECYCLE_OK")
"""


class TestRealOverlayLifecycle:
    """The one test in this file with a real Tk window/thread -- see the
    module docstring for why it runs in its own subprocess."""

    def test_full_lifecycle_start_show_hide_idempotent_stop(self):
        components_dir = str(Path(__file__).resolve().parent.parent / "components")
        script = _LIFECYCLE_SCRIPT.format(components_dir=components_dir)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=20,
        )
        assert "LIFECYCLE_OK" in result.stdout, (
            f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )


class TestNoOpWithoutStarting:
    def test_calls_before_start_do_not_raise_or_block(self):
        overlay = go.GhostOverlay()
        # Nothing is draining the queue yet -- queue.Queue.put() with no
        # maxsize never blocks, so this must return instantly regardless.
        overlay.show_cursor(1, 1)
        overlay.hide_cursor()

    def test_stop_without_start_does_not_raise(self):
        overlay = go.GhostOverlay()
        overlay.stop()


class TestGracefulDegradationWithoutTkinter:
    """_run() imports tkinter lazily, inside the thread, specifically so a
    missing/broken tkinter can't hang or crash the caller. Simulating the
    import failure means _run() returns immediately after setting _ready
    -- no real Tk root ever gets created in these two tests."""

    def test_start_does_not_hang_if_tkinter_import_fails(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "tkinter":
                raise ImportError("simulated missing tkinter")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        overlay = go.GhostOverlay()
        overlay.start()
        assert overlay._ready.is_set()

    def test_show_cursor_after_a_failed_start_does_not_raise(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "tkinter":
                raise ImportError("simulated missing tkinter")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        overlay = go.GhostOverlay()
        overlay.start()
        overlay.show_cursor(10, 10)  # nobody's draining the queue -- must not raise/block


class TestClickThroughWindowStyle:
    """The one thing that would silently break every live click if it
    regressed: WS_EX_TRANSPARENT must actually get OR'd into the window's
    extended style, or the overlay's own drawn pixels would sit in front
    of -- and swallow clicks meant for -- the real target control.

    `user32` is passed in explicitly (dependency injection) rather than
    patching the real `ctypes.windll` singleton -- that was tried first
    and triggered the same class of low-level crash described in the
    module docstring: ctypes.windll is a live, C-backed, process-wide
    object, not an ordinary attribute safe to swap out mid-process."""

    def test_sets_ws_ex_transparent_and_ws_ex_layered(self):
        fake_root = MagicMock()
        fake_root.winfo_id.return_value = 12345
        fake_user32 = MagicMock()
        fake_user32.GetAncestor.return_value = 99999  # the real top-level window
        fake_user32.GetWindowLongW.return_value = 0

        go.GhostOverlay._make_click_through(fake_root, user32=fake_user32)

        args, _ = fake_user32.SetWindowLongW.call_args
        hwnd, gwl_exstyle, new_style = args
        assert hwnd == 99999
        assert gwl_exstyle == go._GWL_EXSTYLE
        assert new_style & go._WS_EX_TRANSPARENT
        assert new_style & go._WS_EX_LAYERED

    def test_operates_on_the_true_top_level_window_not_the_child(self):
        """Found live 2026-08-11: root.winfo_id() for a plain Tk() root
        returns a CHILD window (class 'TkChild'), not the actual
        top-level window Windows composites and manages (class
        'TkTopLevel', one level up via GetAncestor(hwnd, GA_ROOT)) --
        confirmed directly, live, with a real cropped screen capture.
        Every style bit this method sets must land on the resolved
        top-level ancestor, not the raw winfo_id() child, or the fix
        does nothing."""
        fake_root = MagicMock()
        fake_root.winfo_id.return_value = 555  # the child
        fake_user32 = MagicMock()
        fake_user32.GetAncestor.return_value = 777  # the true top-level
        fake_user32.GetWindowLongW.return_value = 0

        go.GhostOverlay._make_click_through(fake_root, user32=fake_user32)

        fake_user32.GetAncestor.assert_called_once_with(555, go._GA_ROOT)
        assert fake_user32.GetWindowLongW.call_args[0][0] == 777
        assert fake_user32.SetWindowLongW.call_args[0][0] == 777

    def test_falls_back_to_the_child_hwnd_if_get_ancestor_returns_nothing(self):
        """GetAncestor can legitimately return NULL (0) if the window has
        no such ancestor -- degrade to the old (imperfect but
        non-crashing) behavior rather than pass a null hwnd to
        SetWindowLongW."""
        fake_root = MagicMock()
        fake_root.winfo_id.return_value = 555
        fake_user32 = MagicMock()
        fake_user32.GetAncestor.return_value = 0
        fake_user32.GetWindowLongW.return_value = 0

        go.GhostOverlay._make_click_through(fake_root, user32=fake_user32)

        assert fake_user32.SetWindowLongW.call_args[0][0] == 555

    def test_sets_ws_ex_noactivate(self):
        """Found live 2026-08-10: creating this overlay steals OS
        foreground/activation status immediately (confirmed directly --
        GetForegroundWindow() before vs. after overlay.start()), and once
        _reassert_form_window()'s reclaim hands focus back, this overlay
        kept re-taking it, which the streak-breaker (a separate, earlier
        fix) then reads as 'the user is deliberately using another
        window' and permanently stops fighting for -- reported live as
        'clicked the form, literally nothing happened.' WS_EX_TRANSPARENT
        only ever governed mouse hit-testing, never activation; confirmed
        directly (start a real overlay, SetForegroundWindow elsewhere,
        watch for 5+ seconds across simulated steps) that WS_EX_NOACTIVATE
        stops the RECURRING theft -- the part _reassert_form_window() has
        no defense against on its own, since it already handles a single
        one-time steal correctly by design."""
        fake_root = MagicMock()
        fake_root.winfo_id.return_value = 12345
        fake_user32 = MagicMock()
        fake_user32.GetAncestor.return_value = 99999
        fake_user32.GetWindowLongW.return_value = 0

        go.GhostOverlay._make_click_through(fake_root, user32=fake_user32)

        _, _, new_style = fake_user32.SetWindowLongW.call_args[0]
        assert new_style & go._WS_EX_NOACTIVATE

    def test_preserves_existing_extended_style_bits(self):
        """OR's the new flags in rather than overwriting -- an existing
        style bit (simulated here) must survive the call."""
        fake_root = MagicMock()
        fake_root.winfo_id.return_value = 1
        fake_user32 = MagicMock()
        fake_user32.GetAncestor.return_value = 2
        fake_user32.GetWindowLongW.return_value = 0x00040000  # some pre-existing bit

        go.GhostOverlay._make_click_through(fake_root, user32=fake_user32)

        _, _, new_style = fake_user32.SetWindowLongW.call_args[0]
        assert new_style & 0x00040000

    def test_does_not_raise_if_winfo_id_fails(self):
        """A real live overlay must never crash the caller over a window-
        style failure -- worst case is it just intercepts clicks near the
        cursor, logged as a warning, not a crash."""
        fake_root = MagicMock()
        fake_root.winfo_id.side_effect = RuntimeError("boom")
        go.GhostOverlay._make_click_through(fake_root, user32=MagicMock())  # must not raise
