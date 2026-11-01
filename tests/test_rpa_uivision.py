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
COORDS = {"first_field": [607, 212], "submit": [652, 835], "window": [40, 40, 1080, 860]}


# ── key streams, for comparing the two macros event by event ──────────────────

_KEY = re.compile(r"\$\{KEY_([A-Z0-9_+]+)\}")
_NAMES = {"TAB": "tab", "SPACE": "space", "HOME": "home", "DOWN": "down", "DELETE": "delete",
          "CTRL+KEY_TAB": "ctrl+tab", "CTRL+KEY_A": "ctrl+a"}
_CALL = re.compile(r"^uiv\.desktop\.(mouse\.click|keyboard\.type)\((.*)\);")


def uivision_stream(js: str) -> list[tuple]:
    out = []
    for line in js.splitlines():
        m = _CALL.match(line.split("   //")[0])
        if not m:
            continue
        if m.group(1) == "mouse.click":
            x, y = (int(v) for v in m.group(2).split(","))
            out.append(("click", x, y))
            continue
        text = json.loads(m.group(2))
        pos = 0
        for k in _KEY.finditer(text):
            out += [("char", ch) for ch in text[pos:k.start()]]
            out.append(("key", _NAMES[k.group(1)]))
            pos = k.end()
        out += [("char", ch) for ch in text[pos:]]
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
    ui = uivision_stream(U.to_desktop_macro(real_macro))
    pg = pyautogui_stream(real_macro, monkeypatch)
    assert ui == pg
    assert ui[0] == ("click", 607, 212) and ui[-1] == ("click", 652, 835)


def test_macro_is_a_desktop_app_macro_using_only_clicks_typing_and_pauses(real_macro):
    js = U.to_desktop_macro(real_macro)
    assert js.splitlines()[0] == '"use desktop-app";'
    assert U.MACRO_NAME.endswith(".d.js")
    calls = [l.split("(")[0] for l in js.splitlines() if l.startswith("uiv.")]
    assert set(calls) == {"uiv.desktop.mouse.click", "uiv.desktop.keyboard.type", "uiv.sleep"}


def test_page_switches_are_ctrl_tab_with_a_pause(real_macro):
    lines = U.to_desktop_macro(real_macro).splitlines()
    switches = [i for i, l in enumerate(lines) if '"${KEY_CTRL+KEY_TAB}"' in l]
    assert len(switches) == len(real_macro["pages"]) - 1 == 7
    assert all(lines[i + 1] == "uiv.sleep(400);" for i in switches)


def test_record_1_em_dash_is_typed_as_text(real_macro):
    assert "Pinnacle Auto — Riverside Branch" in U.to_desktop_macro(real_macro)


@pytest.mark.parametrize("keys,expected", [
    ("{SPACE}", "${KEY_SPACE}"),
    ("{CHOICE:3}", "${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}"),
    ("{CHOICE:0}", "${KEY_HOME}"),
    ("{HOME}", "${KEY_HOME}"),
    ("{CLEAR}", "${KEY_CTRL+KEY_A}${KEY_DELETE}"),
    ("04/01/2026", "04/01/2026"),
])
def test_keys_to_xtype(keys, expected):
    assert U._keys_to_xtype(keys) == expected


def test_a_value_uivision_would_misread_is_refused():
    with pytest.raises(ValueError):
        U._keys_to_xtype("cost ${PRICE}")


def test_last_control_on_a_page_gets_no_tab_and_empty_steps_get_only_a_tab():
    macro = {"coordinates": COORDS, "pages": [
        [{"field": "a", "kind": "text", "keys": "x"}, {"field": "b", "kind": "stop", "keys": ""}],
        [{"field": "c", "kind": "text", "keys": ""}, {"field": "d", "kind": "text", "keys": 'say "hi"'}],
    ]}
    typed = [json.loads(_CALL.match(l.split("   //")[0]).group(2))
             for l in U.to_desktop_macro(macro).splitlines() if l.startswith("uiv.desktop.keyboard.type")]
    assert typed == ["x${KEY_TAB}", "${KEY_CTRL+KEY_TAB}", "${KEY_TAB}", 'say "hi"']


def test_parse_app_log_reads_completion_and_runtime():
    name = U.MACRO_NAME
    lines = [
        "03:34:48.894 [status] [] Ui.Vision for Desktop app 2.1.43 started",
        f"03:34:49.452 [status] [{name}] {name} started",
        f"03:34:49.465 [info] [{name}] uiv API loaded (11 ms, Boa 0.22)",
    ]
    assert U.parse_app_log(lines) == ("", None)
    lines.append(f"03:36:20.100 [status] [{name}] {name} completed (Runtime 90.65s)")
    assert U.parse_app_log(lines) == ("completed", 90.65)


def test_parse_app_log_reports_an_error_line():
    name = U.MACRO_NAME
    lines = [f"03:34:49.452 [status] [{name}] {name} started",
             f"03:34:50.000 [error] [{name}] TypeError: uiv.desktop.foo is not a function"]
    status, runtime = U.parse_app_log(lines)
    assert status.startswith("TypeError") and runtime is None
    assert U.parse_app_log([f"x [status] [other.d.js] other.d.js completed (Runtime 1s)"]) == ("", None)


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


def test_gate_report_scores_against_record_1(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "KEEP_DIR", tmp_path)
    (tmp_path / "s.json").write_text(json.dumps({"first_name": "nobody"}), encoding="utf-8")
    report = U.gate_report("s.json")
    assert report.startswith("Gate: ") and "fully correct = False" in report
    assert U.gate_report("") == "Gate failed: nothing was submitted."
