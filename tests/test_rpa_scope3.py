"""Tests for scripts/rpa_scope3.py (Objective 7, Scope 3 comparison)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rpa_scope3 as s3  # noqa: E402


def test_test_set_matches_the_labels_and_excludes_history():
    inbox, _ = s3.load()
    labels = json.loads(s3.LABELS.read_text(encoding="utf-8"))["labels"]
    test = {m.id for m in inbox if m.id not in s3.HISTORY}
    assert test == set(labels) and len(test) == 25
    assert not (s3.HISTORY & set(labels))


def test_perturbation_keeps_domain_and_body_and_changes_surface():
    inbox, _ = s3.load()
    m = next(x for x in inbox if x.id == "mock-203")
    p = s3.perturb(m)
    assert p.sender_email != m.sender_email
    assert p.sender_email.split("@")[1] == m.sender_email.split("@")[1]
    assert p.body_text == m.body_text
    assert p.subject.startswith("[EXTERNAL] ") and "chat" in p.subject and "call" not in p.subject.lower()


def test_each_single_change_alters_only_its_own_part():
    inbox, _ = s3.load()
    m = next(x for x in inbox if x.id == "mock-203")     # subject has "call"
    snd = s3.perturb(m, ("sender",))
    assert snd.sender_email != m.sender_email and snd.subject == m.subject
    tag = s3.perturb(m, ("tag",))
    assert tag.sender_email == m.sender_email and tag.subject == "[EXTERNAL] " + m.subject
    syn = s3.perturb(m, ("synonyms",))
    assert syn.sender_email == m.sender_email and "chat" in syn.subject and not syn.subject.startswith("[EXTERNAL]")
    for p in (snd, tag, syn):
        assert p.body_text == m.body_text
        assert p.sender_email.split("@")[1] == m.sender_email.split("@")[1]


def test_combined_change_equals_default_perturb():
    inbox, _ = s3.load()
    m = inbox[0]
    a, b = s3.perturb(m), s3.perturb(m, s3.CHANGES)
    assert (a.subject, a.sender_email) == (b.subject, b.sender_email)


def test_baseline_rules_come_only_from_the_history():
    inbox, sent = s3.load()
    history = [m for m in inbox if m.id in s3.HISTORY]
    sender_rules, _ = s3.build_baseline_rules(history, sent)
    assert sender_rules == {"priya.raman@vendorco.com": "reply", "it-alerts@thirdpartyvendor.com": "forward"}


def test_baseline_hands_off_what_no_rule_matches():
    inbox, sent = s3.load()
    rules = s3.build_baseline_rules([m for m in inbox if m.id in s3.HISTORY], sent)
    m = next(x for x in inbox if x.id == "mock-001")
    assert s3.baseline_decide(m, rules) == s3.HANDOFF


def test_mcnemar_exact_known_values():
    assert s3.mcnemar_exact(0, 8) == 2 / 256
    assert s3.mcnemar_exact(0, 0) == 1.0
    assert abs(s3.mcnemar_exact(4, 1) - 0.375) < 1e-9


def test_metrics_counts_handoff_and_wrong_action_separately():
    labels = {"a": {"primary": "reply", "acceptable": ["reply"]},
              "b": {"primary": "schedule", "acceptable": ["schedule", "reply"]},
              "c": {"primary": "forward", "acceptable": ["forward"]}}
    m = s3.metrics({"a": "reply", "b": s3.HANDOFF, "c": "leave_alone"}, labels)
    assert m == {"n": 3, "strict": 1, "lenient": 1, "handoff": 1, "wrong_action": 1}
