"""Does Jev read the board? Eleven options, ten of them illegal moves and one legal move picked at random,
so move quality is no clue. Asked twice: with the usual "which move is best" instruction, which never says
illegal moves are present, and with an instruction that says exactly one option is legal."""

import argparse
import asyncio
import gzip
import json
from collections import Counter
from pathlib import Path
from random import Random
from statistics import mean

import chess
from pico_ioc import DictSource, EnvSource, configuration, init

from experiments.option_order.experiment import POSITIONS_FILE
from jev_chess.jev import JevError, JevMoveChooser
from jev_chess.main import load_env

DECOYS = 10
SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)
KINDS = {
    "empty origin": "the origin square is empty",
    "opponent piece": "the piece belongs to the opponent",
    "wrong geometry": "the piece cannot move that way",
    "blocked path": "another piece stands in the way",
    "own piece on target": "the destination holds a piece of the same side",
    "leaves king in check": "the move leaves the mover's own king in check",
}
INSTRUCTIONS = {
    "implicit": (
        "You are a strong chess player playing {side}. Which move is best? "
        "Prefer checkmate, then winning material safely, then development and king safety. "
        "Never leave a piece where it can be captured for free."
    ),
    "explicit": (
        "You are playing {side}. Exactly one of these moves is legal in this position; every other one "
        "breaks the rules of chess. Which move is the legal one?"
    ),
}


def label(origin: int, target: int) -> str:
    return f"{chess.square_name(origin)}-{chess.square_name(target)}"


def describe(piece_type: int, origin: int, target: int) -> str:
    return f"{chess.piece_name(piece_type)} from {chess.square_name(origin)} to {chess.square_name(target)}"


def _reach_on_empty_board(piece: chess.Piece, square: int) -> chess.SquareSet:
    lonely = chess.Board(None)
    lonely.set_piece_at(square, piece)
    return lonely.attacks(square)


def illegal_moves(board: chess.Board) -> dict[str, list[tuple[int, int, int]]]:
    """Every illegal move of each kind as (piece type, origin, target); none of them is pseudo-legal except the last kind."""
    own = board.turn
    pseudo = {(move.from_square, move.to_square) for move in board.pseudo_legal_moves}
    found: dict[str, list[tuple[int, int, int]]] = {kind: [] for kind in KINDS}
    for square, piece in board.piece_map().items():
        if piece.color != own:
            continue
        attacks = board.attacks(square)
        reach = _reach_on_empty_board(piece, square)
        for target in chess.SQUARES:
            if target == square or (square, target) in pseudo:
                continue
            occupant = board.piece_at(target)
            if target in attacks and occupant and occupant.color == own:
                found["own piece on target"].append((piece.piece_type, square, target))
            elif piece.piece_type in SLIDERS and target in reach and target not in attacks:
                found["blocked path"].append((piece.piece_type, square, target))
            elif target not in reach and piece.piece_type != chess.PAWN and not (occupant and occupant.color == own):
                found["wrong geometry"].append((piece.piece_type, square, target))
    for move in board.pseudo_legal_moves:
        if not board.is_legal(move):
            found["leaves king in check"].append(
                (board.piece_type_at(move.from_square), move.from_square, move.to_square)
            )
    flipped = board.copy(stack=False)
    flipped.turn = not own
    flipped.ep_square = None
    for move in flipped.legal_moves:
        found["opponent piece"].append((flipped.piece_type_at(move.from_square), move.from_square, move.to_square))
    empty = [square for square in chess.SQUARES if board.piece_at(square) is None]
    for square in empty:
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
            for target in _reach_on_empty_board(chess.Piece(piece_type, own), square):
                occupant = board.piece_at(target)
                if not (occupant and occupant.color == own):
                    found["empty origin"].append((piece_type, square, target))
    return found


def build_options(board: chess.Board, rng: Random) -> tuple[dict[str, str], str, dict[str, str]]:
    """Eleven labelled options in random order, the label of the only legal one, and the kind of each decoy."""
    legal = rng.choice(sorted(board.legal_moves, key=str))
    legal_label = label(legal.from_square, legal.to_square)
    taken = {label(move.from_square, move.to_square) for move in board.legal_moves}
    pools = {kind: rng.sample(moves, len(moves)) for kind, moves in illegal_moves(board).items()}
    decoys: dict[str, tuple[str, str]] = {}
    while len(decoys) < DECOYS and any(pools.values()):
        for kind, pool in pools.items():
            while pool and len(decoys) < DECOYS:
                piece_type, origin, target = pool.pop()
                name = label(origin, target)
                if name not in taken and name not in decoys:
                    decoys[name] = (describe(piece_type, origin, target), kind)
                    break
    entries = [(legal_label, describe(board.piece_type_at(legal.from_square), legal.from_square, legal.to_square))]
    entries += [(name, text) for name, (text, _) in decoys.items()]
    rng.shuffle(entries)
    return dict(entries), legal_label, {name: kind for name, (_, kind) in decoys.items()}


def summarize(trials: list[dict]) -> dict:
    wrong = Counter(trial["picked_kind"] for trial in trials if not trial["correct"])
    offered = Counter(kind for trial in trials for kind in trial["decoy_kinds"].values())
    mass: dict[str, list[float]] = {}
    for trial in trials:
        for name, kind in trial["decoy_kinds"].items():
            mass.setdefault(kind, []).append(trial["probabilities"].get(name, 0.0))
    return {
        "trials": len(trials),
        "picked_the_legal_move": mean(trial["correct"] for trial in trials),
        "chance": mean(1 / len(trial["asked"]) for trial in trials),
        "mean_probability_on_the_legal_move": mean(trial["probabilities"].get(trial["legal"], 0.0) for trial in trials),
        "wrong_picks_by_kind": dict(wrong.most_common()),
        "decoys_offered_by_kind": dict(offered.most_common()),
        "mean_probability_per_decoy_by_kind": {kind: mean(values) for kind, values in sorted(mass.items())},
    }


def interval(trials: list[dict], rng: Random, resamples: int = 2000) -> tuple[float, float]:
    """95% bootstrap interval of the rate of picking the legal move, resampling whole positions."""
    by_position: dict[str, list[bool]] = {}
    for trial in trials:
        by_position.setdefault(trial["name"], []).append(trial["correct"])
    groups = list(by_position.values())
    rates = sorted(
        mean(flag for group in (rng.choice(groups) for _ in groups) for flag in group) for _ in range(resamples)
    )
    return rates[int(0.025 * resamples)], rates[int(0.975 * resamples) - 1]


async def measure(chooser: JevMoveChooser, positions: list[dict], draws: int, seed: int, workers: int) -> dict:
    rng = Random(seed)
    gate = asyncio.Semaphore(workers)
    totals = {"calls": 0, "cost_usd": 0.0, "retries": 0}

    async def ask(position: dict, board: chess.Board, variant: str, criteria, legal, kinds) -> dict:
        instructions = INSTRUCTIONS[variant].format(side="white" if board.turn else "black")
        async with gate:
            for attempt in range(4):
                try:
                    answer = await chooser.ask(board, instructions, criteria)
                    break
                except JevError:
                    if attempt == 3:
                        raise
                    totals["retries"] += 1
                    await asyncio.sleep(1 + attempt)
        totals["calls"] += 1
        totals["cost_usd"] += answer.cost_usd
        return {
            "name": position["name"],
            "fen": position["fen"],
            "variant": variant,
            "asked": list(criteria),
            "legal": legal,
            "choice": answer.choice,
            "correct": answer.choice == legal,
            "picked_kind": "legal" if answer.choice == legal else kinds[answer.choice],
            "decoy_kinds": kinds,
            "probabilities": answer.probabilities,
        }

    jobs = []
    for position in positions:
        board = chess.Board(position["fen"])
        for _ in range(draws):
            criteria, legal, kinds = build_options(board, rng)
            if len(criteria) == DECOYS + 1:
                jobs += [ask(position, board, variant, criteria, legal, kinds) for variant in INSTRUCTIONS]
    trials = await asyncio.gather(*jobs)
    variants = {}
    for variant in INSTRUCTIONS:
        own = [trial for trial in trials if trial["variant"] == variant]
        variants[variant] = {**summarize(own), "interval": interval(own, Random(seed))}
    both = {}
    for trial in trials:
        both.setdefault((trial["name"], tuple(trial["asked"])), {})[trial["variant"]] = trial["correct"]
    pairs = [pair for pair in both.values() if len(pair) == 2]
    agreement = {
        "both right": sum(pair["implicit"] and pair["explicit"] for pair in pairs),
        "only when told": sum(pair["explicit"] and not pair["implicit"] for pair in pairs),
        "only when not told": sum(pair["implicit"] and not pair["explicit"] for pair in pairs),
        "both wrong": sum(not pair["implicit"] and not pair["explicit"] for pair in pairs),
    }
    design = {
        "positions": len(positions),
        "draws_per_position": draws,
        "decoys": DECOYS,
        "seed": seed,
        "model": chooser.model_for(),
    }
    return {"design": design, **totals, "variants": variants, "same_question_both_ways": agreement, "trials": trials}


def render(report: dict) -> str:
    lines = [
        f"{report['design']['positions']} positions, {report['calls']} calls, ${report['cost_usd']:.4f}, {report['retries']} retries",
        "",
    ]
    for variant, entry in report["variants"].items():
        low, high = entry["interval"]
        lines.append(
            f"{variant}: picked the legal move {entry['picked_the_legal_move']:.1%} (95% {low:.1%} to {high:.1%}), "
            f"chance {entry['chance']:.1%}, mean probability on it {entry['mean_probability_on_the_legal_move']:.3f}"
        )
        for kind, offered in entry["decoys_offered_by_kind"].items():
            picked = entry["wrong_picks_by_kind"].get(kind, 0)
            lines.append(
                f"    {kind:<22} offered {offered:>5}, picked {picked:>4} ({picked / offered:.1%} of those offered), "
                f"mean probability each {entry['mean_probability_per_decoy_by_kind'][kind]:.3f}"
            )
        lines.append("")
    lines.append(
        "same options asked both ways: "
        + ", ".join(f"{key} {value}" for key, value in report["same_question_both_ways"].items())
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m experiments.legality", description=__doc__.splitlines()[0])
    parser.add_argument("--draws", type=int, default=3, help="different sets of eleven options per position")
    parser.add_argument("--positions", type=int, help="use only the first N positions")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent, help="directory for report.json and trials.json.gz"
    )
    args = parser.parse_args()
    load_env()
    modules = ["jev_chess.jev", "jev_chess.provider", "jev_chess.settings"]
    container = init(modules=modules, config=configuration(EnvSource(), DictSource({})))
    positions = json.loads(POSITIONS_FILE.read_text())[: args.positions]
    report = asyncio.run(measure(container.get(JevMoveChooser), positions, args.draws, args.seed, args.workers))
    print(render(report))
    args.out.mkdir(parents=True, exist_ok=True)
    trials = report.pop("trials")
    (args.out / "report.json").write_text(json.dumps(report, indent=1))
    with gzip.open((args.out / "trials.json.gz"), "wt") as handle:
        json.dump(trials, handle, separators=(",", ":"))


if __name__ == "__main__":
    main()
