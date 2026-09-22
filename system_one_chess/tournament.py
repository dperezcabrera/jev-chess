"""A Swiss tournament for the session: a fixed number of rounds, each pairing players on equal scores.

Games between models play themselves; a game with you waits for your moves. The next game starts when the
browser asks for it, so a result stays on screen until then. With an odd number of players one gets a bye."""

from pico_ioc import component

from .game import Game, IllegalMove
from .models import ModelRegistry, SessionModels
from .provider import SessionCredentials
from .standings import Standings

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
    def __init__(self, game: Game, registry: ModelRegistry, credentials: SessionCredentials, session: SessionModels):
        self._game = game
        self._registry = registry
        self._credentials = credentials
        self._session = session
        self._standings = Standings(registry)
        self._participants: list[str] = []
        self._rounds_total = 0
        self._rounds: list[dict] = []
        self._pairing = 0
        self._played: set[frozenset] = set()
        self._balance: dict[str, int] = {}
        self._byes: set[str] = set()
        self._opponents: dict[str, list[str]] = {}

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
        self._participants = ids
        self._rounds_total = rounds
        self._rounds = []
        self._pairing = 0
        self._played = set()
        self._balance = dict.fromkeys(ids, 0)
        self._byes = set()
        self._opponents = {player: [] for player in ids}
        self._standings = Standings(self._registry)
        for player in ids:
            self._standings.ensure(player)
        self._new_round()
        return await self._begin()

    async def next(self) -> dict:
        """Records the game that just ended and starts the following one, pairing a new round when needed."""
        if not self.active:
            raise IllegalMove("no tournament is running")
        entry = self._current()
        outcome = await self._game.outcome()
        if outcome["game_id"] != entry["game_id"]:
            raise IllegalMove("the tournament game was replaced by another game")
        if not outcome["over"]:
            raise IllegalMove("the current game is not over")
        self._standings.record(
            outcome["game_id"],
            outcome["players"],
            outcome["result"],
            outcome["forfeited"],
            outcome["illegal"],
            outcome["cost"],
        )
        entry["result"] = outcome["result"]
        self._pairing += 1
        if self._pairing >= len(self._rounds[-1]["pairings"]):
            if len(self._rounds) >= self._rounds_total:
                return await self._game.snapshot()
            self._new_round()
        return await self._begin()

    def stop(self) -> None:
        self._rounds = []
        self._rounds_total = 0
        self._participants = []

    @property
    def active(self) -> bool:
        return bool(self._rounds) and not self.done

    @property
    def done(self) -> bool:
        return (
            bool(self._rounds)
            and len(self._rounds) >= self._rounds_total
            and self._pairing >= len(self._rounds[-1]["pairings"])
        )

    def _current(self) -> dict:
        return self._rounds[-1]["pairings"][self._pairing]

    def _new_round(self) -> None:
        order = self._participants if not self._rounds else [row["id"] for row in self._table()]
        pairs, bye = pair_round(order, self._played, self._balance, self._byes)
        for white, black in pairs:
            self._played.add(frozenset((white, black)))
            self._balance[white] += 1
            self._balance[black] -= 1
            self._opponents[white].append(black)
            self._opponents[black].append(white)
        if bye is not None:
            self._byes.add(bye)
            self._standings.bye(bye)
        self._rounds.append(
            {"pairings": [{"white": w, "black": b, "game_id": None, "result": None} for w, b in pairs], "bye": bye}
        )
        self._pairing = 0

    async def _begin(self) -> dict:
        entry = self._current()
        human = "white" if entry["white"] == HUMAN else "black" if entry["black"] == HUMAN else "none"
        white = entry["white"] if entry["white"] != HUMAN else entry["black"]
        black = entry["black"] if entry["black"] != HUMAN else entry["white"]
        state = await self._game.new(human, white, black)
        entry["game_id"] = state["game_id"]
        return state

    def _table(self) -> list[dict]:
        """The standings with the Buchholz tie-break: the sum of the points of everyone a player has faced."""
        rows = self._standings.table()
        points = {row["id"]: row["points"] for row in rows}
        for row in rows:
            row["buchholz"] = sum(points[opponent] for opponent in self._opponents.get(row["id"], []))
        rows.sort(key=lambda row: (-row["points"], -row["buchholz"], row["cost_usd"], row["name"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows

    def view(self) -> dict:
        def player(model_id: str | None) -> dict | None:
            if model_id is None:
                return None
            return {"id": model_id, "name": self._registry.name_of(model_id), "logo": self._registry.logo_of(model_id)}

        current = self._current() if self.active else None
        return {
            "active": self.active,
            "done": self.done,
            "rounds_total": self._rounds_total,
            "round": len(self._rounds),
            "game": self._pairing + 1 if self.active else None,
            "games_in_round": len(self._rounds[-1]["pairings"]) if self._rounds else 0,
            "current_game_id": current["game_id"] if current else None,
            "rounds": [
                {
                    "pairings": [
                        {"white": player(p["white"]), "black": player(p["black"]), "result": p["result"]}
                        for p in round_["pairings"]
                    ],
                    "bye": player(round_["bye"]),
                }
                for round_ in self._rounds
            ],
            "standings": self._table() if self._rounds else [],
        }
