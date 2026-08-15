"""Sheet Reader (3.4) - the execution-time view of a grade sheet.

No live Excel connection: pandas only. Emits columns, headers, inferred types
and per-column value samples, which is exactly what the Feature Extractor needs
for its value-shape features (3.6, features 9-12).

The template these sheets come from puts a merged institution/course block above
the data, so the column headers are on row 12, not row 1. 3.1 anticipates this
("assume row 1 is the header, with a one-time user override at session start");
`header_row` is that override, and `find_header_row` proposes it.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_SHEET = "SUMMARY"
SAMPLE_SIZE = 5  # 3.4: "take 5 non-null values per column for shape features"

# Column headers pandas invents for unnamed columns.
UNNAMED = re.compile(r"^Unnamed:\s*\d+$")

TYPE_PATTERNS = [
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("id", re.compile(r"^\d{2,4}-\d{3,6}(-\d+)?$")),
    ("phone", re.compile(r"^\+?\d[\d\s\-()]{6,}$")),
]


@dataclass
class SourceColumn:
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


def infer_type(series):
    """Column-level type, per 3.1: infer over the whole column, not one cell."""
    values = series.dropna()
    if values.empty:
        return "text"

    if pd.api.types.is_numeric_dtype(values):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(values):
        return "date"

    strings = [str(v).strip() for v in values]
    for name, pattern in TYPE_PATTERNS:
        if all(pattern.match(s) for s in strings):
            return name

    coerced = pd.to_numeric(pd.Series(strings), errors="coerce")
    if coerced.notna().all():
        return "numeric"
    return "text"


def find_header_row(path, sheet_name=DEFAULT_SHEET, max_scan=30):
    """Propose the header row: the first row where most cells are non-empty
    strings and the row below it is not. Returns a 0-based index for pandas."""
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=max_scan)
    best, best_score = 0, -1
    for i in range(len(raw)):
        row = raw.iloc[i]
        labels = [v for v in row if isinstance(v, str) and v.strip()]
        score = len(labels)
        if score > best_score:
            best, best_score = i, score
    return best


def read_sheet(path, sheet_name=DEFAULT_SHEET, header_row=None, key_column=None):
    """Load a grade sheet into (DataFrame, [SourceColumn]).

    `header_row` is 0-based. When omitted it is proposed by find_header_row,
    which is the 3.1 one-time override surfaced rather than guessed silently.
    """
    path = Path(path)
    if header_row is None:
        header_row = find_header_row(path, sheet_name)

    df = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
    df = df.dropna(how="all")

    # Trailing footer rows: keep only rows that carry the key column, which is
    # the first column whose values look like identifiers if not given.
    if key_column is None:
        key_column = df.columns[1] if len(df.columns) > 1 else df.columns[0]
    df = df[df[key_column].notna()].reset_index(drop=True)

    columns = []
    for index, name in enumerate(df.columns):
        header = str(name).strip()
        if UNNAMED.match(header):
            # A merged header spanning several columns leaves the continuation
            # columns unnamed. They still carry data (LASTNAME/FIRSTNAME/MI),
            # so keep them and let the matcher abstain rather than dropping
            # them silently.
            header = ""
        series = df[name]
        samples = [v for v in series.dropna().tolist()[:SAMPLE_SIZE]]
        columns.append(
            SourceColumn(
                header=header,
                index=index,
                inferred_type=infer_type(series),
                samples=samples,
                non_null=int(series.notna().sum()),
                total=int(len(series)),
            )
        )

    return df, columns


def describe(path, sheet_name=DEFAULT_SHEET, header_row=None):
    df, columns = read_sheet(path, sheet_name, header_row)
    print(f"{Path(path).name} [{sheet_name}] - {len(df)} rows, {len(columns)} columns")
    print(f"  {'#':<3} {'header':<20} {'type':<9} {'non-null':<9} samples")
    print("  " + "-" * 78)
    for c in columns:
        label = c.header if c.header else "(unnamed)"
        sample = ", ".join(str(s) for s in c.samples[:3])
        print(f"  {c.index:<3} {label[:19]:<20} {c.inferred_type:<9} "
              f"{c.non_null:<9} {sample[:36]}")
    return df, columns


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?",
                    default=str(REPO / "data" / "sheets" / "grade_sheet.xlsx"))
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--header-row", type=int, default=None)
    args = ap.parse_args()
    describe(args.path, args.sheet, args.header_row)
