"""
components/chess/predictor.py
=================================
Predicts a move for a new board position by finding the most similar
position in the user's own recorded games and returning what they
actually played there. This is the entire mechanism -- no chess engine,
no LLM, no pretrained model of any kind, and no network call anywhere in
this file. Every prediction is a real recorded move from a real game
this specific person played, traceable back to which file and which
move number it came from. If nothing recorded is similar enough, this
says so instead of inventing an answer.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from board_features import encode_fen, similarity  # noqa: E402

DEFAULT_MIN_SIMILARITY = 0.85
DEFAULT_GAMES_DIR = Path(_THIS_DIR) / "data" / "games"


def load_recorded_examples(games_dir=DEFAULT_GAMES_DIR) -> List[dict]:
    """Reads every recorded move from every .jsonl file under games_dir
    -- the exact files game_recorder.py writes while a human plays.
    Nothing here reads from anywhere else."""
    examples: List[dict] = []
    games_dir = Path(games_dir)
    if not games_dir.exists():
        return examples
    for path in sorted(games_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if "fen_before" in entry and "from_square" in entry:
                entry = dict(entry)
                entry["_source_file"] = path.name
                examples.append(entry)
    return examples


def predict_move(fen_before: str, examples: List[dict],
                  min_similarity: float = DEFAULT_MIN_SIMILARITY) -> Optional[Dict]:
    """Finds the recorded example whose position is most similar to
    fen_before and returns what the user actually played there. Returns
    None if there are no recorded examples, or the closest one isn't
    similar enough to count as a real prediction rather than a guess --
    this never falls back to computing a move any other way."""
    if not examples:
        return None

    target = encode_fen(fen_before)
    best_example = None
    best_score = -1.0
    for example in examples:
        score = similarity(target, encode_fen(example["fen_before"]))
        if score > best_score:
            best_score = score
            best_example = example

    if best_example is None or best_score < min_similarity:
        return None

    return {
        "from_square": best_example["from_square"],
        "to_square": best_example["to_square"],
        "piece_type": best_example["piece_type"],
        "color": best_example["color"],
        "similarity": round(best_score, 4),
        "based_on_file": best_example.get("_source_file", "?"),
        "based_on_move_number": best_example.get("move_number"),
    }
