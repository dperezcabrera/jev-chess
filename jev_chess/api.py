from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from pico_fastapi import FastApiConfigurer, controller, get, post
from pico_ioc import component
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from .game import Game, IllegalMove
from .jev import JevError
from .settings import SessionSettings

STATIC_DIR = Path(__file__).with_name("static")
SQUARE = r"^[a-h][1-8]$"


class MoveRequest(BaseModel):
    origin: str = Field(alias="from", pattern=SQUARE)
    target: str = Field(alias="to", pattern=SQUARE)
    promotion: Literal["q", "r", "b", "n"] = "q"


class NewGameRequest(BaseModel):
    human: Literal["white", "black", "none"] = "white"


@controller
class PageController:
    @get("/", include_in_schema=False)
    async def index(self):
        return FileResponse(STATIC_DIR / "index.html")


@controller(prefix="/api")
class GameController:
    def __init__(self, game: Game):
        self._game = game

    @get("/state")
    async def state(self):
        return await self._game.snapshot()

    @get("/pgn")
    async def pgn(self):
        filename, text = await self._game.pgn()
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return Response(text, media_type="application/x-chess-pgn", headers=headers)

    @post("/move")
    async def move(self, body: MoveRequest):
        return await self._game.human_move(body.origin, body.target, body.promotion)

    @post("/jev")
    async def jev(self):
        return await self._game.jev_move()

    @post("/new")
    async def new(self, body: NewGameRequest):
        return await self._game.new(body.human)


@component
class SessionConfigurer(FastApiConfigurer):
    priority = -50

    def __init__(self, settings: SessionSettings):
        self._secret = settings.secret

    def configure_app(self, app: FastAPI) -> None:
        app.add_middleware(SessionMiddleware, secret_key=self._secret, session_cookie="jev_chess", same_site="strict")


@component
class WebConfigurer(FastApiConfigurer):
    priority = -100

    def configure_app(self, app: FastAPI) -> None:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        app.add_exception_handler(IllegalMove, lambda _, e: JSONResponse({"error": str(e)}, status_code=409))
        app.add_exception_handler(JevError, lambda _, e: JSONResponse({"error": str(e)}, status_code=502))
