"""Who can play: the System One models the server knows about and the LLMs a session adds.

A model id is what the start screen sends for each colour. `jev` and `laya` are the built-in System One
models; an LLM is added by its OpenRouter id, such as `openai/gpt-5.6-luna`, and answers through the chat API."""

from dataclasses import dataclass

from pico_ioc import component

from . import laya as local_model
from .provider import JevProvider, SessionCredentials

SUGGESTED_LLMS = {
    "anthropic/claude-fable-5.1": "frontier",
    "anthropic/claude-opus-5": "frontier",
    "meta/muse-spark-1.3": "frontier",
    "google/gemini-3.8-flash": "frontier",
    "google/gemini-3.1-pro-preview": "frontier",
    "openai/gpt-5.6-luna": "ultra cheap",
    "google/gemma-4-31b-it": "ultra cheap",
}
LLM_LIMIT = 12


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    kind: str
    provider: str
    upstream: str
    ready: bool
    note: str = ""


@component(scope="session")
class SessionModels:
    """LLM ids this browser session added; they live in memory like its key."""

    def __init__(self):
        self.llms: list[str] = []

    def add(self, upstream: str) -> None:
        upstream = upstream.strip()
        if not upstream or "/" not in upstream or any(c.isspace() for c in upstream):
            raise ValueError("an OpenRouter model id looks like vendor/model")
        if upstream not in self.llms:
            if len(self.llms) >= LLM_LIMIT:
                raise ValueError(f"at most {LLM_LIMIT} models per session")
            self.llms.append(upstream)

    def remove(self, upstream: str) -> None:
        self.llms = [entry for entry in self.llms if entry != upstream]


def llm_id(upstream: str) -> str:
    return f"llm:{upstream}"


def llm_name(upstream: str) -> str:
    return upstream.split("/", 1)[-1]


@component
class ModelRegistry:
    def __init__(self, provider: JevProvider):
        self._provider = provider

    def list(self, credentials: SessionCredentials, session: SessionModels) -> list[Model]:
        jev = self._provider.gateway(credentials, "jev")
        openrouter = self._provider.gateway_for("openrouter", credentials)
        installed = local_model.available()
        models = [
            Model("jev", "Jev", "system_one", jev.name, jev.model, jev.ready, "" if jev.ready else "needs a key"),
            Model(
                "laya",
                "Laya",
                "system_one",
                "laya",
                self._provider.gateway_for("laya").model,
                installed,
                "" if installed else "not installed",
            ),
        ]
        for upstream in session.llms:
            ready = bool(openrouter.api_key)
            models.append(
                Model(
                    llm_id(upstream),
                    llm_name(upstream),
                    "llm",
                    "openrouter",
                    upstream,
                    ready,
                    "" if ready else "needs an OpenRouter key",
                )
            )
        return models

    def get(self, model_id: str, credentials: SessionCredentials, session: SessionModels) -> Model:
        for model in self.list(credentials, session):
            if model.id == model_id:
                return model
        raise KeyError(model_id)
