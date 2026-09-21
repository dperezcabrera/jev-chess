import asyncio
import uuid

import chess
from pico_ioc import component

from .jev import JevMoveChooser

COLORS = {"white": {chess.WHITE}, "black": {chess.BLACK}, "none": set()}


class IllegalMove(Exception):
    pass


@component(scope="session")
class Game:
    def __init__(self, chooser: JevMoveChooser):
        self._chooser = chooser
        self._lock = asyncio.Lock()
        self._reset("white")

    def _reset(self, human: str) -> None:
        self._id = uuid.uuid4().hex[:8]
        self._board = chess.Board()
        self._human = human
        self._jev_top: list[dict] = []

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
            move, top = await self._chooser.choose(board)
            self._jev_top = [{"san": san, "probability": p} for san, p in top]
            board.push(move)
            return self._snapshot()

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
            "jev_top": self._jev_top,
        }
