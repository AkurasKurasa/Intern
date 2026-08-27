import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHESS_DIR = os.path.join(_ROOT, "components", "chess")
for _p in (_ROOT, _CHESS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from board_features import encode_fen, similarity
from board_reader import STARTING_FEN_RANKS
import predictor

# Real FEN strings, captured earlier this session from the actual live
# Chess.com analysis board (not fabricated): the starting position, and
# the position after a real e2-e4 click was made on it.
AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
# A position sharing nothing with the starting position, for a
# genuinely-dissimilar test case.
EMPTY_BOARD_FEN = "8/8/8/8/8/8/8/8"


def _example(fen_before, from_sq, to_sq, piece_type="p", color="w", move_number=1, source="game_1.jsonl"):
    return {
        "move_number": move_number, "from_square": from_sq, "to_square": to_sq,
        "color": color, "piece_type": piece_type, "fen_before": fen_before,
        "fen_after": AFTER_E4_FEN, "recorded_at": "2026-08-27T00:00:00Z",
        "_source_file": source,
    }


class TestEncodeFen:
    def test_starting_position_has_32_nonzero_squares(self):
        encoded = encode_fen(STARTING_FEN_RANKS)
        assert len(encoded) == 64
        assert sum(1 for code in encoded if code != 0) == 32

    def test_white_king_square_is_index_60(self):
        # FEN reads rank 8 -> rank 1, file a -> h, so e1 (white king) is
        # the 4th rank's... concretely: index 4 (e-file, 0-based) of the
        # LAST rank block (rank 1), i.e. index 56 + 4 = 60.
        encoded = encode_fen(STARTING_FEN_RANKS)
        assert encoded[60] == 6  # "K"


class TestSimilarity:
    def test_identical_positions_are_1(self):
        encoded = encode_fen(STARTING_FEN_RANKS)
        assert similarity(encoded, encoded) == 1.0

    def test_completely_different_positions_score_low(self):
        # The starting position already has 32 empty squares, so an
        # all-empty board ties on exactly those -- 32/64 = 0.5 is the
        # correct value here, not a bug; this checks it doesn't score
        # any higher than that coincidence explains.
        a = encode_fen(STARTING_FEN_RANKS)
        b = encode_fen(EMPTY_BOARD_FEN)
        assert similarity(a, b) == 0.5

    def test_one_square_different_out_of_64(self):
        a = encode_fen(STARTING_FEN_RANKS)
        b = encode_fen(AFTER_E4_FEN)  # exactly one pawn moved
        assert similarity(a, b) == 62 / 64


class TestLoadRecordedExamples:
    def test_reads_real_jsonl_files_from_disk(self, tmp_path):
        game_file = tmp_path / "game_1.jsonl"
        game_file.write_text(
            json.dumps(_example(STARTING_FEN_RANKS, "e2", "e4")) + "\n" +
            json.dumps(_example(AFTER_E4_FEN, "e7", "e5", color="b")) + "\n",
            encoding="utf-8",
        )

        examples = predictor.load_recorded_examples(tmp_path)

        assert len(examples) == 2
        assert examples[0]["from_square"] == "e2"
        assert examples[0]["_source_file"] == "game_1.jsonl"

    def test_returns_empty_list_when_no_games_recorded_yet(self, tmp_path):
        empty_dir = tmp_path / "no_games_here"
        assert predictor.load_recorded_examples(empty_dir) == []

    def test_skips_malformed_lines_gracefully(self, tmp_path):
        game_file = tmp_path / "game_1.jsonl"
        game_file.write_text(
            json.dumps({"not_a_move": True}) + "\n" +
            json.dumps(_example(STARTING_FEN_RANKS, "e2", "e4")) + "\n",
            encoding="utf-8",
        )
        examples = predictor.load_recorded_examples(tmp_path)
        assert len(examples) == 1


class TestPredictMove:
    def test_exact_position_match_returns_the_real_recorded_move(self):
        examples = [_example(STARTING_FEN_RANKS, "e2", "e4", move_number=7, source="game_3.jsonl")]

        result = predictor.predict_move(STARTING_FEN_RANKS, examples)

        assert result["from_square"] == "e2"
        assert result["to_square"] == "e4"
        assert result["similarity"] == 1.0
        assert result["based_on_file"] == "game_3.jsonl"
        assert result["based_on_move_number"] == 7

    def test_no_examples_at_all_returns_none(self):
        assert predictor.predict_move(STARTING_FEN_RANKS, []) is None

    def test_only_dissimilar_examples_returns_none_rather_than_guessing(self):
        # This is the load-bearing behavior: an unfamiliar position must
        # never produce a fabricated answer, only a real one or nothing.
        examples = [_example(EMPTY_BOARD_FEN, "a1", "a2")]
        assert predictor.predict_move(STARTING_FEN_RANKS, examples) is None

    def test_picks_the_closer_of_two_recorded_positions(self):
        near_exact = _example(AFTER_E4_FEN, "g1", "f3", move_number=2, source="closer.jsonl")
        far_off = _example(EMPTY_BOARD_FEN, "a1", "a8", move_number=99, source="farther.jsonl")

        result = predictor.predict_move(STARTING_FEN_RANKS, [far_off, near_exact])

        assert result["based_on_file"] == "closer.jsonl"
        assert result["to_square"] == "f3"

    def test_respects_a_custom_similarity_threshold(self):
        # The near-e4 example is 62/64 = 0.96875 similar to the starting
        # position -- clears a lower bar but not an unreasonably strict one.
        examples = [_example(AFTER_E4_FEN, "g1", "f3")]

        assert predictor.predict_move(STARTING_FEN_RANKS, examples, min_similarity=0.99) is None
        assert predictor.predict_move(STARTING_FEN_RANKS, examples, min_similarity=0.9) is not None
