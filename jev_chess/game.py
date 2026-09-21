import asyncio
import uuid
from datetime import UTC, datetime

import chess
import chess.pgn
from pico_ioc import component

from .jev import JevMoveChooser
from .provider import SessionCredentials

COLORS = {"white": {chess.WHITE}, "black": {chess.BLACK}, "none": set()}


class IllegalMove(Exception):
    pass


@component(scope="session")
class Game:
    def __init__(self, chooser: JevMoveChooser, credentials: SessionCredentials):
        self._chooser = chooser
        self._credentials = credentials
        self._lock = asyncio.Lock()
        self._reset("white")

    def _reset(self, human: str) -> None:
        self._id = uuid.uuid4().hex[:8]
        self._board = chess.Board()
        self._human = human
        self._jev_top: list[dict] = []
        self._usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0}

    async def new(self, human: str) -> dict:
        if human not in COLORS:
            raise IllegalMove(f"unknown color: {human!r}")
        async with self._lock:
            self._reset(human)
            return self._snapshot()

    async def snapshot(self) -> dict:
        async with self._lock:
            return self._snapshot()

    async def human_move(self, origin: str, target: str, promotion: str = "q") -> dict:
        async with self._lock:
            board = self._board
            if board.is_game_over(claim_draw=True) or board.turn not in COLORS[self._human]:
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
            return self._snapshot()

    async def jev_move(self) -> dict:
        async with self._lock:
            board = self._board
            if board.is_game_over(claim_draw=True) or board.turn in COLORS[self._human]:
                raise IllegalMove("it is not Jev's turn")
            decision = await self._chooser.choose(board, self._credentials)
            self._jev_top = [{"san": san, "probability": p} for san, p in decision.top]
            self._usage["calls"] += 1
            self._usage["input_tokens"] += decision.input_tokens
            self._usage["output_tokens"] += decision.output_tokens
            self._usage["cost_usd"] += decision.cost_usd
            self._usage["seconds"] += decision.seconds
            board.push(decision.move)
            return self._snapshot()

    async def pgn(self) -> tuple[str, str]:
        async with self._lock:
            game = chess.pgn.Game.from_board(self._board)
            jev = f"Jev ({self._chooser.model_for(self._credentials)})"
            game.headers["Event"] = "jev-chess"
            game.headers["Site"] = "https://github.com/dperezcabrera/jev-chess"
            game.headers["Date"] = datetime.now(UTC).strftime("%Y.%m.%d")
            game.headers["White"] = "Human" if self._human == "white" else jev
            game.headers["Black"] = "Human" if self._human == "black" else jev
            game.headers["Result"] = self._board.result(claim_draw=True)
            return f"jev-chess-{self._id}.pgn", str(game) + "\n"

    def _snapshot(self) -> dict:
        board = self._board
        over = board.is_game_over(claim_draw=True)
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
        if outcome:
            result = f"{board.result(claim_draw=True)} by {outcome.termination.name.lower().replace('_', ' ')}"
        return {
            "game_id": self._id,
            "fen": board.fen(),
            "turn": "white" if board.turn else "black",
            "human": self._human,
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
