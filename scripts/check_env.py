"""Check that everything requirements.txt declares is actually importable.

    python scripts/check_env.py            # human-readable report
    python scripts/check_env.py --json     # machine-readable (the Electron app)
    python scripts/check_env.py --only wx  # just these import names

WHY THIS EXISTS
---------------
Two dependencies were declared in requirements.txt and simply never installed
on a working machine, and in both cases the failure was silent rather than
loud:

  * `uiautomation` -- components/agent/executor.py soft-imports it inside a
    try/except, so the module loaded fine and 29 tests failed later with
    "no attribute '_uia'", which reads like a code bug rather than a missing
    package.
  * `wxPython` -- the Electron app's "Launch Test Tools" button spawns the wx
    form with `stdio: "ignore"` and `detached: true`, so the ImportError went
    nowhere and the button reported success while nothing opened.

Neither was a bug in the code. Both were an environment that had never been
fully installed from requirements.txt, and neither said so. This script is the
one place that answers "is this machine actually set up?" out loud.

requirements.txt is the single source of truth -- the list is parsed from it
rather than duplicated here, so a dependency added there is checked here
automatically. Only the pip-name -> import-name mapping lives below, because
that mapping genuinely cannot be derived (pip installs `opencv-python`, you
import `cv2`).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "requirements.txt"

# pip distribution name -> the name you actually import. Only entries that
# differ need to be here; anything not listed is assumed to import under its
# own name, lowercased with dashes turned into underscores.
IMPORT_NAMES = {
    "pywin32": "win32gui",
    "wxPython": "wx",
    "opencv-python": "cv2",
    "Pillow": "PIL",
    "sentence-transformers": "sentence_transformers",
    "google-api-python-client": "googleapiclient",
    "google-auth-oauthlib": "google_auth_oauthlib",
    "google-auth-httplib2": "google_auth_httplib2",
    "google-genai": "google.genai",
}

# Which scope stops working when a given import is missing. Purely so the
# report can say what breaks rather than only what is absent.
USED_BY = {
    "wx": "Scope #1 - the car insurance form the agent types into",
    "win32gui": "Scope #1 - window location and foreground detection",
    "uiautomation": "Scope #1 - cursor-free clicks and caret placement",
    "pyautogui": "Scope #1 - the real cursor fallback",
    "pynput": "Scope #1 - demonstration recording",
    "playwright": "Scope #2 and #3 - both drive a real browser",
    "pandas": "Scope #2 - reading the grade sheet",
    "openpyxl": "Scope #2 - building and verifying the grade sheet",
    "xlwings": "Scope #2 - watching a live Excel demonstration",
    "torch": "the trained models (BC transformer, Scope #2 matcher)",
    "sentence_transformers": "Scope #2 - the semantic features",
    "cv2": "perception - the CV/OCR vision backend",
    "pytesseract": "perception - OCR text extraction",
    "openai": "the LLM half (LM Studio and Groq both use this client)",
}

# Not pip-installable; requirements.txt says so in its own header.
SYSTEM_BINARIES = {
    "tesseract": (
        "OCR perception backend",
        "winget install --id UB-Mannheim.TesseractOCR",
    ),
}


def declared_packages(requirements: Path = REQUIREMENTS) -> list[str]:
    """Distribution names from requirements.txt, comments and pins stripped."""
    names = []
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;]", line, 1)[0].strip()
        if name:
            names.append(name)
    return names


def import_name(package: str) -> str:
    return IMPORT_NAMES.get(package, package.lower().replace("-", "_"))


def is_importable(module: str) -> bool:
    """Whether `import <module>` would succeed, without paying to run it.

    find_spec does not execute the module, which matters here: importing torch
    or wx costs seconds, and this check runs on an app button press.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        # A namespace parent that is itself missing (google.genai without
        # google) raises rather than returning None.
        return False


def check(only: list[str] | None = None) -> dict:
    missing, present = [], []
    for package in declared_packages():
        module = import_name(package)
        if only and module not in only:
            continue
        entry = {"package": package, "module": module, "used_by": USED_BY.get(module, "")}
        (present if is_importable(module) else missing).append(entry)

    binaries = []
    if not only:
        for binary, (purpose, install) in SYSTEM_BINARIES.items():
            if shutil.which(binary) is None:
                binaries.append({"binary": binary, "purpose": purpose, "install": install})

    return {
        "ok": not missing,
        "missing": missing,
        "present": present,
        "missing_binaries": binaries,
        "fix": "python -m pip install -r requirements.txt",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--only", nargs="*", metavar="MODULE",
                    help="check only these import names")
    args = ap.parse_args()

    result = check(only=args.only or None)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if result["ok"]:
        print(f"All {len(result['present'])} declared dependencies are installed.")
    else:
        print(f"{len(result['missing'])} declared dependencies are NOT installed:\n")
        width = max(len(m["package"]) for m in result["missing"])
        for m in result["missing"]:
            print(f"  {m['package']:<{width}}  (import {m['module']})")
            if m["used_by"]:
                print(f"  {'':<{width}}  breaks: {m['used_by']}")
        print(f"\n  Fix:  {result['fix']}")

    for b in result["missing_binaries"]:
        print(f"\n  Note: '{b['binary']}' is not on PATH - {b['purpose']}.")
        print(f"        It is not pip-installable:  {b['install']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
