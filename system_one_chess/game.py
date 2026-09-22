import asyncio
import time
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

    def _reset(self, human: str, white: str = "jev", black: str = "jev", time_limit: float | None = None) -> None:
        self._id = uuid.uuid4().hex[:8]
        self._board = chess.Board()
        self._human = human
        self._models = {chess.WHITE: white, chess.BLACK: black}
        self._time_limit = time_limit
        self._timed_out: chess.Color | None = None
        self._forfeited: chess.Color | None = None
        self._pardons = 0
        self._illegal = {chess.WHITE: 0, chess.BLACK: 0}
        self._jev_top: list[dict] = []
        self._moves: list[dict] = []
        self._deciding = False
        self._turn_started = time.monotonic()
        self._usage = self._empty_usage()
        self._usage_by_colour = {chess.WHITE: self._empty_usage(), chess.BLACK: self._empty_usage()}

    async def new(self, human: str, white: str = "jev", black: str = "jev", time_limit: float | None = None) -> dict:
        """`time_limit` is the seconds of deciding time each side has for the whole game; over it, it loses."""
        if human not in COLORS:
            raise IllegalMove(f"unknown color: {human!r}")
        known = {model.id for model in self._registry.list(self._credentials, self._session_models)}
        if white not in known or black not in known:
            raise IllegalMove(f"unknown model: {white!r}, {black!r}")
        async with self._lock:
            self._reset(human, white, black, time_limit)
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
            seconds = time.monotonic() - self._turn_started
            self._usage_by_colour[board.turn]["seconds"] += seconds
            self._check_time(board.turn)
            self._moves.append(
                {
                    "ply": len(board.move_stack) + 1,
                    "colour": self._colour_name(board.turn),
                    "player": "human",
                    "san": board.san(move),
                    "seconds": seconds,
                }
            )
            board.push(move)
            self._turn_started = time.monotonic()
            self._finish()
            return self._snapshot()

    async def jev_move(self) -> dict:
        """One model move. The lock is held only to read and to write the position, never during the call to
        the model, so the state stays readable while a model thinks; a game changed meanwhile is not touched."""
        async with self._lock:
            board = self._board
            if self._over() or board.turn in COLORS[self._human]:
                raise IllegalMove("it is not Jev's turn")
            if self._deciding:
                raise IllegalMove("the model is already deciding")
            self._deciding = True
            game_id, ply = self._id, len(board.move_stack)
            position, model, illegal = board.copy(), self._models[board.turn], self._illegal[board.turn]
        try:
            try:
                decision = await self._chooser.choose(position, self._credentials, model=model, illegal_so_far=illegal)
            except Forfeit as e:
                async with self._lock:
                    self._still(game_id, ply)
                    self._count(e.usage)
                    self._moves.append({**self._move_record(self._board, e.usage), "san": None, "forfeit": True})
                    self._forfeited = self._board.turn
                    self._finish()
                    return self._snapshot()
            async with self._lock:
                self._still(game_id, ply)
                self._jev_top = [{"san": san, "probability": p} for san, p in decision.top]
                self._count(decision)
                self._moves.append(
                    {**self._move_record(self._board, decision), "san": decision.san, "top": decision.top}
                )
                self._board.push(decision.move)
                self._turn_started = time.monotonic()
                self._finish()
                return self._snapshot()
        finally:
            self._deciding = False

    def _still(self, game_id: str, ply: int) -> None:
        if self._id != game_id or len(self._board.move_stack) != ply:
            raise IllegalMove("the game changed while the model was deciding")

    def to_record(self) -> dict:
        """Everything needed to rebuild this game later, in plain JSON."""
        return {
            "id": self._id,
            "human": self._human,
            "models": {"white": self._models[chess.WHITE], "black": self._models[chess.BLACK]},
            "moves_uci": [move.uci() for move in self._board.move_stack],
            "forfeited": self._colour_name(self._forfeited) if self._forfeited is not None else None,
            "illegal": {"white": self._illegal[chess.WHITE], "black": self._illegal[chess.BLACK]},
            "usage": dict(self._usage),
            "usage_by_colour": {
                "white": dict(self._usage_by_colour[chess.WHITE]),
                "black": dict(self._usage_by_colour[chess.BLACK]),
            },
            "moves": [dict(move) for move in self._moves],
            "jev_top": list(self._jev_top),
            "pardons": self._pardons,
            "time_limit": self._time_limit,
            "timed_out": self._colour_name(self._timed_out) if self._timed_out is not None else None,
        }

    def restore(self, record: dict) -> None:
        colours = {"white": chess.WHITE, "black": chess.BLACK}
        self._reset(record["human"], record["models"]["white"], record["models"]["black"], record.get("time_limit"))
        self._timed_out = colours.get(record.get("timed_out"))
        self._id = record["id"]
        for uci in record["moves_uci"]:
            self._board.push_uci(uci)
        self._forfeited = colours.get(record.get("forfeited"))
        self._illegal = {colours[name]: n for name, n in record["illegal"].items()}
        self._usage = dict(record["usage"])
        self._usage_by_colour = {colours[name]: dict(u) for name, u in record["usage_by_colour"].items()}
        self._moves = [dict(move) for move in record["moves"]]
        self._jev_top = list(record.get("jev_top", []))
        self._pardons = int(record.get("pardons", 0))

    def _players(self) -> dict[chess.Color, str]:
        """Who sat at each colour for the rankings: a model id, or `human` for the seat the browser played."""
        return {
            color: "human" if color in COLORS[self._human] else self._models[color]
            for color in (chess.WHITE, chess.BLACK)
        }

    def _finish(self) -> None:
        """Once a game is over it goes into the session ranking."""
        if not self._over():
            return
        self._standings.record(
            self._id, self._players(), self._result() or "*", self._forfeited, self._illegal, self._usage_by_colour
        )

    async def pardon(self) -> dict:
        """Lets a game lost by illegal moves go on: the loser's illegal count starts again from zero. The forfeit
        already counted in the ranking; from here the game counts as a new one."""
        async with self._lock:
            if self._forfeited is None:
                raise IllegalMove("nobody has lost by illegal moves")
            colour = self._forfeited
            self._forfeited = None
            self._illegal[colour] = 0
            self._pardons += 1
            self._id = f"{self._id.split('p')[0]}p{self._pardons}"
            self._turn_started = time.monotonic()
            return self._snapshot()

    async def outcome(self) -> dict:
        """What a ranking needs to know about the current game, in the terms `Standings.record` takes."""
        async with self._lock:
            return {
                "game_id": self._id,
                "over": self._over(),
                "players": self._players(),
                "result": self._result() or "*",
                "forfeited": self._forfeited,
                "illegal": dict(self._illegal),
                "usage": {color: dict(totals) for color, totals in self._usage_by_colour.items()},
                "moves": [dict(move) for move in self._moves],
                "fen": self._board.fen(),
            }

    @staticmethod
    def _colour_name(color: chess.Color) -> str:
        return "white" if color == chess.WHITE else "black"

    def _move_record(self, board: chess.Board, usage) -> dict:
        """One line of the game log: who decided, what it cost and how long it took."""
        return {
            "ply": len(board.move_stack) + 1,
            "colour": self._colour_name(board.turn),
            "player": self._models[board.turn],
            "seconds": usage.seconds,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
            "illegal": usage.illegal,
            "illegal_answers": list(usage.illegal_answers),
        }

    @staticmethod
    def _empty_usage() -> dict:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0, "illegal": 0}

    def _count(self, usage) -> None:
        for totals in (self._usage, self._usage_by_colour[self._board.turn]):
            totals["calls"] += 1
            totals["input_tokens"] += usage.input_tokens
            totals["output_tokens"] += usage.output_tokens
            totals["cost_usd"] += usage.cost_usd
            totals["seconds"] += usage.seconds
            totals["illegal"] += usage.illegal
        self._illegal[self._board.turn] += usage.illegal
        self._check_time(self._board.turn)

    def _check_time(self, color: chess.Color) -> None:
        """A side whose deciding time passes the limit loses on time, once the move it was making is counted."""
        if self._time_limit is None or self._timed_out is not None or self._forfeited is not None:
            return
        if self._usage_by_colour[color]["seconds"] > self._time_limit and not self._board.is_game_over(claim_draw=True):
            self._timed_out = color

    def _over(self) -> bool:
        return self._forfeited is not None or self._timed_out is not None or self._board.is_game_over(claim_draw=True)

    def _loser(self) -> chess.Color | None:
        return self._forfeited if self._forfeited is not None else self._timed_out

    def _result(self) -> str | None:
        loser = self._loser()
        if loser is not None:
            return "0-1" if loser == chess.WHITE else "1-0"
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
            elif self._timed_out is not None:
                game.headers["Termination"] = "time forfeit"
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
        fens = [chess.STARTING_FEN]
        replay = chess.Board()
        for move in board.move_stack:
            history.append(replay.san(move))
            replay.push(move)
            fens.append(replay.fen())
        last = board.peek() if board.move_stack else None
        outcome = board.outcome(claim_draw=True)
        result = None
        if self._forfeited is not None:
            result = f"{self._result()} by illegal moves"
        elif self._timed_out is not None:
            result = f"{self._result()} on time"
        elif outcome:
            result = f"{board.result(claim_draw=True)} by {outcome.termination.name.lower().replace('_', ' ')}"
        return {
            "game_id": self._id,
            "fen": board.fen(),
            "turn": "white" if board.turn else "black",
            "human": self._human,
            "models": {"white": self._models[chess.WHITE], "black": self._models[chess.BLACK]},
            "illegal": {"white": self._illegal[chess.WHITE], "black": self._illegal[chess.BLACK]},
            "illegal_attempts": [
                {
                    "ply": move["ply"],
                    "colour": move["colour"],
                    "player": move["player"],
                    "answers": move["illegal_answers"],
                }
                for move in self._moves
                if move.get("illegal_answers")
            ],
            "pardons": self._pardons,
            "humans_turn": humans_turn,
            "jevs_turn": not over and not humans_turn,
            "dests": dests,
            "last_move": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
            "check": board.is_check(),
            "over": over,
            "result": result,
            "history": history,
            "moves_uci": [move.uci() for move in board.move_stack],
            "fens": fens,
            "thinking_seconds": 0.0 if over else time.monotonic() - self._turn_started,
            "time_limit": self._time_limit,
            "usage": dict(self._usage),
            "usage_by_colour": {
                "white": dict(self._usage_by_colour[chess.WHITE]),
                "black": dict(self._usage_by_colour[chess.BLACK]),
            },
            "jev_top": self._jev_top,
        }
