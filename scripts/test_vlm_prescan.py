"""
scripts/test_vlm_prescan.py
============================
Smoke test for VisualDataReader.pre_scan record-aware bucketing.

Runs ONLY the VLM pre-scan against the open Notepad, then prints how many
records were detected and how many fields each got.

Usage
-----
1. Open Notepad with data_entry_tasks/data_entry_intake.txt
2. From repo root:
       python scripts/test_vlm_prescan.py
3. Read the log lines:
   "VisualDataReader: scan complete — N total field(s) cached across M record(s)"
   followed by per-record breakdown:
   "  record 1: X fields", "  record 2: Y fields", ...
"""

from __future__ import annotations

import ctypes
try:
    ctypes.windll.ole32.CoInitialize(None)
except Exception:
    pass

import logging
import os
import sys

_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMP_DIR = os.path.join(_ROOT, "components")
for _p in (_ROOT, _COMP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load .env
_env_path = os.path.join(_ROOT, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("test_vlm_prescan")

SOURCE_WINDOW = "data_entry_intake"   # title fragment of the open Notepad

if __name__ == "__main__":
    groq_key   = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not (groq_key or gemini_key):
        log.error("No GROQ_API_KEY or GEMINI_API_KEY in .env — cannot test VLM")
        sys.exit(1)

    from observers.vlm.visual_data_reader import VisualDataReader

    if groq_key:
        log.info("Backend: Groq (llama-4-scout)")
        reader = VisualDataReader(api_key=gemini_key, groq_api_key=groq_key)
    else:
        log.info("Backend: Gemini")
        reader = VisualDataReader(api_key=gemini_key)

    log.info("Pre-scanning %r ...", SOURCE_WINDOW)
    flat = reader.pre_scan(SOURCE_WINDOW)
    rec_cache = reader.get_record_cache()

    print("\n" + "=" * 60)
    print("PRE-SCAN RESULTS")
    print("=" * 60)
    print(f"Flat cache total fields:   {len(flat)}")
    print(f"Records bucketed:          {len(rec_cache)}")
    for rn in sorted(rec_cache):
        fields = rec_cache[rn]
        sample = list(fields.items())[:3]
        sample_s = ", ".join(f"{k}={v!r}" for k, v in sample)
        print(f"  record {rn:>2}: {len(fields):>3} fields   sample: {sample_s}")
    print("=" * 60)

    if len(rec_cache) <= 1:
        print("\nVERDICT: pre_scan did NOT bucket multiple records.")
        print("  - either Notepad scroll bug still present (mouse not over Notepad)")
        print("  - or VLM did not detect 'RECORD N OF M' headers")
        print("  - or the file only contains 1 record")
        sys.exit(2)
    print("\nVERDICT: pre_scan record-aware bucketing is working.")
