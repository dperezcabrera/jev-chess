"""Who can play: the System One models the server knows about, the LLMs the models file configures, and the
LLMs a session adds on top.

A model id is what the start screen sends for each colour. `jev` and `laya` are the built-in System One
models; an LLM is added by its OpenRouter id, such as `openai/gpt-5.6-luna`, and answers through the chat API."""

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from pico_ioc import cleanup, component

from . import laya as local_model
from .provider import JevProvider, SessionCredentials
from .settings import LLMSettings, ModelsSettings

LLM_LIMIT = 12
LLM_PREFIX = "llm:"
DEFAULT_MODELS_FILE = Path(__file__).with_name("models.json")


def read_models_file(path: Path) -> dict:
    """The models file, read on every call so it can be edited without a restart.

    `suggested` lists the LLM ids the Models dialog offers; `logos` maps a built-in id (`jev`, `laya`) or an
    OpenRouter vendor (`openai`, `x-ai`) to an image URL. A bare list is accepted as `suggested` alone."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read the models file {path}: {e}") from e
    if isinstance(data, list):
        data = {"suggested": data}
    entries = data.get("suggested", []) if isinstance(data, dict) else None
    logos = data.get("logos", {}) if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and "/" in str(entry.get("upstream", "")) for entry in entries
    ):
        raise ValueError(f'{path} must hold "suggested": a list of {{"upstream": "vendor/model", "tier": "..."}}')
    if not isinstance(logos, dict) or not all(isinstance(url, str) for url in logos.values()):
        raise ValueError(f'{path} must hold "logos": an object mapping a vendor or model id to an image URL')
    for entry in entries:
        if entry.get("reasoning") is not None and not isinstance(entry["reasoning"], dict):
            raise ValueError(
                f'{path}: "reasoning" must be an object such as {{"effort": "low"}} or {{"max_tokens": 2000}}'
            )
    return {
        "suggested": [
            {"upstream": str(e["upstream"]), "tier": str(e.get("tier", ""))}
            | ({"reasoning": dict(e["reasoning"])} if e.get("reasoning") else {})
            for e in entries
        ],
        "logos": {str(key): url for key, url in logos.items()},
    }


def logo_key(model_id: str) -> str:
    """What a model's logo is looked up by: the built-in id, or the vendor of an OpenRouter id."""
    return model_id.removeprefix(LLM_PREFIX).split("/", 1)[0] if is_llm(model_id) else model_id


def logo_path(model_id: str, logos: dict[str, str]) -> str:
    key = logo_key(model_id)
    return f"/api/logos/{key}" if key in logos else ""


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    kind: str
    provider: str
    upstream: str
    ready: bool
    note: str = ""
    logo: str = ""
    removable: bool = False


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


def is_llm(kind: str) -> bool:
    return kind.startswith(LLM_PREFIX)


def llm_id(upstream: str) -> str:
    return f"llm:{upstream}"


def llm_name(upstream: str) -> str:
    return upstream.removeprefix(LLM_PREFIX).split("/", 1)[-1]


@component
class ModelRegistry:
    def __init__(self, provider: JevProvider, settings: ModelsSettings, llm: LLMSettings):
        self._provider = provider
        self._file = Path(settings.file) if settings.file else DEFAULT_MODELS_FILE
        self._default_reasoning = {"effort": llm.reasoning_effort} if llm.reasoning_effort else None

    def reasoning_for(self, upstream: str) -> dict | None:
        """OpenRouter's `reasoning` parameter for a model: its own entry in the models file, else the
        LLM_REASONING_EFFORT default, else nothing, which leaves the model's own default."""
        for entry in read_models_file(self._file)["suggested"]:
            if entry["upstream"] == upstream and entry.get("reasoning"):
                return dict(entry["reasoning"])
        return dict(self._default_reasoning) if self._default_reasoning else None

    def suggested(self) -> list[dict]:
        return read_models_file(self._file)["suggested"]

    def logo_url(self, key: str) -> str:
        """The configured image URL behind a logo key, empty when there is none."""
        return read_models_file(self._file)["logos"].get(key, "")

    def name_of(self, model_id: str) -> str:
        if model_id == "human":
            return "You"
        return {"jev": "Jev", "laya": "Laya"}.get(model_id) or llm_name(model_id)

    def logo_of(self, model_id: str) -> str:
        return logo_path(model_id, read_models_file(self._file)["logos"]) if model_id != "human" else ""

    def list(self, credentials: SessionCredentials, session: SessionModels) -> list[Model]:
        file = read_models_file(self._file)
        logos = file["logos"]
        jev = self._provider.gateway(credentials, "jev")
        openrouter = self._provider.gateway_for("openrouter", credentials)
        installed = local_model.available()
        models = [
            Model(
                "jev",
                "Jev",
                "system_one",
                jev.name,
                jev.model,
                jev.ready,
                "" if jev.ready else "needs a key",
                logo_path("jev", logos),
            ),
            Model(
                "laya",
                "Laya",
                "system_one",
                "laya",
                self._provider.gateway_for("laya").model,
                installed,
                "" if installed else "not installed",
                logo_path("laya", logos),
            ),
        ]
        configured = [entry["upstream"] for entry in file["suggested"]]
        for upstream in configured + [u for u in session.llms if u not in configured]:
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
                    logo_path(llm_id(upstream), logos),
                    upstream not in configured,
                )
            )
        return models

    def get(self, model_id: str, credentials: SessionCredentials, session: SessionModels) -> Model:
        for model in self.list(credentials, session):
            if model.id == model_id:
                return model
        raise KeyError(model_id)


LOGO_LIMIT = 2_000_000


@component
class LogoCache:
    """Downloads each configured logo once per process and serves it from memory; a failed download is not cached."""

    def __init__(self, registry: ModelRegistry):
        self._registry = registry
        self._cache: dict[str, tuple[bytes, str]] = {}
        self._client = httpx.AsyncClient(follow_redirects=True, timeout=10.0)

    async def get(self, key: str) -> tuple[bytes, str] | None:
        if key in self._cache:
            return self._cache[key]
        url = self._registry.logo_url(key)
        if not url:
            return None
        try:
            response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        media_type = response.headers.get("content-type", "").split(";")[0].strip()
        if not media_type.startswith("image/") or len(response.content) > LOGO_LIMIT:
            return None
        self._cache[key] = (response.content, media_type)
        return self._cache[key]

    @cleanup
    def close(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())
