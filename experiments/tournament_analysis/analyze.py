"""Engine analysis of every game of a tournament, per player, for writing about it.

Reads a saved tournament file (`tournaments/<id>.json`) or a Data export, evaluates every position with
Stockfish and reports, per player: moves, average centipawn loss, accuracy (lichess's formula on the win
chance lost per move), best-move rate, inaccuracies, mistakes and blunders, plus time and cost. Usage:

    .venv/bin/python experiments/tournament_analysis/analyze.py tournaments/<id>.json --depth 14

Stockfish is looked up in STOCKFISH, then on PATH, then in .venv/bin/stockfish."""

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

import chess
import chess.engine

MATE_CP = 1000
THRESHOLDS = (("blunders", 300), ("mistakes", 100), ("inaccuracies", 50))


def win_chance(cp: int) -> float:
    """Lichess's mapping from centipawns to win probability, from the mover's side."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def accuracy(win_before: float, win_after: float) -> float:
    """Lichess's move accuracy: how much of the win chance survived the move."""
    if win_after >= win_before:
        return 100.0
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * (win_before - win_after)) - 3.1669))


def find_stockfish() -> str:
    candidates = [
        os.environ.get("STOCKFISH"),
        shutil.which("stockfish"),
        str(Path(sys.executable).with_name("stockfish")),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    raise SystemExit("Stockfish not found: set STOCKFISH, put it on PATH, or in .venv/bin/stockfish")


def games_of(data: dict) -> list[dict]:
    games = []
    for round_number, round_ in enumerate(data["rounds"], 1):
        boards = round_.get("games") or round_.get("pairings") or []
        for board, entry in enumerate(boards, 1):
            game = entry.get("game") or entry
            if not entry.get("result"):
                continue
            games.append(
                {
                    "round": round_number,
                    "board": board,
                    "white": entry["white"] if isinstance(entry["white"], str) else entry["white"]["id"],
                    "black": entry["black"] if isinstance(entry["black"], str) else entry["black"]["id"],
                    "result": entry["result"],
                    "moves_uci": game["moves_uci"],
                    "moves": game.get("moves", []),
                }
            )
    return games


def analyze(data: dict, depth: int, engine_path: str) -> dict:
    players: dict[str, dict] = {}

    def row(player: str) -> dict:
        return players.setdefault(
            player,
            {
                "moves": 0,
                "cp_loss": 0,
                "accuracy": 0.0,
                "best": 0,
                "inaccuracies": 0,
                "mistakes": 0,
                "blunders": 0,
                "seconds": 0.0,
                "cost_usd": 0.0,
                "games": set(),
            },
        )

    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        for game in games_of(data):
            board = chess.Board()
            record_by_ply = {m["ply"]: m for m in game["moves"]}
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            score_before = info["score"].pov(board.turn).score(mate_score=MATE_CP)
            best = info.get("pv", [None])[0]
            for ply, uci in enumerate(game["moves_uci"], 1):
                mover = game["white"] if board.turn == chess.WHITE else game["black"]
                move = chess.Move.from_uci(uci)
                r = row(mover)
                r["games"].add((game["round"], game["board"]))
                board.push(move)
                info = engine.analyse(board, chess.engine.Limit(depth=depth))
                score_after = (
                    -info["score"].pov(board.turn).score(mate_score=MATE_CP)
                    if not board.is_game_over()
                    else (MATE_CP if board.is_checkmate() else 0)
                )
                loss = max(0, score_before - score_after)
                r["moves"] += 1
                r["cp_loss"] += min(loss, MATE_CP)
                r["accuracy"] += accuracy(win_chance(score_before), win_chance(score_after))
                r["best"] += int(best == move)
                for name, threshold in THRESHOLDS:
                    if loss >= threshold:
                        r[name] += 1
                        break
                record = record_by_ply.get(ply, {})
                r["seconds"] += float(record.get("seconds", 0) or 0)
                r["cost_usd"] += float(record.get("cost_usd", 0) or 0)
                if board.is_game_over():
                    break
                score_before = info["score"].pov(board.turn).score(mate_score=MATE_CP)
                best = info.get("pv", [None])[0]
    report = {}
    for player, r in players.items():
        n = max(1, r["moves"])
        report[player] = {
            "games": len(r["games"]),
            "moves": r["moves"],
            "acpl": round(r["cp_loss"] / n, 1),
            "accuracy": round(r["accuracy"] / n, 1),
            "best_move_rate": round(r["best"] / n, 3),
            "inaccuracies": r["inaccuracies"],
            "mistakes": r["mistakes"],
            "blunders": r["blunders"],
            "seconds_per_move": round(r["seconds"] / n, 1),
            "cost_per_move": round(r["cost_usd"] / n, 6),
        }
    return dict(sorted(report.items(), key=lambda kv: (-kv[1]["accuracy"], kv[1]["acpl"])))


def render(report: dict) -> str:
    lines = [
        "| Player | Games | Moves | ACPL | Accuracy | Best move | Inacc. | Mistakes | Blunders | s/move | $/move |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for player, r in report.items():
        name = player.split("/")[-1]
        lines.append(
            f"| {name} | {r['games']} | {r['moves']} | {r['acpl']} | {r['accuracy']}% | {r['best_move_rate']:.0%} | {r['inaccuracies']} | {r['mistakes']} | {r['blunders']} | {r['seconds_per_move']} | {r['cost_per_move']:.5f} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="a saved tournament file or a Data export")
    parser.add_argument("--depth", type=int, default=12, help="Stockfish search depth per position")
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args()
    data = json.loads(args.file.read_text())
    report = analyze(data, args.depth, find_stockfish())
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
