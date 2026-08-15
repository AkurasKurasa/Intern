"""What the right answer is, stated once (7).

Ground truth is expressed against the portal's data-key rather than its visible
label, because a variant that renames every field must still be scored against
the same truth. The scanner surfaces the key as `truth_key`; nothing in the
matcher, the extractor or the resolver may read it.

The other half of the truth is what should *not* map. Four of the sheet's
columns feed no field at all, and on V3 and V5 several fields have no source
column. Abstaining on those is correct behaviour, not a miss, so both sides are
recorded here and scored separately.
"""

# Sheet column -> the portal data-key it belongs in.
COLUMN_TO_KEY = {
    "PROGRAM": "course",
    "YEAR LEVEL": "year",
    "FINAL GRADE": "grade",
}

# Sheet columns with no target field. 3.11 wants non-assignment tested in the
# base case, and these are what test it: two identity columns the sheet portal
# prints rather than accepts, plus two term grades that are not the final one.
COLUMNS_WITHOUT_TARGET = {
    "No.", "STUDENT NUMBER", "NAME OF STUDENT", "MIDTERM", "FINAL",
}

# Fields that must never receive a column, per variant. `remarks` is derived
# everywhere; the rest are the deliberate distractors.
FIELDS_WITHOUT_SOURCE = {
    "v0_base": {"recommendations"},
    "v1_reordered": {"recommendations"},
    "v2_relabeled": {"recommendations"},
    "v3_extra_fields": {"recommendations", "section", "adviser"},
    "v4_unassociated": {"recommendations"},
    "v5_near_duplicates": {"recommendations", "grade_recomputed", "year_enrolled"},
    "v6a_options": {"recommendations"},
    "v6b_scale": {"recommendations"},
}

DERIVED_KEY = "remarks"

# The passing mark on each scale, for scoring induced rules.
TRUE_CUTOFF = {"0-100": 75.0, "1-5": 3.0}
TRUE_OPERATOR = {"0-100": ">=", "1-5": "<="}


def expected_key(header):
    """The data-key this column should land in, or None if it should abstain."""
    return COLUMN_TO_KEY.get(header)


def should_abstain(header):
    return header not in COLUMN_TO_KEY


def scorable_fields(descriptors, variant):
    """Fields that enter the score matrix: inputs, minus the derived one."""
    return [d for d in descriptors
            if d.kind == "input" and d.truth_key != DERIVED_KEY]
