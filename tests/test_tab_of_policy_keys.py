"""
Regression test for bc_fidelity._tab_of().

Bug: 9 Policy-tab submission keys (effective_date, expiration_date, agent_id,
agent_name, agency_name, underwriter, renewal_flag, paperless, esign) don't
carry the "policy_" prefix that _tab_of()'s _TAB_PREFIXES matching relies on,
so they silently fell into "Other" tab everywhere _tab_of() is used —
including tab_coverage in score_submission(), 10% of the fidelity score
reported in Thesis.docx Chapter 4. Found 2026-08-06 while reframing the
recording quality gate's scroll check, which uses the same function.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bc_fidelity import _tab_of, _LABEL_TO_KEY, _UNPREFIXED_POLICY_KEYS


def test_unprefixed_policy_keys_map_to_policy():
    for key in _UNPREFIXED_POLICY_KEYS:
        assert _tab_of(key) == "Policy", f"{key} should be Policy, not {_tab_of(key)}"


def test_prefixed_keys_still_work():
    assert _tab_of("ph_first") == "Policyholder"
    assert _tab_of("v_vin") == "Vehicle"
    assert _tab_of("policy_number") == "Policy"


def test_unknown_key_still_falls_to_other():
    assert _tab_of("totally_unmapped_key") == "Other"


def test_every_label_to_key_value_resolves_to_a_real_tab():
    # No entry in the label→key map should silently land in "Other" —
    # every field in this map genuinely belongs to Policy, Policyholder,
    # or Vehicle (the only tabs currently covered).
    for key in _LABEL_TO_KEY.values():
        assert _tab_of(key) != "Other", f"{key} unexpectedly resolved to Other"
