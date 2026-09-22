from dataclasses import dataclass

from pico_ioc import component

from . import laya as local_model
from .settings import JevSettings, LayaSettings, OpenRouterSettings, VercelSettings

LABELS = {"vercel": "Vercel AI Gateway", "openrouter": "OpenRouter", "laya": "Laya, local and open source"}
DEFAULT_MODELS = {"openrouter": "jev-latest", "vercel": "typesafe-ai/jev", "laya": "convaiinnovations/laya"}
LOCAL = "laya"
NO_KEY = (
    "No API key. Open Settings (the gear icon) and add a Vercel AI Gateway or OpenRouter key, "
    "or set AI_GATEWAY_API_KEY or OPENROUTER_API_KEY on the server."
)


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Gateway:
    name: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    own_key: bool

    @property
    def local(self) -> bool:
        return self.name == LOCAL

    @property
    def ready(self) -> bool:
        return self.local or bool(self.api_key)


@component(scope="session")
class SessionCredentials:
    """A key typed into the browser: kept in memory for that session only, never sent back."""

    def __init__(self):
        self.provider = ""
        self.api_key = ""

    def store(self, provider: str, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key.strip()

    def clear(self) -> None:
        self.provider = ""
        self.api_key = ""


@component
class JevProvider:
    """Which gateway serves Jev: the session's choice, else JEV_PROVIDER, else the one whose key is set."""

    def __init__(self, jev: JevSettings, openrouter: OpenRouterSettings, vercel: VercelSettings, laya: LayaSettings):
        self._settings = {"openrouter": openrouter, "vercel": vercel, LOCAL: laya}
        self._model = jev.model
        name = jev.provider.strip().lower()
        if name and name not in self._settings:
            raise ProviderError(f"JEV_PROVIDER must be one of {sorted(self._settings)}, not {jev.provider!r}")
        self._default = name or next(
            (key for key, entry in self._settings.items() if getattr(entry, "api_key", "")), None
        )
        if self._default is None:
            self._default = LOCAL if local_model.available() else "openrouter"

    def gateway(self, credentials: SessionCredentials | None = None) -> Gateway:
        name = (credentials.provider if credentials else "") or self._default
        if name not in self._settings:
            raise ProviderError(f"unknown provider: {name!r}")
        entry = self._settings[name]
        if name == LOCAL:
            return Gateway(name, "", "", entry.model, 0.0, False)
        own_key = bool(credentials and credentials.api_key)
        return Gateway(
            name=name,
            base_url=entry.base_url,
            api_key=credentials.api_key if own_key else entry.api_key,
            model=self._model if self._model and name == self._default else DEFAULT_MODELS[name],
            timeout_seconds=entry.timeout_seconds,
            own_key=own_key,
        )

    @staticmethod
    def cost_usd(response: dict) -> float:
        usage = response.get("usage") or {}
        metadata = (response.get("provider_metadata") or {}).get("gateway") or {}
        return float(usage.get("cost") or metadata.get("cost") or 0.0)
