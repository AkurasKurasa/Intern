"""Tests for scripts/rpa_uivision_web.py (Objective 7, Scopes 2 and 3 with the
UI.Vision browser extension). The live runs are exercised by running them;
these pin the pieces they are built from."""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_uivision_web as W  # noqa: E402


# ── setup: unpacking the official package ─────────────────────────────────────

def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _field(num: int, data: bytes) -> bytes:
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _crx(files: dict, key: bytes) -> bytes:
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    proof = _field(1, key) + _field(2, b"signature")
    header = _field(2, proof)
    return b"Cr24" + (3).to_bytes(4, "little") + len(header).to_bytes(4, "little") + header + z.getvalue()


def test_unpack_keeps_the_store_id_by_writing_the_key(tmp_path):
    key = b"fake public key bytes"
    want = W.extension_id(key)
    m = W.unpack_crx(_crx({"manifest.json": json.dumps({"name": "x", "version": "1"})}, key), tmp_path, want)
    assert m["version"] == "1"
    saved = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert W.extension_id(__import__("base64").b64decode(saved["key"])) == want


def test_unpack_refuses_a_package_signed_with_another_key(tmp_path):
    with pytest.raises(ValueError, match="not signed"):
        W.unpack_crx(_crx({"manifest.json": "{}"}, b"other key"), tmp_path, W.EXT_ID)


def test_unpack_refuses_paths_outside_the_folder(tmp_path):
    key = b"k"
    with pytest.raises(ValueError, match="unsafe path"):
        W.unpack_crx(_crx({"../evil.txt": "x", "manifest.json": "{}"}, key), tmp_path / "ext", W.extension_id(key))


def test_extension_id_matches_chromes_scheme():
    assert re.fullmatch(r"[a-p]{32}", W.extension_id(b"anything"))


# ── Scope 2: the recorded macro ───────────────────────────────────────────────

EXPECTED = {"2021-10001": {"course": "BS CS", "year": 3.0, "grade": 88.5, "remarks": "Passed"},
            "2021-10008": {"course": "BS IT", "year": 2.0, "grade": 70.0, "remarks": "Failed"}}


def test_scope2_steps_are_four_per_row_then_save():
    steps = W.scope2_steps(EXPECTED)
    assert steps[:4] == [("type", "course_2021-10001", "BS CS"), ("type", "year_2021-10001", "3"),
                         ("type", "grade_2021-10001", "88.5"), ("select", "remarks_2021-10001", "label=Passed")]
    assert steps[6] == ("type", "grade_2021-10008", "70")
    assert steps[-1] == ("click", "submit-btn", "")
    assert len(steps) == 4 * len(EXPECTED) + 1


def test_scope2_macro_keeps_backup_locators_only_when_ui_vision_saves_them():
    steps = W.scope2_steps(EXPECTED)
    locs = [{"target": f"id={e}", "targets": [f"id={e}", "css=#" + e]} for _, e, _ in steps]
    with_alt = W.scope2_macro(steps, locs, True)
    assert with_alt["Name"] == W.SCOPE2_MACRO
    assert with_alt["Commands"][0] == {"Command": "type", "Target": "id=course_2021-10001", "Value": "BS CS",
                                       "Targets": ["id=course_2021-10001", "css=#course_2021-10001"]}
    assert "Targets" not in W.scope2_macro(steps, locs, False)["Commands"][0]


def _state(staged_rows, committed_rows):
    base = lambda sid: {"student_id": sid}
    return {"staged": [dict(base(s), **v) for s, v in staged_rows.items()],
            "committed": [dict(base(s), **v) for s, v in committed_rows.items()], "status": "Saved 1 row."}


def test_score_portal_counts_saved_rows_and_typed_values():
    right = {"course": "BS CS", "year": "3", "grade": "88.5", "remarks": "Passed"}
    wrong = {"course": "", "year": "2", "grade": "70", "remarks": "Failed"}
    s = W.score_portal(_state({"2021-10001": right, "2021-10008": wrong},
                              {"2021-10001": right, "2021-10008": {}}), EXPECTED)
    assert s["rows_correct"] == 1 and s["rows_saved"] == 1
    assert s["values_correct"] == 7 and s["noncourse_correct"] == 6
    assert s["fields_placed_row1"] == 4 and s["save_status"] == "Saved 1 row."


def test_a_row_typed_right_but_refused_on_save_is_not_correct():
    right = {"course": "BS CS", "year": "3", "grade": "88.5", "remarks": "Passed"}
    s = W.score_portal(_state({"2021-10001": right}, {"2021-10001": {}}), {"2021-10001": EXPECTED["2021-10001"]})
    assert s["values_correct"] == 4 and s["rows_correct"] == 0 and s["rows_saved"] == 0


@pytest.fixture(scope="module")
def browser_page():
    sync = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync.sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
    except Exception as e:                              # no browser installed
        pytest.skip(f"no Playwright browser: {e}")
    page = browser.new_page()
    yield page
    browser.close()
    pw.stop()


def test_recorder_port_picks_id_first_and_keeps_position_xpath_as_backup(browser_page):
    browser_page.set_content("""<table><tbody>
      <tr><td><input id="course_1" name="course"></td><td><input id="year_1" name="year"></td></tr>
      <tr><td><input id="course_2" name="course"></td><td><input id="year_2" name="year"></td></tr>
    </tbody></table>""")
    locs = browser_page.evaluate(W.RECORDER_JS, ["course_2", "year_1", "missing"])
    assert locs[2] is None
    c2 = locs[0]
    assert c2["target"] == "id=course_2"
    assert c2["targets"][:3] == ["id=course_2", "name=course", 'xpath=//*[@id="course_2"]']
    assert "xpath=//input[@id='course_2']" in c2["targets"]
    assert any(t.startswith("xpath=//tr[2]") for t in c2["targets"])      # the position backup
    assert c2["targets"][-1].startswith("css=")


# ── Scope 3: the mail-rules macro ─────────────────────────────────────────────

def test_scope3_script_decides_exactly_like_the_scripted_mail_rules():
    """Run the macro's own decide() in Node on every message in every condition
    and compare with rpa_scope3.baseline_decide()."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    import rpa_scope3 as S3
    inbox, sent = S3.load()
    history = [m for m in inbox if m.id in S3.HISTORY]
    test = [m for m in inbox if m.id not in S3.HISTORY]
    rules = S3.build_baseline_rules(history, sent)
    script = W.scope3_script(*rules)
    decide_js = script[:script.index("var BUTTON")]
    cases, want = [], []
    for m in test:
        for msg in [m, S3.perturb(m)] + [S3.perturb(m, (c,)) for c in S3.CHANGES]:
            shown = msg.sender or msg.sender_email           # what the page shows in the header
            hit = re.search(r"<([^>]+)>", shown)
            cases.append([(hit.group(1) if hit else shown).strip().lower(), msg.subject])
            want.append(S3.baseline_decide(msg, rules))
    prog = decide_js + "\nconst cases = " + json.dumps(cases) + ";\nconsole.log(JSON.stringify(cases.map(c => decide(c[0], c[1]))));"
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == want


def test_scope3_script_presses_the_page_buttons_and_waits_for_the_list():
    script = W.scope3_script({"a@b.com": "reply"}, [(r"\bmeeting\b", "schedule")])
    for needle in ("id=replyPillBtn", "id=forwardPillBtn", "id=scheduleBtn", "id=archiveBtn", "id=backBtn",
                   "id=sendBtn", 'uiv.$("css=#rowList li.row-item", {timeout: 15})'):
        assert needle in script


def test_inbox_server_serves_the_page_and_records_decisions_locally():
    srv = W.InboxServer(port=0)
    port = srv.srv.server_address[1]
    try:
        srv.messages = [{"message_id": "m1", "sender": "A <a@b.com>", "subject": "Hi"}]
        base = f"http://127.0.0.1:{port}"
        assert b"rowList" in urllib.request.urlopen(base + "/practice/").read()
        assert json.loads(urllib.request.urlopen(base + "/practice/api/inbox").read())["messages"][0]["message_id"] == "m1"
        req = urllib.request.Request(base + "/practice/api/record", method="POST",
                                     data=json.dumps({"message_id": "m1", "decision": "reply", "reply_body": "x"}).encode(),
                                     headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is True
        assert srv.decisions == {"m1": {"decision": "reply", "reply_body": "x"}}
    finally:
        srv.close()
