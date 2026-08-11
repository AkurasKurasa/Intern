"""Feature Extractor (3.6) - THE shared module.

One `(source column, target field)` candidate becomes a fixed-length vector in
[0,1]. This same code runs at training time over demonstrated pairs and at
execution time over live candidates. That is the load-bearing rule of the whole
architecture: nothing in here may read a demonstration-only signal (the observed
paste target, event timing, the portal's data-key ground truth), because a
feature that exists only during training is a train/inference skew that no
accuracy number will reveal.

The vector is 17-dimensional. Note that 3.6 heads its own table "v1, 16 dims"
and then lists seventeen rows, and 3.7 declares Linear(16->32); the feature list
is treated as authoritative here and the discrepancy is flagged rather than
silently resolved. FEATURE_NAMES is the order of record, and VERSION must be
bumped whenever it changes - a trained matcher is invalid across a feature
change, and model artifacts record this string.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from features import encoders  # noqa: E402
from labeling.resolve import de_snake_case  # noqa: E402

VERSION = "extractor-v1-17d"

FEATURE_NAMES = [
    "sem_header_label",       # 1
    "sem_header_name",        # 2
    "sem_header_placeholder",  # 3
    "sem_max",                # 4
    "lex_levenshtein",        # 5
    "lex_jaccard",            # 6
    "lex_containment",        # 7
    "lex_abbreviation",       # 8
    "val_type_match",         # 9
    "val_regex_family",       # 10
    "val_constraints",        # 11
    "val_length_fit",         # 12
    "str_type_compat",        # 13
    "str_required_fit",       # 14
    "str_already_filled",     # 15
    "pos_rank_distance",      # 16
    "opt_option_overlap",     # 17
]

DIMS = len(FEATURE_NAMES)

# Feature 13: how plausible is this column type in this control (3.6).
TYPE_COMPATIBILITY = {
    ("numeric", "number"): 1.0,
    ("numeric", "text"): 0.6,
    ("numeric", "select"): 0.3,
    ("numeric", "textarea"): 0.2,
    ("text", "text"): 1.0,
    ("text", "textarea"): 0.9,
    ("text", "select"): 0.6,
    ("text", "number"): 0.0,
    ("id", "text"): 1.0,
    ("id", "textarea"): 0.5,
    ("id", "number"): 0.2,
    ("id", "select"): 0.1,
    ("date", "date"): 1.0,
    ("date", "text"): 0.7,
    ("date", "number"): 0.1,
    ("email", "email"): 1.0,
    ("email", "text"): 0.8,
    ("phone", "tel"): 1.0,
    ("phone", "text"): 0.8,
}
DEFAULT_COMPATIBILITY = 0.4

NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
ID_RE = re.compile(r"^\d{2,4}-\d{3,6}(-\d+)?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

REGEX_FAMILY = {
    "number": NUMERIC_RE,
    "email": EMAIL_RE,
    "tel": re.compile(r"^\+?\d[\d\s\-()]{6,}$"),
}


# --------------------------------------------------------------- helpers


def tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def levenshtein_ratio(a, b):
    a, b = (a or "").lower(), (b or "").lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def containment(a, b):
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return 0.0
    return 1.0 if a in b or b in a else 0.0


def is_abbreviation(short, long):
    """`DOB` for `date of birth`, `Yr Level` for `Year Level`."""
    short_tokens, long_tokens = tokens(short), tokens(long)
    if not short_tokens or not long_tokens:
        return 0.0

    # Initialism: one token whose letters are the initials of the other side.
    if len(short_tokens) == 1 and len(long_tokens) > 1:
        initials = "".join(t[0] for t in long_tokens)
        if short_tokens[0] == initials:
            return 1.0

    # Token-wise contraction: every short token abbreviates its matching long
    # token, and at least one is a genuine shortening rather than a repeat.
    # Prefix matching alone is not enough - the sheet header this feature exists
    # for is "Yr Level", and "year" does not start with "yr".
    if len(short_tokens) == len(long_tokens):
        pairs = list(zip(short_tokens, long_tokens))
        if all(_token_abbreviates(s, l) or _token_abbreviates(l, s)
               for s, l in pairs) and any(s != l for s, l in pairs):
            return 1.0
    return 0.0


def _token_abbreviates(short, long):
    """`yr` abbreviates `year`, `sec` abbreviates `section`, `grade` does not
    abbreviate `course`. Same initial letter plus subsequence containment -
    tight enough that unrelated tokens do not collide."""
    if short == long:
        return True
    if not short or not long or len(short) >= len(long):
        return False
    if short[0] != long[0]:
        return False
    remaining = iter(long)
    return all(character in remaining for character in short)


def as_number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- context


@dataclass
class Candidate:
    """Everything a feature may read about one (column, field) pair.

    `filled_fields` is the set of target labels already written *this row*, so
    feature 15 reflects live fill state at execution time and demonstrated fill
    order at training time. It is a property of the row, not of the demo.
    """
    column: object          # executor.sheet_reader.SourceColumn
    field: object           # executor.scanner.FieldDescriptor
    n_columns: int
    n_fields: int
    filled_fields: frozenset = frozenset()


# -------------------------------------------------------------- features


def semantic(candidate, require_encoder=True):
    """Features 1-4. Raises when the encoder is missing rather than zeroing."""
    header = candidate.column.header
    field = candidate.field

    if not encoders.available():
        if require_encoder:
            raise encoders.EncoderUnavailable(
                "features 1-4 need sentence-transformers; pass "
                "require_encoder=False only to inspect the other 13"
            )
        return [0.0, 0.0, 0.0, 0.0]

    f1 = encoders.similarity(header, field.label)
    f2 = encoders.similarity(header, de_snake_case(field.name))
    f3 = encoders.similarity(header, field.placeholder)
    return [f1, f2, f3, max(f1, f2, f3)]


def lexical(candidate):
    """Features 5-8."""
    header = candidate.column.header
    label = candidate.field.label
    return [
        levenshtein_ratio(header, label),
        jaccard(tokens(header), tokens(label)),
        containment(header, label),
        is_abbreviation(header, label),
    ]


def value_shape(candidate):
    """Features 9-12."""
    column, field = candidate.column, candidate.field
    samples = [str(s).strip() for s in column.samples if str(s).strip()]

    f9 = 1.0 if TYPE_COMPATIBILITY.get(
        (column.inferred_type, field.input_type), 0.0) == 1.0 else 0.0

    pattern = REGEX_FAMILY.get(field.input_type)
    if pattern is None or not samples:
        f10 = 0.0
    else:
        f10 = sum(1 for s in samples if pattern.match(s)) / len(samples)

    low, high = as_number(field.min), as_number(field.max)
    if not samples or (low is None and high is None):
        f11 = 0.0
    else:
        satisfied = 0
        for s in samples:
            n = as_number(s)
            if n is None:
                continue
            if (low is None or n >= low) and (high is None or n <= high):
                satisfied += 1
        f11 = satisfied / len(samples)

    if not samples or not field.maxlength:
        f12 = 0.0
    else:
        mean_length = sum(len(s) for s in samples) / len(samples)
        f12 = max(0.0, min(1.0, mean_length / field.maxlength))

    return [f9, f10, f11, f12]


def structural(candidate):
    """Features 13-15."""
    column, field = candidate.column, candidate.field

    f13 = TYPE_COMPATIBILITY.get(
        (column.inferred_type, field.input_type), DEFAULT_COMPATIBILITY)

    # A required field wants a complete column; an optional one is indifferent.
    f14 = column.completeness if field.required else 0.5

    f15 = 1.0 if field.label in candidate.filled_fields else 0.0
    return [f13, f14, f15]


def positional(candidate):
    """Feature 16 - deliberately included and deliberately suspect (3.6).

    Helps on the base UI and hurts on the reordered one. The ablation in 7 is
    the point; keep it computable so it can be switched off and measured.
    """
    if candidate.n_columns <= 1 or candidate.n_fields <= 1:
        return [1.0]
    column_rank = candidate.column.index / (candidate.n_columns - 1)
    field_rank = candidate.field.dom_order / (candidate.n_fields - 1)
    return [1.0 - abs(column_rank - field_rank)]


def option_overlap(candidate):
    """Feature 17 - what lets a select be matched at all (3.6).

    A sheet column of PASSED/FAILED overlaps a Remarks option list exactly and
    maps directly; no overlap anywhere is corroborating evidence the select is
    derived rather than copied.
    """
    field = candidate.field
    if field.input_type != "select" or not field.options:
        return [0.0]
    values = {str(v).strip().casefold() for v in candidate.column.samples
              if str(v).strip()}
    options = {str(o).strip().casefold() for o in field.options}
    return [jaccard(values, options)]


def extract(candidate, require_encoder=True):
    """The full vector, in FEATURE_NAMES order."""
    vector = (
        semantic(candidate, require_encoder)
        + lexical(candidate)
        + value_shape(candidate)
        + structural(candidate)
        + positional(candidate)
        + option_overlap(candidate)
    )
    if len(vector) != DIMS:
        raise AssertionError(f"expected {DIMS} features, built {len(vector)}")
    return [max(0.0, min(1.0, float(v))) for v in vector]


def extract_named(candidate, require_encoder=True):
    return dict(zip(FEATURE_NAMES, extract(candidate, require_encoder)))


def candidates(columns, fields, filled_fields=frozenset()):
    """Every (column, field) pair - the full candidate grid."""
    return [
        Candidate(column=c, field=f, n_columns=len(columns),
                  n_fields=len(fields), filled_fields=filled_fields)
        for c in columns for f in fields
    ]
