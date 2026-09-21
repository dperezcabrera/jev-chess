import asyncio
import time
from dataclasses import dataclass

import chess
import httpx
from pico_ioc import cleanup, component

from .provider import NO_KEY, Gateway, JevProvider, SessionCredentials


class JevError(Exception):
    pass


@dataclass(frozen=True)
class Answer:
    choice: str
    probabilities: dict[str, float]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float


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


@component
class JevApi:
    def __init__(self):
        self._client = httpx.AsyncClient()

    async def system_one(self, gateway: Gateway, body: dict) -> dict:
        response = await self._client.post(
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
    elif board.is_check():
        text += ", gives check"
    if board.is_attacked_by(board.turn, move.to_square):
        text += ", moved piece can be captured next turn"
    board.pop()
    return text


@component
class JevMoveChooser:
    def __init__(self, api: JevApi, provider: JevProvider):
        self._api = api
        self._provider = provider

    def model_for(self, credentials: SessionCredentials | None = None) -> str:
        return self._provider.gateway(credentials).model

    async def ask(
        self,
        board: chess.Board,
        instructions: str,
        criteria: dict[str, str],
        credentials: SessionCredentials | None = None,
    ) -> Answer:
        """One Choice question about a position: `criteria` maps each option label to its description."""
        gateway = self._provider.gateway(credentials)
        if not gateway.api_key:
            raise JevError(NO_KEY)
        state = {
            "game": "chess",
            "side_to_move": "white" if board.turn else "black",
            "fen": board.fen(),
            "board": str(board),
            "moves_so_far": chess.Board().variation_san(board.move_stack) if board.move_stack else "",
        }
        question = {"type": "choice", "instructions": instructions, "criteria": criteria}
        started = time.perf_counter()
        try:
            body = {"model": gateway.model, "state": state, "questions": {"move": question}}
            response = await self._api.system_one(gateway, body)
            answer = response["answers"]["move"]
            choice = answer["choice"]
        except httpx.HTTPStatusError as e:
            raise JevError(f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            raise JevError(f"request to Jev failed: {e}") from e
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

    async def choose(
        self,
        board: chess.Board,
        credentials: SessionCredentials | None = None,
        order: list[chess.Move] | None = None,
    ) -> Decision:
        """Ask Jev for a move; `order` lists the legal moves in the order to offer them, to test whether it matters."""
        moves = list(board.legal_moves)
        if order is not None:
            if sorted(order, key=str) != sorted(moves, key=str):
                raise ValueError("order must contain exactly the legal moves")
            moves = order
        options = {board.san(m): m for m in moves}
        side = "white" if board.turn else "black"
        instructions = (
            f"You are a strong chess player playing {side}. Which move is best? "
            "Prefer checkmate, then winning material safely, then development and king safety. "
            "Never leave a piece where it can be captured for free."
        )
        answer = await self.ask(
            board, instructions, {san: describe(board, m) for san, m in options.items()}, credentials
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
        )
