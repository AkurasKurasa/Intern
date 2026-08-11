"""Milestone 4: one demo session yields correct confirmed pairs.

The browser recorder is exercised by injecting the real content script into the
real portal and driving real events - not by a mock of it. The Excel recorder's
pure half (header resolution, column type inference, event construction) is
tested directly; its COM half needs a live Excel and is covered by a manual
check documented in the module.

Run:  python -m pytest tests/test_recorder.py -q
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import CHROMIUM, variant_url  # noqa: E402
from recorder.confirm import (  # noqa: E402
    ACCEPT, CORRECT, REJECT, Decision, apply_decisions, proposals, render,
)
from recorder.events import (  # noqa: E402
    BrowserEvent, ExcelEvent, assign_column_labels, read_session, write_session,
)
from recorder.excel_recorder import (  # noqa: E402
    build_event, column_values, header_for_column, infer_column_type,
)
from recorder.reconciler import (  # noqa: E402
    CONFIDENCE_CONFIRMED, CONFIDENCE_NEEDS_REVIEW, CONFIDENCE_RECONCILED,
    last_write_per_row, normalize, reconcile,
)

CONTENT_JS = (REPO / "recorder" / "extension" / "content.js").read_text(encoding="utf-8")

BASE = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def excel(seconds, header, value, column_index=0, cell="A1"):
    return ExcelEvent(t=at(seconds), sheet="SUMMARY", cell=cell,
                      column_index=column_index, header=header, value=str(value),
                      inferred_type="text")


def browser(seconds, label, value, row=0, seq=0, trigger="paste",
            input_type="text", options=None):
    return BrowserEvent(
        t=at(seconds), url="http://127.0.0.1:8765/v0_base/index.html",
        value=str(value), trigger=trigger, row=row, seq=seq,
        context={
            "aria": label, "label_for": "", "label_wrapping": "",
            "placeholder": "", "preceding_text": "", "name": "",
            "input_type": input_type, "options": options, "dom_order": seq,
        },
    )


# ------------------------------------------------- 2.1 / excel recorder


def test_infer_column_type_reads_the_column_not_the_cell():
    assert infer_column_type(["2021-10001", "2021-10008"]) == "id"
    assert infer_column_type([85, 96, 72]) == "numeric"
    assert infer_column_type(["85", "96"]) == "numeric"
    assert infer_column_type(["a@b.com"]) == "email"
    assert infer_column_type(["BS CS", "BS IT"]) == "text"
    assert infer_column_type([]) == "text"
    # A date in Excel is a number: the serial 45000 under a date format is a
    # date, and reading "numeric" off its digits would be exactly wrong.
    assert infer_column_type(["45000"], number_format="dd/mm/yy") == "date"
    assert infer_column_type(["x"], number_format="dd/mmm/yy") == "date"
    assert infer_column_type(["45000"], number_format="0") == "numeric"


def test_header_resolution_uses_the_header_row():
    values = [
        ["No.", "STUDENT NUMBER", "FINAL GRADE"],
        [1, "2021-10001", 85],
        [2, "2021-10008", 96],
    ]
    assert header_for_column(values, 2) == "FINAL GRADE"
    assert header_for_column(values, 99) == ""
    assert header_for_column([], 0) == ""
    assert column_values(values, 2) == [85, 96]


def test_build_event_matches_the_2_1_contract():
    values = [["No.", "STUDENT NUMBER", "FINAL GRADE"], [1, "2021-10001", 85]]
    event = build_event("SUMMARY", "D2", 2, values, 85, number_format="0")

    assert event.source == "excel"
    assert event.sheet == "SUMMARY"
    assert event.cell == "D2"
    assert event.column_index == 2
    assert event.header == "FINAL GRADE"
    assert event.value == "85"
    assert event.inferred_type == "numeric"


# ------------------------------------------------------ 3.3 reconciler


def test_join_matches_on_value_and_time_proximity():
    excel_events = [excel(0, "PROGRAM", "BS CS"), excel(2, "FINAL GRADE", "85")]
    browser_events = [browser(1, "Course", "BS CS"), browser(3, "Grade", "85", seq=1)]

    result = reconcile(excel_events, browser_events)
    assert {(p.source_header, p.target_label) for p in result.pairs} == {
        ("PROGRAM", "Course"), ("FINAL GRADE", "Grade"),
    }
    assert all(p.confidence == CONFIDENCE_RECONCILED for p in result.pairs)


def test_a_stale_clipboard_does_not_reconcile():
    """Time alone breaks on a stale clipboard; the window is what stops it."""
    excel_events = [excel(0, "FINAL GRADE", "85")]
    late = browser(3600, "Grade", "85")   # an hour later
    result = reconcile(excel_events, [late])

    assert result.pairs == []
    assert len(result.unreconciled) == 1


def test_an_excel_event_after_the_write_is_never_matched():
    result = reconcile([excel(10, "FINAL GRADE", "85")], [browser(5, "Grade", "85")])
    assert result.pairs == []


def test_the_later_of_two_equal_values_wins():
    """3.3: prefer the later one when two Excel events tie on value."""
    excel_events = [excel(0, "MIDTERM", "85"), excel(5, "FINAL GRADE", "85")]
    result = reconcile(excel_events, [browser(6, "Grade", "85")])

    assert len(result.pairs) == 1
    assert result.pairs[0].source_header == "FINAL GRADE"
    assert result.pairs[0].confidence == CONFIDENCE_RECONCILED


def test_a_genuine_tie_is_flagged_for_review():
    """Same instant, different columns - preferring the later one settles
    nothing, so 3.3 says mark it."""
    excel_events = [excel(5, "MIDTERM", "85"), excel(5, "FINAL GRADE", "85")]
    result = reconcile(excel_events, [browser(6, "Grade", "85")])

    assert len(result.pairs) == 1
    assert result.pairs[0].confidence == CONFIDENCE_NEEDS_REVIEW


def test_numbers_compare_numerically():
    assert normalize("85.0") == normalize(85) == normalize(" 85 ")
    result = reconcile([excel(0, "FINAL GRADE", "85.0")], [browser(1, "Grade", "85")])
    assert len(result.pairs) == 1


def test_a_correction_within_a_row_voids_the_earlier_write():
    """3.2: last write wins per row."""
    events = [
        browser(1, "Grade", "58", row=0, seq=0),
        browser(2, "Grade", "85", row=0, seq=1),
    ]
    kept, voided = last_write_per_row(events)
    assert [e.value for e in kept] == ["85"]
    assert [e.value for e in voided] == ["58"]

    result = reconcile([excel(0, "FINAL GRADE", "85")], events)
    assert len(result.pairs) == 1
    assert len(result.voided) == 1


def test_clearing_a_field_retracts_it():
    events = [
        browser(1, "Grade", "85", row=0, seq=0),
        browser(2, "Grade", "", row=0, seq=1),
    ]
    kept, voided = last_write_per_row(events)
    assert kept == []
    assert len(voided) == 2


def test_an_unreconciled_select_across_all_rows_is_a_derived_candidate():
    """3.3: the absence of a source cell is the evidence a field is computed."""
    excel_events = [excel(0, "FINAL GRADE", "85"), excel(10, "FINAL GRADE", "60")]
    browser_events = [
        browser(1, "Grade", "85", row=0, seq=0),
        browser(2, "Remarks", "Passed", row=0, seq=1,
                trigger="select", input_type="select", options=["Passed", "Failed"]),
        browser(11, "Grade", "60", row=1, seq=2),
        browser(12, "Remarks", "Failed", row=1, seq=3,
                trigger="select", input_type="select", options=["Passed", "Failed"]),
    ]

    result = reconcile(excel_events, browser_events)
    assert {c.target_label for c in result.derived_candidates} == {"Remarks"}
    assert len(result.derived_candidates) == 2
    assert all(c.closed_option_field for c in result.derived_candidates)
    assert result.unreconciled == []


def test_a_typed_value_is_not_mistaken_for_a_derived_field():
    """3.3's false positive: a user reading a value off the screen and typing it
    produces the same signature. A free-text field never qualifies."""
    excel_events = [excel(0, "FINAL GRADE", "85"), excel(10, "FINAL GRADE", "60")]
    browser_events = [
        browser(1, "Grade", "85", row=0, seq=0),
        browser(2, "Recommendations", "See adviser", row=0, seq=1, trigger="type"),
        browser(11, "Grade", "60", row=1, seq=2),
        browser(12, "Recommendations", "See adviser", row=1, seq=3, trigger="type"),
    ]

    result = reconcile(excel_events, browser_events)
    assert result.derived_candidates == []
    assert len(result.unreconciled) == 2
    assert all("closed-option" in u["reason"] for u in result.unreconciled)


def test_a_select_written_in_only_some_rows_is_not_derived():
    excel_events = [excel(0, "FINAL GRADE", "85"), excel(10, "FINAL GRADE", "60")]
    browser_events = [
        browser(1, "Grade", "85", row=0, seq=0),
        browser(2, "Remarks", "Passed", row=0, seq=1,
                trigger="select", input_type="select", options=["Passed", "Failed"]),
        browser(11, "Grade", "60", row=1, seq=2),
    ]
    result = reconcile(excel_events, browser_events)
    assert result.derived_candidates == []
    assert any("every demonstrated row" in u["reason"] for u in result.unreconciled)


# ------------------------------------------------ session round-trip / 2.2


def test_session_round_trips_through_jsonl(tmp_path):
    events = [excel(0, "PROGRAM", "BS CS"), browser(1, "Course", "BS CS")]
    path = write_session(tmp_path / "s.jsonl", events)

    excel_back, browser_back = read_session(path)
    assert len(excel_back) == 1 and len(browser_back) == 1
    assert excel_back[0].header == "PROGRAM"
    assert browser_back[0].label == "Course"


def test_browser_event_exposes_the_2_2_contract_with_a_resolved_label():
    """2.2 shows a resolved label; 3.5 forbids the browser deciding one. The
    label is filled in on read, by the single cascade."""
    event = browser(1, "Grade 0-100", "85")
    contract = event.as_contract()

    assert contract["source"] == "browser"
    assert contract["trigger"] == "paste"
    assert set(contract["field"]) >= {
        "label", "name", "id", "input_type", "placeholder", "required",
        "maxlength", "aria_label", "options", "dom_order",
    }
    assert contract["field"]["label"] == "Grade 0-100"
    assert contract["field"]["label_rule"] == 3   # resolved from aria


# --------------------------------------------------- 3.3 confirmation gate


@pytest.fixture
def demo_result():
    excel_events = [
        excel(0, "PROGRAM", "BS CS"), excel(1, "YEAR LEVEL", "3"),
        excel(2, "FINAL GRADE", "85"),
        excel(20, "PROGRAM", "BS IT"), excel(21, "YEAR LEVEL", "2"),
        excel(22, "FINAL GRADE", "60"),
    ]
    browser_events = [
        browser(3, "Course", "BS CS", row=0, seq=0),
        browser(4, "Year 1-5", "3", row=0, seq=1),
        browser(5, "Grade 0-100", "85", row=0, seq=2),
        browser(6, "Remarks", "Passed", row=0, seq=3,
                trigger="select", input_type="select", options=["Passed", "Failed"]),
        browser(23, "Course", "BS IT", row=1, seq=4),
        browser(24, "Year 1-5", "2", row=1, seq=5),
        browser(25, "Grade 0-100", "60", row=1, seq=6),
        browser(26, "Remarks", "Failed", row=1, seq=7,
                trigger="select", input_type="select", options=["Passed", "Failed"]),
    ]
    return reconcile(excel_events, browser_events)


def test_a_demo_session_yields_the_correct_pairs(demo_result):
    """Milestone 4's done-when."""
    found = {(p.source_header, p.target_label) for p in demo_result.pairs}
    assert found == {
        ("PROGRAM", "Course"),
        ("YEAR LEVEL", "Year 1-5"),
        ("FINAL GRADE", "Grade 0-100"),
    }
    assert len(demo_result.pairs) == 6          # three fields, two rows
    assert {c.target_label for c in demo_result.derived_candidates} == {"Remarks"}


def test_accepting_every_proposal_confirms_every_pair(demo_result):
    decisions = [Decision(p["target_label"], ACCEPT) for p in proposals(demo_result)]
    confirmed = apply_decisions(demo_result, decisions)

    assert confirmed.accepted == 3
    assert confirmed.corrections == 0
    assert len(confirmed.pairs) == 6
    assert all(p.confidence == CONFIDENCE_CONFIRMED for p in confirmed.pairs)


def test_a_correction_replaces_the_header_and_is_counted(demo_result):
    """The corrections count is a reportable metric (7)."""
    decisions = [
        Decision("Course", ACCEPT),
        Decision("Year 1-5", ACCEPT),
        Decision("Grade 0-100", CORRECT, "MIDTERM"),
    ]
    confirmed = apply_decisions(demo_result, decisions)

    assert confirmed.corrections == 1
    assert confirmed.accepted == 2
    grade_headers = {p.source_header for p in confirmed.pairs
                     if p.target_label == "Grade 0-100"}
    assert grade_headers == {"MIDTERM"}


def test_a_rejected_proposal_produces_no_pair_at_all(demo_result):
    decisions = [
        Decision("Course", REJECT),
        Decision("Year 1-5", ACCEPT),
        Decision("Grade 0-100", ACCEPT),
    ]
    confirmed = apply_decisions(demo_result, decisions)

    assert confirmed.rejections == 1
    assert not any(p.target_label == "Course" for p in confirmed.pairs)
    assert len(confirmed.pairs) == 4


def test_a_correction_must_name_a_header(demo_result):
    with pytest.raises(ValueError, match="names no source header"):
        apply_decisions(demo_result, [Decision("Course", CORRECT, "")])


def test_render_shows_the_derived_candidate_separately(demo_result):
    text = render(demo_result)
    assert "PROGRAM" in text and "Course" in text
    assert "No source cell was observed for:" in text
    assert "Remarks" in text


# ------------------------------- the real content script, in a real browser


@pytest.fixture(scope="module")
def recorded_events():
    """Inject the actual extension content script into the actual portal and
    drive real paste/type/select events through it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser_instance = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None
        )
        page = browser_instance.new_page()
        try:
            page.goto(variant_url("v0_base"), wait_until="load")
            page.wait_for_selector("#records-body tr")
            page.evaluate(CONTENT_JS)

            # Two rows, which is the demonstration size 0 specifies. One row
            # is not enough on a sheet portal: see the limitation test below.
            for index, (course, year, grade, remark) in enumerate([
                ("BS Computer Science", "3", "85", "Passed"),
                ("BS Information Technology", "2", "60", "Failed"),
            ]):
                row = page.locator("#records-body tr").nth(index)
                row.locator("[data-key=course] input").fill(course)
                row.locator("[data-key=year] input").fill(year)
                row.locator("[data-key=grade] input").fill(grade)
                row.locator("[data-key=remarks] select").select_option(remark)
            page.locator("h1").click()   # blur the last control

            raw = page.evaluate("() => window.__demo")
        finally:
            browser_instance.close()

    # What loading a real session does: the per-control label names its row as
    # well as its column, so column labels are assigned across the session.
    events = assign_column_labels([BrowserEvent(**e) for e in raw])
    return raw, events
def test_content_script_records_every_kind_of_write(recorded_events):
    raw, events = recorded_events
    assert events, "no events recorded"
    labels = {e.label for e in events}
    assert "Course" in labels
    assert "Grade 0-100" in labels
    assert "Remarks" in labels


def test_content_script_captures_a_select_with_its_option_list(recorded_events):
    """3.2: record the chosen option *and* the full option list - it is what
    makes derived-field detection and option matching possible."""
    _, events = recorded_events
    remarks = [e for e in events if e.label == "Remarks"]
    assert len(remarks) == 2          # one per demonstrated row
    event = next(e for e in remarks if e.row == 0)
    assert event.trigger == "select"
    assert event.value == "Passed"
    assert event.options == ["Passed", "Failed"]
    assert event.is_closed_option_field


def test_content_script_records_fill_order_and_row(recorded_events):
    """The rule inducer needs to know Grade was filled before Remarks."""
    _, events = recorded_events
    first_row = [e for e in events if e.row == 0]
    order = {}
    for event in first_row:
        order.setdefault(event.label, event.seq)

    assert order["Grade 0-100"] < order["Remarks"]
    assert {e.row for e in events} == {0, 1}


def test_one_demonstrated_row_cannot_name_a_column_on_a_sheet_portal():
    """A real limitation, not a bug.

    The accessible name of a cell control covers its column *and* its row. The
    invariant part only emerges across rows, so a single-row demonstration
    yields "Grade 0-100 Abad, Andrea A." as the field identity. 0 asks for 2-3
    demonstrated rows; on this portal two is a floor, not a preference, and the
    demonstration-efficiency curve in 7 cannot start at one row.
    """
    one_row = [
        browser(1, "Grade 0-100 Abad, Andrea A.", "85", row=0, seq=0),
    ]
    assign_column_labels(one_row)
    assert one_row[0].label == "Grade 0-100 Abad, Andrea A."

    two_rows = [
        browser(1, "Grade 0-100 Abad, Andrea A.", "85", row=0, seq=0),
        browser(2, "Grade 0-100 Aguilar, Benjamin L.", "60", row=1, seq=1),
    ]
    for event in two_rows:
        event.context["column_key"] = "records-table:col6"
    assign_column_labels(two_rows)
    assert all(e.label == "Grade 0-100" for e in two_rows)


def test_content_script_decides_no_labels(recorded_events):
    """The event carries raw context; the label only appears once resolve.py
    has run over it."""
    raw_events, _ = recorded_events
    raw = raw_events[0]
    assert "label" not in raw
    assert "context" in raw
    for key in ("label_for", "label_wrapping", "aria", "placeholder",
                "preceding_text", "name"):
        assert key in raw["context"]


# ------------------------------------------- Milestone 4 end to end


@pytest.fixture(scope="module")
def live_session(tmp_path_factory):
    """A demonstration recorded through the real content script on the real
    portal, then reconciled. Milestone 4's done-when, end to end."""
    from recorder.demo_session import record

    sheet = REPO / "data" / "sheets" / "grade_sheet.xlsx"
    if not sheet.exists():
        pytest.skip("run data/sheets/make_sheets.py first")

    path = record(rows=3, out=tmp_path_factory.mktemp("demo") / "s.jsonl")
    excel_events, browser_events = read_session(path)
    return reconcile(excel_events, browser_events)


def test_live_session_yields_the_right_confirmed_pairs(live_session):
    found = {(p.source_header, p.target_label) for p in live_session.pairs}
    assert found == {
        ("PROGRAM", "Course"),
        ("YEAR LEVEL", "Year 1-5"),
        ("FINAL GRADE", "Grade 0-100"),
    }
    # Three fields across three demonstrated rows, nothing lost.
    assert len(live_session.pairs) == 9
    assert live_session.unreconciled == []


def test_live_session_finds_remarks_derived_not_mapped(live_session):
    """The derived field must not acquire a source column - 3.9 depends on it
    being kept out of the assignment entirely."""
    assert {c.target_label for c in live_session.derived_candidates} == {"Remarks"}
    assert "Remarks" not in {p.target_label for p in live_session.pairs}
    assert all(c.closed_option_field for c in live_session.derived_candidates)


def test_live_session_records_one_event_per_write(live_session):
    """change and blur both fire; one write should still produce one pair per
    row, not two."""
    per_field = {}
    for pair in live_session.pairs:
        per_field.setdefault(pair.target_label, []).append(pair.row)
    for label, rows in per_field.items():
        assert sorted(rows) == [0, 1, 2], f"{label}: {rows}"
