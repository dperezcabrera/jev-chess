"""The ranking of a browser session: every finished game, tallied per model and for you."""

import chess
from pico_ioc import component

from .models import ModelRegistry

POINTS = {"1-0": (1.0, 0.0), "0-1": (0.0, 1.0), "1/2-1/2": (0.5, 0.5)}


@component(scope="session")
class Standings:
    def __init__(self, registry: ModelRegistry):
        self._registry = registry
        self._rows: dict[str, dict] = {}
        self._recorded: set[str] = set()

    def to_record(self) -> dict:
        return {
            "rows": {model_id: dict(row) for model_id, row in self._rows.items()},
            "recorded": sorted(self._recorded),
        }

    def restore(self, record: dict) -> None:
        self._rows = {model_id: {**self._empty(), **row} for model_id, row in record["rows"].items()}
        self._recorded = set(record["recorded"])

    def ensure(self, model_id: str) -> None:
        self._rows.setdefault(model_id, self._empty())

    def bye(self, model_id: str) -> None:
        """A round without an opponent scores a full point, as in most Swiss events, and is not a game."""
        row = self._rows.setdefault(model_id, self._empty())
        row["points"] += 1.0
        row["byes"] += 1

    def bye_back(self, model_id: str) -> None:
        """Takes a bye away again, when the player it went to found an opponent after all."""
        row = self._rows.setdefault(model_id, self._empty())
        row["points"] -= 1.0
        row["byes"] -= 1

    @staticmethod
    def _empty() -> dict:
        return {
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "points": 0.0,
            "byes": 0,
            "forfeits": 0,
            "illegal": 0,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "seconds": 0.0,
            "cost_usd": 0.0,
        }

    def record(
        self,
        game_id: str,
        players: dict[chess.Color, str],
        result: str,
        forfeited: chess.Color | None,
        illegal: dict[chess.Color, int],
        usage: dict[chess.Color, dict],
    ) -> None:
        """`usage` holds, per colour, the calls, tokens, seconds and cost that side spent on the game."""
        if game_id in self._recorded or result not in POINTS:
            return
        self._recorded.add(game_id)
        for color, points in zip((chess.WHITE, chess.BLACK), POINTS[result]):
            row = self._rows.setdefault(players[color], self._empty())
            row["games"] += 1
            row["points"] += points
            row["wins" if points == 1.0 else "draws" if points == 0.5 else "losses"] += 1
            row["forfeits"] += int(forfeited == color)
            row["illegal"] += illegal[color]
            for key in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd"):
                row[key] += usage[color][key]

    def unrecord(
        self,
        game_id: str,
        players: dict[chess.Color, str],
        result: str,
        forfeited: chess.Color | None,
        illegal: dict[chess.Color, int],
        usage: dict[chess.Color, dict],
    ) -> None:
        """Takes a recorded game out again, with the same arguments, so it can be played on."""
        if game_id not in self._recorded or result not in POINTS:
            return
        self._recorded.discard(game_id)
        for color, points in zip((chess.WHITE, chess.BLACK), POINTS[result]):
            row = self._rows[players[color]]
            row["games"] -= 1
            row["points"] -= points
            row["wins" if points == 1.0 else "draws" if points == 0.5 else "losses"] -= 1
            row["forfeits"] -= int(forfeited == color)
            row["illegal"] -= illegal[color]
            for key in ("calls", "input_tokens", "output_tokens", "seconds", "cost_usd"):
                row[key] -= usage[color][key]

    def table(self) -> list[dict]:
        rows = [
            {"id": model_id, "name": self._registry.name_of(model_id), "logo": self._registry.logo_of(model_id), **row}
            for model_id, row in self._rows.items()
        ]
        rows.sort(key=lambda row: (-row["points"], row["games"], row["cost_usd"], row["name"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows
