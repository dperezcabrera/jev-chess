import asyncio
import uuid
from datetime import UTC, datetime

import chess
import chess.pgn
from pico_ioc import component

from .jev import Forfeit, JevMoveChooser
from .models import ModelRegistry, SessionModels
from .provider import SessionCredentials
from .standings import Standings

COLORS = {"white": {chess.WHITE}, "black": {chess.BLACK}, "none": set()}


class IllegalMove(Exception):
    pass


@component(scope="session")
class Game:
    def __init__(
        self,
        chooser: JevMoveChooser,
        credentials: SessionCredentials,
        registry: ModelRegistry,
        session_models: SessionModels,
        standings: Standings,
    ):
        self._chooser = chooser
        self._standings = standings
        self._credentials = credentials
        self._registry = registry
        self._session_models = session_models
        self._lock = asyncio.Lock()
        self._reset("white")

    def _reset(self, human: str, white: str = "jev", black: str = "jev") -> None:
        self._id = uuid.uuid4().hex[:8]
        self._board = chess.Board()
        self._human = human
        self._models = {chess.WHITE: white, chess.BLACK: black}
        self._forfeited: chess.Color | None = None
        self._illegal = {chess.WHITE: 0, chess.BLACK: 0}
        self._cost = {chess.WHITE: 0.0, chess.BLACK: 0.0}
        self._jev_top: list[dict] = []
        self._usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0, "illegal": 0}

    async def new(self, human: str, white: str = "jev", black: str = "jev") -> dict:
        if human not in COLORS:
            raise IllegalMove(f"unknown color: {human!r}")
        known = {model.id for model in self._registry.list(self._credentials, self._session_models)}
        if white not in known or black not in known:
            raise IllegalMove(f"unknown model: {white!r}, {black!r}")
        async with self._lock:
            self._reset(human, white, black)
            return self._snapshot()

    async def snapshot(self) -> dict:
        async with self._lock:
            return self._snapshot()

    async def human_move(self, origin: str, target: str, promotion: str = "q") -> dict:
        async with self._lock:
            board = self._board
            if self._over() or board.turn not in COLORS[self._human]:
                raise IllegalMove("it is not your turn")
            try:
                move = chess.Move.from_uci(f"{origin}{target}")
                if move not in board.legal_moves:
                    move = chess.Move.from_uci(f"{origin}{target}{promotion}")
            except ValueError as e:
                raise IllegalMove("malformed move") from e
            if move not in board.legal_moves:
                raise IllegalMove(f"illegal move: {origin}{target}")
            board.push(move)
            self._finish()
            return self._snapshot()

    async def jev_move(self) -> dict:
        async with self._lock:
            board = self._board
            if self._over() or board.turn in COLORS[self._human]:
                raise IllegalMove("it is not Jev's turn")
            try:
                decision = await self._chooser.choose(
                    board, self._credentials, model=self._models[board.turn], illegal_so_far=self._illegal[board.turn]
                )
            except Forfeit as e:
                self._count(e.usage)
                self._forfeited = board.turn
                self._finish()
                return self._snapshot()
            self._jev_top = [{"san": san, "probability": p} for san, p in decision.top]
            self._count(decision)
            board.push(decision.move)
            self._finish()
            return self._snapshot()

    def _finish(self) -> None:
        """Once a game is over it goes into the session ranking; a human seat counts as the player `human`."""
        if not self._over():
            return
        players = {
            color: "human" if color in COLORS[self._human] else self._models[color]
            for color in (chess.WHITE, chess.BLACK)
        }
        self._standings.record(self._id, players, self._result() or "*", self._forfeited, self._illegal, self._cost)

    def _count(self, usage) -> None:
        self._usage["calls"] += 1
        self._usage["input_tokens"] += usage.input_tokens
        self._usage["output_tokens"] += usage.output_tokens
        self._usage["cost_usd"] += usage.cost_usd
        self._usage["seconds"] += usage.seconds
        self._usage["illegal"] += usage.illegal
        self._illegal[self._board.turn] += usage.illegal
        self._cost[self._board.turn] += usage.cost_usd

    def _over(self) -> bool:
        return self._forfeited is not None or self._board.is_game_over(claim_draw=True)

    def _result(self) -> str | None:
        if self._forfeited is not None:
            return "0-1" if self._forfeited == chess.WHITE else "1-0"
        return self._board.result(claim_draw=True) if self._board.is_game_over(claim_draw=True) else None

    async def pgn(self) -> tuple[str, str]:
        async with self._lock:
            game = chess.pgn.Game.from_board(self._board)

            def player(color):
                model = self._registry.get(self._models[color], self._credentials, self._session_models)
                return f"{model.name} ({model.upstream})"

            game.headers["Event"] = "system-one-chess"
            game.headers["Site"] = "https://github.com/dperezcabrera/system-one-chess"
            game.headers["Date"] = datetime.now(UTC).strftime("%Y.%m.%d")
            game.headers["White"] = "Human" if self._human == "white" else player(chess.WHITE)
            game.headers["Black"] = "Human" if self._human == "black" else player(chess.BLACK)
            game.headers["Result"] = self._result() or "*"
            if self._forfeited is not None:
                game.headers["Termination"] = "illegal moves"
            return f"system-one-chess-{self._id}.pgn", str(game) + "\n"

    def _snapshot(self) -> dict:
        board = self._board
        over = self._over()
        humans_turn = not over and board.turn in COLORS[self._human]
        dests: dict[str, list[str]] = {}
        if humans_turn:
            for move in board.legal_moves:
                dests.setdefault(chess.square_name(move.from_square), []).append(chess.square_name(move.to_square))
        history = []
        replay = chess.Board()
        for move in board.move_stack:
            history.append(replay.san(move))
            replay.push(move)
        last = board.peek() if board.move_stack else None
        outcome = board.outcome(claim_draw=True)
        result = None
        if self._forfeited is not None:
            result = f"{self._result()} by illegal moves"
        elif outcome:
            result = f"{board.result(claim_draw=True)} by {outcome.termination.name.lower().replace('_', ' ')}"
        return {
            "game_id": self._id,
            "fen": board.fen(),
            "turn": "white" if board.turn else "black",
            "human": self._human,
            "models": {"white": self._models[chess.WHITE], "black": self._models[chess.BLACK]},
            "illegal": {"white": self._illegal[chess.WHITE], "black": self._illegal[chess.BLACK]},
            "humans_turn": humans_turn,
            "jevs_turn": not over and not humans_turn,
            "dests": dests,
            "last_move": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
            "check": board.is_check(),
            "over": over,
            "result": result,
            "history": history,
            "moves_uci": [move.uci() for move in board.move_stack],
            "usage": dict(self._usage),
            "jev_top": self._jev_top,
        }
