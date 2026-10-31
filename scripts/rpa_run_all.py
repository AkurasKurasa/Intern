"""
scripts/rpa_run_all.py
======================
Objective 7: run the whole Intern vs PyAutoGUI-baseline comparison unattended.

LIVE GUI RUN -- this takes over the mouse and keyboard. Do not touch the PC
while it runs. Move the mouse into a screen corner to abort a baseline replay
(pyautogui fail-safe).

Order:
  Gate 1  one baseline replay on the NORMAL form (can the macro drive it?)
  Gate 2  one Intern run on the MESSY form (never run live before)
  runs    baseline x5 normal, baseline x5 modified, Intern x5 normal, Intern x5 modified

Each run opens a fresh copy of the right form, runs the system, waits for the
form's own auto-saved submission, closes the form, and writes one row to the
run sheet (submission file name, finished_without_help, minutes, notes).
Every submission is also copied to docs/rpa_comparison/submissions/ so the
scoring can be repeated from the repo.

Usage
-----
  python scripts/rpa_run_all.py                      # gates + all 20 runs
  python scripts/rpa_run_all.py --only gates
  python scripts/rpa_run_all.py --runs 5 --intern_timeout 900
Then:  python scripts/rpa_score.py --submissions docs/rpa_comparison/submissions
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from rpa_baseline import DEFAULT_MACRO, _dpi_aware, find_form_hwnd, record, replay  # noqa: E402

FORMS = {
    "normal":   ROOT / "practice_apps" / "car_insurance_entry" / "car_insurance_form_wx.py",
    "modified": ROOT / "practice_apps" / "car_insurance_entry" / "car_insurance_form_wx_MESSY_TEST.py",
}
FORM_SUBMISSIONS = ROOT / "practice_apps" / "data" / "output" / "submissions"
KEEP_DIR = ROOT / "docs" / "rpa_comparison" / "submissions"
SHEET = ROOT / "docs" / "rpa_comparison" / "run_sheet.csv"
LOG = ROOT / "docs" / "rpa_comparison" / "run_log.jsonl"
INTAKE = ROOT / "data_entry_tasks" / "data_entry_intake.txt"
BASELINE = "PyAutoGUI"
SHEET_FIELDS = ["system", "form_version", "run", "record", "submission",
                "finished_without_help", "minutes", "notes"]


def _log(event: dict) -> None:
    event = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    print(json.dumps(event), flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def _submissions() -> set[str]:
    return {p.name for p in FORM_SUBMISSIONS.glob("*.json")} if FORM_SUBMISSIONS.exists() else set()


def _wait_new_submission(before: set[str], timeout: float, alive=lambda: True) -> str:
    end = time.time() + timeout
    while time.time() < end:
        new = sorted(_submissions() - before)
        if new:
            time.sleep(0.5)                      # let the write finish
            return new[-1]
        if not alive():
            break
        time.sleep(0.5)
    return ""


def _bring_to_front(hwnd: int) -> None:
    """Click the window's title bar -- more reliable than SetForegroundWindow
    from a background process, and the title bar has no form controls."""
    import pyautogui
    import win32gui
    l, t, r, _ = win32gui.GetWindowRect(hwnd)
    pyautogui.click((l + r) // 2, t + 12)
    time.sleep(0.4)


class Form:
    def __init__(self, version: str):
        self.version = version
        self.proc = subprocess.Popen([sys.executable, str(FORMS[version])], cwd=str(ROOT))
        frag = "(Messy UI Test)" if version == "modified" else "Car Insurance"
        self.hwnd = find_form_hwnd(frag)
        if not self.hwnd:
            self.close()
            raise RuntimeError(f"{version} form did not open")
        time.sleep(1.5)
        _bring_to_front(self.hwnd)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        time.sleep(1.0)


def _keep(sub: str, tag: str) -> str:
    """Copy a submission into the repo under a run-specific name."""
    if not sub:
        return ""
    KEEP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{tag}__{sub}"
    shutil.copy2(FORM_SUBMISSIONS / sub, KEEP_DIR / name)
    return name


# ── one run of each system ────────────────────────────────────────────────────

def run_baseline(version: str, macro: dict, tag: str) -> dict:
    form = Form(version)
    before = _submissions()
    t0 = time.time()
    note = ""
    try:
        replay(macro)
        sub = _wait_new_submission(before, timeout=15)
    except Exception as e:                         # incl. pyautogui fail-safe
        sub, note = "", f"replay error: {e!r}"
    minutes = (time.time() - t0) / 60
    form.close()
    kept = _keep(sub, tag)
    return {"submission": kept, "finished_without_help": "yes" if sub else "no",
            "minutes": f"{minutes:.2f}",
            "notes": note or ("reached Submit" if sub else "no submission saved")}


def _ensure_notepad() -> None:
    import win32gui
    hits = []
    win32gui.EnumWindows(lambda h, _: hits.append(h) if "data_entry_intake" in win32gui.GetWindowText(h) else None, None)
    if not hits:
        subprocess.Popen(["notepad.exe", str(INTAKE)])
        time.sleep(3)


def run_intern(version: str, tag: str, timeout: float) -> dict:
    _ensure_notepad()
    form = Form(version)
    before = _submissions()
    env = dict(os.environ, INTERN_PROVIDER="none", PYTHONIOENCODING="utf-8")
    t0 = time.time()
    proc = subprocess.Popen(
        [sys.executable, "run_task.py", "--start_record", "1", "--end_record", "1"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    tail: list[str] = []

    def pump():
        for line in proc.stdout:
            tail.append(line.rstrip())
            del tail[:-40]
            if line.startswith("COUNTDOWN_BEGIN"):
                _bring_to_front(form.hwnd)       # the agent targets the window in front
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=30)
    th.join(timeout=5)
    sub = _wait_new_submission(before, timeout=5)
    minutes = (time.time() - t0) / 60
    form.close()
    kept = _keep(sub, tag)
    if timed_out:
        note = f"stopped by runner after {timeout / 60:.0f} min"
    elif sub:
        note = f"reached Submit; exit code {proc.returncode}"
    else:
        note = f"no submission saved; exit code {proc.returncode}"
    _log({"event": "intern_output_tail", "tag": tag, "tail": tail[-15:]})
    return {"submission": kept,
            "finished_without_help": "yes" if (sub and not timed_out) else "no",
            "minutes": f"{minutes:.2f}", "notes": note}


# ── run sheet ─────────────────────────────────────────────────────────────────

def _load_sheet() -> list[dict]:
    rows = list(csv.DictReader(SHEET.open(encoding="utf-8")))
    for r in rows:
        if r["system"] == "UI.Vision":
            r["system"] = BASELINE
    return rows


def _save_sheet(rows: list[dict]) -> None:
    with SHEET.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in SHEET_FIELDS} for r in rows)


def _put(rows, system, version, run, result) -> None:
    for r in rows:
        if r["system"] == system and r["form_version"] == version and r["run"] == str(run):
            r.update(result)
            _save_sheet(rows)
            return
    raise KeyError((system, version, run))


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Objective 7 comparison (live GUI).")
    ap.add_argument("--only", choices=["gate1", "gates", "baseline", "intern", "all"], default="all")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--intern_timeout", type=float, default=900, help="seconds per Intern run")
    args = ap.parse_args()
    _dpi_aware()
    LOG.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    macro = record(1, DEFAULT_MACRO)             # built on the NORMAL form only
    rec_s = time.time() - t0
    _log({"event": "baseline_macro_recorded", "seconds": round(rec_s, 1),
          "coordinates": macro["coordinates"]})
    rows = _load_sheet()
    for r in rows:
        if r["system"] == BASELINE and r["run"] == "setup":
            r["notes"] = (f"Macro generated automatically from the normal form in {rec_s:.0f} s "
                          "(scripts/rpa_baseline.py record). The script itself was written by an AI "
                          "coding assistant; that authoring time was not measured, so no human "
                          "setup time exists for this baseline.")
    _save_sheet(rows)

    if args.only in ("gate1", "gates", "all"):
        g1 = run_baseline("normal", macro, f"gate1_baseline_normal_{time.strftime('%H%M%S')}")
        _log({"event": "gate1", **g1})
        if args.only == "gate1":
            return
        g2 = run_intern("modified", "gate2_intern_modified", args.intern_timeout)
        _log({"event": "gate2", **g2})
        if args.only == "gates":
            return
    if args.only in ("baseline", "all"):
        for version in ("normal", "modified"):
            for i in range(1, args.runs + 1):
                res = run_baseline(version, macro, f"{BASELINE}_{version}_{i}")
                _put(rows, BASELINE, version, i, res)
                _log({"event": "run", "system": BASELINE, "form": version, "run": i, **res})
    if args.only in ("intern", "all"):
        for version in ("normal", "modified"):
            for i in range(1, args.runs + 1):
                res = run_intern(version, f"Intern_{version}_{i}", args.intern_timeout)
                _put(rows, "Intern", version, i, res)
                _log({"event": "run", "system": "Intern", "form": version, "run": i, **res})
    _log({"event": "done", "total_minutes": round((time.time() - t0) / 60, 1)})


if __name__ == "__main__":
    main()
