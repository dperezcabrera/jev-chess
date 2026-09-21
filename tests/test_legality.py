import json
from random import Random

import chess
import pytest

from experiments.legality import DECOYS, KINDS, build_options, illegal_moves, interval, summarize
from experiments.option_order import POSITIONS_FILE


def pairs(board: chess.Board, kind: str) -> set[str]:
    return {
        f"{chess.square_name(origin)}-{chess.square_name(target)}" for _, origin, target in illegal_moves(board)[kind]
    }


def test_each_kind_of_illegal_move_is_recognised_on_the_starting_position():
    board = chess.Board()
    assert "a1-a3" in pairs(board, "blocked path")
    assert "a1-a2" in pairs(board, "own piece on target")
    assert "b1-b3" in pairs(board, "wrong geometry")
    assert "e7-e5" in pairs(board, "opponent piece")
    assert "e4-e6" in pairs(board, "empty origin")
    assert pairs(board, "leaves king in check") == set()


def test_a_pinned_piece_moving_away_is_the_subtle_kind():
    board = chess.Board("4r2k/8/8/8/8/8/4N3/4K3 w - - 0 1")
    assert "e2-c3" in pairs(board, "leaves king in check")
    assert "e2-c3" not in pairs(board, "wrong geometry")


def test_no_decoy_is_ever_a_legal_move_in_any_of_the_positions():
    rng = Random(3)
    kinds_seen = set()
    for position in json.loads(POSITIONS_FILE.read_text()):
        board = chess.Board(position["fen"])
        legal = {f"{chess.square_name(m.from_square)}-{chess.square_name(m.to_square)}" for m in board.legal_moves}
        criteria, legal_label, kinds = build_options(board, rng)
        assert len(criteria) == DECOYS + 1, position["name"]
        assert legal_label in legal and set(criteria) & legal == {legal_label}, position["name"]
        assert set(kinds) == set(criteria) - {legal_label}
        assert set(kinds.values()) <= set(KINDS)
        assert len(set(kinds.values())) >= 4, position["name"]
        kinds_seen |= set(kinds.values())
    assert kinds_seen == set(KINDS)


def test_the_legal_move_is_not_always_in_the_same_slot_and_draws_are_reproducible():
    board = chess.Board()
    slots = set()
    rng = Random(5)
    for _ in range(40):
        criteria, legal_label, _ = build_options(board, rng)
        slots.add(list(criteria).index(legal_label))
    assert len(slots) >= 8
    assert build_options(board, Random(9)) == build_options(board, Random(9))


def test_descriptions_give_no_tactical_hints():
    criteria, _, _ = build_options(chess.Board(), Random(1))
    for text in criteria.values():
        assert text.split()[1] == "from" and text.split()[3] == "to"
        assert not any(word in text.lower() for word in ("capture", "check", "mate", "legal"))


def trial(name, correct, kind="wrong geometry"):
    asked = ["a1-a2", "b1-c3"]
    return {
        "name": name,
        "asked": asked,
        "legal": "b1-c3",
        "correct": correct,
        "picked_kind": "legal" if correct else kind,
        "decoy_kinds": {"a1-a2": kind},
        "probabilities": {"b1-c3": 0.7 if correct else 0.2, "a1-a2": 0.3 if correct else 0.8},
    }


def test_summary_counts_hits_and_blames_the_right_kind():
    report = summarize([trial("p1", True), trial("p2", False), trial("p3", False, "blocked path"), trial("p4", True)])
    assert report["picked_the_legal_move"] == 0.5 and report["chance"] == 0.5
    assert report["wrong_picks_by_kind"] == {"wrong geometry": 1, "blocked path": 1}
    assert report["mean_probability_on_the_legal_move"] == pytest.approx(0.45)
    low, high = interval([trial("p1", True), trial("p2", True)], Random(1), resamples=50)
    assert (low, high) == (1.0, 1.0)
