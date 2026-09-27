"""
components/chess/game_recorder.py
=====================================
Scope #3's Record stage for chess: opens a real, visible browser to a
Chess.com game, and while a human plays it normally (real clicks, real
opponent, nothing automated here), watches the board and logs every
move -- board state before, the move made, and the FEN after -- to a
JSONL file. Train reads this file later; this script's only job is
capturing real demonstrations, the same role run_task.py's recorder
plays for Scope #1.

Named game_recorder.py, not recorder.py -- components/recorder/recorder.py
already exists as an unrelated module, and this project imports bare
module names after a sys.path insert rather than qualified package
paths, so a same-named file here would silently shadow (or be shadowed
by) that one depending on import order.

Never sends any input of its own. It only reads the page.

    python game_recorder.py                                  # opens chess.com/play
    python game_recorder.py --url https://www.chess.com/game/live/12345678
    python game_recorder.py --out data/games/my_game.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from board_reader import read_board, diff_boards, board_to_fen_ranks  # noqa: E402

DEFAULT_URL = "https://www.chess.com/play/online"
DEFAULT_OUT_DIR = REPO / "data" / "games"


def move_to_str(move) -> str:
    from_sq, to_sq, (color, piece_type) = move
    return f"{color}{piece_type} {from_sq}{to_sq}"


def record_session(page, out_path: Path, poll_interval: float = 1.0,
                    max_moves: int = None) -> int:
    """Watches `page`'s board and appends one JSON line per detected move
    to out_path. Runs until the page is closed, or until max_moves have
    been recorded (used by tests -- a real recording session leaves this
    None and relies on the human closing the browser). Returns the number
    of moves recorded."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    previous = read_board(page)
    move_count = 0

    print(f"Recording to {out_path}")
    print("Play normally in the browser window -- this only watches.")
    print("Close the browser window when the game is over.\n")

    with open(out_path, "a", encoding="utf-8") as f:
        while max_moves is None or move_count < max_moves:
            try:
                page.wait_for_timeout(int(poll_interval * 1000))
                current = read_board(page)
            except Exception:
                # The page/browser was closed -- that's the normal way
                # this loop ends, not an error to surface.
                break

            move = diff_boards(previous, current)
            if move is not None:
                move_count += 1
                entry = {
                    "move_number": move_count,
                    "from_square": move[0], "to_square": move[1],
                    "color": move[2][0], "piece_type": move[2][1],
                    "fen_before": board_to_fen_ranks(previous),
                    "fen_after": board_to_fen_ranks(current),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                f.write(json.dumps(entry) + "\n")
                f.flush()
                print(f"  {move_count:>3}. {move_to_str(move)}")

            previous = current

    return move_count


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL,
                    help="the Chess.com page to watch (default: the Play lobby -- "
                         "navigate to your actual game from there)")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to append recorded moves (default: "
                         "components/chess/data/games/<timestamp>.jsonl)")
    ap.add_argument("--poll", type=float, default=1.0,
                    help="seconds between board reads (default: 1.0)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or (DEFAULT_OUT_DIR / f"game_{stamp}.jsonl")
    if not out_path.is_absolute():
        out_path = REPO / out_path

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(args.url)

        count = record_session(page, out_path, poll_interval=args.poll)
        browser.close()

    print(f"\nRecorded {count} moves to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
