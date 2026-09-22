import asyncio
import json
import os

import chess
import httpx
import pytest
from pico_ioc import DictSource, FlatDictSource, configuration

from system_one_chess.game import Game
from system_one_chess.jev import JevApi, describe
from system_one_chess.main import load_env
from system_one_chess.standings import Standings


def jev_stub(pick):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        san = pick(body["questions"]["move"]["criteria"])
        answers = {"move": {"choice": san, "probabilities": {san: 1.0}}}
        usage = {"input_tokens": 300, "output_tokens": 20, "cost": 0.00002}
        return httpx.Response(200, json={"answers": answers, "usage": usage})

    return handler, seen


@pytest.fixture
def app(make_container, make_client):
    def build(handler, api_key="test-key"):
        flat = FlatDictSource({"OPENROUTER_API_KEY": api_key, "JEV_MODEL": "jev-test"})
        config = configuration(flat, DictSource({}))
        container = make_container("system_one_chess", "pico_fastapi", config=config)
        build.container = container
        container.get(JevApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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

    assert state["moves_uci"][0] == "e2e4" and len(state["moves_uci"]) == 2
    usage = state["usage"]
    assert usage["calls"] == 1 and usage["input_tokens"] == 300 and usage["output_tokens"] == 20
    assert usage["cost_usd"] == pytest.approx(0.00002) and usage["seconds"] >= 0

    state = client.post("/api/new", json={"human": "black"}).json()
    assert state["history"] == [] and state["jevs_turn"] and state["usage"]["calls"] == 0


def test_jev_is_offered_checkmate_as_such(app):
    handler, _ = jev_stub(lambda criteria: next((k for k, v in criteria.items() if "CHECKMATE" in v), "e5"))
    client = app(handler)
    for origin, target in (("f2", "f3"), ("g2", "g4")):
        client.post("/api/move", json={"from": origin, "to": target})
        state = client.post("/api/jev").json()
    assert state["history"] == ["f3", "e5", "g4", "Qh4#"] and state["over"]
    assert state["result"] == "0-1 by checkmate" and state["dests"] == {}


def test_game_exports_as_pgn(app):
    client = app(jev_stub(lambda criteria: "e5")[0])
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    game_id = client.post("/api/jev").json()["game_id"]
    response = client.get("/api/pgn")
    assert response.headers["content-disposition"] == f'attachment; filename="system-one-chess-{game_id}.pgn"'
    assert '[White "Human"]' in response.text and '[Black "Jev (jev-test)"]' in response.text
    assert '[Result "*"]' in response.text and "1. e4 e5 *" in response.text


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
    game = Game(chooser=None, credentials=None, registry=None, session_models=None, standings=Standings(registry=None))
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


def provider_for(make_container, **env):
    from system_one_chess.provider import JevProvider

    container = make_container(
        "system_one_chess", "pico_fastapi", config=configuration(FlatDictSource(env), DictSource({}))
    )
    return container.get(JevProvider).gateway()


def test_the_gateway_is_picked_from_whichever_key_is_set(make_container):
    vercel = provider_for(make_container, AI_GATEWAY_API_KEY="vck")
    assert (vercel.name, vercel.model, vercel.api_key) == ("vercel", "typesafe-ai/jev", "vck")
    assert vercel.base_url == "https://ai-gateway.vercel.sh/typesafe"

    openrouter = provider_for(make_container, OPENROUTER_API_KEY="ork")
    assert (openrouter.name, openrouter.model) == ("openrouter", "jev-latest")
    assert openrouter.base_url == "https://openrouter.ai/api"


def test_an_explicit_provider_and_model_win(make_container):
    both = provider_for(make_container, AI_GATEWAY_API_KEY="vck", OPENROUTER_API_KEY="ork")
    assert both.name == "openrouter"
    chosen = provider_for(
        make_container,
        AI_GATEWAY_API_KEY="vck",
        OPENROUTER_API_KEY="ork",
        JEV_PROVIDER="Vercel",
        JEV_MODEL="typesafe-ai/jev-1.13",
    )
    assert (chosen.name, chosen.api_key, chosen.model) == ("vercel", "vck", "typesafe-ai/jev-1.13")


def test_cost_is_read_from_either_gateway_shape():
    from system_one_chess.provider import JevProvider

    assert JevProvider.cost_usd({"usage": {"input_tokens": 275, "cost": 0.00003}}) == 0.00003
    vercel = {"usage": {"input_tokens": 275}, "provider_metadata": {"gateway": {"cost": "0.00001155"}}}
    assert JevProvider.cost_usd(vercel) == pytest.approx(0.00001155)
    assert JevProvider.cost_usd({}) == 0.0


def test_a_game_played_through_vercel_adds_up_its_cost(make_container, make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert str(request.url) == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
        assert request.headers["authorization"] == "Bearer vck"
        assert body["model"] == "typesafe-ai/jev"
        san = next(iter(body["questions"]["move"]["criteria"]))
        answers = {"move": {"choice": san, "probabilities": {san: 1.0}}}
        metadata = {"gateway": {"cost": "0.00001155"}}
        return httpx.Response(
            200,
            json={
                "answers": answers,
                "usage": {"input_tokens": 275, "output_tokens": 20},
                "provider_metadata": metadata,
            },
        )

    config = configuration(FlatDictSource({"AI_GATEWAY_API_KEY": "vck"}), DictSource({}))
    container = make_container("system_one_chess", "pico_fastapi", config=config)
    container.get(JevApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = make_client(container)
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    usage = client.post("/api/jev").json()["usage"]
    assert usage["calls"] == 1 and usage["cost_usd"] == pytest.approx(0.00001155)


def test_an_unknown_provider_is_rejected_at_startup(make_container):
    from system_one_chess.provider import ProviderError

    with pytest.raises(Exception) as error:
        provider_for(make_container, JEV_PROVIDER="azure")
    assert "JEV_PROVIDER" in str(error.value) or isinstance(error.value, ProviderError)


def test_without_any_key_the_error_names_both_options(app):
    client = app(jev_stub(lambda criteria: next(iter(criteria)))[0], api_key="")
    client.post("/api/new", json={"human": "black"})
    message = client.post("/api/jev").json()["error"]
    assert "AI_GATEWAY_API_KEY" in message and "OPENROUTER_API_KEY" in message


def settings_app(make_container, make_client, seen, **env):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(
            {"url": str(request.url), "authorization": request.headers["authorization"], "model": body["model"]}
        )
        san = next(iter(body["questions"]["move"]["criteria"]))
        return httpx.Response(200, json={"answers": {"move": {"choice": san, "probabilities": {san: 1.0}}}})

    container = make_container(
        "system_one_chess", "pico_fastapi", config=configuration(FlatDictSource(env), DictSource({}))
    )
    container.get(JevApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return container, make_client(container)


def test_a_key_typed_in_the_browser_is_used_but_never_sent_back(make_container, make_client):
    seen = []
    _, client = settings_app(make_container, make_client, seen)
    assert client.get("/api/settings").json()["key_source"] == "none"
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    assert client.post("/api/jev").status_code == 502

    saved = client.post("/api/settings", json={"provider": "vercel", "api_key": "  secret-key-abcd  "})
    assert saved.json()["key_source"] == "session" and saved.json()["key_hint"] == "abcd"
    assert saved.json()["provider"] == "vercel" and saved.json()["model"] == "typesafe-ai/jev"
    assert "secret-key" not in saved.text and "secret-key" not in client.get("/api/settings").text

    assert client.post("/api/jev").status_code == 200
    assert seen == [
        {
            "url": "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
            "authorization": "Bearer secret-key-abcd",
            "model": "typesafe-ai/jev",
        }
    ]


def test_a_session_key_stays_in_its_own_session(make_container, make_client):
    seen = []
    container, alice = settings_app(make_container, make_client, seen, OPENROUTER_API_KEY="server-key")
    bob = make_client(container)
    alice.post("/api/settings", json={"provider": "vercel", "api_key": "alice-key-1234"})
    assert bob.get("/api/settings").json() == {
        **bob.get("/api/settings").json(),
        "provider": "openrouter",
        "key_source": "environment",
        "key_hint": "",
    }
    bob.post("/api/move", json={"from": "e2", "to": "e4"})
    bob.post("/api/jev")
    assert seen[-1]["authorization"] == "Bearer server-key" and "openrouter.ai" in seen[-1]["url"]


def test_forgetting_the_key_goes_back_to_the_server_one(make_container, make_client):
    _, client = settings_app(make_container, make_client, [], OPENROUTER_API_KEY="server-key", JEV_MODEL="jev-1.13")
    client.post("/api/settings", json={"provider": "vercel", "api_key": "mine-9999"})
    assert client.get("/api/settings").json()["model"] == "typesafe-ai/jev"
    cleared = client.delete("/api/settings").json()
    assert (cleared["provider"], cleared["key_source"], cleared["model"]) == ("openrouter", "environment", "jev-1.13")


def test_settings_reject_an_unknown_provider_and_an_oversized_key(make_container, make_client):
    _, client = settings_app(make_container, make_client, [])
    assert client.post("/api/settings", json={"provider": "azure", "api_key": "x"}).status_code == 422
    assert client.post("/api/settings", json={"provider": "vercel", "api_key": "x" * 401}).status_code == 422


class FakeLaya:
    """Stands in for the loaded model: answers the first option and records what it was asked."""

    def __init__(self):
        self.asked = []

    def system_one(self, state, questions):
        self.asked.append((state, questions))
        criteria = questions["move"]["criteria"]
        first = next(iter(criteria))
        probabilities = {key: 1 / len(criteria) for key in criteria}
        answers = {"move": {"type": "choice", "choice": first, "probabilities": probabilities, "confidence": 0.5}}
        return {"model": "laya-rl-agent", "answers": answers, "usage": {"input_tokens": 300, "output_tokens": 0}}


def laya_app(make_container, make_client, monkeypatch, installed=True, **env):
    from system_one_chess import laya as laya_module
    from system_one_chess.laya import LayaModel

    monkeypatch.setattr(laya_module, "available", lambda: installed)
    container = make_container(
        "system_one_chess", "pico_fastapi", config=configuration(FlatDictSource(env), DictSource({}))
    )
    fake = FakeLaya()
    monkeypatch.setattr(container.get(LayaModel), "_load", lambda: fake)
    return make_client(container), fake


def test_laya_is_the_default_when_installed_and_no_key_is_set(make_container, make_client, monkeypatch):
    client, fake = laya_app(make_container, make_client, monkeypatch)
    settings = client.get("/api/settings").json()
    assert settings["provider"] == "laya" and settings["key_source"] == "local" and settings["laya_installed"]
    assert {p["id"] for p in settings["providers"]} == {"vercel", "openrouter", "laya"}
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    state = client.post("/api/jev").json()
    assert len(state["history"]) == 2 and state["usage"]["calls"] == 1 and state["usage"]["cost_usd"] == 0
    state_sent, questions = fake.asked[0]
    assert state_sent["side_to_move"] == "black"
    criteria = questions["move"]["criteria"]
    assert len(criteria) == 20 and all(value is None for value in criteria.values()), "chess sends labels only"


def test_a_session_can_pick_laya_over_a_server_key(make_container, make_client, monkeypatch):
    client, fake = laya_app(make_container, make_client, monkeypatch, OPENROUTER_API_KEY="server-key")
    assert client.get("/api/settings").json()["provider"] == "openrouter"
    saved = client.post("/api/settings", json={"provider": "laya", "api_key": ""}).json()
    assert saved["provider"] == "laya" and saved["key_source"] == "local"
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    assert client.post("/api/jev").status_code == 200 and len(fake.asked) == 1


def test_choosing_laya_when_it_is_not_installed_explains_how_to_install_it(make_container, make_client, monkeypatch):
    client, _ = laya_app(make_container, make_client, monkeypatch, installed=False)
    assert client.get("/api/settings").json()["laya_installed"] is False
    client.post("/api/settings", json={"provider": "laya", "api_key": ""})
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    response = client.post("/api/jev")
    assert response.status_code == 502 and "pip install" in response.json()["error"]


def test_each_colour_can_be_played_by_a_different_model(make_container, make_client, monkeypatch):
    seen = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append("jev")
        san = next(iter(body["questions"]["move"]["criteria"]))
        return httpx.Response(200, json={"answers": {"move": {"choice": san, "probabilities": {san: 1.0}}}})

    from system_one_chess import laya as laya_module
    from system_one_chess.laya import LayaModel

    monkeypatch.setattr(laya_module, "available", lambda: True)
    config = configuration(FlatDictSource({"OPENROUTER_API_KEY": "server-key"}), DictSource({}))
    container = make_container("system_one_chess", "pico_fastapi", config=config)
    container.get(JevApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(gateway_handler))
    fake = FakeLaya()
    monkeypatch.setattr(container.get(LayaModel), "_load", lambda: fake)
    client = make_client(container)

    state = client.post("/api/new", json={"human": "none", "white": "jev", "black": "laya"}).json()
    assert state["models"] == {"white": "jev", "black": "laya"}
    for _ in range(4):
        client.post("/api/jev")
    assert seen == ["jev", "jev"] and len(fake.asked) == 2
    pgn = client.get("/api/pgn").text
    assert '[White "Jev (jev-latest)"]' in pgn and '[Black "Laya (convaiinnovations/laya)"]' in pgn

    assert client.post("/api/new", json={"human": "white", "black": "gpt"}).status_code == 409


def llm_stub(replies, seen):
    """A chat completion endpoint that answers from `replies` in order and records every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "model": body["model"], "messages": body["messages"]})
        text = replies.pop(0)
        usage = {"prompt_tokens": 900, "completion_tokens": 12, "cost": 0.0009}
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": usage})

    return handler


def llm_app(make_container, make_client, replies, seen, **env):
    from system_one_chess.llm import LLMApi

    config = configuration(FlatDictSource({"OPENROUTER_API_KEY": "server-key", **env}), DictSource({}))
    container = make_container("system_one_chess", "pico_fastapi", config=config)
    container.get(LLMApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(llm_stub(replies, seen)))
    llm_app.container = container
    return make_client(container)


def test_models_are_listed_and_llms_are_added_per_session(make_container, make_client):
    client = llm_app(make_container, make_client, [], [])
    listed = client.get("/api/models").json()
    configured = [entry["upstream"] for entry in listed["suggested"]]
    assert [m["id"] for m in listed["models"]] == ["jev", "laya"] + [f"llm:{u}" for u in configured]
    assert listed["models"][0]["ready"] and listed["models"][0]["kind"] == "system_one"
    grok = next(m for m in listed["models"] if m["upstream"] == "x-ai/grok-4.7")
    assert grok["ready"] and not grok["removable"], "the models file configures it for every session"
    assert {"upstream": "openai/gpt-5.6-luna", "tier": "ultra cheap"} in listed["suggested"]

    added = client.post("/api/models", json={"upstream": "openai/gpt-5-mini"}).json()
    llm = added["models"][-1]
    assert llm == {
        "id": "llm:openai/gpt-5-mini",
        "name": "gpt-5-mini",
        "kind": "llm",
        "provider": "openrouter",
        "upstream": "openai/gpt-5-mini",
        "ready": True,
        "note": "",
        "logo": "/api/logos/openai",
        "removable": True,
    }
    assert client.post("/api/models", json={"upstream": "not an id"}).status_code == 422
    assert client.delete("/api/models/openai/gpt-5-mini").json()["models"][-1]["id"] != "llm:openai/gpt-5-mini"
    assert client.delete("/api/models/x-ai/grok-4.7").json()["models"][-1]["upstream"] == "google/gemma-4-31b-it", (
        "configured ones stay"
    )


def test_an_llm_plays_a_colour_through_the_chat_api_and_its_cost_is_counted(make_container, make_client):
    seen = []
    client = llm_app(make_container, make_client, ['{"choice": "e5"}', "I think Nf6 is best here"], seen)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    state = client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"}).json()
    assert state["models"]["black"] == "llm:openai/gpt-5-mini"

    client.post("/api/move", json={"from": "e2", "to": "e4"})
    state = client.post("/api/jev").json()
    assert state["history"] == ["e4", "e5"] and state["jev_top"] == []
    assert state["usage"]["calls"] == 1 and state["usage"]["cost_usd"] == pytest.approx(0.0009)
    request = seen[0]
    assert request["url"].endswith("/v1/chat/completions") and request["model"] == "openai/gpt-5-mini"
    assert '"choice"' in request["messages"][0]["content"] and "- e5" in request["messages"][1]["content"]

    client.post("/api/move", json={"from": "g1", "to": "f3"})
    state = client.post("/api/jev").json()
    assert state["history"][-1] == "Nf6", "a label found as plain text in the reply is accepted"
    assert '[Black "gpt-5-mini (openai/gpt-5-mini)"]' in client.get("/api/pgn").text


def test_two_illegal_answers_in_one_turn_lose_the_game(make_container, make_client):
    seen = []
    client = llm_app(make_container, make_client, ["I resign", "still nothing useful"], seen)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"})
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    state = client.post("/api/jev").json()
    assert state["over"] and state["result"] == "1-0 by illegal moves" and state["history"] == ["e4"]
    assert not state["humans_turn"] and not state["jevs_turn"]
    assert state["usage"]["calls"] == 1 and state["usage"]["illegal"] == 2 and state["illegal"]["black"] == 2
    assert state["usage"]["cost_usd"] == pytest.approx(0.0018), "both attempts are paid for"
    assert len(seen) == 2
    retry = seen[1]["messages"][-1]["content"]
    assert retry.startswith("'I resign' is not one of the legal labels") and "loses the game" in retry
    assert client.post("/api/move", json={"from": "d2", "to": "d4"}).status_code == 409
    pgn = client.get("/api/pgn").text
    assert '[Result "1-0"]' in pgn and '[Termination "illegal moves"]' in pgn


def test_illegal_answers_add_up_over_the_game_as_in_chess(make_container, make_client):
    seen = []
    replies = ["Kf9", '{"choice": "e5"}', "resign"]
    client = llm_app(make_container, make_client, replies, seen)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"})
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    state = client.post("/api/jev").json()
    assert state["history"] == ["e4", "e5"] and state["illegal"] == {"white": 0, "black": 1} and not state["over"]
    client.post("/api/move", json={"from": "g1", "to": "f3"})
    state = client.post("/api/jev").json()
    assert state["over"] and state["result"] == "1-0 by illegal moves" and state["illegal"]["black"] == 2
    assert len(seen) == 3, "the second illegal answer of the game gets no retry"
    assert state["usage"]["calls"] == 2 and state["usage"]["cost_usd"] == pytest.approx(0.0027)


def test_an_llm_needs_an_openrouter_key(make_container, make_client):
    from system_one_chess.llm import LLMApi

    container = make_container(
        "system_one_chess",
        "pico_fastapi",
        config=configuration(FlatDictSource({"AI_GATEWAY_API_KEY": "vck"}), DictSource({})),
    )
    container.get(LLMApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(llm_stub([], [])))
    client = make_client(container)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    assert client.get("/api/models").json()["models"][-1]["note"] == "needs an OpenRouter key"
    client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"})
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    assert "OpenRouter key" in client.post("/api/jev").json()["error"]


def test_the_suggested_models_come_from_a_file_that_is_read_on_every_request(make_container, make_client, tmp_path):
    from system_one_chess.models import DEFAULT_MODELS_FILE

    shipped = json.loads(DEFAULT_MODELS_FILE.read_text())
    assert any(entry["upstream"] == "x-ai/grok-4.7" for entry in shipped["suggested"])
    assert shipped["logos"]["x-ai"].startswith("https://")
    custom = tmp_path / "models.json"
    custom.write_text('[{"upstream": "acme/chess-1", "tier": "house"}]')
    client = llm_app(make_container, make_client, [], [], MODELS_FILE=str(custom))
    listed = client.get("/api/models").json()
    assert listed["suggested"] == [{"upstream": "acme/chess-1", "tier": "house"}], "a bare list still works"
    assert listed["models"][0]["logo"] == "", "no logos configured, no logo path"
    custom.write_text('{"suggested": [{"upstream": "acme/chess-2"}], "logos": {"jev": "https://logos.test/jev.png"}}')
    listed = client.get("/api/models").json()
    assert listed["suggested"] == [{"upstream": "acme/chess-2", "tier": ""}]
    assert listed["models"][0]["logo"] == "/api/logos/jev" and listed["models"][1]["logo"] == ""
    custom.write_text("not json")
    with pytest.raises(ValueError, match="cannot read the models file"):
        client.get("/api/models")


def test_logos_are_downloaded_once_and_a_missing_one_is_a_soft_404(make_container, make_client, tmp_path):
    from system_one_chess.models import LogoCache

    custom = tmp_path / "models.json"
    custom.write_text(
        '{"suggested": [], "logos": {"jev": "https://logos.test/jev.png", "openai": "https://logos.test/gone.png", '
        '"laya": "https://logos.test/page.html"}}'
    )
    hits = []

    def logos(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.path == "/jev.png":
            return httpx.Response(200, content=b"PNGDATA", headers={"content-type": "image/png"})
        if request.url.path == "/page.html":
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = llm_app(make_container, make_client, [], [], MODELS_FILE=str(custom))
    llm_app.container.get(LogoCache)._client = httpx.AsyncClient(transport=httpx.MockTransport(logos))
    first = client.get("/api/logos/jev")
    assert first.status_code == 200 and first.content == b"PNGDATA" and first.headers["content-type"] == "image/png"
    assert "max-age=86400" in first.headers["cache-control"]
    assert client.get("/api/logos/jev").content == b"PNGDATA" and hits == ["https://logos.test/jev.png"]
    assert client.get("/api/logos/openai").status_code == 404, "an upstream failure is a 404, never an error"
    assert client.get("/api/logos/laya").status_code == 404, "a non-image is not served as a logo"
    assert client.get("/api/logos/nobody").status_code == 404 and len(hits) == 3


def test_finished_games_build_a_session_ranking(make_container, make_client):
    seen = []
    replies = ['{"choice": "e5"}', "nothing", "still nothing"]
    client = llm_app(make_container, make_client, replies, seen)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    assert client.get("/api/standings").json() == {"rows": []}
    client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"})
    client.post("/api/move", json={"from": "e2", "to": "e4"})
    client.post("/api/jev")
    client.post("/api/move", json={"from": "g1", "to": "f3"})
    state = client.post("/api/jev").json()
    assert state["over"] and state["result"] == "1-0 by illegal moves"
    rows = client.get("/api/standings").json()["rows"]
    assert [(row["rank"], row["id"], row["name"], row["points"]) for row in rows] == [
        (1, "human", "You", 1.0),
        (2, "llm:openai/gpt-5-mini", "gpt-5-mini", 0.0),
    ]
    llm = rows[1]
    assert llm["games"] == 1 and llm["losses"] == 1 and llm["forfeits"] == 1 and llm["illegal"] == 2
    assert llm["cost_usd"] == pytest.approx(0.0027) and llm["logo"] == "/api/logos/openai"
    assert rows[0]["cost_usd"] == 0.0 and rows[0]["logo"] == ""
    client.post("/api/new", json={"human": "white", "black": "llm:openai/gpt-5-mini"})
    assert client.get("/api/standings").json()["rows"][0]["games"] == 1, "an unfinished game does not count"


def test_swiss_pairing_matches_neighbours_avoids_rematches_and_gives_the_bye_to_the_last():
    from system_one_chess.tournament import pair_round

    balance = {"a": 0, "b": 0, "c": 0, "d": 0, "e": 0}
    pairs, bye = pair_round(["a", "b", "c", "d", "e"], set(), balance, set())
    assert pairs == [("a", "b"), ("c", "d")] and bye == "e"
    balance = {"a": 1, "b": -1, "c": 1, "d": -1, "e": 0}
    pairs, bye = pair_round(["a", "c", "e", "b", "d"], {frozenset("ab"), frozenset("cd")}, balance, {"e"})
    assert pairs == [("a", "c"), ("b", "e")] and bye == "d", (
        "a-c and e-b are new; b had black, so it takes white; d sits out"
    )
    pairs, bye = pair_round(["a", "b"], {frozenset("ab")}, {"a": 1, "b": -1}, set())
    assert pairs == [("b", "a")] and bye is None, "a rematch with no alternative swaps the colours"


def test_a_swiss_tournament_plays_itself_and_waits_for_you(make_container, make_client):
    seen = []
    client = llm_app(make_container, make_client, ["nothing", "still nothing"] * 6, seen)
    client.post("/api/models", json={"upstream": "openai/gpt-5-mini"})
    assert client.get("/api/tournament").json()["active"] is False
    assert client.post("/api/tournament", json={"participants": ["jev", "nobody"]}).status_code == 409
    assert client.post("/api/tournament", json={"participants": ["jev"]}).status_code == 409
    assert client.post("/api/tournament", json={"participants": ["jev", "laya"], "rounds": 0}).status_code == 422
    assert client.post("/api/tournament/next").status_code == 409

    body = {"participants": ["llm:openai/gpt-5-mini", "jev"], "human": True, "rounds": 2}
    started = client.post("/api/tournament", json=body).json()
    view, state = started["tournament"], started["state"]
    assert view["active"] and view["round"] == 1 and view["rounds_total"] == 2 and view["games_in_round"] == 1
    first = view["rounds"][0]
    assert (first["pairings"][0]["white"]["id"], first["pairings"][0]["black"]["id"]) == (
        "llm:openai/gpt-5-mini",
        "jev",
    )
    assert first["bye"] == {"id": "human", "name": "You", "logo": ""}, "three players: the last seed sits out"
    assert {row["id"]: row["points"] for row in view["standings"]} == {
        "human": 1.0,
        "jev": 0.0,
        "llm:openai/gpt-5-mini": 0.0,
    }
    assert state["human"] == "none" and state["game_id"] == view["current_game_id"]
    assert client.post("/api/tournament/next").status_code == 409, "the game is not over yet"

    state = client.post("/api/jev").json()
    assert state["over"] and state["result"] == "0-1 by illegal moves"
    following = client.post("/api/tournament/next").json()
    view, state = following["tournament"], following["state"]
    assert view["round"] == 2 and view["rounds"][0]["pairings"][0]["result"] == "0-1"
    second = view["rounds"][1]
    assert {second["pairings"][0]["white"]["id"], second["pairings"][0]["black"]["id"]} == {"human", "jev"}, (
        "the two leaders meet"
    )
    assert second["bye"]["id"] == "llm:openai/gpt-5-mini" and state["human"] in ("white", "black")
    rows = {row["id"]: row for row in view["standings"]}
    assert (
        rows["jev"]["points"] == 1.0 and rows["human"]["byes"] == 1 and rows["llm:openai/gpt-5-mini"]["forfeits"] == 1
    )
    assert rows["llm:openai/gpt-5-mini"]["points"] == 1.0 and rows["llm:openai/gpt-5-mini"]["byes"] == 1
    assert rows["jev"]["buchholz"] == 2.0, "Jev faced the LLM (a point from its bye) and now you (a point from yours)"

    client.post("/api/new", json={"human": "white"})
    assert client.post("/api/tournament/next").status_code == 409, "a game started outside the tournament"
    assert client.delete("/api/tournament").json()["active"] is False
