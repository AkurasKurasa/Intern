"""Executor (3.10) - fill a portal from a sheet, verify, then commit.

Adapted to a sheet-style portal, where the differences from 3.10 as written are
these and only these:

  * There is no per-row submit button. The row transaction still holds: a row is
    filled and every assignment verified before anything is committed, and a row
    that fails verification has its cells cleared so the page's own save cannot
    pick it up. Commit is one click at the end, over the rows that survived.
  * Student ID is printed, not an input, so it aligns rows instead of being
    filled, and Student Name verifies the alignment. A row whose printed name
    disagrees with the sheet is a misalignment and is failed before any write.

Everything else is 3.10 as specified: dependency order, rules evaluated against
the value read back off the form, semantic location, resolved select options,
readback verification, untouched control fields, dry-run, append-only log.

Usage:
    python executor/runner.py --dry-run
    python executor/runner.py --commit --variant v0_base
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from executor.scanner import (  # noqa: E402
    CHROMIUM, KIND_INPUT, extract_contexts, group_columns, header_columns,
    header_index, variant_url,
)
from executor.sheet_reader import read_sheet  # noqa: E402

RUNS_DIR = REPO / "data" / "runs"

# Portal chrome that is not a data field.
SAVE_BUTTON = "#submit-btn"
STATUS_EL = "#form-status"


# ---------------------------------------------------------------- outcomes


@dataclass
class RowResult:
    row: int
    student_id: str
    status: str                      # filled | failed | skipped
    reason: str = ""
    filled: dict = field(default_factory=dict)
    verified: dict = field(default_factory=dict)
    escalations: list = field(default_factory=list)


@dataclass
class RunLog:
    variant: str
    mapping: str
    started: str
    dry_run: bool
    rows: list = field(default_factory=list)
    committed: bool = False
    commit_status: str = ""
    # The portal's own committed records, read from the instrument's evaluation
    # hook (window.__portal). This is deliberately not part of the executor's
    # normal contract - a real portal exposes no such hook - but it turns the
    # run log into evidence that can be checked without trusting the readback
    # the executor performed on itself.
    portal_state: list = field(default_factory=list)

    def write(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["rows"] = [asdict(r) if not isinstance(r, dict) else r
                           for r in self.rows]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path


# ---------------------------------------------------------------- helpers


def normalize_value(value):
    """Compare what we meant to write with what the field now holds.

    Numbers must compare numerically: writing 85.0 and reading back '85' is a
    match, and treating it as a mismatch would fail every numeric field.
    """
    if value is None:
        return ""
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text.casefold()
    return f"{number:g}"


def sheet_value(raw):
    """A cell as the portal should receive it: no trailing .0 on whole numbers."""
    if raw is None:
        return ""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


def resolve_option(intended, options):
    """3.8 step 4: the rule's outcome must become an option that exists.

    Exact, then case-insensitive, then prefix. Anything less certain returns
    None so the caller escalates rather than picking the nearest string.
    """
    if not options:
        return None
    if intended in options:
        return intended
    folded = {o.casefold(): o for o in options}
    if intended.casefold() in folded:
        return folded[intended.casefold()]
    matches = [o for o in options if o.casefold().startswith(intended.casefold())]
    return matches[0] if len(matches) == 1 else None


def apply_rule(rule, driver_value):
    """Evaluate a threshold rule against a value read off the form."""
    try:
        number = float(driver_value)
    except (TypeError, ValueError):
        return None
    operator = rule["operator"]
    cutoff = float(rule["cutoff"])
    if operator == ">=":
        hit = number >= cutoff
    elif operator == "<=":
        hit = number <= cutoff
    elif operator == ">":
        hit = number > cutoff
    elif operator == "<":
        hit = number < cutoff
    else:
        raise ValueError(f"unsupported operator {operator!r}")
    return rule["if_true"] if hit else rule["if_false"]


# ---------------------------------------------------------------- locating


class PortalSheet:
    """Locating cells on a sheet portal, semantically.

    Primary route is the control's accessible name, which on this portal is the
    column header plus the row's student. Where a variant strips that (V4), the
    fallback is the column index the scanner derived from the header text on
    this page load. Neither is a selector recorded at demonstration time, which
    is the property 3.10 actually requires.
    """

    def __init__(self, page, descriptors, headers):
        self.page = page
        self.by_label = {d.label: d for d in descriptors}
        self.headers = headers
        self.rows = page.locator("#records-body tr")

    def printed_index(self, header_label):
        """Cell position of a printed (non-input) column, found by its header
        text on this page load. A reordered variant moves the column and this
        moves with it; nothing here is carried over from a demonstration."""
        index = header_index(self.headers, header_label)
        if index is None:
            raise SystemExit(
                f"no column header matches {header_label!r}; "
                f"page has {[h['text'] for h in self.headers]}"
            )
        return index

    def row_for(self, student_id, id_index):
        """The portal row whose Student ID cell holds exactly this ID.

        Matching the identity cell rather than the row's whole text matters:
        substring matching over a filled row can collide with a value the
        executor itself just wrote.
        """
        count = self.rows.count()
        for i in range(count):
            row = self.rows.nth(i)
            printed = row.locator("td").nth(id_index).inner_text().strip()
            if printed == student_id:
                return row
        return None

    def printed_text(self, row, index):
        return row.locator("td").nth(index).inner_text().strip()

    def control(self, row, label):
        descriptor = self.by_label[label]
        candidate = row.get_by_label(re.compile(re.escape(label)))
        if candidate.count() == 1:
            return candidate.first
        # V4: no accessible name. Fall back to the scanned column position.
        cell = row.locator("td").nth(descriptor.column_index + 1)
        return cell.locator("input, select, textarea").first

    def fill(self, row, label, value):
        descriptor = self.by_label[label]
        control = self.control(row, label)
        if descriptor.input_type == "select":
            option = resolve_option(value, descriptor.options or [])
            if option is None:
                return None
            control.select_option(option)
            return option
        control.fill(str(value))
        return value

    def read(self, row, label):
        return self.control(row, label).input_value()

    def clear(self, row, labels):
        for label in labels:
            descriptor = self.by_label[label]
            control = self.control(row, label)
            if descriptor.input_type == "select":
                control.select_option("")
            else:
                control.fill("")

    def checkbox_states(self):
        return self.page.eval_on_selector_all(
            "#records-body input[type=checkbox]", "els => els.map(e => e.checked)"
        )


# ---------------------------------------------------------------- the run


def relative_to_repo(path):
    """Repo-relative for readability, absolute when it is somewhere else.

    A mapping does not have to live in the repo - a test fixture writes one to
    a temp directory - and the run log is not worth crashing over.
    """
    path = Path(path)
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def load_mapping(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def fill_order(mapping):
    """Mapped fields first, then derived fields in dependency order (3.10).

    A derived field is written only after the field it reads, because the rule
    is evaluated against the live value rather than the spreadsheet.
    """
    mapped = [a["target_label"] for a in mapping["assignments"]]
    derived = list(mapping.get("derived_rules", []))

    ordered, remaining = [], derived[:]
    available = set(mapped)
    while remaining:
        progressed = False
        for rule in remaining[:]:
            if rule["depends_on_field"] in available:
                ordered.append(rule)
                available.add(rule["field"])
                remaining.remove(rule)
                progressed = True
        if not progressed:
            unresolved = [r["field"] for r in remaining]
            raise ValueError(f"derived fields have unmet dependencies: {unresolved}")
    return mapped, ordered


def run(variant, mapping_path, dry_run=True, base_url=None, limit=None,
        capture_state=True, show=False):
    from playwright.sync_api import sync_playwright

    mapping = load_mapping(mapping_path)
    mapped_labels, derived_rules = fill_order(mapping)
    alignment = mapping["row_alignment"]

    sheet_cfg = mapping["sheet"]
    df, _ = read_sheet(
        REPO / sheet_cfg["path"],
        sheet_cfg["sheet_name"],
        sheet_cfg.get("header_row"),
        sheet_cfg.get("key_column"),
    )
    if limit:
        df = df.head(limit)

    column_for = {a["target_label"]: a["source_header"] for a in mapping["assignments"]}

    log = RunLog(
        variant=variant,
        mapping=relative_to_repo(mapping_path),
        started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dry_run=dry_run,
    )

    with sync_playwright() as p:
        # `show` is for demonstrating the result to a person: the portal has no
        # backend, so once the browser closes the filled sheet is gone. Holding
        # the window open is the only way to look at it.
        browser = p.chromium.launch(
            executable_path=str(CHROMIUM) if CHROMIUM.exists() else None,
            headless=not show,
        )
        page = browser.new_page(no_viewport=True) if show else browser.new_page()
        try:
            url = variant_url(variant, base_url)
            descriptors = group_columns(extract_contexts(page, url))
            inputs = [d for d in descriptors if d.kind == KIND_INPUT]
            sheet = PortalSheet(page, inputs, header_columns(page))

            id_index = sheet.printed_index(alignment["key_field"])
            name_index = sheet.printed_index(alignment["verify_field"])

            missing = [l for l in mapped_labels if l not in sheet.by_label]
            missing += [r["field"] for r in derived_rules if r["field"] not in sheet.by_label]
            if missing:
                raise SystemExit(f"{variant}: mapping targets not on the page: {missing}")

            controls_before = sheet.checkbox_states()

            for position, record in df.iterrows():
                student_id = str(record[alignment["key_column"]]).strip()
                result = RowResult(row=int(position) + 1, student_id=student_id,
                                   status="filled")

                row = sheet.row_for(student_id, id_index)
                if row is None:
                    result.status = "failed"
                    result.reason = "no portal row prints this Student ID"
                    log.rows.append(result)
                    continue

                # Alignment check before any write: 3.10's readback logic applied
                # to identity. A name mismatch means we found the wrong row.
                expected_name = str(record.get(alignment["verify_column"], "")).strip()
                printed = sheet.printed_text(row, name_index)
                if expected_name and expected_name.casefold() not in printed.casefold():
                    result.status = "failed"
                    result.reason = (
                        f"row alignment: sheet says {expected_name!r}, "
                        f"portal row prints {printed!r}"
                    )
                    log.rows.append(result)
                    continue

                written = []
                try:
                    for label in mapped_labels:
                        value = sheet_value(record[column_for[label]])
                        if value == "":
                            continue
                        sheet.fill(row, label, value)
                        result.filled[label] = value
                        written.append(label)

                    # 3.10: evaluate the rule against the filled value, not the
                    # spreadsheet, so a reformatted or clamped entry is caught.
                    for rule in derived_rules:
                        driver = sheet.read(row, rule["depends_on_field"])
                        outcome = apply_rule(rule, driver)
                        if outcome is None:
                            raise RuntimeError(
                                f"rule for {rule['field']!r} could not read "
                                f"{rule['depends_on_field']!r} (got {driver!r})"
                            )
                        chosen = sheet.fill(row, rule["field"], outcome)
                        if chosen is None:
                            result.escalations.append(
                                f"no option on {rule['field']!r} resolves {outcome!r}"
                            )
                            raise RuntimeError(result.escalations[-1])
                        result.filled[rule["field"]] = chosen
                        written.append(rule["field"])

                    for label, intended in result.filled.items():
                        actual = sheet.read(row, label)
                        if normalize_value(actual) != normalize_value(intended):
                            raise RuntimeError(
                                f"readback {label!r}: wrote {intended!r}, "
                                f"field holds {actual!r}"
                            )
                        result.verified[label] = actual

                except RuntimeError as exc:
                    result.status = "failed"
                    result.reason = str(exc)
                    # Do not leave a half-filled row where the page's own save
                    # could commit it.
                    sheet.clear(row, written)
                    result.filled, result.verified = {}, {}

                log.rows.append(result)

            controls_after = sheet.checkbox_states()
            if controls_before != controls_after:
                raise SystemExit("a control field changed state - aborting before commit")

            ok = [r for r in log.rows if r.status == "filled"]
            if dry_run:
                log.commit_status = f"dry run - {len(ok)} rows filled and verified, not saved"
            elif ok:
                page.click(SAVE_BUTTON)
                page.wait_for_timeout(200)
                log.committed = True
                log.commit_status = page.inner_text(STATUS_EL).strip()
            else:
                log.commit_status = "nothing verified, nothing saved"

            if capture_state:
                log.portal_state = page.evaluate(
                    "() => window.__portal ? window.__portal.records : []"
                )

            if show:
                print("\n  The filled portal is on screen - scroll through it.")
                print("  Press Enter here to close it...")
                input()
        finally:
            browser.close()

    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="v0_base")
    ap.add_argument("--mapping", default=str(REPO / "data" / "mappings" / "v0_handwritten.json"))
    ap.add_argument("--commit", action="store_true",
                    help="actually save; default is a dry run (3.10)")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args()

    log = run(args.variant, args.mapping, dry_run=not args.commit,
              base_url=args.base_url, limit=args.limit)

    filled = sum(1 for r in log.rows if r.status == "filled")
    failed = [r for r in log.rows if r.status == "failed"]

    print(f"\n{log.variant} <- {log.mapping}")
    print(f"  {filled} rows filled and verified, {len(failed)} failed")
    for r in failed[:10]:
        print(f"    row {r.row} ({r.student_id}): {r.reason}")
    print(f"  {log.commit_status}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(args.log) if args.log else (RUNS_DIR / f"{log.variant}_{stamp}.json")
    if not path.is_absolute():
        path = REPO / path
    written = log.write(path)
    print(f"  log: {written.relative_to(REPO)}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
