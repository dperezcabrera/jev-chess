import asyncio
import json
import os

import chess
import httpx
import pytest
from pico_ioc import DictSource, FlatDictSource, configuration

from jev_chess.game import Game
from jev_chess.jev import JevApi, describe
from jev_chess.main import load_env


def jev_stub(pick):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        san = pick(body["questions"]["move"]["criteria"])
        return httpx.Response(200, json={"answers": {"move": {"choice": san, "probabilities": {san: 1.0}}}})

    return handler, seen


@pytest.fixture
def app(make_container, make_client):
    def build(handler, api_key="test-key"):
        flat = FlatDictSource({"OPENROUTER_API_KEY": api_key, "JEV_MODEL": "jev-test"})
        config = configuration(flat, DictSource({}))
        container = make_container("jev_chess", "pico_fastapi", "pico_httpx", config=config)
        build.container = container
        container.get(JevApi)._pico_httpx_aclient = httpx.AsyncClient(
            base_url="https://jev.test", transport=httpx.MockTransport(handler)
        )
        return make_client(container)

    return build


def test_full_turn_cycle(app):
    handler, seen = jev_stub(lambda criteria: next(iter(criteria)))
    client = app(handler)

    state = client.get("/api/state").json()
    assert state["humans_turn"] and "e4" in state["dests"]["e2"]

    assert client.post("/api/move", json={"from": "e2", "to": "e5"}).status_code == 409
    assert client.post("/api/jev").status_code == 409
    assert client.post("/api/move", json={"from": "z9", "to": "e4"}).status_code == 422

    state = client.post("/api/move", json={"from": "e2", "to": "e4"}).json()
    assert state["history"] == ["e4"] and state["jevs_turn"] and state["dests"] == {}

    state = client.post("/api/jev").json()
    assert len(state["history"]) == 2 and state["humans_turn"]
    assert state["jev_top"] == [{"san": state["history"][1], "probability": 1.0}]

    request = seen[0]
    question = request["questions"]["move"]
    assert request["model"] == "jev-test" and request["state"]["side_to_move"] == "black"
    assert question["type"] == "choice" and len(question["criteria"]) == 20

    state = client.post("/api/new", json={"human": "black"}).json()
    assert state["history"] == [] and state["jevs_turn"]


def test_jev_is_offered_checkmate_as_such(app):
    handler, _ = jev_stub(lambda criteria: next((k for k, v in criteria.items() if "CHECKMATE" in v), "e5"))
    client = app(handler)
    for origin, target in (("f2", "f3"), ("g2", "g4")):
        client.post("/api/move", json={"from": origin, "to": target})
        state = client.post("/api/jev").json()
    assert state["history"] == ["f3", "e5", "g4", "Qh4#"] and state["over"]
    assert state["result"] == "0-1 by checkmate" and state["dests"] == {}


def test_upstream_error_is_reported(app):
    client = app(lambda request: httpx.Response(401, text="bad key"))
    client.post("/api/new", json={"human": "black"})
    response = client.post("/api/jev")
    assert response.status_code == 502 and "401" in response.json()["error"]


def test_unknown_option_is_rejected(app):
    client = app(jev_stub(lambda criteria: "Zz9")[0])
    client.post("/api/new", json={"human": "black"})
    assert client.post("/api/jev").status_code == 502


def test_missing_api_key_is_explained(app):
    client = app(jev_stub(lambda criteria: next(iter(criteria)))[0], api_key="")
    client.post("/api/new", json={"human": "black"})
    response = client.post("/api/jev")
    assert response.status_code == 502 and "OPENROUTER_API_KEY" in response.json()["error"]


def test_promotion_piece_is_honoured():
    game = Game(chooser=None)
    game._board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    state = asyncio.run(game.human_move("a7", "a8", "n"))
    assert state["fen"].startswith("N7/")


def test_each_session_plays_its_own_game(app, make_client):
    alice = app(jev_stub(lambda criteria: next(iter(criteria)))[0])
    bob = make_client(app.container)
    alice.post("/api/move", json={"from": "e2", "to": "e4"})
    assert alice.get("/api/state").json()["history"] == ["e4"]
    assert bob.get("/api/state").json()["history"] == []
    assert alice.get("/api/state").json()["game_id"] != bob.get("/api/state").json()["game_id"]
    first = alice.get("/api/state").json()["game_id"]
    assert alice.post("/api/new", json={"human": "white"}).json()["game_id"] != first


def test_en_passant_is_described_as_a_capture():
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    assert "en passant" in describe(board, chess.Move.from_uci("e5d6"))


def test_page_and_assets_are_served(app):
    client = app(jev_stub(lambda criteria: next(iter(criteria)))[0])
    assert "chessground" in client.get("/").text
    assert client.get("/static/vendor/chessground/chessground.min.js").status_code == 200
    assert client.get("/static/../main.py").status_code == 404


def test_env_file_does_not_override_the_shell(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nJEV_TEST_A = 'quoted'\nJEV_TEST_B=from_file\n")
    monkeypatch.delenv("JEV_TEST_A", raising=False)
    monkeypatch.setenv("JEV_TEST_B", "from_shell")
    load_env(env)
    assert os.environ["JEV_TEST_A"] == "quoted" and os.environ["JEV_TEST_B"] == "from_shell"
    monkeypatch.delenv("JEV_TEST_A")
