"""Laya (github.com/convaiinnovations, Apache-2.0): an open System One model that answers the same typed
questions as Jev, running on this machine. Loaded once per process, on first use, off the event loop."""

import asyncio
import threading
import time

from pico_ioc import component

from .settings import LayaSettings

NOT_INSTALLED = (
    "Laya is not installed on this server. Install it with `pip install 'jev-chess[laya]'` "
    "(about 1.2 GB with PyTorch) or pick another provider in Settings."
)


def available() -> bool:
    try:
        import laya  # noqa: F401
    except ImportError:
        return False
    return True


@component
class LayaModel:
    """The loaded model, shared by every session. `ask` mirrors the gateway's system_one response shape."""

    def __init__(self, settings: LayaSettings):
        self._settings = settings
        self._agent = None
        self._lock = threading.Lock()
        self.load_seconds = 0.0

    def _load(self):
        with self._lock:
            if self._agent is None:
                import laya

                started = time.perf_counter()
                self._agent = laya.load(self._settings.model, device=self._settings.device or None)
                self.load_seconds = time.perf_counter() - started
        return self._agent

    async def system_one(self, body: dict) -> dict:
        if not available():
            raise RuntimeError(NOT_INSTALLED)
        agent = await asyncio.to_thread(self._load)
        return await asyncio.to_thread(agent.system_one, body["state"], body["questions"])

    @property
    def loaded(self) -> bool:
        return self._agent is not None
