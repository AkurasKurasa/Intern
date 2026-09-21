"""
Tests for the Chapter 4 reporting pipeline.

Three layers, matching the project's testing rule:

  unit         — individual measurements and the verdict logic
  integration  — the full grid assembled from the live metric files
  e2e          — figures rendered and a chapter written into a real .docx

The pipeline's whole purpose is that no number in the thesis is typed by hand,
so the tests concentrate on the ways that guarantee could quietly break: a
verdict that flatters a mixed result, a test-suite fixture leaking into a
reported figure, a measurement scored against the wrong objective's target.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "scripts", "thesis_figures")
sys.path.insert(0, SCRIPTS)

import objective_metrics as om  # noqa: E402


# ==========================================================================
# unit — verdict logic
# ==========================================================================


def _obj(**cells) -> om.Objective:
    return om.Objective(
        key="t", number=1, branch="B", title="t", target_text="x",
        target_value=0.9,
        scopes={k: om.Cell(k, verdict=v) for k, v in cells.items()},
    )


def test_all_measured_scopes_met_is_met():
    assert _obj(S1=om.MET, S2=om.MET).verdict() == om.MET


def test_all_measured_scopes_failed_is_not_met():
    assert _obj(S1=om.NOT_MET, S2=om.NOT_MET).verdict() == om.NOT_MET


def test_mixed_scopes_is_partial_not_met():
    """The regression this rollup exists to prevent.

    A rollup that let the best scope stand for the objective would report
    this as MET while two of three scopes fall short.
    """
    o = _obj(S1=om.NOT_MET, S2=om.MET, S3=om.NOT_MET)
    assert o.verdict() == om.PARTIAL
    assert o.verdict() != om.MET


def test_unmeasured_scopes_do_not_count_toward_verdict():
    o = _obj(S1=om.MET, S2=om.NOT_EVAL, S3=om.NA)
    assert o.verdict() == om.MET


def test_no_measured_scope_is_not_evaluated():
    assert _obj(S1=om.NOT_EVAL, S2=om.NA).verdict() == om.NOT_EVAL


def test_not_evaluated_is_distinct_from_not_met():
    assert om.NOT_EVAL != om.NOT_MET


@pytest.mark.parametrize("value,target,higher,expected", [
    (0.95, 0.90, True, om.MET),
    (0.80, 0.90, True, om.NOT_MET),
    (0.04, 0.05, False, om.MET),      # ceiling objectives invert
    (0.11, 0.05, False, om.NOT_MET),
    (None, 0.90, True, om.NOT_EVAL),
    (0.90, None, True, om.NOT_EVAL),
])
def test_verdict_respects_direction(value, target, higher, expected):
    assert om._verdict(value, target, higher) == expected


def test_boundary_value_counts_as_met():
    """Objective 6 lands exactly on 75%, so this boundary is load-bearing."""
    assert om._verdict(0.75, 0.75, True) == om.MET


# ==========================================================================
# unit — guarding the reported numbers
# ==========================================================================


def test_pytest_fixtures_excluded_from_training_results(tmp_path, monkeypatch):
    """A test fixture must never be reported as a training result."""
    log = tmp_path / "train.jsonl"
    rows = [
        {"trace_dir": r"C:\Temp\pytest-of-paula\pytest-244\x", "n_train": 4,
         "best_val_click_acc": 0.99},
        {"trace_dir": r"C:\real\traces", "n_train": 6401,
         "best_val_click_acc": 0.5225},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf8")
    monkeypatch.setattr(om, "P_TRAIN_LOG", str(log))

    real = om._real_training_runs()
    assert len(real) == 1
    assert real[0]["n_train"] == 6401
    # The 0.99 fixture would otherwise become the headline accuracy.
    assert om.s1_model_accuracy().value == pytest.approx(0.5225)


def test_descriptive_recast_drops_the_verdict_but_keeps_the_value():
    """A measure reused under another objective must not carry its verdict."""
    scored = om.Cell("S3", value=0.827, n=277, verdict=om.MET, method="m", note="orig")
    recast = om._descriptive(scored, "Context.")
    assert recast.value == scored.value
    assert recast.n == scored.n
    assert recast.verdict == om.NOT_EVAL
    assert "Context." in recast.note


def test_single_training_run_reports_no_scalability_value(tmp_path, monkeypatch):
    log = tmp_path / "train.jsonl"
    log.write_text(json.dumps(
        {"trace_dir": r"C:\real", "n_train": 6401, "best_val_click_acc": 0.52}
    ), encoding="utf8")
    monkeypatch.setattr(om, "P_TRAIN_LOG", str(log))

    cell = om.s1_scalability()
    assert cell.value is None, "one point must not be presented as a trend"
    assert cell.verdict == om.NOT_EVAL
    assert cell.detail["points"]


def test_loaders_tolerate_missing_files(monkeypatch):
    monkeypatch.setattr(om, "P_ENCODING", "does_not_exist.jsonl")
    assert om.s1_encoding_ambiguity().verdict == om.NOT_EVAL


def test_loader_survives_a_torn_final_line(tmp_path):
    f = tmp_path / "torn.jsonl"
    f.write_text('{"a": 1}\n{"b": 2}\n{"c": ', encoding="utf8")
    assert len(om._read_jsonl(str(f))) == 2


def test_cell_display_formats_rates_as_percent():
    assert om.Cell("S1", value=0.8267).display() == "82.7%"
    assert om.Cell("S1", value=None).display() == "\u2014"


# ==========================================================================
# integration — the real grid over the real metric files
# ==========================================================================


@pytest.fixture(scope="module")
def grid():
    return om.collect_all()


def test_grid_covers_all_twelve_objectives(grid):
    assert len(grid) == 12
    assert [o.number for o in grid.values()] == list(range(1, 13))


def test_every_objective_addresses_all_three_scopes(grid):
    for o in grid.values():
        assert set(o.scopes) == set(om.SCOPES), f"Objective {o.number} misses a scope"


def test_every_cell_without_a_value_explains_itself(grid):
    """An empty cell must say why it is empty, never sit blank."""
    for o in grid.values():
        for s, c in o.scopes.items():
            if c.value is None and c.verdict in (om.NOT_EVAL, om.NA):
                assert c.note.strip(), f"Objective {o.number}/{s} is blank with no reason"


def test_scored_cells_carry_a_sample_size(grid):
    for o in grid.values():
        for s, c in o.scopes.items():
            if c.verdict in (om.MET, om.NOT_MET) and c.value is not None:
                assert c.n, f"Objective {o.number}/{s} reports a value without n"


def test_adaptability_result_states_its_qualifications(grid):
    """Objective 6 is the headline claim; it must not read as a clean pass."""
    cell = grid["obj6"].scopes["S2"]
    assert cell.verdict == om.MET
    for phrase in ("sample is small", "threshold", "pooling flatters"):
        assert phrase in cell.note, f"missing qualification: {phrase}"
    assert cell.detail["variants_fully_resolved"] < cell.detail["n_variants"]


def test_scope1_execution_figures_disclose_the_instrument_defect(grid):
    note = grid["obj8"].scopes["S1"].note
    assert "metrics" in note.lower()
    assert "under-report" in note.lower()


def test_scope2_write_fidelity_is_not_claimed_as_correctness(grid):
    note = grid["obj8"].scopes["S2"].note.lower()
    assert "not" in note and "correctness" in note


def test_representation_objective_not_scored_on_stability(grid):
    """Scope 3 stability informs Objective 2 but never scores it."""
    assert grid["obj2"].scopes["S3"].verdict == om.NOT_EVAL
    assert grid["obj6"].scopes["S3"].verdict in (om.MET, om.NOT_MET)


def test_grid_serialises_to_json(grid):
    blob = json.dumps(om.to_json(grid), default=str)
    assert len(blob) > 1000


# ==========================================================================
# e2e — figures and the written chapter
# ==========================================================================


@pytest.mark.slow
def test_renders_every_figure(tmp_path):
    pytest.importorskip("matplotlib")
    import render_figures as rf

    paths = rf.render_all(str(tmp_path))
    assert len(paths) >= 13
    for p in paths:
        assert os.path.getsize(p) > 5000, f"{p} looks empty"
    assert any("matrix" in p for p in paths)


@pytest.mark.slow
def test_writes_chapter_into_a_document(tmp_path):
    """Full pipeline: metrics -> figures -> a real .docx that still opens."""
    docx = pytest.importorskip("docx")
    pytest.importorskip("matplotlib")
    import build_chapter4 as bc
    import render_figures as rf

    src = bc.THESIS
    if not os.path.exists(src):
        pytest.skip("thesis document not present on this machine")
    try:
        with open(src, "r+b"):
            pass
    except PermissionError:
        pytest.skip("thesis is open in Word")

    rf.render_all(bc.FIG_DIR)
    target = tmp_path / "t.docx"
    shutil.copy2(src, target)

    doc = docx.Document(str(target))
    bc.build(doc, om.collect_all())
    doc.save(str(target))

    out = docx.Document(str(target))
    heads = [p.text.strip() for p in out.paragraphs if p.style.name == "Heading 3"]
    for i in range(1, 13):
        assert any(h.startswith(f"4.1.{i} ") for h in heads), f"4.1.{i} missing"
    assert any("Summary of Objective Attainment" in h for h in heads)

    texts = [p.text for p in out.paragraphs]
    assert any("4.2 Presentation of the Application" in t for t in texts), \
        "the following section was destroyed"
    assert len(out.inline_shapes) >= 13


@pytest.mark.slow
def test_refuses_to_write_a_locked_document(tmp_path, monkeypatch, capsys):
    """Writing under Word's lock would corrupt the thesis."""
    import build_chapter4 as bc

    fake = tmp_path / "locked.docx"
    fake.write_bytes(b"x")

    def boom(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr("builtins.open", boom)
    monkeypatch.setattr(sys, "argv", ["build_chapter4.py", "--thesis", str(fake)])
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    assert bc.main() == 2
    assert "open in Microsoft Word" in capsys.readouterr().out


def test_section_bounds_refuse_to_guess(tmp_path):
    """Without both anchors the script must stop, not edit a guessed range."""
    docx = pytest.importorskip("docx")
    import build_chapter4 as bc

    d = docx.Document()
    d.add_paragraph("nothing resembling the thesis")
    p = tmp_path / "x.docx"
    d.save(str(p))

    with pytest.raises(SystemExit):
        bc.find_section_bounds(docx.Document(str(p)))
