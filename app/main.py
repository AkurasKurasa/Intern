"""
app/main.py — Intern Control Panel
====================================
Tabbed GUI with:
  • Control Panel — start / stop ScreenObserver recording
  • Workflow Builder — visual n8n-style pipeline editor

Run with: python app/main.py
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from datetime import datetime

# ── path setup ────────────────────────────────────────────────────────────────
_APP_DIR    = os.path.dirname(os.path.abspath(__file__))   # app/
_ROOT       = os.path.dirname(_APP_DIR)                    # Intern/
_COMP       = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from screen_observer.screen_observer import ScreenObserver
from workflow_builder.workflow_builder import WorkflowBuilderPanel

# ══════════════════════════════════════════════════════════════════════════════
#  Design tokens
# ══════════════════════════════════════════════════════════════════════════════
BG          = "#0f1117"
BG_CARD     = "#1a1d27"
BG_HOVER    = "#22263a"
ACCENT      = "#6c63ff"
ACCENT_DIM  = "#4b44c2"
SUCCESS     = "#22c55e"
DANGER      = "#ef4444"
TEXT        = "#e2e8f0"
TEXT_DIM    = "#64748b"
BORDER      = "#2d3148"

FONT_TITLE  = ("Segoe UI", 20, "bold")
FONT_LABEL  = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI", 9)
FONT_MONO   = ("Consolas", 9)
FONT_STATUS = ("Segoe UI", 13, "bold")
FONT_COUNT  = ("Segoe UI", 28, "bold")


# ══════════════════════════════════════════════════════════════════════════════
#  Control Panel tab (original functionality)
# ══════════════════════════════════════════════════════════════════════════════
class ControlPanelTab(tk.Frame):

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self._observer: ScreenObserver | None = None
        self._recording = False
        self._frame_count = 0
        self._trace_count = 0
        self._start_time: datetime | None = None
        self._timer_id = None

        self._output_dir = tk.StringVar(value="data/output/traces/live")
        self._interval   = tk.DoubleVar(value=2.0)
        self._trace_type = tk.StringVar(value="gui")

        self._build_ui()
        self._tick_timer()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG, pady=24)
        hdr.pack(fill="x", padx=28)

        tk.Label(hdr, text="[I]  Intern", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="Control Panel", font=("Segoe UI", 11),
                 bg=BG, fg=TEXT_DIM).pack(side="left", padx=(8, 0), pady=(6, 0))

        # Status card
        self._status_card = self._card(self)
        self._status_card.pack(fill="x", padx=28, pady=(0, 14))
        self._build_status_card(self._status_card)

        # Stats row
        stats_row = tk.Frame(self, bg=BG)
        stats_row.pack(fill="x", padx=28, pady=(0, 14))
        stats_row.columnconfigure(0, weight=1)
        stats_row.columnconfigure(1, weight=1)

        self._frame_card = self._stat_card(stats_row, "Frames")
        self._frame_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._frame_lbl = self._frame_card._count_lbl

        self._trace_card = self._stat_card(stats_row, "Traces Saved")
        self._trace_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._trace_lbl = self._trace_card._count_lbl

        # Settings card
        cfg = self._card(self)
        cfg.pack(fill="x", padx=28, pady=(0, 14))
        self._build_settings(cfg)

        # Log card
        log = self._card(self)
        log.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        self._build_log(log)

    def _build_status_card(self, parent):
        row = tk.Frame(parent, bg=BG_CARD)
        row.pack(fill="x")

        # Indicator dot
        self._dot = tk.Label(row, text="●", font=("Segoe UI", 18),
                               bg=BG_CARD, fg=TEXT_DIM)
        self._dot.pack(side="left", padx=(0, 12))

        col = tk.Frame(row, bg=BG_CARD)
        col.pack(side="left", fill="x", expand=True)

        self._status_lbl = tk.Label(col, text="Idle", font=FONT_STATUS,
                                     bg=BG_CARD, fg=TEXT_DIM)
        self._status_lbl.pack(anchor="w")

        self._timer_lbl = tk.Label(col, text="00:00:00", font=FONT_MONO,
                                    bg=BG_CARD, fg=TEXT_DIM)
        self._timer_lbl.pack(anchor="w")

        # Buttons
        btn_row = tk.Frame(row, bg=BG_CARD)
        btn_row.pack(side="right")

        self._start_btn = self._btn(btn_row, "▶  Start", ACCENT, self._start)
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = self._btn(btn_row, "■  Stop", DANGER, self._stop, state="disabled")
        self._stop_btn.pack(side="left")

    def _build_settings(self, parent):
        tk.Label(parent, text="Settings", font=("Segoe UI", 10, "bold"),
                 bg=BG_CARD, fg=TEXT_DIM).pack(anchor="w", pady=(0, 10))

        # Output dir
        row1 = tk.Frame(parent, bg=BG_CARD)
        row1.pack(fill="x", pady=(0, 8))
        tk.Label(row1, text="Output directory", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT, width=18, anchor="w").pack(side="left")
        dir_entry = tk.Entry(row1, textvariable=self._output_dir,
                              bg=BG_HOVER, fg=TEXT, bd=0,
                              insertbackground=TEXT, font=FONT_MONO,
                              relief="flat")
        dir_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
        self._btn(row1, "Browse", BORDER, self._browse_dir,
                  padx=10, pady=4).pack(side="left")

        # Interval
        row2 = tk.Frame(parent, bg=BG_CARD)
        row2.pack(fill="x", pady=(0, 8))
        tk.Label(row2, text="Capture interval (s)", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT, width=18, anchor="w").pack(side="left")
        tk.Spinbox(row2, textvariable=self._interval, from_=0.5, to=10.0,
                   increment=0.5, width=6, bg=BG_HOVER, fg=TEXT,
                   buttonbackground=BG_HOVER, bd=0, font=FONT_MONO,
                   relief="flat").pack(side="left", ipady=4)

        # Trace type
        row3 = tk.Frame(parent, bg=BG_CARD)
        row3.pack(fill="x")
        tk.Label(row3, text="Trace type", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT, width=18, anchor="w").pack(side="left")
        for val, label in [("gui", "GUI"), ("web", "Web"), ("excel", "Excel")]:
            tk.Radiobutton(row3, text=label, variable=self._trace_type, value=val,
                           bg=BG_CARD, fg=TEXT, selectcolor=ACCENT_DIM,
                           activebackground=BG_CARD, activeforeground=TEXT,
                           font=FONT_LABEL).pack(side="left", padx=(0, 12))

    def _build_log(self, parent):
        tk.Label(parent, text="Activity Log", font=("Segoe UI", 10, "bold"),
                 bg=BG_CARD, fg=TEXT_DIM).pack(anchor="w", pady=(0, 8))
        self._log = tk.Text(parent, bg=BG, fg=TEXT_DIM, bd=0, relief="flat",
                             font=FONT_MONO, height=8, state="disabled",
                             wrap="word", insertbackground=TEXT)
        self._log.pack(fill="both", expand=True)
        self._log.tag_configure("ok",      foreground=SUCCESS)
        self._log.tag_configure("err",     foreground=DANGER)
        self._log.tag_configure("accent",  foreground=ACCENT)
        self._log.tag_configure("dim",     foreground=TEXT_DIM)

    # ── Widget helpers ────────────────────────────────────────────────────────

    def _card(self, parent, pady=16, padx=18):
        f = tk.Frame(parent, bg=BG_CARD, padx=padx, pady=pady,
                     highlightbackground=BORDER, highlightthickness=1)
        return f

    def _btn(self, parent, text, color, cmd, state="normal", padx=18, pady=7):
        b = tk.Button(parent, text=text, font=FONT_LABEL,
                       bg=color, fg="white", bd=0, relief="flat",
                       padx=padx, pady=pady, cursor="hand2",
                       activebackground=ACCENT_DIM, activeforeground="white",
                       command=cmd, state=state)
        b.bind("<Enter>", lambda e: b.config(bg=ACCENT_DIM if color != DANGER else "#c53030"))
        b.bind("<Leave>", lambda e: b.config(bg=color))
        return b

    def _stat_card(self, parent, label):
        frame = self._card(parent, pady=14, padx=16)
        tk.Label(frame, text=label, font=FONT_SMALL,
                 bg=BG_CARD, fg=TEXT_DIM).pack(anchor="w")
        count = tk.Label(frame, text="0", font=FONT_COUNT,
                          bg=BG_CARD, fg=TEXT)
        count.pack(anchor="w")
        frame._count_lbl = count
        return frame

    # ── Actions ───────────────────────────────────────────────────────────────

    def _start(self):
        if self._recording:
            return
        try:
            output = self._output_dir.get().strip() or "data/output/traces/live"
            self._observer = ScreenObserver(
                output_dir=output,
                trace_type=self._trace_type.get(),
            )
            self._observer.start(interval_sec=self._interval.get())
            self._recording   = True
            self._start_time  = datetime.now()
            self._frame_count = 0
            self._trace_count = 0
            self._set_recording_state(True)
            self._log_msg(f"Started recording to {output}", "ok")
            self._poll_frames()
        except Exception as exc:
            self._log_msg(f"Error: {exc}", "err")

    def _stop(self):
        if not self._recording:
            return
        self._recording = False
        self._set_recording_state(False)
        self._log_msg("Stopping — translating frames...", "accent")

        def _do_stop():
            try:
                traces = self._observer.stop()
                self._trace_count = len(traces)
                self.after(0, lambda: self._trace_lbl.config(
                    text=str(self._trace_count), fg=SUCCESS))
                self.after(0, lambda: self._log_msg(
                    f"Done — {len(traces)} trace(s) saved.", "ok"))
            except Exception as exc:
                self.after(0, lambda: self._log_msg(f"Error: {exc}", "err"))

        threading.Thread(target=_do_stop, daemon=True).start()

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self._output_dir.get())
        if d:
            self._output_dir.set(d)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _set_recording_state(self, recording: bool):
        if recording:
            self._dot.config(fg=SUCCESS)
            self._status_lbl.config(text="Recording", fg=SUCCESS)
            self._start_btn.config(state="disabled")
            self._stop_btn.config(state="normal")
        else:
            self._dot.config(fg=TEXT_DIM)
            self._status_lbl.config(text="Idle", fg=TEXT_DIM)
            self._start_btn.config(state="normal")
            self._stop_btn.config(state="disabled")
            self._timer_lbl.config(text="00:00:00")

    def _tick_timer(self):
        if self._recording and self._start_time:
            elapsed = int((datetime.now() - self._start_time).total_seconds())
            h, r = divmod(elapsed, 3600)
            m, s = divmod(r, 60)
            self._timer_lbl.config(text=f"{h:02d}:{m:02d}:{s:02d}", fg=SUCCESS)
        self._timer_id = self.after(1000, self._tick_timer)

    def _poll_frames(self):
        if not self._recording:
            return
        if self._observer:
            self._frame_count = len(self._observer._frames)
            self._frame_lbl.config(text=str(self._frame_count))
        self.after(500, self._poll_frames)

    def _log_msg(self, msg: str, tag: str = "dim"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.config(state="normal")
        self._log.insert("end", f"[{ts}] ", "dim")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application  — tabbed shell
# ══════════════════════════════════════════════════════════════════════════════
class InternApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Intern — AI Automation Suite")
        self.geometry("1100x740")
        self.minsize(800, 580)
        self.configure(bg=BG)

        self._setup_styles()
        self._build_header()
        self._build_tabs()

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",
                         background=BG, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab",
                         background=BG_HOVER, foreground=TEXT_DIM,
                         font=("Segoe UI", 10, "bold"),
                         padding=[18, 9], borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.configure("TFrame", background=BG)

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG, pady=14)
        hdr.pack(fill="x", padx=24)
        tk.Label(hdr, text="[I]", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="  Intern — AI Automation Suite",
                 font=("Segoe UI", 14), bg=BG, fg=TEXT).pack(side="left")
        # timestamp
        self._clock = tk.Label(hdr, font=FONT_MONO, bg=BG, fg=TEXT_DIM)
        self._clock.pack(side="right")
        self._tick_clock()

    def _tick_clock(self):
        self._clock.config(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ── Tab 1: Control Panel ─────────────────────────────────────────────
        cp_outer = tk.Frame(nb, bg=BG)
        nb.add(cp_outer, text="🎛  Control Panel")
        ControlPanelTab(cp_outer).pack(fill="both", expand=True)

        # ── Tab 2: Workflow Builder ──────────────────────────────────────────
        wb_outer = tk.Frame(nb, bg=BG)
        nb.add(wb_outer, text="🔗  Workflow Builder")
        WorkflowBuilderPanel(wb_outer).pack(fill="both", expand=True)


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = InternApp()
    app.mainloop()
