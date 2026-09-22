"""A Swiss tournament for the session: a fixed number of rounds, each pairing players on equal scores.

Every board of a round is its own game. The server plays the games between models itself, several at a
time up to `TOURNAMENT_CONCURRENCY`, while a game with you waits for your moves; when every board of a round
is over the next round is paired. With an odd number of players one gets a bye."""

import asyncio
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

import chess
from pico_ioc import component

from .game import Game, IllegalMove
from .jev import JevError, JevMoveChooser
from .models import ModelRegistry, SessionModels
from .provider import SessionCredentials
from .settings import TournamentSettings
from .standings import POINTS, Standings

HUMAN = "human"
MAX_PARTICIPANTS = 10
MAX_ROUNDS = 20


def pair_round(
    order: list[str], played: set[frozenset], balance: dict[str, int], byes: set[str]
) -> tuple[list, str | None]:
    """Pairs neighbours in the standings, avoiding a rematch when a lower neighbour is free.

    `balance` is whites minus blacks so far; the player who has had more whites takes black. The bye goes to the
    lowest-placed player who has not had one."""
    pool = list(order)
    bye = None
    if len(pool) % 2:
        bye = next((player for player in reversed(pool) if player not in byes), pool[-1])
        pool.remove(bye)
    pairs = []
    while pool:
        first = pool.pop(0)
        second = next((other for other in pool if frozenset((first, other)) not in played), pool[0])
        pool.remove(second)
        pairs.append((second, first) if balance[first] > balance[second] else (first, second))
    return pairs, bye


@component(scope="session")
class Tournament:
    def __init__(
        self,
        chooser: JevMoveChooser,
        registry: ModelRegistry,
        credentials: SessionCredentials,
        session: SessionModels,
        standings: Standings,
        settings: TournamentSettings,
    ):
        self._chooser = chooser
        self._registry = registry
        self._credentials = credentials
        self._session = session
        self._session_standings = standings
        self._concurrency = max(1, settings.concurrency)
        self._dir = Path(settings.dir)
        self._rounds: list[dict] = []
        self._reset()

    def _reset(self) -> None:
        self._id = ""
        self._participants: list[str] = []
        self._rounds_total = 0
        self._rounds = []
        self._played: set[frozenset] = set()
        self._balance: dict[str, int] = {}
        self._byes: set[str] = set()
        self._opponents: dict[str, list[str]] = {}
        self._scores: dict[str, list[tuple[str, float]]] = {}
        self._standings = Standings(self._registry)
        self._started_at: float | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None

    async def start(self, participants: list[str], human: bool, rounds: int) -> dict:
        ids = list(dict.fromkeys(participants))
        known = {model.id: model for model in self._registry.list(self._credentials, self._session)}
        unknown = [model_id for model_id in ids if model_id not in known]
        if unknown:
            raise IllegalMove(f"unknown model: {unknown[0]!r}")
        not_ready = [model_id for model_id in ids if not known[model_id].ready]
        if not_ready:
            raise IllegalMove(f"{known[not_ready[0]].name} is not ready: {known[not_ready[0]].note}")
        if human:
            ids.append(HUMAN)
        if not 2 <= len(ids) <= MAX_PARTICIPANTS:
            raise IllegalMove(f"a tournament needs between 2 and {MAX_PARTICIPANTS} participants")
        if not 1 <= rounds <= MAX_ROUNDS:
            raise IllegalMove(f"a tournament has between 1 and {MAX_ROUNDS} rounds")
        self.stop()
        self._participants = ids
        self._rounds_total = rounds
        self._balance = dict.fromkeys(ids, 0)
        self._opponents = {player: [] for player in ids}
        self._scores = {player: [] for player in ids}
        for player in ids:
            self._standings.ensure(player)
        self._started_at = time.time()
        self._id = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._lock = asyncio.Lock()
        await self._new_round()
        return await self.view()

    def saved(self) -> list[dict]:
        """The tournaments on disk, newest first: enough to pick one to resume or to look at."""
        entries = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            rounds = data.get("rounds", [])
            last = rounds[-1]["pairings"] if rounds else []
            done = len(rounds) >= data.get("rounds_total", 0) and all(e["result"] is not None for e in last)
            entries.append(
                {
                    "id": data["id"],
                    "started_at": datetime.fromtimestamp(data["started_at"], UTC).isoformat(timespec="seconds"),
                    "participants": [self._registry.name_of(p) for p in data.get("participants", [])],
                    "round": len(rounds),
                    "rounds_total": data.get("rounds_total", 0),
                    "done": done,
                    "finished_games": sum(1 for r in rounds for e in r["pairings"] if e["result"] is not None),
                    "current": data["id"] == self._id,
                }
            )
        return entries

    async def resume(self, tournament_id: str) -> dict:
        """Loads a saved tournament into this session and plays on from where it stopped."""
        path = self._dir / f"{tournament_id}.json"
        if not path.is_file() or not tournament_id.replace("-", "").isalnum():
            raise IllegalMove(f"no saved tournament {tournament_id!r}")
        data = json.loads(path.read_text())
        self.stop()
        self._id = data["id"]
        self._participants = list(data["participants"])
        self._rounds_total = data["rounds_total"]
        self._played = {frozenset(pair) for pair in data["played"]}
        self._balance = dict(data["balance"])
        self._byes = set(data["byes"])
        self._opponents = {k: list(v) for k, v in data["opponents"].items()}
        self._scores = {k: [(o, float(e)) for o, e in v] for k, v in data["scores"].items()}
        self._standings.restore(data["standings"])
        self._started_at = time.time() - data.get("elapsed", 0.0)
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._lock = asyncio.Lock()
        for round_ in data["rounds"]:
            boards = []
            for saved in round_["pairings"]:
                game = Game(self._chooser, self._credentials, self._registry, self._session, self._session_standings)
                game.restore(saved["game"])
                boards.append(
                    {
                        "white": saved["white"],
                        "black": saved["black"],
                        "game": game,
                        "game_id": saved["game"]["id"],
                        "result": saved["result"],
                        "pgn": saved.get("pgn", ""),
                        "record": saved.get("record"),
                        "error": None,
                        "event": asyncio.Event(),
                        "thinking_since": None,
                        "task": None,
                    }
                )
            self._rounds.append({"pairings": boards, "bye": round_["bye"]})
        if self._rounds:
            for entry in self._rounds[-1]["pairings"]:
                if entry["result"] is None:
                    entry["task"] = asyncio.create_task(self._run_board(entry))
        return await self.view()

    def _save(self) -> None:
        """Writes the whole tournament to its file, atomically, so a restart loses nothing."""
        if not self._id:
            return
        data = {
            "id": self._id,
            "started_at": self._started_at,
            "elapsed": time.time() - self._started_at if self._started_at else 0.0,
            "participants": self._participants,
            "rounds_total": self._rounds_total,
            "played": [sorted(pair) for pair in self._played],
            "balance": self._balance,
            "byes": sorted(self._byes),
            "opponents": self._opponents,
            "scores": self._scores,
            "standings": self._standings.to_record(),
            "rounds": [
                {
                    "bye": round_["bye"],
                    "pairings": [
                        {
                            "white": entry["white"],
                            "black": entry["black"],
                            "result": entry["result"],
                            "pgn": entry["pgn"],
                            "record": entry["record"],
                            "game": entry["game"].to_record(),
                        }
                        for entry in round_["pairings"]
                    ],
                }
                for round_ in self._rounds
            ],
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{self._id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)

    def stop(self) -> None:
        for round_ in self._rounds:
            for entry in round_["pairings"]:
                if entry["task"] is not None:
                    entry["task"].cancel()
        self._reset()

    @property
    def active(self) -> bool:
        return bool(self._rounds) and not self.done

    @property
    def done(self) -> bool:
        return (
            bool(self._rounds)
            and len(self._rounds) >= self._rounds_total
            and all(entry["result"] is not None for entry in self._rounds[-1]["pairings"])
        )

    def _board(self, number: int, round_number: int | None = None) -> dict:
        """Board `number` of a round, the current one unless `round_number` says otherwise."""
        if not self._rounds:
            raise IllegalMove("no tournament is running")
        if round_number is not None and not 1 <= round_number <= len(self._rounds):
            raise IllegalMove(f"round {round_number} has not been played")
        boards = self._rounds[(round_number or len(self._rounds)) - 1]["pairings"]
        if not 1 <= number <= len(boards):
            raise IllegalMove(f"board {number} is not in that round")
        return boards[number - 1]

    async def board_state(self, number: int, round_number: int | None = None) -> dict:
        return await self._board(number, round_number)["game"].snapshot()

    async def board_pgn(self, number: int, round_number: int | None = None) -> tuple[str, str]:
        return await self._board(number, round_number)["game"].pgn()

    async def human_move(self, number: int, origin: str, target: str, promotion: str = "q") -> dict:
        entry = self._board(number)
        state = await entry["game"].human_move(origin, target, promotion)
        self._save()
        entry["event"].set()
        return state

    async def retry(self, number: int) -> None:
        """Starts a board again after a gateway error stopped it."""
        entry = self._board(number)
        if entry["error"] is None:
            return
        entry["error"] = None
        entry["task"] = asyncio.create_task(self._run_board(entry))

    async def pardon(self, number: int) -> dict:
        """Lets a board of the current round that was lost by illegal moves go on, as if the forfeit had not
        happened: its result leaves the standings and the game continues from the same position."""
        entry = self._board(number)
        outcome = await entry["game"].outcome()
        if entry["result"] is None or outcome["forfeited"] is None:
            raise IllegalMove(f"board {number} was not lost by illegal moves")
        async with self._lock:
            self._standings.unrecord(
                outcome["game_id"],
                outcome["players"],
                outcome["result"],
                outcome["forfeited"],
                outcome["illegal"],
                outcome["usage"],
            )
            for player in (entry["white"], entry["black"]):
                if self._scores[player]:
                    self._scores[player].pop()
            entry["result"] = None
            entry["pgn"] = ""
            entry["record"] = None
            entry["error"] = None
            await entry["game"].pardon()
            entry["game_id"] = (await entry["game"].snapshot())["game_id"]
            self._save()
        entry["task"] = asyncio.create_task(self._run_board(entry))
        return await self.view()

    async def _new_round(self) -> None:
        order = self._participants if not self._rounds else [row["id"] for row in self._table()]
        pairs, bye = pair_round(order, self._played, self._balance, self._byes)
        boards = []
        for white, black in pairs:
            self._played.add(frozenset((white, black)))
            self._balance[white] += 1
            self._balance[black] -= 1
            self._opponents[white].append(black)
            self._opponents[black].append(white)
            game = Game(self._chooser, self._credentials, self._registry, self._session, self._session_standings)
            human = "white" if white == HUMAN else "black" if black == HUMAN else "none"
            state = await game.new(human, white if white != HUMAN else black, black if black != HUMAN else white)
            boards.append(
                {
                    "white": white,
                    "black": black,
                    "game": game,
                    "game_id": state["game_id"],
                    "result": None,
                    "pgn": "",
                    "record": None,
                    "error": None,
                    "event": asyncio.Event(),
                    "thinking_since": None,
                    "task": None,
                }
            )
        if bye is not None:
            self._byes.add(bye)
            self._standings.bye(bye)
        self._rounds.append({"pairings": boards, "bye": bye})
        self._save()
        for entry in boards:
            entry["task"] = asyncio.create_task(self._run_board(entry))

    async def _run_board(self, entry: dict) -> None:
        """Plays a board to the end: model moves under the concurrency limit, your moves when they come."""
        game = entry["game"]
        try:
            while True:
                state = await game.snapshot()
                if state["over"]:
                    break
                if state["humans_turn"]:
                    entry["event"].clear()
                    await entry["event"].wait()
                    continue
                async with self._semaphore:
                    entry["thinking_since"] = time.time()
                    try:
                        await game.jev_move()
                    finally:
                        entry["thinking_since"] = None
                self._save()
        except (JevError, IllegalMove) as error:
            entry["error"] = str(error)
            return
        await self._board_finished(entry)

    async def _board_finished(self, entry: dict) -> None:
        async with self._lock:
            if entry["result"] is not None:
                return
            outcome = await entry["game"].outcome()
            round_number = next(i for i, r in enumerate(self._rounds, 1) if entry in r["pairings"])
            board_number = self._rounds[round_number - 1]["pairings"].index(entry) + 1
            self._standings.record(
                outcome["game_id"],
                outcome["players"],
                outcome["result"],
                outcome["forfeited"],
                outcome["illegal"],
                outcome["usage"],
            )
            entry["result"] = outcome["result"]
            _, pgn = await entry["game"].pgn()
            entry["pgn"] = pgn.replace(
                '[Event "system-one-chess"]', '[Event "system-one-chess tournament"]', 1
            ).replace('[Round "?"]', f'[Round "{round_number}.{board_number}"]', 1)
            entry["record"] = {
                "forfeited": colour_name(outcome["forfeited"]),
                "illegal": {colour_name(c): n for c, n in outcome["illegal"].items()},
                "usage": {colour_name(c): dict(u) for c, u in outcome["usage"].items()},
                "moves": outcome["moves"],
                "final_fen": outcome["fen"],
            }
            white_points, black_points = POINTS[outcome["result"]]
            self._scores[entry["white"]].append((entry["black"], white_points))
            self._scores[entry["black"]].append((entry["white"], black_points))
            current = self._rounds[-1]
            self._save()
            if all(e["result"] is not None for e in current["pairings"]) and len(self._rounds) < self._rounds_total:
                await self._new_round()

    def export(self) -> dict:
        """Everything recorded about the tournament, for analysis and writing: players, rounds with every game
        move by move (who decided, tokens, seconds, cost, illegal answers, the probabilities a System One model
        gave), results, byes, standings with the tie-breaks and what each player spent."""
        known = {model.id: model for model in self._registry.list(self._credentials, self._session)}

        def participant(model_id: str) -> dict:
            model = known.get(model_id)
            return {
                "id": model_id,
                "name": self._registry.name_of(model_id),
                "kind": "human" if model_id == HUMAN else model.kind if model else "unknown",
                "upstream": model.upstream if model else "",
                "provider": model.provider if model else "",
            }

        return {
            "format": "system-one-chess tournament",
            "id": self._id,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "system": "Swiss",
            "rounds_total": self._rounds_total,
            "done": self.done,
            "participants": [participant(model_id) for model_id in self._participants],
            "rounds": [
                {
                    "round": number,
                    "bye": round_["bye"],
                    "games": [
                        {
                            "board": board,
                            "white": entry["white"],
                            "black": entry["black"],
                            "result": entry["result"],
                            "game_id": entry["game_id"],
                            "pgn": entry.get("pgn", ""),
                            **(entry.get("record") or {}),
                        }
                        for board, entry in enumerate(round_["pairings"], 1)
                    ],
                }
                for number, round_ in enumerate(self._rounds, 1)
            ],
            "standings": self._table() if self._rounds else [],
        }

    def pgn(self) -> str:
        """Every finished game of the tournament, in the order played, as one PGN file."""
        return "\n".join(entry["pgn"] for round_ in self._rounds for entry in round_["pairings"] if entry.get("pgn"))

    def _table(self) -> list[dict]:
        """The standings with the usual Swiss tie-breaks, in the order they decide: Buchholz Cut 1 (the points of
        the opponents faced, without the lowest), full Buchholz, Buchholz Cut 2, Sonneborn-Berger (the points of
        the opponents beaten plus half the points of those drawn), then wins. A bye adds no opponent."""
        rows = self._standings.table()
        points = {row["id"]: row["points"] for row in rows}
        for row in rows:
            faced = sorted(points[opponent] for opponent in self._opponents.get(row["id"], []))
            row["buchholz"] = sum(faced)
            row["buchholz_cut1"] = sum(faced[1:]) if faced else 0.0
            row["buchholz_cut2"] = sum(faced[2:]) if len(faced) > 1 else 0.0
            row["sonneborn_berger"] = sum(
                points[opponent] * earned for opponent, earned in self._scores.get(row["id"], [])
            )
        rows.sort(
            key=lambda row: (
                -row["points"],
                -row["buchholz_cut1"],
                -row["buchholz"],
                -row["buchholz_cut2"],
                -row["sonneborn_berger"],
                -row["wins"],
                row["cost_usd"],
                row["name"],
            )
        )
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows

    async def view(self) -> dict:
        def player(model_id: str | None) -> dict | None:
            if model_id is None:
                return None
            return {"id": model_id, "name": self._registry.name_of(model_id), "logo": self._registry.logo_of(model_id)}

        async def board(number: int, entry: dict) -> dict:
            state = await entry["game"].snapshot()
            usage = state["usage_by_colour"]
            return {
                "board": number,
                "white": player(entry["white"]),
                "black": player(entry["black"]),
                "result": entry["result"],
                "game_id": entry["game_id"],
                "fen": state["fen"],
                "turn": state["turn"],
                "ply": len(state["moves_uci"]),
                "last_move": state["last_move"],
                "check": state["check"],
                "over": state["over"],
                "human": state["human"],
                "humans_turn": state["humans_turn"],
                "clock": {"white": usage["white"]["seconds"], "black": usage["black"]["seconds"]},
                "cost": usage["white"]["cost_usd"] + usage["black"]["cost_usd"],
                "thinking_seconds": state["thinking_seconds"],
                "thinking_since": entry["thinking_since"],
                "forfeited": entry["result"] is not None and (state["result"] or "").endswith("illegal moves"),
                "error": entry["error"],
            }

        rounds = []
        for round_ in self._rounds:
            boards = [await board(number, entry) for number, entry in enumerate(round_["pairings"], 1)]
            rounds.append({"pairings": boards, "bye": player(round_["bye"])})
        current = rounds[-1]["pairings"] if rounds else []
        human_board = next((b["board"] for b in current if b["human"] != "none"), None)
        return {
            "id": self._id,
            "active": self.active,
            "done": self.done,
            "rounds_total": self._rounds_total,
            "round": len(self._rounds),
            "boards_total": len(current),
            "boards_finished": sum(1 for b in current if b["result"] is not None),
            "human_board": human_board,
            "elapsed": time.time() - self._started_at if self._started_at else 0.0,
            "now": time.time(),
            "finished_games": sum(1 for r in self._rounds for e in r["pairings"] if e["result"] is not None),
            "rounds": rounds,
            "standings": self._table() if self._rounds else [],
        }


def colour_name(color: chess.Color | None) -> str | None:
    return None if color is None else ("white" if color == chess.WHITE else "black")
