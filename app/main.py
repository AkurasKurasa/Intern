"""
app/main.py — Intern Recorder
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog
from datetime import datetime

# Windows consoles default to cp1252 → printing Unicode (→, …) crashes. Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT    = os.path.dirname(_APP_DIR)
_COMP    = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recorder.recorder import ScreenObserver, DemoRecorder

# ── Design tokens ─────────────────────────────────────────────────────────────
BG       = "#0d0f18"
CARD     = "#13151f"
CARD2    = "#1a1d2e"
ACCENT   = "#6c63ff"
ACCENT2  = "#4b44c2"
GREEN    = "#22c55e"
GREEN2   = "#16a34a"
RED      = "#ef4444"
RED2     = "#b91c1c"
AMBER    = "#f59e0b"
TEXT     = "#e2e8f0"
DIM      = "#64748b"
BORDER   = "#1e2235"

F_TITLE  = ("Segoe UI", 11, "bold")
F_LABEL  = ("Segoe UI", 9)
F_SMALL  = ("Segoe UI", 8)
F_MONO   = ("Consolas", 9)
F_BIG    = ("Segoe UI", 32, "bold")
F_MED    = ("Segoe UI", 18, "bold")
F_STATUS = ("Segoe UI", 12, "bold")


def _sep(parent, color=BORDER):
    tk.Frame(parent, bg=color, height=1).pack(fill="x", padx=20, pady=6)


def _card(parent, **kw):
    return tk.Frame(parent, bg=CARD,
                    highlightbackground=BORDER, highlightthickness=1,
                    **kw)


def _btn(parent, text, bg, fg="white", cmd=None, state="normal", px=16, py=6):
    b = tk.Button(parent, text=text, font=F_LABEL, bg=bg, fg=fg,
                  bd=0, relief="flat", padx=px, pady=py,
                  cursor="hand2", activeforeground="white",
                  activebackground=ACCENT2, command=cmd, state=state)
    _hover_bg = ACCENT2
    b.bind("<Enter>", lambda e: b.config(bg=_hover_bg) if b["state"] == "normal" else None)
    b.bind("<Leave>", lambda e: b.config(bg=bg) if b["state"] == "normal" else None)
    return b


# ══════════════════════════════════════════════════════════════════════════════
#  BC Demo Recorder panel
# ══════════════════════════════════════════════════════════════════════════════
class DemoPanel(tk.Frame):

    def __init__(self, parent, log_fn):
        super().__init__(parent, bg=BG)
        self._log     = log_fn
        self._running = False
        self._frames  = 0
        self._sessions= 0
        self._out_dir = tk.StringVar(value=os.path.join(_ROOT, "data", "demos", "three_Tabs"))
        self._replay_n = 5          # default replay count for the F8 hotkey
        self._build()
        self._start_hotkeys()

    def _start_hotkeys(self):
        # Global F8 → replay the newest recorded session, no dialogs (so the
        # form keeps focus). Press F8 with the form focused.
        try:
            from pynput import keyboard as _kb
            self._hk = _kb.GlobalHotKeys({"<f8>": self._replay_hotkey})
            self._hk.daemon = True
            self._hk.start()
        except Exception as _e:
            self._log(f"Hotkey init failed: {_e}", "dim")

    def _replay_hotkey(self):
        # Triggered from the pynput listener thread — find newest session and go.
        self.after(0, lambda: self._log("F8 pressed.", "dim"))
        if self._running:
            self.after(0, lambda: self._log(
                "F8 ignored — STOP recording first, then press F8.", "dim"))
            return
        import glob as _glob
        base = self._out_dir.get()
        cands = [d for d in _glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
        if not cands:
            self.after(0, lambda: self._log(f"F8: no sessions in {base}", "dim"))
            return
        src = max(cands, key=os.path.getmtime)
        # bounce to the Tk thread to launch (keeps UI state consistent)
        self.after(0, lambda: self._do_replay(src, self._replay_n))

    def _build(self):
        # ── Title row ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(hdr, text="BC Demo Recorder", font=F_TITLE,
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="action-triggered · F9 toggle · F10 save · F8 replay-newest",
                 font=F_SMALL, bg=BG, fg=DIM).pack(side="left", padx=(10, 0), pady=(2, 0))

        _sep(self)

        # ── Stats row ────────────────────────────────────────────────────────
        stats = tk.Frame(self, bg=BG)
        stats.pack(fill="x", padx=20, pady=(0, 8))
        stats.columnconfigure(0, weight=1)
        stats.columnconfigure(1, weight=1)
        stats.columnconfigure(2, weight=1)

        self._dot_lbl, _ = self._stat_col(stats, 0, "Status", "●  Idle", DIM)
        self._frame_lbl, _ = self._stat_col(stats, 1, "Frames", "0", TEXT)
        self._session_lbl, _ = self._stat_col(stats, 2, "Sessions", "0", TEXT)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_bar = tk.Label(self, text="Ready — click Start Demo to begin",
                                     font=F_SMALL, bg=CARD2, fg=DIM,
                                     anchor="w", padx=20, pady=6)
        self._status_bar.pack(fill="x", padx=20, pady=(0, 10))

        # ── Output dir ───────────────────────────────────────────────────────
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(row, text="Save to", font=F_LABEL, bg=BG, fg=DIM,
                 width=7, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=self._out_dir, bg=CARD2, fg=TEXT,
                 bd=0, relief="flat", insertbackground=TEXT,
                 font=F_MONO).pack(side="left", fill="x", expand=True,
                                   ipady=5, padx=(0, 8))
        _btn(row, "Browse", CARD2, DIM, self._browse, px=10, py=5).pack(side="left")

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(0, 16))

        self._start_btn = _btn(btn_row, "⏺  Start Demo", GREEN, cmd=self._start, px=20, py=8)
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = _btn(btn_row, "⏹  Stop & Save", RED, cmd=self._stop,
                               state="disabled", px=20, py=8)
        self._stop_btn.pack(side="left")

        self._replay_btn = _btn(btn_row, "⟳  Replay ×N", ACCENT, cmd=self._replay,
                                px=16, py=8)
        self._replay_btn.pack(side="left", padx=(8, 0))

    def _stat_col(self, parent, col, label, value, fg):
        f = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                     highlightthickness=1, padx=14, pady=10)
        f.grid(row=0, column=col, sticky="nsew",
               padx=(0 if col > 0 else 0, 6 if col < 2 else 0))
        tk.Label(f, text=label, font=F_SMALL, bg=CARD, fg=DIM).pack(anchor="w")
        val = tk.Label(f, text=value, font=F_MED, bg=CARD, fg=fg)
        val.pack(anchor="w")
        return val, f

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._out_dir.get())
        if d:
            self._out_dir.set(d)

    def _start(self):
        if self._running:
            return
        self._running = True
        self._frames  = 0
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._replay_btn.config(state="disabled")   # can't replay mid-recording
        self._dot_lbl.config(text="●  Recording", fg=GREEN)
        self._status_bar.config(text="Recording — fill the form, then click Stop & Save", fg=GREEN, bg=CARD2)
        self._log("Demo recorder started.", "ok")

        self._recorder = DemoRecorder(output_dir=self._out_dir.get(), trace_type="form_filling")

        def _run():
            try:
                self._recorder.run()
                steps = len(self._recorder._steps)
                self.after(0, lambda: self._on_saved(steps))
            except Exception as exc:
                _m = str(exc)
                self.after(0, lambda m=_m: self._on_error(m))

        threading.Thread(target=_run, daemon=True).start()
        self._poll()

    def _replay(self):
        if self._running:
            self._log("Replay blocked — STOP recording first.", "err")
            return
        import glob as _glob
        base = self._out_dir.get()
        cands = [d for d in _glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
        if not cands:
            self._log(f"No sessions to duplicate in {base}", "dim")
            return
        src = max(cands, key=os.path.getmtime)   # newest = what you just recorded
        from tkinter import simpledialog
        n = simpledialog.askinteger("Replay ×N",
                                    f"Duplicate '{os.path.basename(src)}' how many times?",
                                    initialvalue=10, minvalue=1, maxvalue=500)
        if not n:
            return
        self._do_replay(src, n)

    def _do_replay(self, src, n):
        # COPY/PASTE the recorded session N times — pure file duplication, no
        # mouse, no live form. Each copy is a new session folder of identical
        # traces, so training sees the session repeated N times.
        if self._running:
            print("  [replay] BLOCKED — still recording. Click STOP first.", flush=True)
            return
        import shutil, glob as _glob
        from datetime import datetime as _dt
        out_base = os.path.join(_ROOT, "data", "demos", "human")
        os.makedirs(out_base, exist_ok=True)
        step_files = sorted(_glob.glob(os.path.join(src, "live_step_*.json")))
        if not step_files:
            self._log(f"No steps in {os.path.basename(src)} to copy.", "err")
            print(f"  [replay] no steps in {src}", flush=True)
            return
        print(f"\n  [replay] copying '{os.path.basename(src)}' "
              f"({len(step_files)} steps) x{n} ...", flush=True)
        # print the actual step sequence being replicated
        import json as _json
        try:
            from recorder.recorder import _semantic_desc as _sd
        except Exception:
            _sd = None
        print("  [replay] --- steps being replicated ---", flush=True)
        for si, sf in enumerate(step_files):
            try:
                t = _json.load(open(sf, encoding="utf-8"))
                m = t.get("mouse", {}).get("actions", [])
                k = t.get("keyboard", {}).get("actions", [])
                if _sd:
                    at = "click" if m else ("keyboard" if k else "?")
                    cp = m[0].get("position") if m else None
                    txt = "".join(s.get("pasted_text") or s.get("key", "")
                                  for s in (k[0].get("strokes", []) if k else []))
                    hk = k[0].get("hotkey", "") if k else ""
                    desc = _sd(at, cp, txt, [], t.get("next_state", {}), hotkey=hk)
                else:
                    desc = (m[0].get("type") if m else "kbd")
                print(f"  [replay]   [{si:04d}] {desc}", flush=True)
            except Exception as _e:
                print(f"  [replay]   [{si:04d}] <parse error: {_e}>", flush=True)
        print("  [replay] -----------------------------------", flush=True)
        made = 0
        for i in range(n):
            ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")
            dst = os.path.join(out_base, f"session_copy_{ts}_{i:03d}")
            try:
                shutil.copytree(src, dst)
                made += 1
                print(f"  [replay] [{i+1:>3}/{n}] -> {os.path.basename(dst)}", flush=True)
            except Exception as exc:
                self._log(f"Copy {i+1} failed: {exc}", "err")
                print(f"  [replay] copy {i+1} FAILED: {exc}", flush=True)
        self._sessions += made
        self._session_lbl.config(text=str(self._sessions), fg=GREEN)
        self._status_bar.config(
            text=f"Copied ×{made} → data/demos/human ({len(step_files)} steps each)",
            fg=ACCENT, bg=CARD2)
        self._log(f"Replay = {made} copies of '{os.path.basename(src)}' "
                  f"({len(step_files)} steps each) → data/demos/human", "ok")
        print(f"  [replay] DONE — {made} copies x {len(step_files)} steps "
              f"-> data/demos/human\n", flush=True)

    def _stop(self):
        if hasattr(self, "_recorder") and self._running:
            self._recorder._quit_event.set()

    def _poll(self):
        if not self._running:
            return
        if hasattr(self, "_recorder"):
            with self._recorder._lock:
                self._frames = len(self._recorder._steps)
            self._frame_lbl.config(text=str(self._frames))
        self.after(300, self._poll)

    def _on_saved(self, steps):
        self._running  = False
        self._sessions += 1
        self._dot_lbl.config(text="●  Idle", fg=DIM)
        self._frame_lbl.config(text=str(steps))
        self._session_lbl.config(text=str(self._sessions), fg=GREEN)
        self._status_bar.config(
            text=f"Saved — {steps} frames · {self._sessions} session(s) total", fg=ACCENT, bg=CARD2)
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._replay_btn.config(state="normal")   # now safe to replay
        self._log(f"Session saved — {steps} frames. Now click Replay ×N to repeat it.", "ok")

    def _on_error(self, msg):
        self._running = False
        self._dot_lbl.config(text="●  Error", fg=RED)
        self._status_bar.config(text=f"Error: {msg}", fg=RED, bg=CARD2)
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._replay_btn.config(state="normal")
        self._log(f"Error: {msg}", "err")


# ══════════════════════════════════════════════════════════════════════════════
#  Screen Observer panel (secondary)
# ══════════════════════════════════════════════════════════════════════════════
class ObserverPanel(tk.Frame):

    def __init__(self, parent, log_fn):
        super().__init__(parent, bg=BG)
        self._log      = log_fn
        self._observer = None
        self._recording= False
        self._start_t  = None
        self._out_dir  = tk.StringVar(value=os.path.join(_ROOT, "data", "output", "traces", "live"))
        self._interval = tk.DoubleVar(value=2.0)
        self._build()
        self._tick()

    def _build(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(hdr, text="Screen Observer", font=F_TITLE,
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="time-based · for general capture",
                 font=F_SMALL, bg=BG, fg=DIM).pack(side="left", padx=(10, 0), pady=(2, 0))

        _sep(self)

        # Status + controls row
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 8))

        dot_col = tk.Frame(row, bg=BG)
        dot_col.pack(side="left")
        self._dot = tk.Label(dot_col, text="●", font=("Segoe UI", 16),
                              bg=BG, fg=DIM)
        self._dot.pack()
        self._timer = tk.Label(dot_col, text="00:00", font=F_MONO, bg=BG, fg=DIM)
        self._timer.pack()

        mid = tk.Frame(row, bg=BG)
        mid.pack(side="left", fill="x", expand=True, padx=(12, 12))

        r1 = tk.Frame(mid, bg=BG)
        r1.pack(fill="x", pady=(0, 4))
        tk.Label(r1, text="Output", font=F_LABEL, bg=BG, fg=DIM,
                 width=7, anchor="w").pack(side="left")
        tk.Entry(r1, textvariable=self._out_dir, bg=CARD2, fg=TEXT,
                 bd=0, relief="flat", insertbackground=TEXT,
                 font=F_MONO).pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        _btn(r1, "Browse", CARD2, DIM, self._browse, px=8, py=4).pack(side="left")

        r2 = tk.Frame(mid, bg=BG)
        r2.pack(fill="x")
        tk.Label(r2, text="Interval", font=F_LABEL, bg=BG, fg=DIM,
                 width=7, anchor="w").pack(side="left")
        tk.Spinbox(r2, textvariable=self._interval, from_=0.5, to=10.0,
                   increment=0.5, width=5, bg=CARD2, fg=TEXT,
                   buttonbackground=CARD2, bd=0, font=F_MONO,
                   relief="flat").pack(side="left", ipady=4)
        tk.Label(r2, text="sec", font=F_SMALL, bg=BG, fg=DIM).pack(side="left", padx=(4, 0))

        btn_col = tk.Frame(row, bg=BG)
        btn_col.pack(side="right")
        self._start_btn = _btn(btn_col, "▶  Start", ACCENT, cmd=self._start, px=14, py=6)
        self._start_btn.pack(pady=(0, 4))
        self._stop_btn = _btn(btn_col, "■  Stop", RED, cmd=self._stop,
                               state="disabled", px=14, py=6)
        self._stop_btn.pack()

        # Frames counter
        self._frame_lbl = tk.Label(self, text="0 frames captured",
                                    font=F_SMALL, bg=BG, fg=DIM)
        self._frame_lbl.pack(anchor="w", padx=20, pady=(0, 12))

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self._out_dir.get())
        if d:
            self._out_dir.set(d)

    def _start(self):
        if self._recording:
            return
        try:
            self._observer = ScreenObserver(output_dir=self._out_dir.get(), trace_type="gui")
            self._observer.start(interval_sec=self._interval.get())
            self._recording = True
            self._start_t   = datetime.now()
            self._dot.config(fg=GREEN)
            self._start_btn.config(state="disabled")
            self._stop_btn.config(state="normal")
            self._log(f"Observer started → {self._out_dir.get()}", "ok")
            self._poll()
        except Exception as exc:
            self._log(f"Error: {exc}", "err")

    def _stop(self):
        if not self._recording:
            return
        self._recording = False
        self._dot.config(fg=DIM)
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")

        def _do():
            try:
                traces = self._observer.stop()
                self.after(0, lambda: self._frame_lbl.config(
                    text=f"{len(traces)} frames saved", fg=GREEN))
                self.after(0, lambda: self._log(f"Observer stopped — {len(traces)} frames.", "ok"))
            except Exception as exc:
                _m = str(exc)
                self.after(0, lambda m=_m: self._log(f"Error: {m}", "err"))

        threading.Thread(target=_do, daemon=True).start()

    def _poll(self):
        if not self._recording:
            return
        if self._observer:
            n = len(self._observer._frames)
            self._frame_lbl.config(text=f"{n} frames captured", fg=TEXT)
        self.after(500, self._poll)

    def _tick(self):
        if self._recording and self._start_t:
            s = int((datetime.now() - self._start_t).total_seconds())
            self._timer.config(text=f"{s//60:02d}:{s%60:02d}", fg=GREEN)
        else:
            self._timer.config(text="00:00", fg=DIM)
        self.after(1000, self._tick)


# ══════════════════════════════════════════════════════════════════════════════
#  Main App
# ══════════════════════════════════════════════════════════════════════════════
class InternApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Intern — BC Recorder")
        self.geometry("860x700")
        self.minsize(700, 560)
        self.configure(bg=BG)
        self._build()

    def _build(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG, pady=12)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="[I]", font=("Segoe UI", 18, "bold"),
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="  Intern", font=("Segoe UI", 13),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="Behavioral Cloning Recorder",
                 font=("Segoe UI", 9), bg=BG, fg=DIM).pack(side="left", padx=(8, 0), pady=(3, 0))
        self._clock = tk.Label(hdr, font=F_MONO, bg=BG, fg=DIM)
        self._clock.pack(side="right")
        self._tick_clock()

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Body: left column ─────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        # Demo recorder (primary)
        demo_card = _card(left)
        demo_card.pack(fill="x", padx=12, pady=(12, 6))
        DemoPanel(demo_card, self._log).pack(fill="both", expand=True)

        # Observer (secondary)
        obs_card = _card(left)
        obs_card.pack(fill="x", padx=12, pady=(0, 6))
        ObserverPanel(obs_card, self._log).pack(fill="both", expand=True)

        # ── Right column: log ─────────────────────────────────────────────────
        right = tk.Frame(body, bg=BG, width=280)
        right.pack(side="right", fill="y", padx=(0, 12), pady=12)
        right.pack_propagate(False)

        tk.Label(right, text="Activity Log", font=F_TITLE,
                 bg=BG, fg=TEXT).pack(anchor="w", pady=(4, 6))

        self._log_box = tk.Text(right, bg=CARD, fg=DIM, bd=0, relief="flat",
                                 font=F_MONO, wrap="word",
                                 insertbackground=TEXT, state="disabled")
        self._log_box.pack(fill="both", expand=True)
        self._log_box.tag_configure("ok",  foreground=GREEN)
        self._log_box.tag_configure("err", foreground=RED)
        self._log_box.tag_configure("dim", foreground=DIM)

    def _log(self, msg: str, tag: str = "dim"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.config(state="normal")
        self._log_box.insert("end", f"[{ts}]\n", "dim")
        self._log_box.insert("end", msg + "\n\n", tag)
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _tick_clock(self):
        self._clock.config(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        self.after(1000, self._tick_clock)


if __name__ == "__main__":
    app = InternApp()
    app.mainloop()
