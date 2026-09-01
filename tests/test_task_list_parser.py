import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INBOX_DIR = os.path.join(_ROOT, "components", "inbox_router")
for _p in (_ROOT, _INBOX_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from task_list_parser import ColdEmailTarget, DEFAULT_TASK_LIST_PATH, parse_cold_email_targets


def _write(tmp_path, content):
    path = tmp_path / "task_list.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_parses_three_targets_from_the_standard_format(tmp_path):
    path = _write(tmp_path,
        "Cold email:\n"
        "Dana Whitfield <dana.whitfield@northline.example.com>\n"
        "Marcus Oyelaran <m.oyelaran@delridge.example.com>\n"
        "Priya Ramaswami <priya@ramaswami-consulting.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [
        ColdEmailTarget(name="Dana Whitfield", email="dana.whitfield@northline.example.com", context_line=""),
        ColdEmailTarget(name="Marcus Oyelaran", email="m.oyelaran@delridge.example.com", context_line=""),
        ColdEmailTarget(name="Priya Ramaswami", email="priya@ramaswami-consulting.example.com", context_line=""),
    ]


def test_the_committed_task_list_parses_to_well_formed_targets():
    # Deliberately structural, not an exact-match: the real task_list.txt is
    # meant to be edited by whoever uses this feature (the empty state on
    # the Cold Email page tells them to), so this test must survive real edits.
    targets = parse_cold_email_targets(DEFAULT_TASK_LIST_PATH)
    assert len(targets) > 0
    for t in targets:
        assert t.name.strip() != ""
        assert "@" in t.email


def test_heading_with_context_text_becomes_the_pre_filled_subject(tmp_path):
    path = _write(tmp_path, "Cold email: Q3 partnership outreach\nDana Whitfield <dana@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com",
                                        context_line="Q3 partnership outreach")]


def test_malformed_target_line_is_skipped_not_guessed_at(tmp_path):
    path = _write(tmp_path, "Cold email:\nnot a valid target line\nDana Whitfield <dana@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_blank_line_ends_the_section(tmp_path):
    path = _write(tmp_path, "Cold email:\nDana Whitfield <dana@x.example.com>\n\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_a_different_heading_ends_the_section(tmp_path):
    path = _write(tmp_path, "Cold email:\nDana Whitfield <dana@x.example.com>\nOther section:\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_a_malformed_colon_terminated_line_also_ends_the_section(tmp_path):
    path = _write(tmp_path, "Cold email:\nDana Whitfield <dana@x.example.com>\nFollow up soon:\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="")]


def test_two_separate_headings_each_get_their_own_context(tmp_path):
    path = _write(tmp_path,
        "Cold email: Conference follow-up\nDana Whitfield <dana@x.example.com>\n"
        "\n"
        "Cold email: Referral thank-you\nMarcus Oyelaran <m@x.example.com>\n")
    targets = parse_cold_email_targets(path)
    assert targets == [
        ColdEmailTarget(name="Dana Whitfield", email="dana@x.example.com", context_line="Conference follow-up"),
        ColdEmailTarget(name="Marcus Oyelaran", email="m@x.example.com", context_line="Referral thank-you"),
    ]


def test_missing_file_returns_empty_list(tmp_path):
    assert parse_cold_email_targets(str(tmp_path / "does_not_exist.txt")) == []


def test_no_heading_at_all_returns_no_targets(tmp_path):
    path = _write(tmp_path, "Dana Whitfield <dana@x.example.com>\n")
    assert parse_cold_email_targets(path) == []
