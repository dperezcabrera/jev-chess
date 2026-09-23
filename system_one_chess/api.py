from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from pico_fastapi import FastApiConfigurer, controller, delete, get, post
from pico_ioc import component
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from .game import Game, IllegalMove
from .jev import JevError
from .laya import LayaModel
from .models import LogoCache, ModelRegistry, SessionModels
from .provider import LABELS, JevProvider, ProviderError, SessionCredentials
from .settings import SessionSettings
from .standings import Standings
from .tournament import MAX_PARTICIPANTS, MAX_ROUNDS, Tournament

STATIC_DIR = Path(__file__).with_name("static")
SQUARE = r"^[a-h][1-8]$"


class MoveRequest(BaseModel):
    origin: str = Field(alias="from", pattern=SQUARE)
    target: str = Field(alias="to", pattern=SQUARE)
    promotion: Literal["q", "r", "b", "n"] = "q"


class SettingsRequest(BaseModel):
    provider: Literal["vercel", "openrouter", "laya"]
    api_key: str = Field(default="", max_length=400)


class NewGameRequest(BaseModel):
    human: Literal["white", "black", "none"] = "white"
    white: str = Field(default="jev", max_length=120)
    black: str = Field(default="jev", max_length=120)


class ParticipantsRequest(BaseModel):
    participants: list[str] = Field(min_length=1, max_length=MAX_PARTICIPANTS)


class TournamentRequest(BaseModel):
    participants: list[str] = Field(max_length=MAX_PARTICIPANTS)
    human: bool = False
    rounds: int = Field(default=3, ge=1, le=MAX_ROUNDS)
    time_limit: float = Field(default=60.0, ge=0, le=24 * 60, description="minutes per player and game, 0 for none")


class TakebackRequest(BaseModel):
    ply: int | None = Field(default=None, ge=0)


class ModelRequest(BaseModel):
    upstream: str = Field(min_length=3, max_length=120)


@controller
class PageController:
    @get("/", include_in_schema=False)
    async def index(self):
        return FileResponse(STATIC_DIR / "index.html")

    @get("/tournament", include_in_schema=False)
    async def tournament(self):
        return FileResponse(STATIC_DIR / "index.html")


@controller(prefix="/api")
class GameController:
    def __init__(self, game: Game):
        self._game = game

    @get("/state")
    async def state(self):
        return await self._game.snapshot()

    @get("/export")
    async def export(self):
        headers = {"Content-Disposition": 'attachment; filename="system-one-chess-tournament.json"'}
        return JSONResponse(self._tournament.export(), headers=headers)

    @get("/pgn")
    async def pgn(self):
        filename, text = await self._game.pgn()
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(text, media_type="application/x-chess-pgn", headers=headers)

    @post("/move")
    async def move(self, body: MoveRequest):
        return await self._game.human_move(body.origin, body.target, body.promotion)

    @post("/pardon")
    async def pardon(self):
        return await self._game.pardon()

    @post("/takeback")
    async def takeback(self, body: TakebackRequest | None = None):
        return await self._game.takeback(body.ply if body else None)

    @post("/jev")
    async def jev(self):
        return await self._game.jev_move()

    @post("/new")
    async def new(self, body: NewGameRequest):
        return await self._game.new(body.human, body.white, body.black)


@controller(prefix="/api/settings")
class SettingsController:
    def __init__(self, provider: JevProvider, credentials: SessionCredentials, laya: LayaModel):
        self._provider = provider
        self._credentials = credentials
        self._laya = laya

    def _view(self) -> dict:
        gateway = self._provider.gateway(self._credentials)
        source = (
            "local" if gateway.local else "session" if gateway.own_key else "environment" if gateway.api_key else "none"
        )
        return {
            "provider": gateway.name,
            "model": gateway.model,
            "key_source": source,
            "key_hint": gateway.api_key[-4:] if gateway.own_key else "",
            "providers": [{"id": name, "label": label} for name, label in LABELS.items()],
            "laya_installed": self._laya.ready,
            "laya_mode": self._laya.mode,
        }

    @get("")
    async def read(self):
        return self._view()

    @post("")
    async def save(self, body: SettingsRequest):
        self._credentials.store(body.provider, body.api_key)
        return self._view()

    @delete("")
    async def forget(self):
        self._credentials.clear()
        return self._view()


@controller(prefix="/api/logos")
class LogosController:
    def __init__(self, logos: LogoCache):
        self._logos = logos

    @get("/{key}")
    async def read(self, key: str):
        found = await self._logos.get(key)
        if found is None:
            return JSONResponse({"error": "no logo"}, status_code=404, headers={"Cache-Control": "max-age=300"})
        content, media_type = found
        return Response(content, media_type=media_type, headers={"Cache-Control": "max-age=86400"})


@controller(prefix="/api/tournament")
class TournamentController:
    def __init__(self, tournament: Tournament):
        self._tournament = tournament

    @get("")
    async def read(self):
        return await self._tournament.view()

    @post("")
    async def start(self, body: TournamentRequest):
        limit = body.time_limit * 60 if body.time_limit else None
        return await self._tournament.start(body.participants, body.human, body.rounds, limit)

    @post("/rounds")
    async def add_round(self):
        return await self._tournament.add_round()

    @post("/pause")
    async def pause(self):
        return await self._tournament.pause()

    @post("/play")
    async def play(self):
        return await self._tournament.play()

    @post("/participants")
    async def add_participants(self, body: ParticipantsRequest):
        return await self._tournament.add_participants(body.participants)

    @get("/board/{number}")
    async def board(self, number: int, round: int | None = None):
        return await self._tournament.board_state(number, round)

    @post("/board/{number}/move")
    async def move(self, number: int, body: MoveRequest):
        return await self._tournament.human_move(number, body.origin, body.target, body.promotion)

    @post("/board/{number}/takeback")
    async def takeback(self, number: int, body: TakebackRequest | None = None):
        return await self._tournament.takeback(number, body.ply if body else None)

    @post("/board/{number}/clock/pause")
    async def pause_clock(self, number: int):
        return await self._tournament.pause_clock(number)

    @post("/board/{number}/clock/play")
    async def play_clock(self, number: int):
        return await self._tournament.play_clock(number)

    @post("/board/{number}/retry")
    async def retry(self, number: int):
        await self._tournament.retry(number)
        return await self._tournament.view()

    @post("/board/{number}/pardon")
    async def pardon(self, number: int):
        return await self._tournament.pardon(number)

    @get("/board/{number}/pgn")
    async def board_pgn(self, number: int, round: int | None = None):
        filename, text = await self._tournament.board_pgn(number, round)
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(text, media_type="application/x-chess-pgn", headers=headers)

    @get("/export")
    async def export(self):
        headers = {"Content-Disposition": 'attachment; filename="system-one-chess-tournament.json"'}
        return JSONResponse(self._tournament.export(), headers=headers)

    @get("/pgn")
    async def pgn(self):
        headers = {"Content-Disposition": 'attachment; filename="system-one-chess-tournament.pgn"'}
        return Response(self._tournament.pgn(), media_type="application/x-chess-pgn", headers=headers)

    @delete("")
    async def stop(self):
        self._tournament.stop()
        return await self._tournament.view()


@controller(prefix="/api/tournaments")
class SavedTournamentsController:
    def __init__(self, tournament: Tournament):
        self._tournament = tournament

    @get("")
    async def list_saved(self):
        return {"tournaments": self._tournament.saved()}

    @post("/{tournament_id}/resume")
    async def resume(self, tournament_id: str):
        return await self._tournament.resume(tournament_id)


@controller(prefix="/api/standings")
class StandingsController:
    def __init__(self, standings: Standings):
        self._standings = standings

    @get("")
    async def read(self):
        return {"rows": self._standings.table()}


@controller(prefix="/api/models")
class ModelsController:
    def __init__(self, registry: ModelRegistry, credentials: SessionCredentials, session: SessionModels):
        self._registry = registry
        self._credentials = credentials
        self._session = session

    def _view(self) -> dict:
        models = [asdict(model) for model in self._registry.list(self._credentials, self._session)]
        return {"models": models, "suggested": self._registry.suggested()}

    @get("")
    async def read(self):
        return self._view()

    @post("")
    async def add(self, body: ModelRequest):
        try:
            self._session.add(body.upstream)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
        return self._view()

    @delete("/{upstream:path}")
    async def remove(self, upstream: str):
        self._session.remove(upstream)
        return self._view()


@component
class SessionConfigurer(FastApiConfigurer):
    priority = -50

    def __init__(self, settings: SessionSettings):
        self._secret = settings.secret

    def configure_app(self, app: FastAPI) -> None:
        app.add_middleware(
            SessionMiddleware, secret_key=self._secret, session_cookie="system_one_chess", same_site="strict"
        )


@component
class WebConfigurer(FastApiConfigurer):
    priority = -100

    def configure_app(self, app: FastAPI) -> None:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        app.add_exception_handler(IllegalMove, lambda _, e: JSONResponse({"error": str(e)}, status_code=409))
        app.add_exception_handler(JevError, lambda _, e: JSONResponse({"error": str(e)}, status_code=502))
        app.add_exception_handler(ProviderError, lambda _, e: JSONResponse({"error": str(e)}, status_code=400))
