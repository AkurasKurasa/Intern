"""
Regression tests for scripts/bc_fidelity.py's tab-coverage fix, 2026-08-11,
found while investigating scope1_tab_order (the transformer sometimes skips
or revisits tabs).

The tab-order/tab-jump problem itself is a model-training-data problem, not
a code bug (see DEVELOPERS.md/Task Tree for that investigation) -- but while
tracing how tab coverage gets measured, found the QA tooling used to check
whether new recordings actually cover all 8 tabs had its own real bugs that
made it structurally impossible to see past the first 3 tabs (Policy,
Policyholder, Vehicle):

1. `_TAB_PREFIXES` had a phantom "d1_" -> "Driver 1" entry (no such prefix
   exists in the real form -- the Policyholder tab IS the primary driver),
   split Driver 2/3 into two fictitious separate tabs instead of one real
   "Drivers" tab, and was missing "disc_" (Coverage's discount checkboxes)
   and "hist_" (the entire History tab) outright.
2. `_LABEL_TO_KEY` (used to parse the human-readable intake .txt file into
   a gold-standard reference) only had entries for Policy/Policyholder/
   Vehicle -- Coverage/Drivers/History/Claims/Payment were never
   extractable from the intake file at all, so the gold-standard tab list
   could never include them regardless of what the agent actually did.
3. Extending (2) surfaced three real label collisions the OLD flat-dict
   parser could never have handled correctly even if entries were added
   naively: "First Name" etc. mean different fields under [ Driver 2 ] vs
   [ Driver 3 ] vs the Policyholder default; "City"/"State"/"ZIP Code"
   mean different fields under Payment's [ Billing Address ] vs the
   Policyholder default; "Total Premium ($)"/"Payment Frequency" mean
   different fields under Payment's [ Billing Summary ] vs Coverage's
   default. Fixed by making _parse_intake_record() track the current
   "[ Section ]" header and checking _SECTION_OVERRIDES before the
   section-agnostic default.
4. Two unrelated value-cleaning bugs found and fixed in the same pass:
   the old `\\[VERIFY\\]` regex only matched the bare literal, missing every
   real variant in the file ("[VERIFY before saving]", "[VERIFY -- do not
   store]", ...), leaving annotation text stuck in parsed values; inline
   "<- NOTE: ..." comments the intake file uses for data-entry notes were
   never stripped either. Both would have falsely mismatched a correct
   agent-typed value against a corrupted gold value.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bc_fidelity


class TestTabPrefixesMatchRealForm:
    def test_no_phantom_driver_1_tab(self):
        assert "d1_" not in bc_fidelity._TAB_PREFIXES

    def test_driver_2_and_driver_3_share_one_real_drivers_tab(self):
        assert bc_fidelity._tab_of("d2_first") == "Drivers"
        assert bc_fidelity._tab_of("d3_first") == "Drivers"

    def test_discount_fields_map_to_coverage_not_other(self):
        assert bc_fidelity._tab_of("disc_good_driver") == "Coverage"
        assert bc_fidelity._tab_of("disc_multi_car") == "Coverage"

    def test_history_tab_is_recognized(self):
        assert bc_fidelity._tab_of("hist_dui") == "History"

    def test_unknown_prefix_still_falls_back_to_other(self):
        assert bc_fidelity._tab_of("totally_unknown_key") == "Other"


class TestSectionAwareLabelDisambiguation:
    """The core fix: a flat label->key dict can't represent 'same label,
    different field' -- these labels are only resolvable correctly given
    which [ Section ] the parser is currently inside."""

    def _parse(self, body: str) -> dict:
        text = f"RECORD 1 OF 1\n{body}\nRECORD 2 OF 1\n"
        return bc_fidelity._parse_intake_record(text, record_num=1)

    def test_driver_2_and_driver_3_first_name_do_not_collide(self):
        body = (
            "[ Driver 2 ]\n"
            "First Name           : Maria\n"
            "[ Driver 3 ]\n"
            "First Name           : Tyler\n"
        )
        fields = self._parse(body)
        assert fields["d2_first"] == "Maria"
        assert fields["d3_first"] == "Tyler"

    def test_driver_dl_fields_do_not_collide_with_policyholder_dl(self):
        body = (
            "[ Driver's License ]\n"
            "DL Number            : D7734821\n"
            "[ Driver 2 ]\n"
            "DL Number            : F8821047\n"
        )
        fields = self._parse(body)
        assert fields["ph_drivers_license"] == "D7734821"
        assert fields["d2_dl"] == "F8821047"

    def test_billing_address_does_not_collide_with_policyholder_address(self):
        body = (
            "[ Address ]\n"
            "City                 : Riverside\n"
            "State                : California\n"
            "[ Billing Address ]\n"
            "City                 : Dallas\n"
            "State                : Texas\n"
        )
        fields = self._parse(body)
        assert fields["ph_city"] == "Riverside"
        assert fields["ph_state"] == "California"
        assert fields["pay_billing_city"] == "Dallas"
        assert fields["pay_billing_state"] == "Texas"

    def test_billing_summary_does_not_collide_with_coverage_premium(self):
        body = (
            "[ Premium Summary ]\n"
            "Total Premium ($)        : 187.42\n"
            "Payment Frequency        : Monthly\n"
            "[ Billing Summary ]\n"
            "Total Premium ($)        : 187.42\n"
            "Payment Frequency        : Monthly\n"
        )
        fields = self._parse(body)
        assert fields["cov_premium_total"] == "187.42"
        assert fields["cov_premium_period"] == "Monthly"
        assert fields["pay_amount"] == "187.42"
        assert fields["pay_frequency"] == "Monthly"

    def test_section_context_resets_correctly_across_three_drivers(self):
        """Regression guard: current_section must track the MOST RECENT
        header, not stick to the first one seen."""
        body = (
            "[ Driver 2 ]\n"
            "Last Name            : Delgado\n"
            "[ Driver 3 ]\n"
            "Last Name            : Alsoelgado\n"
        )
        fields = self._parse(body)
        assert fields["d2_last"] == "Delgado"
        assert fields["d3_last"] == "Alsoelgado"


class TestAllEightTabsExtractable:
    def test_full_real_intake_file_covers_all_eight_tabs(self):
        """End-to-end guard against the original bug: gold_tabs used to be
        structurally capped at 3 tabs no matter what the intake file
        contained. Uses the real intake file, record 1 (the fullest
        record)."""
        intake_path = (Path(__file__).resolve().parent.parent
                        / "data_entry_tasks" / "data_entry_intake.txt")
        text = intake_path.read_text(encoding="utf-8")
        fields = bc_fidelity._parse_intake_record(text, record_num=1)
        tabs = {bc_fidelity._tab_of(k) for k in fields}

        assert tabs == {
            "Policy", "Policyholder", "Vehicle", "Coverage",
            "Drivers", "History", "Claims", "Payment",
        }


class TestBoolKeysCompleteForNewFields:
    """Caught by a direct sanity check while building this fix: several new
    checkbox fields (Coverage's additional-coverage toggles, all 8 discount
    fields) were initially left out of bool_keys and came back as the raw
    string 'YES (check)' instead of True/False."""

    def _parse(self, body: str) -> dict:
        text = f"RECORD 1 OF 1\n{body}\nRECORD 2 OF 1\n"
        return bc_fidelity._parse_intake_record(text, record_num=1)

    def test_coverage_checkbox_fields_parse_as_bool(self):
        body = "Uninsured/Underinsured Motorist : YES (check)\n"
        fields = self._parse(body)
        assert fields["cov_um_uim"] is True

    def test_discount_checkbox_fields_parse_as_bool(self):
        body = "Good Driver (5+ yr clean): YES (check)\n"
        fields = self._parse(body)
        assert fields["disc_good_driver"] is True

    def test_history_and_claims_checkbox_fields_parse_as_bool(self):
        body = (
            "DUI / DWI on Record      : NO\n"
            "Police Report Filed      : YES (check)\n"
        )
        fields = self._parse(body)
        assert fields["hist_dui"] is False
        assert fields["claim_police_rpt"] is True


class TestValueCleaning:
    def _parse(self, body: str) -> dict:
        text = f"RECORD 1 OF 1\n{body}\nRECORD 2 OF 1\n"
        return bc_fidelity._parse_intake_record(text, record_num=1)

    def test_strips_verify_annotations_with_extra_text(self):
        """The old regex only matched the bare literal '[VERIFY]' -- real
        variants in the intake file like '[VERIFY before saving]' were
        never stripped, leaking into the gold value."""
        body = "SSN                  : 512-88-4401  [VERIFY]\n"
        fields = self._parse(body)
        assert fields["ph_ssn"] == "512-88-4401"

    def test_strips_verify_annotations_with_trailing_commentary(self):
        body = "Card Number              : 4532 8812 0044 7761  [VERIFY before saving]\n"
        fields = self._parse(body)
        assert fields["pay_cc_number"] == "4532 8812 0044 7761"

    def test_strips_trailing_arrow_comments(self):
        body = "Policy Term          : 12 Month  ← NOTE: Annual term, not 6-month\n"
        fields = self._parse(body)
        assert fields["policy_term"] == "12 Month"

    def test_arrow_comment_stripping_does_not_break_plain_values(self):
        body = "Policy Term          : 6 Month\n"
        fields = self._parse(body)
        assert fields["policy_term"] == "6 Month"
