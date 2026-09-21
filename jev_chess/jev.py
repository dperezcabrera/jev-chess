import time
from dataclasses import dataclass

import chess
import httpx
from pico_httpx import http_client, post
from pico_httpx.config import HttpSettings
from pico_ioc import component

from .settings import JevSettings, OpenRouterSettings


class JevError(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    move: chess.Move
    top: list[tuple[str, float]]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float


@http_client
class JevApi:
    def __init__(self, http: HttpSettings, openrouter: OpenRouterSettings):
        self._pico_httpx_settings = http
        self._pico_httpx_aclient = httpx.AsyncClient(
            base_url=openrouter.base_url,
            timeout=openrouter.timeout_seconds,
            headers={"Authorization": f"Bearer {openrouter.api_key}"},
        )

    @post("/v1/systemone")
    async def system_one(self, json: dict) -> dict: ...


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
    def __init__(self, api: JevApi, openrouter: OpenRouterSettings, jev: JevSettings):
        self._api = api
        self._has_key = bool(openrouter.api_key)
        self.model = jev.model

    async def choose(self, board: chess.Board) -> Decision:
        if not self._has_key:
            raise JevError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key "
                "(https://openrouter.ai/settings/keys)."
            )
        options = {board.san(m): m for m in board.legal_moves}
        side = "white" if board.turn else "black"
        state = {
            "game": "chess",
            "side_to_move": side,
            "fen": board.fen(),
            "board": str(board),
            "moves_so_far": chess.Board().variation_san(board.move_stack) if board.move_stack else "",
        }
        question = {
            "type": "choice",
            "instructions": f"You are a strong chess player playing {side}. Which move is best? "
            "Prefer checkmate, then winning material safely, then development and king safety. "
            "Never leave a piece where it can be captured for free.",
            "criteria": {san: describe(board, m) for san, m in options.items()},
        }
        started = time.perf_counter()
        try:
            response = await self._api.system_one(
                json={"model": self.model, "state": state, "questions": {"move": question}}
            )
            answer = response["answers"]["move"]
            choice = answer["choice"]
        except httpx.HTTPStatusError as e:
            raise JevError(f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            raise JevError(f"request to Jev failed: {e}") from e
        if choice not in options:
            raise JevError(f"Jev returned an unknown option: {choice!r}")
        top = sorted(answer.get("probabilities", {}).items(), key=lambda kv: -kv[1])[:3]
        usage = response.get("usage") or {}
        return Decision(
            move=options[choice],
            top=top,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(usage.get("cost") or 0.0),
            seconds=time.perf_counter() - started,
        )
