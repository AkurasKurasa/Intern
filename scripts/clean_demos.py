"""
clean_demos.py — produce clean NAVIGATION training data from raw recordings.

Removes the noise that corrupts the recorded field order:
  1. Dropdown-SELECTION clicks — a click made while a combobox dropdown is open
     (listitems present in the state) lands on the option, which visually sits
     OVER a lower field, so it gets mis-logged as a click on that field
     (e.g. a phantom "Expiration Date" between Type and Term). These are value
     selection, not navigation — dropped.
  2. Non-form-window clicks (terminal, recorder GUI, Notepad).
  3. Clicks on panes / non-interactive chrome.
  4. Consecutive duplicate clicks on the same field (combobox open + reopen).

Typing frames are KEPT — they fill the form, which is the navigation signal.

Usage:
    python scripts/clean_demos.py <src_dir> <dst_dir>
    python scripts/clean_demos.py data/demos/policy_nav data/demos/policy_clean
"""
from __future__ import annotations
import sys, os, glob, json, shutil

FIELD = {"editcontrol", "input", "comboboxcontrol", "combobox",
         "checkboxcontrol", "checkbox", "buttoncontrol", "button"}


def elem_at(state, pos, role="active"):
    if not state or not pos:
        return None
    px, py = pos
    best, ba = None, 1e18
    for e in state.get("elements", []):
        if role and e.get("window_role") != role:
            continue
        b = e.get("bbox")
        if not b or len(b) != 4:
            continue
        if b[0] <= px <= b[2] and b[1] <= py <= b[3]:
            a = (b[2] - b[0]) * (b[3] - b[1])
            if a < ba:
                best, ba = e, a
    if best is None and role == "active":
        return elem_at(state, pos, None)
    return best


def is_form(st):
    t = (st.get("window_title") or "").lower()
    a = (st.get("application") or "").lower()
    return "insurance" in t or "insurance" in a or "data entry" in t


def n_listitems(st):
    return sum(1 for e in st.get("elements", [])
               if "listitem" in (e.get("type") or "").lower())


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/clean_demos.py <src_dir> <dst_dir>")
        return
    src, dst = sys.argv[1], sys.argv[2]
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    kept = drop_sel = drop_junk = drop_dupe = 0
    for sess in sorted(glob.glob(os.path.join(src, "session_*"))):
        files = sorted(glob.glob(os.path.join(sess, "live_step_*.json")))
        out = os.path.join(dst, os.path.basename(sess))
        os.makedirs(out, exist_ok=True)
        oi = 0
        prev_lbl = None
        for f in files:
            t = json.load(open(f, encoding="utf-8"))
            st = t.get("state", {})
            ns = t.get("next_state", {})
            m = t.get("mouse", {}).get("actions", [])
            k = t.get("keyboard", {}).get("actions", [])

            if m:
                # must be on the form window
                if not is_form(ns) and not is_form(st):
                    drop_junk += 1
                    continue
                # DROPDOWN SELECTION: dropdown was open when this click happened →
                # it's a value-pick, not navigation. THE KEY FIX.
                if n_listitems(st) > 0:
                    drop_sel += 1
                    continue
                tgt = elem_at(ns, m[0].get("position")) or {}
                ty = (tgt.get("type") or "").lower()
                lbl = tgt.get("label") or tgt.get("text") or ""
                if ty not in FIELD:
                    drop_junk += 1
                    continue
                if lbl and lbl == prev_lbl:   # combobox open + reopen on same field
                    drop_dupe += 1
                    continue
                prev_lbl = lbl
            elif k:
                pass   # keep typing (fills the form)
            else:
                drop_junk += 1
                continue

            json.dump(t, open(os.path.join(out, f"live_step_{oi:04d}.json"),
                              "w", encoding="utf-8"), ensure_ascii=False)
            oi += 1
            kept += 1

    print(f"kept {kept}  |  dropped: dropdown-select={drop_sel}, "
          f"junk={drop_junk}, dupes={drop_dupe}  ->  {dst}")


if __name__ == "__main__":
    main()
