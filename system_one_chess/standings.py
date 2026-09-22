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

    def record(
        self,
        game_id: str,
        players: dict[chess.Color, str],
        result: str,
        forfeited: chess.Color | None,
        illegal: dict[chess.Color, int],
        cost: dict[chess.Color, float],
    ) -> None:
        if game_id in self._recorded or result not in POINTS:
            return
        self._recorded.add(game_id)
        for color, points in zip((chess.WHITE, chess.BLACK), POINTS[result]):
            row = self._rows.setdefault(
                players[color],
                {
                    "games": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "points": 0.0,
                    "forfeits": 0,
                    "illegal": 0,
                    "cost_usd": 0.0,
                },
            )
            row["games"] += 1
            row["points"] += points
            row["wins" if points == 1.0 else "draws" if points == 0.5 else "losses"] += 1
            row["forfeits"] += int(forfeited == color)
            row["illegal"] += illegal[color]
            row["cost_usd"] += cost[color]

    def table(self) -> list[dict]:
        rows = [
            {"id": model_id, "name": self._registry.name_of(model_id), "logo": self._registry.logo_of(model_id), **row}
            for model_id, row in self._rows.items()
        ]
        rows.sort(key=lambda row: (-row["points"], row["games"], row["cost_usd"], row["name"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        return rows
