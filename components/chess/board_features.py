"""
components/chess/board_features.py
======================================
Named board_features.py, not features.py -- components/scope2/features/
is already a real package, and this project imports bare module names
after a sys.path insert (no packages), so a same-named module here would
silently shadow or be shadowed by it depending on which test imports
first. (Caught this the same way as the recorder.py/game_recorder.py
collision: passed in isolation, failed only as part of the full suite.)

Encodes a FEN ranks string (as produced by board_reader.board_to_fen_ranks)
into a fixed-length numeric fingerprint, and measures how similar two
positions are by counting how many of the 64 squares agree. Deliberately
this simple -- no piece-value weighting, no positional heuristics, no
external chess knowledge baked in anywhere. The point is comparing a new
position to ones this specific user has actually faced, not evaluating
how "good" a position is by some general standard.
"""
from __future__ import annotations

from typing import Tuple

PIECE_CODE = {
    "P": 1, "N": 2, "B": 3, "R": 4, "Q": 5, "K": 6,
    "p": 7, "n": 8, "b": 9, "r": 10, "q": 11, "k": 12,
}


def encode_fen(fen_ranks: str) -> Tuple[int, ...]:
    """Turns "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR" into a 64-long
    tuple of piece codes (0 = empty), read in FEN's own order (rank 8
    down to rank 1, file a to h within each rank)."""
    squares = []
    for rank_str in fen_ranks.split("/"):
        for ch in rank_str:
            if ch.isdigit():
                squares.extend([0] * int(ch))
            else:
                squares.append(PIECE_CODE.get(ch, 0))
    return tuple(squares)


def similarity(a: Tuple[int, ...], b: Tuple[int, ...]) -> float:
    """Fraction of the 64 squares that hold the same thing (same piece,
    same color, or both empty) in both positions. 1.0 = identical
    position, 0.0 = nothing in common or malformed input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / len(a)
