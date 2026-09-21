"""Does Jev's answer depend on the order of the options?

Every position is asked several times with one fixed order, which measures Jev's own run-to-run noise,
and then once per rotation: in rotation k a different legal move goes first and the rest are shuffled,
so exposure to the first slot is balanced by design. Whatever changes beyond the noise is the order."""

import argparse
import asyncio
import gzip
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from random import Random
from statistics import mean

import chess
from pico_ioc import DictSource, EnvSource, configuration, init

from jev_chess.jev import JevError, JevMoveChooser
from jev_chess.main import load_env

HERE = Path(__file__).parent
POSITIONS_FILE = HERE.parent / "positions.json"


def agreement(choices: list[str]) -> float:
    """Share of runs that picked the most common move: 1.0 means it always answered the same."""
    return Counter(choices).most_common(1)[0][1] / len(choices)


def pair_agreement(choices: list[str]) -> float:
    """Chance that two runs picked the same move. Unlike `agreement`, it does not depend on how many runs there are."""
    pairs = list(combinations(choices, 2))
    return mean(first == second for first, second in pairs) if pairs else 1.0


def top_two_gap(probabilities: dict[str, float]) -> float:
    ranked = sorted(probabilities.values(), reverse=True)
    return ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)


def distance(first: dict[str, float], second: dict[str, float]) -> float:
    """Total variation distance between two probability distributions, from 0 (identical) to 1."""
    return 0.5 * sum(abs(first.get(key, 0.0) - second.get(key, 0.0)) for key in first.keys() | second.keys())


def mean_pairwise_distance(distributions: list[dict[str, float]]) -> float:
    pairs = list(combinations(distributions, 2))
    return mean(distance(a, b) for a, b in pairs) if pairs else 0.0


def _slope_terms(runs: list[dict]) -> tuple[float, float]:
    points: dict[str, list[tuple[float, float]]] = {}
    for run in runs:
        last = max(1, len(run["asked"]) - 1)
        for index, san in enumerate(run["asked"]):
            points.setdefault(san, []).append((index / last, run["probabilities"].get(san, 0.0)))
    covariance = spread = 0.0
    for pairs in points.values():
        centre_x = mean(x for x, _ in pairs)
        centre_y = mean(y for _, y in pairs)
        covariance += sum((x - centre_x) * (y - centre_y) for x, y in pairs)
        spread += sum((x - centre_x) ** 2 for x, _ in pairs)
    return covariance, spread


def _ratio(terms: list[tuple[float, float]]) -> float | None:
    bottom = sum(term[1] for term in terms)
    return sum(term[0] for term in terms) / bottom if bottom else None


def _slot_terms(runs: list[dict], slot: int) -> tuple[float, float]:
    gaps = _slot_gaps(runs, slot)
    return sum(gaps), len(gaps)


def position_effect(positions: list[list[dict]]) -> float | None:
    """Change in a move's probability when it goes from first to last in the list, other things equal.

    A pooled slope with one intercept per move of each position, so a move's quality cancels out."""
    return _ratio([_slope_terms(runs) for runs in positions])


def _slot_gaps(runs: list[dict], slot: int) -> list[float]:
    inside: dict[str, list[float]] = {}
    outside: dict[str, list[float]] = {}
    for run in runs:
        target = run["asked"][slot]
        for san in run["asked"]:
            (inside if san == target else outside).setdefault(san, []).append(run["probabilities"].get(san, 0.0))
    return [mean(inside[san]) - mean(outside[san]) for san in inside if san in outside]


def slot_bonus(positions: list[list[dict]], slot: int) -> float | None:
    """Mean probability of a move when it sits in `slot` (0 first, -1 last) minus the same move elsewhere."""
    return _ratio([_slot_terms(runs, slot) for runs in positions])


def interval(terms: list[tuple[float, float]], rng: Random, resamples: int = 2000) -> tuple[float, float]:
    """95% bootstrap interval of a ratio of sums, resampling whole positions because their runs are not independent.

    Each position is reduced to its (numerator, denominator) once, so a resample is a sum and not a recount."""
    values = sorted(
        value for _ in range(resamples) if (value := _ratio([rng.choice(terms) for _ in terms])) is not None
    )
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def rotations(board: chess.Board, count: int, rng: Random) -> list[list[chess.Move]]:
    """`count` orders of the legal moves; in order k a different move goes first and the rest are shuffled."""
    base = list(board.legal_moves)
    rng.shuffle(base)
    orders = []
    for k in range(count):
        first = base[k % len(base)]
        rest = [move for move in base if move != first]
        rng.shuffle(rest)
        orders.append([first, *rest])
    return orders


def summarize_position(same: list[dict], rotated: list[dict]) -> dict:
    modal = Counter(run["choice"] for run in rotated).most_common(1)[0][0]
    modal_probabilities = [run["probabilities"].get(modal, 0.0) for run in rotated]
    return {
        "legal_moves": len(same[0]["asked"]),
        "confidence": mean(max(run["probabilities"].values()) for run in same),
        "top_two_gap": mean(top_two_gap(run["probabilities"]) for run in same),
        "same_order_pair_agreement": pair_agreement([run["choice"] for run in same]),
        "rotated_pair_agreement": pair_agreement([run["choice"] for run in rotated]),
        "same_order_agreement": agreement([run["choice"] for run in same]),
        "same_order_distance": mean_pairwise_distance([run["probabilities"] for run in same]),
        "rotated_agreement": agreement([run["choice"] for run in rotated]),
        "rotated_distance": mean_pairwise_distance([run["probabilities"] for run in rotated]),
        "rotated_choices": dict(Counter(run["choice"] for run in rotated).most_common()),
        "modal_move": modal,
        "modal_probability_min": min(modal_probabilities),
        "modal_probability_max": max(modal_probabilities),
        "first_move_chosen": mean(run["choice"] == run["asked"][0] for run in rotated),
    }


def overall(entries: list[dict], rotated: list[list[dict]], rng: Random) -> dict:
    def averaged(key):
        return mean(entry[key] for entry in entries)

    def group(label, members):
        if not members:
            return None
        keys = ("same_order_pair_agreement", "rotated_pair_agreement", "same_order_distance", "rotated_distance")
        return {"group": label, "positions": len(members), **{key: mean(m[key] for m in members) for key in keys}}

    swings = [entry["modal_probability_max"] - entry["modal_probability_min"] for entry in entries]
    groups = [
        group("Jev is confident (top move 60% or more)", [e for e in entries if e["confidence"] >= 0.6]),
        group("Jev is unsure (top move under 60%)", [e for e in entries if e["confidence"] < 0.6]),
        group("top two moves within 5 points", [e for e in entries if e["top_two_gap"] < 0.05]),
        group("top two moves 5 to 15 points apart", [e for e in entries if 0.05 <= e["top_two_gap"] < 0.15]),
        group("top two moves 15 to 30 points apart", [e for e in entries if 0.15 <= e["top_two_gap"] < 0.3]),
        group("top move ahead by 30 points or more", [e for e in entries if e["top_two_gap"] >= 0.3]),
        group("up to 20 legal moves", [e for e in entries if e["legal_moves"] <= 20]),
        group("21 to 35 legal moves", [e for e in entries if 20 < e["legal_moves"] <= 35]),
        group("more than 35 legal moves", [e for e in entries if e["legal_moves"] > 35]),
    ]
    drops = [entry["same_order_pair_agreement"] - entry["rotated_pair_agreement"] for entry in entries]
    resampled = sorted(mean(rng.choice(drops) for _ in drops) for _ in range(2000))
    return {
        "positions": len(entries),
        "same_order_pair_agreement": averaged("same_order_pair_agreement"),
        "rotated_pair_agreement": averaged("rotated_pair_agreement"),
        "pair_agreement_drop": mean(drops),
        "pair_agreement_drop_interval": (resampled[50], resampled[1949]),
        "same_order_agreement": averaged("same_order_agreement"),
        "rotated_agreement": averaged("rotated_agreement"),
        "same_order_distance": averaged("same_order_distance"),
        "rotated_distance": averaged("rotated_distance"),
        "positions_where_rotation_changed_the_move": sum(entry["rotated_agreement"] < 1 for entry in entries),
        "positions_where_the_same_order_changed_the_move": sum(entry["same_order_agreement"] < 1 for entry in entries),
        "mean_swing_of_the_chosen_move_probability": mean(swings),
        "largest_swing_of_the_chosen_move_probability": max(swings),
        "first_move_chosen_rate": averaged("first_move_chosen"),
        "first_move_chosen_rate_if_order_did_not_matter": mean(1 / entry["legal_moves"] for entry in entries),
        "position_effect": position_effect(rotated),
        "position_effect_interval": interval([_slope_terms(runs) for runs in rotated], rng),
        "first_slot_bonus": slot_bonus(rotated, 0),
        "first_slot_bonus_interval": interval([_slot_terms(runs, 0) for runs in rotated], rng),
        "last_slot_bonus": slot_bonus(rotated, -1),
        "last_slot_bonus_interval": interval([_slot_terms(runs, -1) for runs in rotated], rng),
        "groups": [entry for entry in groups if entry],
    }


async def measure(chooser: JevMoveChooser, positions: list[dict], repeats: int, rotated: int, seed: int, workers: int):
    rng = Random(seed)
    gate = asyncio.Semaphore(workers)
    totals = {"calls": 0, "cost_usd": 0.0, "retries": 0}

    async def ask(board: chess.Board, order: list[chess.Move] | None) -> dict:
        async with gate:
            for attempt in range(4):
                try:
                    decision = await chooser.choose(board, order=order)
                    break
                except JevError:
                    if attempt == 3:
                        raise
                    totals["retries"] += 1
                    await asyncio.sleep(1 + attempt)
        totals["calls"] += 1
        totals["cost_usd"] += decision.cost_usd
        return {"choice": decision.san, "probabilities": decision.probabilities, "asked": decision.asked}

    async def one(position: dict) -> dict:
        board = chess.Board(position["fen"])
        orders = rotations(board, rotated, rng)
        same = await asyncio.gather(*(ask(board, None) for _ in range(repeats)))
        turned = await asyncio.gather(*(ask(board, order) for order in orders))
        print(f"  {position['name']}", flush=True)
        return {"same": same, "rotated": turned}

    raw = await asyncio.gather(*(one(position) for position in positions))
    runs = [{"name": position["name"], "fen": position["fen"], **entry} for position, entry in zip(positions, raw)]
    design = {"repeats_with_the_same_order": repeats, "rotations": rotated, "seed": seed, "model": chooser.model_for()}
    return {"design": design, **totals, **analyze(runs, seed)}, runs


def analyze(runs: list[dict], seed: int) -> dict:
    """The whole report from stored runs, so the analysis can be redone without calling Jev."""
    entries = [
        {"name": entry["name"], "fen": entry["fen"], **summarize_position(entry["same"], entry["rotated"])}
        for entry in runs
    ]
    return {"overall": overall(entries, [entry["rotated"] for entry in runs], Random(seed)), "positions": entries}


def render(report: dict) -> str:
    total = report["overall"]
    low, high = total["position_effect_interval"]
    first_low, first_high = total["first_slot_bonus_interval"]
    last_low, last_high = total["last_slot_bonus_interval"]
    lines = [
        f"{total['positions']} positions, {report['calls']} calls, ${report['cost_usd']:.4f}, {report['retries']} retries",
        "",
        (
            f"two runs pick the same move:          same order {total['same_order_pair_agreement']:.1%}   rotated {total['rotated_pair_agreement']:.1%}"
            f"   drop {total['pair_agreement_drop']:.1%} (95% {total['pair_agreement_drop_interval'][0]:.1%} to {total['pair_agreement_drop_interval'][1]:.1%})"
        ),
        f"distance between distributions:       same order {total['same_order_distance']:.3f}   rotated {total['rotated_distance']:.3f}",
        (
            f"positions where the move ever changed: {total['positions_where_the_same_order_changed_the_move']} in {report['design']['repeats_with_the_same_order']} same-order runs, "
            f"{total['positions_where_rotation_changed_the_move']} in {report['design']['rotations']} rotated runs (not comparable: more runs, more chances)"
        ),
        f"swing of the chosen move probability: mean {total['mean_swing_of_the_chosen_move_probability']:.3f}, largest {total['largest_swing_of_the_chosen_move_probability']:.3f}",
        f"first listed move was chosen:         {total['first_move_chosen_rate']:.1%} of rotated runs ({total['first_move_chosen_rate_if_order_did_not_matter']:.1%} if order did not matter)",
        f"position effect, first to last:       {total['position_effect']:+.4f}  (95% {low:+.4f} to {high:+.4f})",
        f"first slot bonus:                     {total['first_slot_bonus']:+.4f}  (95% {first_low:+.4f} to {first_high:+.4f})",
        f"last slot bonus:                      {total['last_slot_bonus']:+.4f}  (95% {last_low:+.4f} to {last_high:+.4f})",
        "",
    ]
    for entry in total["groups"]:
        lines.append(
            f"  {entry['group']:<42} n={entry['positions']:<4} agree {entry['same_order_pair_agreement']:.1%} -> {entry['rotated_pair_agreement']:.1%}"
            f"   distance {entry['same_order_distance']:.3f} -> {entry['rotated_distance']:.3f}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m experiments.option_order", description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=6, help="runs per position with the same option order")
    parser.add_argument("--rotations", type=int, default=30, help="runs per position, each with a different first move")
    parser.add_argument("--positions", type=int, help="use only the first N positions")
    parser.add_argument("--workers", type=int, default=6, help="requests in flight at once")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=HERE, help="directory for report.json and runs.json.gz")
    parser.add_argument(
        "--reanalyze", action="store_true", help="rebuild the report from stored runs, without calling Jev"
    )
    args = parser.parse_args()
    if args.reanalyze:
        with gzip.open((args.out / "runs.json.gz"), "rt") as handle:
            runs = json.load(handle)
        previous = json.loads((args.out / "report.json").read_text())
        report = {key: previous[key] for key in ("design", "calls", "cost_usd", "retries")} | analyze(runs, args.seed)
        print(render(report))
        (args.out / "report.json").write_text(json.dumps(report, indent=1))
        return
    load_env()
    modules = ["jev_chess.jev", "jev_chess.provider", "jev_chess.settings"]
    container = init(modules=modules, config=configuration(EnvSource(), DictSource({})))
    positions = json.loads(POSITIONS_FILE.read_text())[: args.positions]
    chooser = container.get(JevMoveChooser)
    report, runs = asyncio.run(measure(chooser, positions, args.repeats, args.rotations, args.seed, args.workers))
    print()
    print(render(report))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1))
    with gzip.open((args.out / "runs.json.gz"), "wt") as handle:
        json.dump(runs, handle, separators=(",", ":"))


if __name__ == "__main__":
    main()
