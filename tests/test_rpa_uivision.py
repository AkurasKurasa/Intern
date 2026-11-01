"""Tests for scripts/rpa_uivision.py (Objective 7 re-run with UI.Vision RPA).

The key property: the UI.Vision macro plays exactly the same keystrokes and
clicks as the PyAutoGUI baseline it replaces, so only the RPA product changes
between the two comparisons. The live run itself is exercised by running it."""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_baseline  # noqa: E402
import rpa_uivision as U  # noqa: E402
from bc_fidelity import _parse_intake_record  # noqa: E402

INTAKE = (ROOT / "data_entry_tasks" / "data_entry_intake.txt").read_text(encoding="utf-8")
COORDS = {"first_field": [1007, 278], "submit": [1052, 901], "window": [0, 0, 1, 1]}


# ── key streams, for comparing the two macros event by event ──────────────────

_KEY = re.compile(r"\$\{KEY_([A-Z0-9_+]+)\}")
_NAMES = {"TAB": "tab", "SPACE": "space", "HOME": "home", "DOWN": "down", "DEL": "delete",
          "CTRL+KEY_TAB": "ctrl+tab", "CTRL+KEY_A": "ctrl+a"}


def uivision_stream(commands: list[dict]) -> list[tuple]:
    out = []
    for c in commands:
        if c["Command"] == "XClick":
            m = re.fullmatch(r"D(\d+),(\d+)", c["Target"])
            out.append(("click", int(m.group(1)), int(m.group(2))))
        elif c["Command"] == "XType":
            pos = 0
            for m in _KEY.finditer(c["Target"]):
                out += [("char", ch) for ch in c["Target"][pos:m.start()]]
                out.append(("key", _NAMES[m.group(1)]))
                pos = m.end()
            out += [("char", ch) for ch in c["Target"][pos:]]
    return out


def pyautogui_stream(macro: dict, monkeypatch) -> list[tuple]:
    """Run rpa_baseline.replay() against a fake pyautogui and record what it sends."""
    out: list[tuple] = []

    class Fake:
        PAUSE = 0
        FAILSAFE = True

        @staticmethod
        def click(x, y):
            out.append(("click", x, y))

        @staticmethod
        def press(key, presses=1):
            out.extend([("key", key)] * presses)

        @staticmethod
        def hotkey(*keys):
            out.append(("key", "+".join(keys)))

    monkeypatch.setitem(sys.modules, "pyautogui", Fake)
    monkeypatch.setattr(rpa_baseline, "_type_unicode",
                        lambda text, interval=0: out.extend(("char", ch) for ch in text))
    monkeypatch.setattr(rpa_baseline.time, "sleep", lambda s: None)
    monkeypatch.setattr(rpa_baseline, "_dpi_aware", lambda: None)
    rpa_baseline.replay(macro)
    return out


@pytest.fixture(scope="module")
def real_macro():
    pages = rpa_baseline.form_tab_order(rpa_baseline.NORMAL_FORM)
    steps = rpa_baseline.build_steps(pages, _parse_intake_record(INTAKE, 1))
    return {"coordinates": COORDS, "next_tab_key": "ctrl+tab", "pages": steps}


# ── tests ─────────────────────────────────────────────────────────────────────

def test_uivision_plays_the_same_keys_and_clicks_as_the_pyautogui_baseline(real_macro, monkeypatch):
    ui = uivision_stream(U.to_uivision_commands(real_macro))
    pg = pyautogui_stream(real_macro, monkeypatch)
    assert ui == pg
    assert ui[0] == ("click", 1007, 278) and ui[-1] == ("click", 1052, 901)


def test_macro_runs_in_desktop_mode_and_never_brings_the_browser_forward(real_macro):
    cmds = U.to_uivision_commands(real_macro)
    assert cmds[0]["Command"] == "XDesktopAutomation" and cmds[0]["Target"] == "true"
    assert all(c["Command"] in {"XDesktopAutomation", "XClick", "XType", "pause"} for c in cmds)
    assert all(re.fullmatch(r"D\d+,\d+", c["Target"]) for c in cmds if c["Command"] == "XClick")


def test_page_switches_are_ctrl_tab_with_a_pause(real_macro):
    cmds = U.to_uivision_commands(real_macro)
    switches = [i for i, c in enumerate(cmds) if c["Target"] == "${KEY_CTRL+KEY_TAB}"]
    assert len(switches) == len(real_macro["pages"]) - 1 == 7
    assert all(cmds[i + 1]["Command"] == "pause" for i in switches)


def test_record_1_em_dash_is_typed_as_text(real_macro):
    cmds = U.to_uivision_commands(real_macro)
    assert any("Pinnacle Auto — Riverside Branch" in c["Target"] for c in cmds)


@pytest.mark.parametrize("keys,expected", [
    ("{SPACE}", "${KEY_SPACE}"),
    ("{CHOICE:3}", "${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}"),
    ("{CHOICE:0}", "${KEY_HOME}"),
    ("{HOME}", "${KEY_HOME}"),
    ("{CLEAR}", "${KEY_CTRL+KEY_A}${KEY_DEL}"),
    ("04/01/2026", "04/01/2026"),
])
def test_keys_to_xtype(keys, expected):
    assert U._keys_to_xtype(keys) == expected


def test_a_value_xtype_would_misread_is_refused():
    with pytest.raises(ValueError):
        U._keys_to_xtype("cost ${PRICE}")


def test_last_control_on_a_page_gets_no_tab_and_empty_steps_get_only_a_tab():
    macro = {"coordinates": COORDS, "pages": [
        [{"field": "a", "kind": "text", "keys": "x"}, {"field": "b", "kind": "stop", "keys": ""}],
        [{"field": "c", "kind": "text", "keys": ""}, {"field": "d", "kind": "text", "keys": "y"}],
    ]}
    types = [c["Target"] for c in U.to_uivision_commands(macro) if c["Command"] == "XType"]
    assert types == ["x${KEY_TAB}", "${KEY_CTRL+KEY_TAB}", "${KEY_TAB}", "y"]


def test_run_page_embeds_the_macro_and_uses_uivisions_embedded_macro_event():
    macro = U.macro_json([U._cmd("XType", "Pinnacle Auto — x")], "2026-11-02")
    html = U.run_page_html(macro)
    assert "kantuSaveAndRunMacro" in html and "data-kantu" in html
    embedded = re.search(r"var MACRO = (\{.*?\});\nvar s =", html, re.S).group(1)
    assert json.loads(embedded) == macro


def test_run_url_carries_uivision_command_line_switches(tmp_path):
    url = U.run_url(tmp_path / "log1.txt")
    assert url.startswith("file:///") and "run_macro.html?" in url
    for switch in ("direct=1", "closeRPA=1", "closeBrowser=1"):
        assert switch in url
    assert url.endswith("savelog=" + (tmp_path / "log1.txt").as_posix())


def test_browser_uses_its_own_profile_not_the_users(monkeypatch):
    monkeypatch.setattr(U, "_second_display", lambda: True)
    args = U.browser_args("file:///x.html")
    assert f"--user-data-dir={U.PROFILE}" in args
    assert "--window-position=1960,40" in args and args[-1] == "file:///x.html"


def test_new_sheet_has_uivision_rows_and_keeps_interns_runs(tmp_path):
    src = tmp_path / "run_sheet.csv"
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=U.SHEET_FIELDS)
        w.writeheader()
        w.writerow({"system": "PyAutoGUI", "form_version": "normal", "run": "1", "record": "1"})
        w.writerow({"system": "Intern", "form_version": "normal", "run": "setup", "notes": "historical"})
        w.writerow({"system": "Intern", "form_version": "modified", "run": "1", "record": "1",
                    "submission": "Intern_modified_1.json", "finished_without_help": "yes"})
    rows = U.new_sheet(src, runs=2)
    uiv = [(r["form_version"], r["run"]) for r in rows if r["system"] == "UI.Vision"]
    assert uiv == [("normal", "setup"), ("normal", "1"), ("normal", "2"), ("modified", "1"), ("modified", "2")]
    intern = [r for r in rows if r["system"] == "Intern"]
    assert [r["run"] for r in intern] == ["setup", "1"]
    assert intern[1]["submission"] == "Intern_modified_1.json"
    assert not any(r["system"] == "PyAutoGUI" for r in rows)


def test_put_fills_exactly_one_row(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "SHEET", tmp_path / "s.csv")
    rows = [{"system": "UI.Vision", "form_version": v, "run": "1"} for v in ("normal", "modified")]
    U.put(rows, "modified", "1", {"submission": "a.json", "minutes": "2.10"})
    saved = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    assert saved[1]["submission"] == "a.json" and saved[0]["submission"] == ""
    with pytest.raises(KeyError):
        U.put(rows, "normal", "9", {})


def test_read_status_returns_the_first_log_line(tmp_path):
    p = tmp_path / "log.txt"
    assert U.read_status(p) == ""
    p.write_text("Status=OK\nMacro: Objective7_Scope1_Record1\n", encoding="utf-8")
    assert U.read_status(p) == "Status=OK"


def test_gate_report_scores_against_record_1(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "KEEP_DIR", tmp_path)
    (tmp_path / "s.json").write_text(json.dumps({"first_name": "nobody"}), encoding="utf-8")
    report = U.gate_report("s.json")
    assert report.startswith("Gate: ") and "fully correct = False" in report
    assert U.gate_report("") == "Gate failed: nothing was submitted."
