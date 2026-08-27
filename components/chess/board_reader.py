"""
components/chess/board_reader.py
====================================
Reads the real board state directly off Chess.com's page structure --
verified against the public /analysis board (no login, no real game):
every piece is its own DOM element, class
"piece {color}{type} square-{file}{rank}" (e.g. "piece wp square-52" is
white pawn on e2, "piece bk square-58" is black king on e8). No image
recognition needed -- this is a DOM read, the same class of thing this
project already does for Inbox Dispatch (automate_inbox.py) and Scope #2
(executor/scanner.py).

square-XY: X is file 1-8 (a-h), Y is rank 1-8, both taken straight from
the class name -- confirmed against a real starting position where white
rooks sat at square-81 (h1) and square-11 (a1), king at square-51 (e1).
"""
from __future__ import annotations

from typing import Dict, Tuple

Square = str  # e.g. "e2"
Piece = Tuple[str, str]  # (color "w"|"b", piece_type "p"|"n"|"b"|"r"|"q"|"k")

STARTING_FEN_RANKS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


def _parse_piece_class(class_name: str):
    """Returns (square, (color, piece_type)) for one ".piece" element's
    className, or None if the class doesn't match the expected shape."""
    parts = class_name.split()
    piece_code = next((p for p in parts if len(p) == 2 and p[0] in ("w", "b") and p[1].isalpha()), None)
    square_code = next((p for p in parts if p.startswith("square-") and len(p) == 9), None)
    if not piece_code or not square_code:
        return None
    color, piece_type = piece_code[0], piece_code[1]
    file_digit, rank_digit = square_code[7], square_code[8]
    if not (file_digit.isdigit() and rank_digit.isdigit()):
        return None
    file_num = int(file_digit)
    if not (1 <= file_num <= 8):
        return None
    square = f"{chr(ord('a') + file_num - 1)}{rank_digit}"
    return square, (color, piece_type)


def board_from_piece_classes(class_names) -> Dict[Square, Piece]:
    """Pure function over a list of ".piece" element classNames -- kept
    separate from read_board() so this is testable without a real page."""
    board: Dict[Square, Piece] = {}
    for cls in class_names:
        parsed = _parse_piece_class(cls)
        if parsed is not None:
            square, piece = parsed
            board[square] = piece
    return board


def read_board(page) -> Dict[Square, Piece]:
    """Reads the live board state off a real Playwright page pointed at
    a Chess.com board (analysis or a real game)."""
    class_names = page.eval_on_selector_all(".piece", "els => els.map(e => e.className)")
    return board_from_piece_classes(class_names)


def board_to_fen_ranks(board: Dict[Square, Piece]) -> str:
    """Converts {square: (color, type)} into FEN's piece-placement field
    (the 8 ranks; does not include turn/castling/en-passant/clocks)."""
    ranks = []
    for rank in range(8, 0, -1):
        row = ""
        empty = 0
        for file_letter in "abcdefgh":
            square = f"{file_letter}{rank}"
            if square in board:
                if empty:
                    row += str(empty)
                    empty = 0
                color, piece_type = board[square]
                letter = piece_type.upper() if color == "w" else piece_type.lower()
                row += letter
            else:
                empty += 1
        if empty:
            row += str(empty)
        ranks.append(row)
    return "/".join(ranks)


def diff_boards(before: Dict[Square, Piece], after: Dict[Square, Piece]):
    """Infers the single move that turned `before` into `after`. Returns
    (from_square, to_square, piece), or None if it doesn't look like
    exactly one move (multiple squares changed in a way a single move
    can't explain, or nothing changed at all).

    `emptied` is a square that had a piece and now has none at all --
    strict absence, not just "a different piece is there now". That
    distinction matters for a capture: the captured square still holds a
    piece afterward (the capturing piece), so it must never be treated as
    emptied, only as occupied-by-someone-new. Getting this wrong (using
    "value differs, including via .get()" for both checks) makes a
    capture's own destination square look like a second emptied square,
    breaking the 1-emptied/1-occupied case a plain move relies on."""
    emptied = [sq for sq in before if sq not in after]
    occupied = [sq for sq, piece in after.items() if before.get(sq) != piece]

    if len(emptied) == 1 and len(occupied) == 1:
        from_sq, to_sq = emptied[0], occupied[0]
        return from_sq, to_sq, after[to_sq]

    # Castling: two emptied squares (king + rook origins), two occupied.
    if len(emptied) == 2 and len(occupied) == 2:
        king_from = next((sq for sq in emptied if before[sq][1] == "k"), None)
        king_to = next((sq for sq in occupied if after[sq][1] == "k"), None)
        if king_from is not None and king_to is not None:
            return king_from, king_to, after[king_to]

    return None
