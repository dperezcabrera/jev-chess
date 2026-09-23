import asyncio
import time
from dataclasses import dataclass

import chess
import httpx
from pico_ioc import cleanup, component

from .kev import NOT_CONFIGURED as KEV_NOT_CONFIGURED
from .kev import KevModel
from .laya import LayaModel
from .llm import IllegalAnswers, LLMApi, LLMError
from .models import ModelRegistry
from .provider import NO_KEY, Gateway, JevProvider, SessionCredentials
from .retry import post_with_retries
from .settings import IllegalMovesSettings


class JevError(Exception):
    pass


class Forfeit(JevError):
    """The side to move reached its second illegal answer of the game and loses; `usage` is what it cost."""

    def __init__(self, message: str, usage: "Answer"):
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True)
class Answer:
    choice: str
    probabilities: dict[str, float]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float
    illegal: int = 0
    illegal_answers: tuple[str, ...] = ()
    call: dict | None = None


@dataclass(frozen=True)
class Decision:
    move: chess.Move
    san: str
    top: list[tuple[str, float]]
    probabilities: dict[str, float]
    asked: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float
    illegal: int = 0
    illegal_answers: tuple[str, ...] = ()
    call: dict | None = None
    forced: bool = False


@component
class JevApi:
    def __init__(self):
        self._client = httpx.AsyncClient()

    async def system_one(self, gateway: Gateway, body: dict) -> dict:
        response = await post_with_retries(
            self._client,
            f"{gateway.base_url}/v1/systemone",
            json=body,
            headers={"Authorization": f"Bearer {gateway.api_key}"},
            timeout=gateway.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    @cleanup
    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())


def describe(board: chess.Board, move: chess.Move) -> str:
    if board.is_castling(move):
        text = "castle kingside" if board.is_kingside_castling(move) else "castle queenside"
    else:
        piece = chess.piece_name(board.piece_type_at(move.from_square))
        text = f"{piece} {chess.square_name(move.from_square)} to {chess.square_name(move.to_square)}"
        if board.is_en_passant(move):
            text += ", captures pawn en passant"
        elif board.is_capture(move):
            text += f", captures {chess.piece_name(board.piece_type_at(move.to_square))}"
        if move.promotion:
            text += f", promotes to {chess.piece_name(move.promotion)}"
    board.push(move)
    if board.is_checkmate():
        text += ", CHECKMATE"
    elif board.is_stalemate():
        text += ", STALEMATE: the game ends in a draw"
    elif board.is_repetition(3):
        text += ", third repetition of the position: the game ends in a DRAW"
    elif board.is_insufficient_material():
        text += ", DRAW by insufficient material"
    elif board.is_fifty_moves():
        text += ", DRAW by the fifty-move rule"
    else:
        if board.is_check():
            text += ", gives check"
        if board.is_repetition(2):
            text += ", repeats an earlier position (a third time would be a draw)"
    if board.is_attacked_by(board.turn, move.to_square):
        text += ", moved piece can be captured next turn"
    board.pop()
    return text


def move_aliases(board: chess.Board, options: dict[str, chess.Move]) -> dict[str, str]:
    """Other ways an LLM writes a legal move: UCI, no check or mate sign, castling with zeros, `x` left out."""
    aliases: dict[str, str] = {}
    for san, move in options.items():
        for alias in (
            move.uci(),
            san.rstrip("+#"),
            san.replace("O", "0"),
            san.rstrip("+#").replace("O", "0"),
            san.replace("x", ""),
            san.rstrip("+#").replace("x", ""),
            f"{chess.square_name(move.from_square)}-{chess.square_name(move.to_square)}",
        ):
            if alias != san and alias not in options:
                aliases.setdefault(alias, san)
    return aliases


def _state(board: chess.Board) -> dict:
    return {
        "game": "chess",
        "side_to_move": "white" if board.turn else "black",
        "fen": board.fen(),
        "board": str(board),
        "moves_so_far": chess.Board().variation_san(board.move_stack) if board.move_stack else "",
        "material_balance": material_balance(board),
        "halfmoves_since_capture_or_pawn_move": board.halfmove_clock,
    }


PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def material_balance(board: chess.Board) -> str:
    """Material from the side to move's point of view, so a model knows whether a draw is a gift or a loss."""
    own = sum(v * len(board.pieces(p, board.turn)) for p, v in PIECE_VALUES.items())
    other = sum(v * len(board.pieces(p, not board.turn)) for p, v in PIECE_VALUES.items())
    diff = own - other
    if diff == 0:
        return "equal material"
    return f"you are {'up' if diff > 0 else 'down'} {abs(diff)} point{'s' if abs(diff) != 1 else ''} of material"


@component
class JevMoveChooser:
    def __init__(
        self,
        api: JevApi,
        provider: JevProvider,
        laya: LayaModel,
        llm: LLMApi,
        illegal: IllegalMovesSettings,
        registry: ModelRegistry | None = None,
        kev: KevModel | None = None,
    ):
        self._api = api
        self._provider = provider
        self._laya = laya
        self._kev = kev
        self._llm = llm
        self._illegal_limit = max(1, illegal.limit)
        self._registry = registry

    def model_for(self, credentials: SessionCredentials | None = None, model: str = "") -> str:
        if model.startswith("llm:"):
            return model[4:]
        if model == "kev":
            return self._kev.upstream if self._kev else "kev"
        return self._provider.gateway(credentials, model).model

    async def ask(
        self,
        board: chess.Board,
        instructions: str,
        criteria: dict[str, str],
        credentials: SessionCredentials | None = None,
        model: str = "",
        illegal_so_far: int = 0,
        aliases: dict[str, str] | None = None,
    ) -> Answer:
        """One Choice question about a position: `criteria` maps each option label to its description.

        `illegal_so_far` is how many illegal answers this side already gave in the game; `aliases` are other
        spellings of the labels an LLM may use, which do not count as illegal."""
        if model.startswith("llm:"):
            return await self._ask_llm(board, instructions, criteria, credentials, model[4:], illegal_so_far, aliases)
        if model == "kev":
            if self._kev is None or not self._kev.ready:
                raise JevError(KEV_NOT_CONFIGURED)
            gateway = Gateway("kev", "", "", self._kev.upstream, 0.0, False)
        else:
            gateway = self._provider.gateway(credentials, model)
            if not gateway.ready:
                raise JevError(NO_KEY)
        if gateway.local and len(criteria) > 8:
            criteria = dict.fromkeys(criteria)
        state = _state(board)
        question = {"type": "choice", "instructions": instructions, "criteria": criteria}
        started = time.perf_counter()
        try:
            body = {"model": gateway.model, "state": state, "questions": {"move": question}}
            if model == "kev":
                response = await self._kev.system_one(body)
            else:
                response = await (self._laya.system_one(body) if gateway.local else self._api.system_one(gateway, body))
            answer = response["answers"]["move"]
            choice = answer["choice"]
        except httpx.HTTPStatusError as e:
            raise JevError(f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
        except (httpx.HTTPError, KeyError, TypeError, ValueError, RuntimeError) as e:
            raise JevError(f"request to {gateway.name} failed: {e}") from e
        if choice not in criteria:
            raise JevError(f"Jev returned an unknown option: {choice!r}")
        usage = response.get("usage") or {}
        return Answer(
            choice=choice,
            probabilities=answer.get("probabilities") or {},
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=self._provider.cost_usd(response),
            seconds=time.perf_counter() - started,
        )

    async def _ask_llm(
        self, board, instructions, criteria, credentials, upstream, illegal_so_far, aliases=None
    ) -> Answer:
        gateway = self._provider.gateway_for("openrouter", credentials)
        if not gateway.api_key:
            raise JevError("An LLM needs an OpenRouter key. Add one in Settings (the gear icon).")
        attempts = self._illegal_limit - illegal_so_far
        try:
            reasoning = self._registry.reasoning_for(upstream) if self._registry else None
            answer = await self._llm.choose(
                gateway, upstream, _state(board), instructions, criteria, attempts, aliases, reasoning
            )
        except IllegalAnswers as e:
            usage = Answer("", {}, e.input_tokens, e.output_tokens, e.cost_usd, e.seconds, e.illegal, e.answers, e.call)
            raise Forfeit(str(e), usage) from e
        except LLMError as e:
            raise JevError(str(e)) from e
        return Answer(
            choice=answer.choice,
            probabilities={},
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            cost_usd=answer.cost_usd,
            seconds=answer.seconds,
            illegal=answer.illegal,
            illegal_answers=answer.illegal_answers,
            call=getattr(answer, "call", None)
            or {
                "reply": answer.reply,
                "finish_reason": answer.finish_reason,
                "reasoning_chars": answer.reasoning_chars,
                "blanks": answer.blanks,
                "truncated": answer.truncated,
                "schema": answer.schema,
                "reasoning": answer.reasoning,
                "max_tokens": answer.max_tokens,
            }
            if hasattr(answer, "reply")
            else getattr(answer, "call", None),
        )

    async def choose(
        self,
        board: chess.Board,
        credentials: SessionCredentials | None = None,
        order: list[chess.Move] | None = None,
        model: str = "",
        illegal_so_far: int = 0,
    ) -> Decision:
        """Ask Jev for a move; `order` lists the legal moves in the order to offer them, to test whether it matters."""
        moves = list(board.legal_moves)
        if order is not None:
            if sorted(order, key=str) != sorted(moves, key=str):
                raise ValueError("order must contain exactly the legal moves")
            moves = order
        if len(moves) == 1:
            san = board.san(moves[0])
            return Decision(moves[0], san, [(san, 1.0)], {san: 1.0}, [san], 0, 0, 0.0, 0.0, forced=True)
        options = {board.san(m): m for m in moves}
        aliases = move_aliases(board, options)
        side = "white" if board.turn else "black"
        instructions = (
            f"You are a strong chess player playing {side}. Which move is best? "
            "Prefer checkmate, then winning material safely, then development and king safety. "
            "Never leave a piece where it can be captured for free. "
            "A draw ends the game with half a point each: avoid it when you are ahead, welcome it when you are behind."
        )
        answer = await self.ask(
            board,
            instructions,
            {san: describe(board, m) for san, m in options.items()},
            credentials,
            model,
            illegal_so_far,
            aliases,
        )
        return Decision(
            move=options[answer.choice],
            san=answer.choice,
            top=sorted(answer.probabilities.items(), key=lambda kv: -kv[1])[:3],
            probabilities=answer.probabilities,
            asked=list(options),
            input_tokens=answer.input_tokens,
            output_tokens=answer.output_tokens,
            cost_usd=answer.cost_usd,
            seconds=answer.seconds,
            illegal=answer.illegal,
            illegal_answers=answer.illegal_answers,
            call=getattr(answer, "call", None)
            or {
                "reply": answer.reply,
                "finish_reason": answer.finish_reason,
                "reasoning_chars": answer.reasoning_chars,
                "blanks": answer.blanks,
                "truncated": answer.truncated,
                "schema": answer.schema,
                "reasoning": answer.reasoning,
                "max_tokens": answer.max_tokens,
            }
            if hasattr(answer, "reply")
            else getattr(answer, "call", None),
        )
