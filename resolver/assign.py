"""Resolver (3.9) - scores become a mapping, or an admission of doubt.

Order matters more than anything else here. Fields are partitioned *before* any
assignment runs, because Hungarian enforces one-to-one: leave a derived field in
the matrix and it competes for a source column, and to give it one the solver
may displace a correct mapping elsewhere. Partition first, assign second.

Abstention is an output, not an error path. A pair is accepted only when it
scores well *and* wins clearly; a high score with a close runner-up means the
system cannot tell two columns apart, which is worth saying out loud.
"""

import sys
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from scipy.optimize import linear_sum_assignment
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 3.9's starting thresholds. Tau is tuned on a validation set and the
# precision/recall tradeoff reported; delta is the clarity requirement.
TAU = 0.6
DELTA = 0.15
EPSILON = 1e-9

STATUS_AUTO = "auto"
STATUS_ABSTAIN = "abstain"

BUCKET_MAPPABLE = "mappable"
BUCKET_DERIVED = "derived"
BUCKET_CONTROL = "control"
BUCKET_UNMAPPED = "unmapped"


@dataclass
class Assignment:
    source_header: str
    target_label: str
    score: float
    margin: float
    status: str

    def to_dict(self):
        return {
            "source_header": self.source_header,
            "target_label": self.target_label,
            "score": round(self.score, 4),
            "margin": round(self.margin, 4),
            "status": self.status,
        }


@dataclass
class Mapping:
    """2.5, plus the partition that produced it."""
    assignments: list = dataclass_field(default_factory=list)
    unmapped_fields: list = dataclass_field(default_factory=list)
    unmapped_columns: list = dataclass_field(default_factory=list)
    partition: dict = dataclass_field(default_factory=dict)

    @property
    def auto(self):
        return [a for a in self.assignments if a.status == STATUS_AUTO]

    @property
    def abstained(self):
        return [a for a in self.assignments if a.status == STATUS_ABSTAIN]

    def to_dict(self):
        return {
            "assignments": [a.to_dict() for a in self.assignments],
            "unmapped_fields": self.unmapped_fields,
            "unmapped_columns": self.unmapped_columns,
            "partition": self.partition,
        }

    def as_truth(self):
        """The accepted header -> label mapping, for comparison against ground
        truth. Abstentions are deliberately absent: they are not claims."""
        return {a.target_label: a.source_header for a in self.auto}


def partition(fields, derived_labels=(), control_kinds=("control",)):
    """3.9 step 0. Every field lands in exactly one bucket.

    `unmapped` cannot be decided here - a field is unmapped because it won no
    column, which is only known after assignment - so it is filled in later.
    """
    buckets = {BUCKET_MAPPABLE: [], BUCKET_DERIVED: [], BUCKET_CONTROL: []}
    derived_labels = set(derived_labels)

    for field in fields:
        if field.kind in control_kinds:
            buckets[BUCKET_CONTROL].append(field)
        elif field.label in derived_labels:
            buckets[BUCKET_DERIVED].append(field)
        else:
            buckets[BUCKET_MAPPABLE].append(field)
    return buckets


def margins(matrix):
    """Per column: how far the best field beat the second best.

    The margin is computed over the raw scores, not over the assignment, so it
    measures whether the *model* could tell the candidates apart - not whether
    the solver happened to have a free slot.
    """
    out = []
    for row in matrix:
        if len(row) < 2:
            out.append(1.0 if row else 0.0)
            continue
        ordered = sorted(row, reverse=True)
        out.append(ordered[0] - ordered[1])
    return out


def resolve(columns, fields, matrix, derived_labels=(), tau=TAU, delta=DELTA):
    """Assign columns to mappable fields one-to-one, abstaining when unclear.

    `matrix[i][j]` is the score for columns[i] against fields[j], over *all*
    fields; the mappable subset is selected here so callers do not have to
    pre-filter and risk misaligning the indices.
    """
    buckets = partition(fields, derived_labels)
    mappable = buckets[BUCKET_MAPPABLE]
    mappable_indices = [fields.index(f) for f in mappable]

    mapping = Mapping(partition={
        BUCKET_MAPPABLE: [f.label for f in mappable],
        BUCKET_DERIVED: [f.label for f in buckets[BUCKET_DERIVED]],
        BUCKET_CONTROL: [f.label for f in buckets[BUCKET_CONTROL]],
    })

    if not mappable or not columns:
        mapping.unmapped_fields = [f.label for f in mappable]
        mapping.unmapped_columns = [c.header for c in columns]
        return mapping

    scores = np.array([[matrix[ci][fi] for fi in mappable_indices]
                       for ci in range(len(columns))], dtype=float)

    # Hungarian minimises, so cost is -log(score): a probability near 1 costs
    # nearly nothing, and a near-zero probability costs a great deal.
    cost = -np.log(scores + EPSILON)

    # Pad so unequal counts are handled and surplus fields simply go unmapped.
    rows, cols = cost.shape
    size = max(rows, cols)
    padded = np.full((size, size), -np.log(EPSILON))
    padded[:rows, :cols] = cost

    row_index, col_index = linear_sum_assignment(padded)
    column_margins = margins(scores.tolist())

    assigned_fields = set()
    for ci, fj in zip(row_index, col_index):
        if ci >= rows or fj >= cols:
            continue  # padding, not a real pair

        score = float(scores[ci][fj])
        margin = float(column_margins[ci])
        status = STATUS_AUTO if (score >= tau and margin >= delta) else STATUS_ABSTAIN

        mapping.assignments.append(Assignment(
            source_header=columns[ci].header,
            target_label=mappable[fj].label,
            score=score,
            margin=margin,
            status=status,
        ))
        if status == STATUS_AUTO:
            assigned_fields.add(mappable[fj].label)

    mapping.assignments.sort(key=lambda a: (-a.score, a.target_label))
    mapping.unmapped_fields = [f.label for f in mappable
                               if f.label not in assigned_fields]

    claimed = {a.source_header for a in mapping.auto}
    mapping.unmapped_columns = [c.header for c in columns if c.header not in claimed]
    return mapping


def render(mapping):
    lines = [f"{len(mapping.auto)} assigned, {len(mapping.abstained)} abstained"]
    for assignment in mapping.assignments:
        marker = " " if assignment.status == STATUS_AUTO else "?"
        lines.append(
            f" {marker} {assignment.source_header:<18} -> "
            f"{assignment.target_label:<26} "
            f"score {assignment.score:.3f}  margin {assignment.margin:.3f}"
        )
    if mapping.unmapped_fields:
        lines.append(f"   unmapped fields:  {', '.join(mapping.unmapped_fields)}")
    if mapping.partition[BUCKET_DERIVED]:
        lines.append(f"   derived (held out): {', '.join(mapping.partition[BUCKET_DERIVED])}")
    if mapping.partition[BUCKET_CONTROL]:
        lines.append(f"   controls:          {', '.join(mapping.partition[BUCKET_CONTROL])}")
    return "\n".join(lines)
