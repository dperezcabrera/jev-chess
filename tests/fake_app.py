"""The app with every model faked, for trying the interface without keys or network.

Jev and Laya pick a random legal move, an LLM names a random label, and the model `bad/model` always answers
illegally, so forfeits can be seen. Run it with `uvicorn --app-dir tests fake_app:create_app --factory`."""

import json
import random

import httpx
from fastapi import FastAPI
from pico_boot import init
from pico_ioc import DictSource, FlatDictSource, configuration

from system_one_chess import laya as laya_module
from system_one_chess.jev import JevApi
from system_one_chess.laya import LayaModel
from system_one_chess.llm import LLMApi

laya_module.available = lambda: True


def jev(request: httpx.Request) -> httpx.Response:
    sans = list(json.loads(request.content)["questions"]["move"]["criteria"])
    random.shuffle(sans)
    answers = {"move": {"choice": sans[0], "probabilities": dict(zip(sans[:3], (0.55, 0.3, 0.15)))}}
    usage = {"input_tokens": 300, "output_tokens": 20, "cost": 0.00002}
    return httpx.Response(200, json={"answers": answers, "usage": usage})


def llm(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    labels = json.loads(body["messages"][1]["content"].split("Legal labels:\n")[1].split("\n\n")[0])
    text = "I resign" if body["model"] == "bad/model" else json.dumps({"choice": random.choice(labels)})
    usage = {"prompt_tokens": 1200, "completion_tokens": 9, "cost": 0.0012}
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": usage})


class FakeLaya:
    def system_one(self, state, questions):
        sans = list(questions["move"]["criteria"])
        random.shuffle(sans)
        answers = {"move": {"choice": sans[0], "probabilities": dict(zip(sans[:3], (0.5, 0.3, 0.2)))}}
        return {"model": "laya", "answers": answers, "usage": {"input_tokens": 300, "output_tokens": 0}}


def create_app() -> FastAPI:
    config = configuration(FlatDictSource({"OPENROUTER_API_KEY": "fake"}), DictSource({}))
    container = init(modules=["system_one_chess"], config=config)
    container.get(JevApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(jev))
    container.get(LLMApi)._client = httpx.AsyncClient(transport=httpx.MockTransport(llm))
    container.get(LayaModel)._load = lambda: FakeLaya()
    return container.get(FastAPI)
