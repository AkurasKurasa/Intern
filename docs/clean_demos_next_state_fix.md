# Fix: `clean_demos.py` was dropping every click

**Status:** Fixed 2026-08-19, branch `tasktree-fixes`.
**File changed:** [`scripts/clean_demos.py`](../scripts/clean_demos.py)
**Tests:** [`tests/test_clean_demos_next_state.py`](../tests/test_clean_demos_next_state.py) (4 new, all passing)

## Summary for groupmates

`clean_demos.py` is the step between recording a demo and training on it — it
strips out noise (accidental clicks, dropdown-selection clutter) so only real
navigation actions survive. **It had a bug that made it throw away every
click** on any recording made after roughly May 2026 — which is all of our
current data. Only typed text survived; the part of the training signal that
teaches the model *where to click* was silently gone. If you cleaned data
with this script recently, your cleaned output has zero click actions in it —
worth re-running once this fix lands.

No behavior change for old recordings (pre-May 2026) — they already worked
and still do.

## Root cause

Each recorded step is a JSON file (`live_step_NNNN.json`). To figure out
*what element a click landed on*, the script needs to know what the screen
looked like right after the click — call it "the next state."

Old recordings stored that directly, as a `next_state` field on the step
itself. That field was deliberately removed from the recording format later
(it was redundant — it's always identical to the *next file's* `state`, and
training code never read it). Nobody updated `clean_demos.py` to match.

The old code:

```python
ns = t.get("next_state", {})
```

On a current-format file, `"next_state"` doesn't exist, so `ns` silently
became `{}` — not an error, just an empty dict. Every downstream lookup
against `ns` (`elem_at(ns, click_position)`) then had nothing to search, so
it returned `None` for every single click, every time. The script's own
logic (correctly!) treats "couldn't resolve a target element" as junk and
drops the step. That's why it *looked* like it was working — no crash, no
warning, just quietly wrong output.

## The fix

Read `next_state` from wherever it actually lives now, in priority order:

1. If the step still has a literal `next_state` field (old-format
   recordings) — use it directly, unchanged behavior.
2. Otherwise, since the file list is already sorted, load the *next* file in
   the same session and use its `state` field.
3. If this is the last file in the session (no next file exists), fall back
   to the step's own `state` — the best available approximation, since
   there's nothing later to read.

```python
if "next_state" in t:
    ns = t.get("next_state", {})
elif i + 1 < len(files):
    ns = json.load(open(files[i + 1], encoding="utf-8")).get("state", {})
else:
    ns = st   # last step in the session — no next file to read
```

This works for both old- and new-format recordings without a flag or a
migration step — the script just checks what's actually in front of it.

## How it was verified

No live GUI run needed — this is a pure data-transformation bug, testable
against static JSON. Four new tests in
`tests/test_clean_demos_next_state.py`:

- a current-format click (no `next_state` key) now survives cleaning
- an old-format click (real `next_state` key) still works, unchanged
- the "next file's state" is actually what resolves the click — not just a
  silent fallback to the current file's own state
- the last file in a session (no next file to read) doesn't crash and still
  resolves via its own state

All 4 pass against the fixed code; 3 of the 4 fail against the pre-fix code
(confirmed by reasoning through the old logic — the bug is exactly what it
looks like).
