"""Demonstration event contracts (2.1, 2.2) and their on-disk form.

Sessions are append-only JSONL under data/demos/. Both recorders write to the
same file; `source` tells them apart and the Reconciler joins them.

One deliberate departure from 2.2 as written. That contract shows a resolved
`label` inside the browser event, but 3.5 forbids the browser from deciding a
label at all - the cascade must have one implementation. So the extension emits
raw DOM context under `field.context`, and `label`/`label_rule` are filled in by
labeling.resolve when the session is *read*. The contract's shape is honoured at
the boundary; the decision still happens in exactly one place.
"""

import json
import sys
from dataclasses import dataclass, field as dataclass_field, asdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from labeling.resolve import common_label, resolve  # noqa: E402

DEMOS_DIR = REPO / "data" / "demos"

SOURCE_EXCEL = "excel"
SOURCE_BROWSER = "browser"

# 2.2: how the value arrived. `paste` reconciles to an Excel cell; a `select`
# with no reconciling Excel event is the evidence a field is derived.
TRIGGERS = ("paste", "type", "select", "autofill")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class ExcelEvent:
    """2.1 - a cell selection, with the column it belongs to."""
    t: str
    sheet: str
    cell: str
    column_index: int
    header: str
    value: str
    number_format: str = ""
    inferred_type: str = "text"
    source: str = SOURCE_EXCEL


@dataclass
class BrowserEvent:
    """2.2 - a value landing in a control, with raw context for the cascade.

    `seq` is the fill order within the row (3.2): the rule inducer needs to know
    Grade was filled before Remarks, since a field can only derive from one
    already filled.
    """
    t: str
    url: str
    value: str
    trigger: str
    row: int
    seq: int
    context: dict = dataclass_field(default_factory=dict)
    source: str = SOURCE_BROWSER
    # Set by assign_column_labels over a whole session. See `label`.
    column_label: str = ""

    @property
    def resolved(self):
        return resolve(self.context)

    @property
    def label(self):
        """The field this write names.

        On a sheet portal one control's accessible name covers its column *and*
        its row - "Grade 0-100 Abad, Andrea A." - so the per-event resolution is
        not a field identity. `column_label`, assigned across the session, is;
        without it the Reconciler would see fifty fields where there are five.
        """
        return self.column_label or self.resolved.label

    @property
    def label_rule(self):
        return self.resolved.rule

    @property
    def options(self):
        return self.context.get("options")

    @property
    def input_type(self):
        return self.context.get("input_type", "")

    @property
    def is_closed_option_field(self):
        """3.3 requires a derived candidate to be a closed-option field."""
        return self.input_type == "select" and bool(self.options)

    def as_contract(self):
        """The 2.2 field descriptor, with the label resolved on read."""
        resolution = self.resolved
        return {
            "t": self.t,
            "source": self.source,
            "url": self.url,
            "field": {
                "label": resolution.label,
                "label_rule": resolution.rule,
                "name": self.context.get("name"),
                "id": self.context.get("id"),
                "input_type": self.context.get("input_type"),
                "placeholder": self.context.get("placeholder"),
                "required": self.context.get("required"),
                "maxlength": self.context.get("maxlength"),
                "aria_label": self.context.get("aria"),
                "options": self.options,
                "dom_order": self.context.get("dom_order"),
            },
            "value": self.value,
            "trigger": self.trigger,
        }


def write_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(event)) + "\n")


def write_session(path, events):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event)) + "\n")
    return path


def assign_column_labels(browser_events):
    """Give every event the label of its column, not of its cell.

    Groups by the column key the recorder emitted and takes the invariant part
    of the per-event labels, which is exactly what the Page Scanner does for
    live pages. One cascade, one notion of a field label, both sides.
    """
    groups = {}
    for event in browser_events:
        key = event.context.get("column_key") or event.context.get("id") or "field"
        groups.setdefault(key, []).append(event)

    for events in groups.values():
        shared = common_label([e.resolved.label for e in events])
        for event in events:
            event.column_label = shared
    return browser_events


def read_session(path):
    """Load a session into (excel_events, browser_events), each time-sorted."""
    excel, browser = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        if raw.get("source") == SOURCE_EXCEL:
            excel.append(ExcelEvent(**raw))
        elif raw.get("source") == SOURCE_BROWSER:
            browser.append(BrowserEvent(**raw))
        else:
            raise ValueError(f"unknown event source: {raw.get('source')!r}")

    excel.sort(key=lambda e: parse_time(e.t))
    browser.sort(key=lambda e: (parse_time(e.t), e.seq))
    assign_column_labels(browser)
    return excel, browser
