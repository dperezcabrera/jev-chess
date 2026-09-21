import json
from random import Random

import chess
import pytest

from experiments.option_order.experiment import (
    POSITIONS_FILE,
    agreement,
    distance,
    interval,
    mean_pairwise_distance,
    pair_agreement,
    position_effect,
    rotations,
    slot_bonus,
    top_two_gap,
)


def test_the_position_set_is_large_distinct_and_legal():
    positions = json.loads(POSITIONS_FILE.read_text())
    assert len(positions) == 120
    assert len({" ".join(position["fen"].split()[:4]) for position in positions}) == 120
    for position in positions:
        board = chess.Board(position["fen"])
        assert board.is_valid(), position["name"]
        assert 3 <= board.legal_moves.count() <= 255, position["name"]


def test_agreement_is_the_share_of_the_most_common_answer():
    assert agreement(["e4", "e4", "e4", "e4"]) == 1.0
    assert agreement(["e4", "d4", "e4", "c4"]) == 0.5


def test_pair_agreement_does_not_grow_with_the_number_of_runs():
    assert pair_agreement(["e4"] * 6) == 1.0
    assert pair_agreement(["e4", "d4"]) == 0.0
    assert pair_agreement(["e4", "d4"] * 3) == pytest.approx(pair_agreement(["e4", "d4"] * 15), abs=0.1)
    assert agreement(["e4", "e4", "d4"]) > pair_agreement(["e4", "e4", "d4"])
    assert top_two_gap({"e4": 0.5, "d4": 0.3, "c4": 0.2}) == pytest.approx(0.2)
    assert top_two_gap({"e4": 1.0}) == 1.0


def test_distance_is_zero_for_equal_distributions_and_one_for_disjoint_ones():
    assert distance({"e4": 0.7, "d4": 0.3}, {"e4": 0.7, "d4": 0.3}) == 0
    assert distance({"e4": 1.0}, {"d4": 1.0}) == 1
    assert distance({"e4": 0.7, "d4": 0.3}, {"e4": 0.5, "d4": 0.5}) == pytest.approx(0.2)
    assert mean_pairwise_distance([{"e4": 1.0}]) == 0.0


def test_every_rotation_is_a_full_order_with_a_different_first_move():
    board = chess.Board()
    orders = rotations(board, 30, Random(1))
    legal = sorted(board.legal_moves, key=str)
    assert all(sorted(order, key=str) == legal for order in orders)
    firsts = [order[0] for order in orders]
    assert len(set(firsts[:20])) == 20, "with 20 legal moves the first 20 rotations each start differently"
    assert set(firsts) == set(legal)
    assert rotations(board, 30, Random(1)) == orders


def run(asked, probabilities):
    return {"asked": asked, "probabilities": probabilities}


def test_no_effect_when_probabilities_ignore_the_order():
    fixed = {"a": 0.6, "b": 0.3, "c": 0.1}
    runs = [run(["a", "b", "c"], fixed), run(["c", "b", "a"], fixed), run(["b", "a", "c"], fixed)]
    assert position_effect([runs]) == pytest.approx(0)
    assert slot_bonus([runs], 0) == pytest.approx(0)
    assert slot_bonus([runs], -1) == pytest.approx(0)


def test_a_model_that_favours_the_first_option_is_caught():
    def biased(order):
        return run(order, {san: (0.6 if index == 0 else 0.2) for index, san in enumerate(order)})

    runs = [biased(["a", "b", "c"]), biased(["b", "c", "a"]), biased(["c", "a", "b"])]
    assert slot_bonus([runs], 0) == pytest.approx(0.4)
    assert slot_bonus([runs], -1) == pytest.approx(-0.2)
    assert position_effect([runs]) < -0.2
    low, high = interval([runs, runs, runs], lambda sample: slot_bonus(sample, 0), Random(1), resamples=50)
    assert low == pytest.approx(0.4) and high == pytest.approx(0.4)


def test_an_order_that_is_not_the_legal_moves_is_refused():
    import asyncio

    from jev_chess.jev import JevMoveChooser
    from jev_chess.provider import Gateway

    class Provider:
        def gateway(self, credentials=None):
            return Gateway("openrouter", "https://x", "key", "jev-test", 5, False)

    chooser = JevMoveChooser(api=None, provider=Provider())
    with pytest.raises(ValueError):
        asyncio.run(chooser.choose(chess.Board(), order=[chess.Move.from_uci("e2e4")]))
