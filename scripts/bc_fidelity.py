"""
bc_fidelity.py
==============
Measures how closely an agent run matches a perfect human reference fill.

Two modes:

1. Set the gold standard (run once on a perfect human submission):
   python scripts/bc_fidelity.py --set-reference data/output/submissions/PAI-2026-00444_...json

2. Score an agent run against the gold standard (called automatically from run_task.py):
   python scripts/bc_fidelity.py --submission data/output/submissions/<agent_run>.json
   python scripts/bc_fidelity.py  # scores latest submission

Fidelity score (0-100%):
    field_match_rate  × 0.40   (correct fields / total gold fields)
    value_accuracy    × 0.40   (values matching source / total typed)
    tab_coverage      × 0.10   (tabs reached / tabs in gold standard)
    completion_bonus  × 0.10   (did agent reach done action?)
"""

from __future__ import annotations

import json
import logging
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime
from pathlib import Path
from typing import Any

# Bare print() never reaches logs/latest.log (see eval_metrics.py for the full
# explanation) — the BC SCORE block was invisible in the saved log for exactly
# this reason. Silent in standalone CLI usage (no basicConfig → INFO is a no-op).
_logger = logging.getLogger(__name__)

ROOT           = Path(__file__).resolve().parent.parent
REFERENCE_PATH = ROOT / "data" / "output" / "reference" / "gold_standard.json"
PROGRESS_LOG   = ROOT / "data" / "output" / "bc_progress.jsonl"
SUBMISSIONS_DIR = ROOT / "data" / "output" / "submissions"

# Fields to skip when scoring (metadata, not filled by agent)
_SKIP_FIELDS = {"_timestamp", "policy_number", "policy_status"}

# Tab prefix → tab name mapping. Matches the real 8-tab wx form structure
# (car_insurance_entry/car_insurance_form_wx.py) confirmed directly 2026-08-11
# while fixing scope1_tab_order -- there is no "Driver 1" tab (the
# Policyholder IS the primary driver, ph_* fields), Driver 2 and Driver 3 are
# two sections inside ONE "Drivers" tab (not two separate tabs), "disc_"
# (the Coverage tab's Discounts Applied section) was missing entirely and
# fell into "Other" for every discount field, and "hist_" (the whole History
# tab) was missing entirely too.
_TAB_PREFIXES = {
    "policy_": "Policy",
    "ph_":     "Policyholder",
    "v_":      "Vehicle",
    "cov_":    "Coverage",
    "disc_":   "Coverage",
    "d2_":     "Drivers",
    "d3_":     "Drivers",
    "hist_":   "History",
    "claim_":  "Claims",
    "pay_":    "Payment",
}


def _load_json(path: Path) -> Any:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


# Policy-tab keys that don't carry the "policy_" prefix (see the Policy-tab
# block of _LABEL_TO_KEY below) — _TAB_PREFIXES' prefix match can't catch
# these, so without this override they silently fell into "Other" tab
# everywhere _tab_of() is used, including tab_coverage in score_submission()
# (10% of the fidelity score). Found 2026-08-06 while reframing the
# recording quality gate's scroll check.
_UNPREFIXED_POLICY_KEYS = {
    "effective_date", "expiration_date", "agent_id", "agent_name",
    "agency_name", "underwriter", "renewal_flag", "paperless", "esign",
}


def _tab_of(key: str) -> str:
    if key in _UNPREFIXED_POLICY_KEYS:
        return "Policy"
    for prefix, tab in _TAB_PREFIXES.items():
        if key.startswith(prefix):
            return tab
    return "Other"


def _normalize(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v).strip().lower().replace("\n", "").replace("\r", "")


# ── intake label → submission key mapping ────────────────────────────────────
_LABEL_TO_KEY: dict[str, str] = {
    # Policy tab
    "policy number":              "policy_number",
    "policy status":              "policy_status",
    "policy type":                "policy_type",
    "policy term":                "policy_term",
    "effective date":             "effective_date",
    "expiration date":            "expiration_date",
    "agent id":                   "agent_id",
    "agent name":                 "agent_name",
    "agency name":                "agency_name",
    "underwriter":                "underwriter",
    "renewal policy":             "renewal_flag",
    "paperless / e-delivery":     "paperless",
    "e-signature obtained":       "esign",
    # Policyholder tab
    "first name":                 "ph_first",
    "middle name":                "ph_middle",
    "last name":                  "ph_last",
    "suffix":                     "ph_suffix",
    "date of birth":              "ph_dob",
    "gender":                     "ph_gender",
    "ssn":                        "ph_ssn",
    "marital status":             "ph_marital",
    "occupation":                 "ph_occupation",
    "education level":            "ph_education",
    "credit score":               "ph_credit_score",
    "years continuously insured": "ph_years_insured",
    "email address":              "ph_email",
    "home phone":                 "ph_phone_home",
    "cell phone":                 "ph_phone_cell",
    "work phone":                 "ph_phone_work",
    "street address 1":           "ph_addr1",
    "street address 2":           "ph_addr2",
    "city":                       "ph_city",
    "state":                      "ph_state",
    "zip code":                   "ph_zip",
    "county":                     "ph_county",
    "country":                    "ph_country",
    "years at address":           "ph_years_at_addr",
    "homeowner":                  "ph_homeowner",
    "dl number":                  "ph_drivers_license",
    "dl issuing state":           "ph_dl_state",
    "dl expiration":              "ph_dl_exp",
    "prior insurer":              "ph_prior_insurer",
    "prior policy no.":           "ph_prior_policy_no",
    "prior expiry date":          "ph_prior_expiry",
    "prior liability limits":     "ph_prior_liability",
    # Vehicle tab
    "vin":                        "v_vin",
    "year":                       "v_year",
    "make":                       "v_make",
    "model":                      "v_model",
    "trim / sub-model":           "v_trim",
    "body type":                  "v_body",
    "color":                      "v_color",
    "number of doors":            "v_doors",
    "cylinders":                  "v_cylinders",
    "displacement (l)":           "v_displacement",
    "fuel type":                  "v_fuel",
    "transmission":               "v_transmission",
    "drive type":                 "v_drive",
    "current mileage":            "v_mileage",
    "annual miles est.":          "v_annual_miles",
    "primary use":                "v_usage",
    "garaging location":          "v_garaging",
    "purchase date":              "v_purchase_date",
    "purchase price ($)":         "v_purchase_price",
    "current market value ($)":   "v_market_value",
    "vehicle condition":          "v_condition",
    "title state":                "v_title_state",
    "lienholder/lender":          "v_lienholder",
    "lienholder address":         "v_lienholder_addr",
    "loan / lease no.":           "v_loan_number",
    "salvage title":              "v_salvage",
    "anti-theft device":          "v_anti_theft",
    "airbags":                    "v_airbags",
    "abs brakes":                 "v_abs",
    "daytime running lights":     "v_daytime_lights",
    "backup camera":              "v_backup_camera",
    "gps tracking":               "v_gps",
    "parking sensors":            "v_parking_sensors",
    "lane departure warning":     "v_lane_assist",
    "adaptive cruise control":    "v_adaptive_cruise",
    # Coverage tab -- added 2026-08-11 alongside scope1_tab_order (see
    # _TAB_PREFIXES comment above for why Coverage/Drivers/History/Claims/
    # Payment were all missing from this dict until now)
    "bodily injury (k$/k$)":      "cov_bodily_limit",
    "property damage ($)":        "cov_property",
    "collision deductible":       "cov_collision_ded",
    "comprehensive deductible":   "cov_comp_ded",
    "uninsured/underinsured motorist": "cov_um_uim",
    "personal injury protection (pip)": "cov_pip",
    "medical payments":           "cov_medpay",
    "rental reimbursement":       "cov_rental",
    "roadside assistance":        "cov_roadside",
    "gap insurance":              "cov_gap",
    "rideshare coverage":         "cov_rideshare",
    "new car replacement":        "cov_new_car",
    "accident forgiveness":       "cov_acc_forgive",
    "diminishing deductible":     "cov_disappear_ded",
    "um/uim limit":               "cov_um_limit",
    "pip limit ($)":              "cov_pip_limit",
    "medpay limit ($)":           "cov_medpay_limit",
    "rental limit":               "cov_rental_limit",
    "multi-car":                  "disc_multi_car",
    "multi-policy / bundle":      "disc_multi_policy",
    "good driver (5+ yr clean)":  "disc_good_driver",
    "good student":               "disc_good_student",
    "defensive driving course":   "disc_defensive_drv",
    "loyalty discount":           "disc_loyalty",
    "military":                   "disc_military",
    "affinity group":             "disc_affinity",
    # "Total Premium ($)" / "Payment Frequency" appear verbatim on BOTH the
    # Coverage tab ([ Premium Summary ]) and the Payment tab
    # ([ Billing Summary ]) with different underlying fields -- these two
    # entries are the Coverage-tab (default/fallback) meaning; _SECTION_OVERRIDES
    # below redirects them to pay_amount/pay_frequency specifically while
    # inside "[ Billing Summary ]".
    "total premium ($)":          "cov_premium_total",
    "payment frequency":          "cov_premium_period",
    # History tab
    "at-fault accidents":         "hist_at_fault",
    "not-at-fault accidents":     "hist_not_at_fault",
    "total accidents":            "hist_accidents_3yr",
    "moving violations":          "hist_violations_3yr",
    "comprehensive claims":       "hist_comp_claims",
    "total claims filed":         "hist_claims_3yr",
    "dui / dwi on record":        "hist_dui",
    "sr-22 / fr-44 filed":        "hist_sr22",
    "license suspended or revoked": "hist_license_susp",
    # Claims tab
    "claim number":               "claim_number",
    "date of loss":                "claim_date",
    "claim type":                  "claim_type",
    "claim status":                "claim_status",
    "claim amount ($)":            "claim_amount",
    "deductible ($)":              "claim_deductible",
    "adjuster name":                "claim_adjuster",
    "settlement amount ($)":       "claim_settlement",
    "resolution date":             "claim_resolve_date",
    "claim description":           "claim_desc",
    "police report filed":         "claim_police_rpt",
    "policyholder at fault":       "claim_at_fault",
    "injury involved":             "claim_injury",
    "third party involved":        "claim_third_party",
    "police report no.":           "claim_report_no",
    "third party name":            "claim_tp_name",
    "third party policy":          "claim_tp_policy",
    # Payment tab (non-ambiguous fields only -- "City"/"State"/"ZIP Code" and
    # "Total Premium ($)"/"Payment Frequency" are handled by
    # _SECTION_OVERRIDES below since those exact labels already mean
    # something else by default, under Policyholder/Coverage respectively)
    "down payment ($)":           "pay_down_payment",
    "balance due ($)":            "pay_balance_due",
    "payment due date":           "pay_due_date",
    "last payment date":          "pay_last_paid_date",
    "last payment amount ($)":    "pay_last_paid_amt",
    "auto-pay enrolled":          "pay_auto_pay",
    "method":                     "pay_method",
    "cardholder name":            "pay_cc_name",
    "card number":                "pay_cc_number",
    "expiration (mm/yy)":         "pay_cc_exp",
    "cvv":                        "pay_cc_cvv",
    "bank name":                  "pay_bank_name",
    "routing number":             "pay_routing",
    "account number":             "pay_account",
    "account type":               "pay_account_type",
    "street address":             "pay_billing_addr1",
}

# label → key overrides that only apply while parsing is currently inside a
# specific "[ Section ]" block -- for the handful of labels that mean a
# genuinely different field depending on section (checked BEFORE the
# section-agnostic _LABEL_TO_KEY above). Found 2026-08-11 auditing the
# intake file directly while extending _LABEL_TO_KEY to the 5 previously-
# unsupported tabs: Driver 2 and Driver 3 share an identical label set with
# each other AND with several Policyholder labels ("First Name", "DL
# Number", "Gender", ...); Payment's "[ Billing Address ]" reuses
# Policyholder's "City"/"State"/"ZIP Code" labels verbatim; Payment's
# "[ Billing Summary ]" reuses Coverage's "Total Premium ($)"/"Payment
# Frequency" labels verbatim. A flat dict cannot represent "same label,
# different field" -- _parse_intake_record() tracks the current section
# header while iterating lines specifically so this table can disambiguate.
_SECTION_OVERRIDES: dict[str, dict[str, str]] = {
    "driver 2": {
        "first name":          "d2_first",
        "last name":           "d2_last",
        "date of birth":       "d2_dob",
        "gender":              "d2_gender",
        "relationship":        "d2_relation",
        "dl number":           "d2_dl",
        "dl issuing state":    "d2_dl_state",
        "dl expiration":       "d2_dl_exp",
        "accidents (3 yr)":    "d2_accidents",
        "violations (3 yr)":   "d2_violations",
        "sr-22 required":      "d2_sr22",
        "excluded driver":     "d2_excluded",
    },
    "driver 3": {
        "first name":          "d3_first",
        "last name":           "d3_last",
        "date of birth":       "d3_dob",
        "gender":              "d3_gender",
        "relationship":        "d3_relation",
        "dl number":           "d3_dl",
        "dl issuing state":    "d3_dl_state",
        "dl expiration":       "d3_dl_exp",
        "accidents (3 yr)":    "d3_accidents",
        "violations (3 yr)":   "d3_violations",
        "sr-22 required":      "d3_sr22",
        "excluded driver":     "d3_excluded",
    },
    "billing summary": {
        "total premium ($)":  "pay_amount",
        "payment frequency":  "pay_frequency",
    },
    "billing address": {
        "city":                "pay_billing_city",
        "state":               "pay_billing_state",
        "zip code":            "pay_billing_zip",
    },
}


def _parse_bool(v: str) -> bool:
    return v.strip().upper().startswith("YES")


def _parse_intake_record(text: str, record_num: int = 1) -> dict:
    """
    Parse one record from the intake .txt file into a {submission_key: value} dict.
    record_num is 1-based.
    """
    # Split into records
    import re
    records = re.split(r"={40,}[\s\S]*?RECORD\s+\d+\s+OF\s+\d+[\s\S]*?={40,}", text)
    # Alternatively split on RECORD N OF M headers
    parts = re.split(r"RECORD\s+(\d+)\s+OF\s+\d+", text)
    # parts = [pre, '1', record1_body, '2', record2_body, ...]
    if len(parts) < 3:
        return {}

    # Find the target record
    target_body = ""
    for i in range(1, len(parts), 2):
        if int(parts[i]) == record_num:
            target_body = parts[i + 1] if i + 1 < len(parts) else ""
            break

    if not target_body:
        return {}

    result = {}
    # Tracks the current "[ Section ]" block so _SECTION_OVERRIDES can
    # disambiguate labels that mean a different field depending on section
    # (e.g. "First Name" under [ Driver 2 ] vs [ Driver 3 ] vs the
    # Policyholder tab's own default meaning). Added 2026-08-11 -- see
    # _SECTION_OVERRIDES' docstring-comment above for the full reasoning.
    current_section = ""
    _section_re = re.compile(r"^\[\s*(.+?)\s*\]$")

    for line in target_body.splitlines():
        line = line.strip()

        section_match = _section_re.match(line)
        if section_match:
            current_section = section_match.group(1).strip().lower()
            continue

        if ":" not in line or line.startswith("=") or line.startswith("-"):
            continue
        label_raw, _, value_raw = line.partition(":")
        label = label_raw.strip().rstrip(".").lower()
        value = value_raw.strip()

        # Strip [VERIFY ...] annotations -- was `\[VERIFY\]`, which only
        # matched the bare literal and missed every real variant in the
        # file ("[VERIFY before saving]", "[VERIFY -- do not store]", ...),
        # leaving the annotation text stuck in the parsed value. Found
        # 2026-08-11 while extending this parser to the Claims/Payment tabs,
        # both of which carry several [VERIFY ...]-annotated sensitive
        # fields (SSN, card number, CVV, routing number).
        value = re.sub(r"\[VERIFY[^\]]*\]", "", value).strip()
        # Strip trailing "← inline comment" annotations the intake file uses
        # for data-entry notes (e.g. "12 Month  ← NOTE: Annual term, not
        # 6-month") -- same class of bug: left uncleaned, these become part
        # of the "gold" value and would falsely mismatch against whatever an
        # agent actually types.
        value = re.sub(r"\s*←.*$", "", value).strip()

        # Skip empty or placeholder values
        if not value or value.lower() in ("(none)", "n/a", ""):
            continue

        key = (_SECTION_OVERRIDES.get(current_section, {}).get(label)
               or _LABEL_TO_KEY.get(label))
        if key is None:
            continue

        # Parse booleans
        bool_keys = {
            "renewal_flag", "paperless", "esign", "ph_homeowner",
            "v_salvage", "v_anti_theft", "v_airbags", "v_abs",
            "v_daytime_lights", "v_backup_camera", "v_gps",
            "v_parking_sensors", "v_lane_assist", "v_adaptive_cruise",
            "cov_um_uim", "cov_pip", "cov_medpay", "cov_rental",
            "cov_roadside", "cov_gap", "cov_rideshare", "cov_new_car",
            "cov_acc_forgive", "cov_disappear_ded",
            "disc_multi_car", "disc_multi_policy", "disc_good_driver",
            "disc_good_student", "disc_defensive_drv", "disc_loyalty",
            "disc_military", "disc_affinity",
            "d2_sr22", "d2_excluded", "d3_sr22", "d3_excluded",
            "hist_dui", "hist_sr22", "hist_license_susp",
            "claim_police_rpt", "claim_at_fault", "claim_injury",
            "claim_third_party", "pay_auto_pay",
        }
        if key in bool_keys:
            result[key] = _parse_bool(value)
        else:
            result[key] = value

    return result


# ── set reference ──────────────────────────────────────────────────────────────

def set_reference_from_source(source_path: Path, record_num: int = 1) -> None:
    """
    Build gold standard directly from the intake .txt file.
    No perfect human run needed — source data IS the ground truth.
    """
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = source_path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        print(f"ERROR: could not decode {source_path}")
        sys.exit(1)

    fields = _parse_intake_record(text, record_num)
    if not fields:
        print(f"ERROR: no fields parsed for record {record_num} from {source_path}")
        sys.exit(1)

    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)

    tabs = sorted({_tab_of(k) for k in fields})
    ref = {
        "source":                 source_path.name,
        "record_num":             record_num,
        "recorded":               datetime.now().isoformat(),
        "tab_order":              tabs,
        "fields":                 fields,
        "total_scorable_fields":  len(fields),
    }

    REFERENCE_PATH.write_text(json.dumps(ref, indent=2), encoding="utf-8")
    print(f"Gold standard set from source (record {record_num}) -> {REFERENCE_PATH}")
    print(f"  {len(fields)} scorable fields across tabs: {tabs}")
    # Show a sample
    sample = list(fields.items())[:5]
    for k, v in sample:
        print(f"    {k}: {v!r}")


def set_reference(submission_path: Path) -> None:
    """Save a submission JSON as the gold standard reference."""
    data = _load_json(submission_path)
    if not data:
        print(f"ERROR: could not load {submission_path}")
        sys.exit(1)

    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Extract scorable fields (non-empty, non-metadata)
    fields = {
        k: v for k, v in data.items()
        if k not in _SKIP_FIELDS and v not in ("", None, False)
    }

    # Compute which tabs are covered
    tabs = sorted({_tab_of(k) for k in fields})

    ref = {
        "source":    submission_path.name,
        "recorded":  datetime.now().isoformat(),
        "tab_order": tabs,
        "fields":    fields,
        "total_scorable_fields": len(fields),
    }

    REFERENCE_PATH.write_text(json.dumps(ref, indent=2), encoding="utf-8")
    print(f"Gold standard set -> {REFERENCE_PATH}")
    print(f"  {len(fields)} scorable fields across tabs: {tabs}")


# ── behavioral sequence scoring ───────────────────────────────────────────────

def _extract_sequence_from_traces(session_dir: Path) -> list[tuple[str, str, str]]:
    """Extract (tab, field, action_type) sequence from a recorded session."""
    seq = []
    for fpath in sorted(session_dir.glob("*.json")):
        if fpath.name == "session_manifest.json":
            continue
        t = _load_json(fpath)
        if not t:
            continue
        action = t.get("action", {})
        a_type = action.get("action_type", "")
        if not a_type or a_type == "noop":
            continue
        state = t.get("state", {})
        elems = state.get("elements", [])
        fid   = state.get("focused_element_id", "")
        tab   = next((
            e.get("label") or e.get("text") or ""
            for e in elems
            if e.get("type", "").lower() in ("tabitem", "tab") and e.get("selected")
        ), "")
        field = next((
            e.get("label") or e.get("text") or ""
            for e in elems if e.get("element_id") == fid
        ), "")
        if a_type in ("click", "keyboard") and (tab or field):
            seq.append((tab, field, a_type))
    return seq


def _extract_sequence_from_results(results: list[dict]) -> list[tuple[str, str, str]]:
    """Extract (tab, field, action_type) sequence from agent run results."""
    seq = []
    for r in results:
        action = r.get("action", {})
        a_type = action.get("action_type", "")
        if not a_type or a_type in ("noop", "wait"):
            continue
        state = r.get("state", {})
        elems = state.get("elements", [])
        fid   = state.get("focused_element_id", "")
        tab   = next((
            e.get("label") or e.get("text") or ""
            for e in elems
            if e.get("type", "").lower() in ("tabitem", "tab") and e.get("selected")
        ), "")
        field = next((
            e.get("label") or e.get("text") or ""
            for e in elems if e.get("element_id") == fid
        ), "")
        if a_type in ("click", "keyboard") and (tab or field):
            seq.append((tab, field, a_type))
    return seq


def _levenshtein_normalized(s1: list, s2: list) -> float:
    """Normalized edit distance between two sequences. Returns similarity 0-1."""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    m, n = len(s1), len(s2)
    # Cap length to keep it fast
    s1, s2 = s1[:200], s2[:200]
    m, n = len(s1), len(s2)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[j] = min(dp[j] + 1, dp[j-1] + 1, prev[j-1] + cost)
    distance = dp[n]
    return 1.0 - distance / max(m, n)


def build_behavior_reference(traces_dir: Path) -> list[tuple[str, str, str]]:
    """
    Build reference sequence from all human sessions.
    Uses the session whose sequence is closest to the median (min avg distance to all others).
    Returns the representative sequence.
    """
    sessions = sorted([d for d in traces_dir.iterdir()
                       if d.is_dir() and d.name.startswith("session_")])
    sequences = []
    for s in sessions:
        seq = _extract_sequence_from_traces(s)
        if len(seq) >= 10:
            sequences.append(seq)

    if not sequences:
        return []
    if len(sequences) == 1:
        return sequences[0]

    # Find sequence with highest average similarity to all others (the median)
    best_seq, best_score = sequences[0], -1.0
    for i, seq in enumerate(sequences):
        scores = [_levenshtein_normalized(seq, sequences[j])
                  for j in range(len(sequences)) if j != i]
        avg = sum(scores) / len(scores)
        if avg > best_score:
            best_score = avg
            best_seq = seq
    return best_seq


def score_behavior(agent_results: list[dict], reference_sequence: list) -> dict:
    """Score agent's action sequence against the human reference sequence."""
    if not reference_sequence:
        return {"behavioral_match": 0.0, "detail": "No reference sequence available."}

    agent_seq = _extract_sequence_from_results(agent_results)
    if not agent_seq:
        return {"behavioral_match": 0.0, "detail": "Agent produced no scorable actions."}

    sim = _levenshtein_normalized(agent_seq, reference_sequence)

    # Tab order match — did agent visit tabs in same order as reference?
    ref_tabs  = [t for t, _, _ in reference_sequence if t]
    agent_tabs = [t for t, _, _ in agent_seq if t]
    # Deduplicate preserving order
    def _dedup(lst):
        seen, out = set(), []
        for x in lst:
            if x and x not in seen:
                seen.add(x); out.append(x)
        return out
    ref_tab_order   = _dedup(ref_tabs)
    agent_tab_order = _dedup(agent_tabs)
    tab_order_match = _levenshtein_normalized(ref_tab_order, agent_tab_order)

    behavioral_match = sim * 0.7 + tab_order_match * 0.3

    return {
        "behavioral_match":  behavioral_match,
        "sequence_sim":      sim,
        "tab_order_match":   tab_order_match,
        "agent_seq_len":     len(agent_seq),
        "reference_seq_len": len(reference_sequence),
        "detail": (
            f"seq_sim={sim*100:.1f}%  tab_order={tab_order_match*100:.1f}%  "
            f"agent={len(agent_seq)} actions  ref={len(reference_sequence)} actions"
        ),
    }


# ── score ──────────────────────────────────────────────────────────────────────

def score_submission(agent_submission: dict, results: list[dict] | None = None) -> dict:
    """
    Score an agent submission dict against the gold standard.
    results: optional agent.run() results list (used for completion_bonus).
    """
    if not REFERENCE_PATH.exists():
        return {"error": "No gold standard set. Run with --set-reference first."}

    ref = _load_json(REFERENCE_PATH)
    if not ref:
        return {"error": "Gold standard file is corrupt."}

    gold_fields: dict = ref["fields"]
    gold_tabs:   list = ref["tab_order"]
    total_gold        = len(gold_fields)

    if total_gold == 0:
        return {"error": "Gold standard has no scorable fields."}

    # ── field match rate ───────────────────────────────────────────────────────
    matched   = 0
    filled    = 0
    mismatches = []

    for key, gold_val in gold_fields.items():
        agent_val = agent_submission.get(key)
        if agent_val in ("", None, False):
            continue
        filled += 1
        if _normalize(agent_val) == _normalize(gold_val):
            matched += 1
        else:
            mismatches.append({
                "field": key,
                "expected": gold_val,
                "got":      agent_val,
            })

    field_match_rate = matched / total_gold
    fill_rate        = filled  / total_gold

    # ── value accuracy (among filled fields only) ─────────────────────────────
    value_accuracy = matched / filled if filled else 0.0

    # ── tab coverage ──────────────────────────────────────────────────────────
    agent_tabs = {_tab_of(k) for k, v in agent_submission.items()
                  if k not in _SKIP_FIELDS and v not in ("", None, False)}
    tab_coverage = len(agent_tabs & set(gold_tabs)) / len(gold_tabs) if gold_tabs else 0.0

    # ── completion bonus ──────────────────────────────────────────────────────
    completed = False
    if results:
        completed = any(r.get("action", {}).get("action_type") == "done" for r in results)
    elif agent_submission.get("_completed"):
        completed = True
    completion_bonus = 1.0 if completed else 0.0

    # ── fidelity score ────────────────────────────────────────────────────────
    fidelity = (
        field_match_rate  * 0.40 +
        value_accuracy    * 0.40 +
        tab_coverage      * 0.10 +
        completion_bonus  * 0.10
    )

    return {
        "fidelity":          fidelity,
        "field_match_rate":  field_match_rate,
        "value_accuracy":    value_accuracy,
        "tab_coverage":      tab_coverage,
        "completion_bonus":  completion_bonus,
        "fields_matched":    matched,
        "fields_filled":     filled,
        "fields_total":      total_gold,
        "fill_rate":         fill_rate,
        "tabs_covered":      sorted(agent_tabs),
        "tabs_gold":         gold_tabs,
        "mismatches":        mismatches[:10],  # cap for readability
        "completed":         completed,
    }


def score_run(
    results: list[dict],
    goal: str = "",
    duration_sec: float | None = None,
    run_start_ts: float | None = None,
) -> dict | None:
    """
    Called from run_task.py after agent.run().
    Finds the latest submission JSON, scores it, logs progress.
    duration_sec (agent._run_duration_sec) is recorded in the progress log so
    the trend table shows whether speed is holding as fidelity improves.

    run_start_ts (agent._run_start_ts, epoch seconds) guards against scoring a
    STALE submission: data/output/submissions/ also receives auto-saves from
    the standalone form app itself (unrelated to the agent), so "newest by
    mtime" can silently pick up a leftover file from a run that crashed
    before ever submitting anything — producing a plausible-looking score for
    a run that did nothing. If the newest submission predates this run's
    start, there's no submission FROM this run; say so instead of scoring it.
    """
    if not REFERENCE_PATH.exists():
        print("[BC Fidelity] No gold standard set — skipping fidelity score.")
        print("  Run: python scripts/bc_fidelity.py --set-reference <submission.json>")
        return None

    # Find latest submission
    submissions = sorted(SUBMISSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not submissions:
        print("[BC Fidelity] No submission found for this run.")
        return None

    latest = submissions[-1]
    if run_start_ts is not None and latest.stat().st_mtime < run_start_ts:
        print(f"[BC Fidelity] Newest submission ({latest.name}) predates this run's start "
              "— this run produced no submission. Skipping (not scoring stale data).")
        return None

    agent_data = _load_json(latest)
    if not agent_data:
        return None

    scores = score_submission(agent_data, results=results)
    if "error" in scores:
        print(f"[BC Fidelity] {scores['error']}")
        return None

    # Behavioral sequence score
    traces_dir = ROOT / "data" / "output" / "traces" / "forms"
    ref_seq = build_behavior_reference(traces_dir) if traces_dir.exists() else []
    beh = score_behavior(results, ref_seq)
    scores.update(beh)

    # Combined BC score
    scores["bc_score"] = scores["fidelity"] * 0.5 + scores["behavioral_match"] * 0.5
    scores["duration_sec"] = duration_sec

    _print_report(scores, goal, latest.name)
    _append_progress(scores, goal, latest.name)
    return scores


def _print_report(s: dict, goal: str, submission_name: str) -> None:
    border = "=" * 60
    bc     = s.get("bc_score", s["fidelity"])
    star   = "DONE" if bc >= 0.80 else f"{bc*100:.1f}% / 80%"
    dur    = s.get("duration_sec")
    dur_str = f"{dur:.1f}s" if dur is not None else "n/a"

    lines = [
        f"\n{border}",
        f"  BC SCORE: {bc*100:.1f}%  [{star}]   Duration: {dur_str}",
        f"  Goal: {goal[:52]}",
        border,
        f"  Task Fidelity          {s['fidelity']*100:>6.1f}%   (correct values vs intake)",
        f"    Field Match Rate     {s['field_match_rate']*100:>6.1f}%"
        f"   ({s['fields_matched']}/{s['fields_total']} fields correct)",
        f"    Value Accuracy       {s['value_accuracy']*100:>6.1f}%"
        f"   (of {s['fields_filled']} filled)",
        f"    Tab Coverage         {s['tab_coverage']*100:>6.1f}%"
        f"   {s['tabs_covered']}",
        f"    Completion           {'100.0' if s['completed'] else '  0.0'}%",
    ]
    bm = s.get("behavioral_match")
    if bm is not None:
        lines.append(f"  Behavioral Match       {bm*100:>6.1f}%   (sequence vs human sessions)")
        lines.append(f"    Sequence Similarity  {s.get('sequence_sim',0)*100:>6.1f}%")
        lines.append(f"    Tab Order Match      {s.get('tab_order_match',0)*100:>6.1f}%")
        lines.append(f"    {s.get('detail','')}")
    if s.get("mismatches"):
        lines.append("\n  Top mismatches:")
        for m in s["mismatches"][:5]:
            lines.append(f"    {m['field']}: expected {m['expected']!r}, got {m['got']!r}")
    lines.append(f"\n  Submission: {submission_name}")
    lines.append(f"{border}\n")

    for line in lines:
        print(line)
    _logger.info("\n".join(lines))


def _append_progress(s: dict, goal: str, submission_name: str) -> None:
    PROGRESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":          datetime.now().isoformat(),
        "goal":               goal,
        "submission":         submission_name,
        "duration_sec":       round(s["duration_sec"], 2) if s.get("duration_sec") is not None else None,
        "bc_score":           round(s.get("bc_score", s["fidelity"]), 4),
        "fidelity":           round(s["fidelity"], 4),
        "behavioral_match":   round(s.get("behavioral_match", 0.0), 4),
        "field_match_rate":   round(s["field_match_rate"], 4),
        "value_accuracy":     round(s["value_accuracy"], 4),
        "tab_coverage":       round(s["tab_coverage"], 4),
        "sequence_sim":       round(s.get("sequence_sim", 0.0), 4),
        "tab_order_match":    round(s.get("tab_order_match", 0.0), 4),
        "completed":          s["completed"],
        "fields_matched":     s["fields_matched"],
        "fields_total":       s["fields_total"],
    }
    with PROGRESS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def show_progress() -> None:
    """Print the BC progress log as a trend table."""
    if not PROGRESS_LOG.exists():
        print("No progress log yet. Run the agent first.")
        return
    entries = [json.loads(l) for l in PROGRESS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not entries:
        print("Progress log is empty.")
        return
    print(f"\n{'='*70}")
    print("  BC FIDELITY PROGRESS")
    print(f"{'='*70}")
    print(f"  {'#':<4} {'Date':<20} {'BC Score':>9} {'Fidelity':>9} {'Behavior':>9} {'Done':<6} {'Duration':>9}")
    print(f"  {'-'*74}")
    for i, e in enumerate(entries, 1):
        ts   = e["timestamp"][:16].replace("T", " ")
        done = "YES" if e["completed"] else "no"
        bc   = e.get("bc_score", e["fidelity"])
        beh  = e.get("behavioral_match", 0.0)
        dur  = e.get("duration_sec")
        dur_str = f"{dur:.0f}s" if dur is not None else "n/a"
        print(f"  {i:<4} {ts:<20} {bc*100:>8.1f}%"
              f" {e['fidelity']*100:>8.1f}%"
              f" {beh*100:>8.1f}%  {done:<6}{dur_str:>9}")
    best = max(entries, key=lambda e: e.get("bc_score", e["fidelity"]))
    print(f"\n  Best BC Score: {best.get('bc_score', best['fidelity'])*100:.1f}% on {best['timestamp'][:10]}")
    print(f"  Target: 80.0%  {'REACHED' if best.get('bc_score', best['fidelity']) >= 0.80 else 'not yet'}")
    print(f"{'='*70}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="BC fidelity scorer")
    parser.add_argument("--set-reference", metavar="SUBMISSION_JSON",
                        help="Set this submission as the gold standard reference.")
    parser.add_argument("--set-reference-from-source", metavar="INTAKE_TXT",
                        help="Build gold standard from intake .txt file (no perfect run needed).")
    parser.add_argument("--record", type=int, default=1,
                        help="Which record number to use from the intake file (default: 1).")
    parser.add_argument("--submission", metavar="SUBMISSION_JSON",
                        help="Score this submission against the gold standard.")
    parser.add_argument("--progress", action="store_true",
                        help="Show BC progress trend.")
    args = parser.parse_args()

    if args.set_reference_from_source:
        set_reference_from_source(Path(args.set_reference_from_source), args.record)
    elif args.set_reference:
        set_reference(Path(args.set_reference))
    elif args.progress:
        show_progress()
    else:
        path = Path(args.submission) if args.submission else None
        if path is None:
            subs = sorted(SUBMISSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            path = subs[-1] if subs else None
        if path is None:
            print("No submission found.")
            sys.exit(1)
        data = _load_json(path)
        if not data:
            print(f"Could not load {path}")
            sys.exit(1)
        scores = score_submission(data)
        if "error" in scores:
            print(scores["error"])
        else:
            _print_report(scores, goal="", submission_name=path.name)


if __name__ == "__main__":
    main()
