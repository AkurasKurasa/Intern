"""scripts/check_env.py -- the guard against a half-installed environment.

Two dependencies were declared in requirements.txt and never installed on a
working machine this session, and neither failed loudly:

  * uiautomation -- soft-imported inside a try/except, so 29 tests failed much
    later with "no attribute '_uia'", which reads like a code bug.
  * wxPython -- the Electron "Launch Test Tools" button spawned the wx form
    with stdio:"ignore", so the ImportError went nowhere and the button
    reported success while nothing opened.

Running the checker found six more missing straight away, including the two
perception backends the Settings tab offers. These tests pin the behaviour that
makes it trustworthy: it reads requirements.txt rather than a hand-kept list,
it maps pip names to import names, and it fails loudly.

Run:  python -m pytest tests/test_check_env.py -q
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import check_env  # noqa: E402


# ------------------------------------------------------- parsing the source


def test_reads_the_real_requirements_file():
    """The list is derived, not duplicated. A dependency added to
    requirements.txt must be checked without touching this script."""
    packages = check_env.declared_packages()
    assert "torch" in packages
    assert "wxPython" in packages
    assert "uiautomation" in packages
    assert len(packages) > 15


def test_comments_pins_and_blank_lines_are_stripped(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text(
        "# a comment\n"
        "\n"
        "torch>=2.0.0\n"
        "Pillow>=10.0.0   # trailing comment\n"
        "pytest\n"
        "some-extra[all]>=1.0\n"
        "package==1.2.3 ; python_version >= '3.10'\n",
        encoding="utf-8")
    assert check_env.declared_packages(req) == [
        "torch", "Pillow", "pytest", "some-extra", "package"]


def test_the_four_gaps_found_this_session_are_now_declared():
    """playwright, pandas, openpyxl and xlwings were all imported by Scope #2
    or #3 but absent from requirements.txt -- a fresh clone that installed
    exactly that file got an ImportError the moment it ran either scope."""
    packages = set(check_env.declared_packages())
    for missing_before in ("playwright", "pandas", "openpyxl", "xlwings"):
        assert missing_before in packages, f"{missing_before} fell out of requirements.txt"


# ------------------------------------------------------ pip name -> import


@pytest.mark.parametrize("package,module", [
    ("wxPython", "wx"),
    ("opencv-python", "cv2"),
    ("Pillow", "PIL"),
    ("pywin32", "win32gui"),
    ("sentence-transformers", "sentence_transformers"),
    ("google-api-python-client", "googleapiclient"),
    ("google-genai", "google.genai"),
])
def test_known_name_mismatches(package, module):
    """The mappings that cannot be derived: you install opencv-python and
    import cv2. Getting one wrong would report a present package as missing."""
    assert check_env.import_name(package) == module


@pytest.mark.parametrize("package,module", [
    ("torch", "torch"),
    ("pytest", "pytest"),
    ("google-auth-oauthlib", "google_auth_oauthlib"),
])
def test_unmapped_names_fall_back_to_the_normalised_form(package, module):
    assert check_env.import_name(package) == module


# ------------------------------------------------------------- the check


def test_importable_and_missing_are_told_apart():
    assert check_env.is_importable("json") is True
    assert check_env.is_importable("pytest") is True
    assert check_env.is_importable("a_module_that_does_not_exist_anywhere") is False


def test_a_missing_namespace_parent_does_not_raise():
    """find_spec raises rather than returning None when the parent package of
    a dotted name is itself absent, which would crash the whole report."""
    assert check_env.is_importable("nonexistent_parent.child") is False


def test_check_reports_a_missing_package_with_what_it_breaks():
    result = check_env.check(only=["a_module_that_does_not_exist_anywhere"])
    # Nothing in requirements maps to that name, so nothing is checked at all.
    assert result["missing"] == [] and result["present"] == []

    wx = check_env.check(only=["wx"])
    entries = wx["missing"] + wx["present"]
    assert len(entries) == 1
    assert entries[0]["package"] == "wxPython"
    assert "Scope #1" in entries[0]["used_by"]


def test_the_report_always_carries_the_fix_command():
    """The report has to say what to DO about it -- the whole failure this
    guards against was a missing package with no visible remedy."""
    assert check_env.check()["fix"] == "python -m pip install -r requirements.txt"


def test_ok_is_false_when_anything_is_missing(monkeypatch):
    monkeypatch.setattr(check_env, "is_importable", lambda m: False)
    result = check_env.check(only=["wx"])
    assert result["ok"] is False
    assert result["missing"] and not result["present"]


def test_ok_is_true_when_everything_imports(monkeypatch):
    monkeypatch.setattr(check_env, "is_importable", lambda m: True)
    result = check_env.check(only=["wx"])
    assert result["ok"] is True
    assert result["present"] and not result["missing"]


# ------------------------------------------------------------- the CLI


def test_json_mode_is_parseable_and_exits_nonzero_when_incomplete(monkeypatch, capsys):
    """The Electron app parses this output to decide whether to launch a tool,
    so it must be valid JSON and must not be polluted by the human report."""
    import json

    monkeypatch.setattr(sys, "argv", ["check_env.py", "--json", "--only", "wx"])
    monkeypatch.setattr(check_env, "is_importable", lambda m: False)

    code = check_env.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["missing"][0]["package"] == "wxPython"
    assert payload["fix"]


def test_cli_exits_zero_when_the_environment_is_complete(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["check_env.py", "--only", "wx"])
    monkeypatch.setattr(check_env, "is_importable", lambda m: True)

    assert check_env.main() == 0
    assert "installed" in capsys.readouterr().out
