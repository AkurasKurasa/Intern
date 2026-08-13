"""
components/scope2/descriptors.py
=================================
Minimal, dependency-free data shapes the ported matching core (features/,
model/, resolver/, labeling/) operates on -- extracted from the coworker's
repo (RJGanzon/Intern, executor/scanner.py's FieldDescriptor and
executor/sheet_reader.py's SourceColumn) without pulling in their pandas/
playwright-dependent reading code, which isn't needed to test or use the
matching core on its own.

Kept deliberately separate from executor.scanner/executor.sheet_reader
(not ported yet) so this module has zero heavy dependencies -- anything
that can build a SourceColumn/FieldDescriptor (our own DataSource records,
their pandas sheet reader, a future browser scanner, ...) can feed the
same matching core without needing playwright or pandas installed.
"""

from dataclasses import dataclass, field
from typing import List, Optional

KIND_INPUT = "input"
KIND_CONTROL = "control"


@dataclass
class SourceColumn:
    """One column of a data source (a spreadsheet column, or -- for our
    own Scope #1 use -- one field of a demonstrated record)."""
    header: str
    index: int
    inferred_type: str
    samples: list = field(default_factory=list)
    non_null: int = 0
    total: int = 0

    @property
    def completeness(self):
        return self.non_null / self.total if self.total else 0.0

    @property
    def distinct_values(self):
        return sorted({str(v) for v in self.samples})


@dataclass
class FieldDescriptor:
    """One target field to fill -- a form control, or (for our own
    Scope #1 use) one UI element from an observed state."""
    label: str
    label_rule: int
    kind: str
    input_type: str
    column_key: str
    column_index: Optional[int] = None
    header_text: str = ""
    name: str = ""
    placeholder: str = ""   # feature 3 reads this; it is also cascade rule 4
    required: bool = False
    min: Optional[str] = None
    max: Optional[str] = None
    step: Optional[str] = None
    options: Optional[List[str]] = None
    maxlength: Optional[int] = None
    dom_order: int = 0
    control_count: int = 1
    row_labels_agree: bool = True
    truth_key: Optional[str] = None
