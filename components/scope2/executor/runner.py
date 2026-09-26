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
import time
from collections import Counter
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

# The live decision HUD (executor/hud.js). Read once at import; injected only
# when show=True, so a measurement run never loads it and the numbers a
# headless run produces are unaffected by its existence. See hud.js' own header
# for why it is shadow-DOM'd rather than styled into the page.
# The in-page script is now ONLY a row highlighter. The decision panel itself
# moved to the Electron app's floating Agent window, so Scope #1 and Scope #2
# report through the same surface instead of each having its own -- direct
# request, for uniformity.
#
# What stayed in the page is the one thing that cannot live outside it:
# outlining the row being filled and scrolling it into view. That is not the
# panel, and losing it would make a 50-row run much harder to follow.
HUD_JS = (Path(__file__).parent / "hud.js").read_text(encoding="utf-8")


def _emit_hud(kind, **payload):
    """One machine-readable line for the floating Agent window.

    Same convention run_task.py's COUNTDOWN lines and agent.py's DECISION
    lines already use: print to stdout, which recorder_bridge.py is already
    pumping line by line to the app. No new IPC, no second transport.

    Presentation only -- wrapped whole, and any failure is discarded, because
    a HUD line must never break a run whose rows all filled and verified.
    """
    try:
        payload["kind"] = kind
        print("SCOPE2HUD " + json.dumps(payload))
        try:
            sys.stdout.flush()
        except OSError:
            # Windows, spawned with no console (the Electron Play button uses
            # windowsHide) -- the write already landed. Same guard
            # print_countdown() needed for the same reason.
            pass
    except Exception:  # noqa: BLE001 - never break a run over a display line
        pass


class Hud:
    """Drives the floating Agent window, and the in-page row highlight.

    Method names and call sites are unchanged from when this drove an in-page
    panel; only where the output goes has changed. `enabled` still gates
    everything, so a headless measurement run emits nothing and touches no
    page -- the property the equivalence test pins.
    """

    def __init__(self, page=None, enabled=False):
        self._page = page if enabled else None
        self._enabled = enabled

    @property
    def enabled(self):
        return self._enabled

    def _highlight(self, expression, *args):
        """Best-effort call into the page, for the row outline only."""
        if self._page is None:
            return
        try:
            self._page.evaluate(expression, *args)
        except Exception:  # noqa: BLE001 - presentation must not break a run
            pass

    def install(self, payload):
        if not self._enabled:
            return
        _emit_hud("init", **payload)
        if self._page is not None:
            try:
                self._page.evaluate(HUD_JS)
            except Exception:  # noqa: BLE001
                self._page = None

    def stage(self, text):
        if self._enabled:
            _emit_hud("stage", text=text)

    def row(self, index, student_id):
        if not self._enabled:
            return
        _emit_hud("row", index=index, student_id=str(student_id))
        self._highlight("i => window.__agentHUD.row(i)", index)

    def cell(self, label, value):
        if self._enabled:
            _emit_hud("cell", label=str(label), value=str(value))

    def row_done(self, ok):
        if self._enabled:
            _emit_hud("rowDone", ok=bool(ok))

    def finish(self, status, detail=""):
        if not self._enabled:
            return
        _emit_hud("finish", status=str(status), detail=str(detail))
        self._highlight("() => window.__agentHUD.release()")


def hud_payload(mapping, variant, dry_run, total_rows):
    """The Resolver's own output, reshaped for display.

    Reads only what the mapping already carries - no portal-specific and no
    task-specific knowledge - so every variant gets a HUD without this function
    knowing anything about any of them.
    """
    return {
        "variant": variant,
        "dry_run": dry_run,
        "total_rows": total_rows,
        "assignments": [
            {"source_header": a["source_header"], "target_label": a["target_label"],
             "score": a.get("score"), "margin": a.get("margin"),
             # which tier decided it -- source or llm. A hand-written mapping
             # names its columns itself, which is what "source" means.
             "via": a.get("via", "source")}
            for a in mapping.get("assignments", [])
        ],
        # Fields the LLM refused, with its reason, so the HUD can say why a
        # field was left empty.
        "abstained": [
            {"target_label": a.get("target_label"), "reason": a.get("reason", "")}
            for a in mapping.get("abstained", [])
        ],
        "derived_rules": mapping.get("derived_rules", []),
        "unmapped_fields": mapping.get("unmapped_fields", []),
    }


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

    def printed_column(self, header_label, values, contains=False, sample_rows=5):
        """printed_index, falling back to what the column PRINTS.

        The header name is a guess about this portal's wording: V2 calls
        Student ID "Learner Reference Number", and the run stopped before
        writing a thing. What survives relabelling is the content -- the
        column whose cells hold the sheet's own key values. Sampled over a
        few rows; a tie or no hit is no answer, and the run stops as before.
        """
        index = header_index(self.headers, header_label)
        if index is not None:
            return index
        wanted = {str(v).strip().casefold() for v in values if str(v).strip()}
        hits = Counter()
        for i in range(min(self.rows.count(), sample_rows)):
            cells = self.rows.nth(i).locator("td").all_inner_texts()
            for j, text in enumerate(cells):
                t = text.strip().casefold()
                if t and (any(w in t for w in wanted) if contains else t in wanted):
                    hits[j] += 1
        ranked = hits.most_common(2)
        if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            return ranked[0][0]
        return self.printed_index(header_label)   # raises with the page's headers

    def find_row(self, student_id, id_index):
        """As row_for, but also returns the row's position on the page.

        The position is what the HUD needs to highlight the row it is filling.
        Returning it from the scan that already walked the rows keeps that free:
        asking for it separately would repeat an inner_text() per row, which is
        the expensive part of this loop.
        """
        count = self.rows.count()
        for i in range(count):
            row = self.rows.nth(i)
            printed = row.locator("td").nth(id_index).inner_text().strip()
            if printed == student_id:
                return row, i
        return None, -1

    def row_for(self, student_id, id_index):
        """The portal row whose Student ID cell holds exactly this ID.

        Matching the identity cell rather than the row's whole text matters:
        substring matching over a filled row can collide with a value the
        executor itself just wrote.
        """
        return self.find_row(student_id, id_index)[0]

    def printed_text(self, row, index):
        return row.locator("td").nth(index).inner_text().strip()

    def control(self, row, label):
        descriptor = self.by_label[label]
        candidate = row.get_by_label(re.compile(re.escape(label)))
        if candidate.count() == 1:
            return candidate.first
        # V4: no accessible name. Fall back to the scanned column position.
        # column_index is the header's position among ALL the row's header
        # cells -- the select-all checkbox column included -- and every row
        # has exactly one cell per header, so it is the cell's position as
        # is. A "+ 1" here shifted every V4 write one column right (Course
        # into Year, Grade into Remarks), found by filling V4 end to end.
        cell = row.locator("td").nth(descriptor.column_index)
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

            id_index = sheet.printed_column(alignment["key_field"],
                                            df[alignment["key_column"]])
            name_index = sheet.printed_column(alignment["verify_field"],
                                              df[alignment["verify_column"]], contains=True)

            missing = [l for l in mapped_labels if l not in sheet.by_label]
            missing += [r["field"] for r in derived_rules if r["field"] not in sheet.by_label]
            if missing:
                raise SystemExit(f"{variant}: mapping targets not on the page: {missing}")

            controls_before = sheet.checkbox_states()

            # Presentation only, and only under --show: the HUD puts the
            # Resolver's decisions on the page it is deciding about. Disabled,
            # every call below is a no-op that never touches the page.
            hud = Hud(page, enabled=show)
            hud.install(hud_payload(mapping, variant, dry_run, len(df)))
            hud.stage("Filling rows")

            for position, record in df.iterrows():
                student_id = str(record[alignment["key_column"]]).strip()
                result = RowResult(row=int(position) + 1, student_id=student_id,
                                   status="filled")

                row, row_index = sheet.find_row(student_id, id_index)
                if row is None:
                    result.status = "failed"
                    result.reason = "no portal row prints this Student ID"
                    hud.row_done(False)
                    log.rows.append(result)
                    continue

                hud.row(row_index, student_id)

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
                    hud.row_done(False)
                    log.rows.append(result)
                    continue

                written = []
                try:
                    for label in mapped_labels:
                        value = sheet_value(record[column_for[label]])
                        if value == "":
                            continue
                        hud.cell(label, value)
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
                        hud.cell(rule["field"], outcome)
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

                hud.row_done(result.status == "filled")
                log.rows.append(result)

            controls_after = sheet.checkbox_states()
            if controls_before != controls_after:
                raise SystemExit("a control field changed state - aborting before commit")

            ok = [r for r in log.rows if r.status == "filled"]
            if dry_run:
                log.commit_status = f"dry run - {len(ok)} rows filled and verified, not saved"
            elif ok:
                hud.stage("Saving")
                page.click(SAVE_BUTTON)
                page.wait_for_timeout(200)
                log.committed = True
                log.commit_status = page.inner_text(STATUS_EL).strip()
            else:
                log.commit_status = "nothing verified, nothing saved"

            failures = len(log.rows) - len(ok)
            hud.finish("Done" if not failures else f"Done, {failures} failed",
                       log.commit_status)

            if capture_state:
                log.portal_state = page.evaluate(
                    "() => window.__portal ? window.__portal.records : []"
                )

            if show:
                print("\n  The filled portal is on screen - scroll through it.")
                print("  Press Enter here to close it...")
                try:
                    input()
                except EOFError:
                    # No real terminal attached to stdin -- e.g. launched
                    # as a subprocess by the Electron app's Play button
                    # (app/recorder_bridge.py spawns it with
                    # stdin=DEVNULL, same reason run_task.py's own
                    # countdown had to stop blocking on stdin once). Found
                    # live: input() raised EOFError instantly, and the
                    # finally-block close() right below slammed the
                    # browser shut about 2 seconds after it opened -- the
                    # user clicked into the window just after it had
                    # already closed and reported "it didn't fill," even
                    # though the fill and the Save click above both
                    # already succeeded.
                    #
                    # sys.stdin.isatty() looked like the obvious upfront
                    # check instead of try/except, but verified directly
                    # that it's unreliable here -- it reported True even
                    # under stdin=DEVNULL in this environment's subprocess
                    # chain, which would have silently reintroduced the
                    # exact same crash. Reacting to the real EOFError
                    # input() actually raises is the reliable signal.
                    #
                    # Skipping browser.close() alone isn't enough either --
                    # verified directly: exiting the enclosing
                    # `with sync_playwright() as p:` block (p.stop()) kills
                    # every browser it launched regardless of whether
                    # .close() was called explicitly. So this has to
                    # actually wait, inside that block, not just skip the
                    # close and return. Bounded (not forever) so an
                    # unattended run doesn't hold a browser + this whole
                    # process open indefinitely; pressing Stop in the
                    # Electron app (CTRL_BREAK to the whole process group)
                    # still tears it down cleanly before the timeout.
                    print("  (no interactive terminal -- leaving it open for 10 minutes,"
                          " or until Stop is pressed)")
                    time.sleep(600)
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
