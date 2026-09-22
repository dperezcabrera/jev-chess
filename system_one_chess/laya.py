"""Laya (github.com/convaiinnovations, Apache-2.0): an open System One model that answers the same typed
questions as Jev. It runs on this machine when the `laya` package is installed (loaded once per process, on
first use, off the event loop); otherwise it is asked through Convai's public demo Space on Hugging Face,
whose playground takes a state and questions and answers in the System One shape, on a shared free GPU."""

import asyncio
import json
import threading
import time

import httpx
from pico_ioc import cleanup, component

from .settings import LayaSettings

NOT_INSTALLED = (
    "Laya is not installed on this server and no LAYA_ENDPOINT is set. Install it with "
    "`pip install 'system-one-chess[laya]'` (about 1.2 GB with PyTorch) or pick another provider in Settings."
)
REMOTE_NOTE = "Hugging Face Space, shared free GPU"


def available() -> bool:
    """Whether the model can run on this machine."""
    try:
        import laya  # noqa: F401
    except ImportError:
        return False
    return True


@component
class LayaModel:
    """The model, local or remote, shared by every session; `system_one` mirrors the gateway's response shape."""

    def __init__(self, settings: LayaSettings):
        self._settings = settings
        self._agent = None
        self._lock = threading.Lock()
        self._client = httpx.AsyncClient(timeout=90.0, follow_redirects=True)
        self.load_seconds = 0.0

    @property
    def mode(self) -> str:
        """`local`, `remote` or `none`."""
        if available():
            return "local"
        return "remote" if self._settings.endpoint.strip() else "none"

    @property
    def ready(self) -> bool:
        return self.mode != "none"

    def _load(self):
        with self._lock:
            if self._agent is None:
                import laya

                started = time.perf_counter()
                self._agent = laya.load(self._settings.model, device=self._settings.device or None)
                self.load_seconds = time.perf_counter() - started
        return self._agent

    async def system_one(self, body: dict) -> dict:
        mode = self.mode
        if mode == "local":
            agent = await asyncio.to_thread(self._load)
            return await asyncio.to_thread(agent.system_one, body["state"], body["questions"])
        if mode == "remote":
            return await self._remote(body)
        raise RuntimeError(NOT_INSTALLED)

    async def _remote(self, body: dict) -> dict:
        """The Space's Gradio API: a call returns an event id, and reading the event streams the result."""
        base = self._settings.endpoint.rstrip("/")
        payload = {"data": [json.dumps(body["state"]), json.dumps(body["questions"])]}
        submitted = await self._client.post(f"{base}/gradio_api/call/run_playground", json=payload)
        submitted.raise_for_status()
        event = submitted.json()["event_id"]
        streamed = await self._client.get(f"{base}/gradio_api/call/run_playground/{event}")
        streamed.raise_for_status()
        data = None
        for line in streamed.text.splitlines():
            if line.startswith("event: error"):
                raise RuntimeError(f"the Laya Space reported an error: {streamed.text[:300]}")
            if line.startswith("data: "):
                data = json.loads(line[6:])
        if not data or len(data) < 2:
            raise RuntimeError(f"unexpected reply from the Laya Space: {streamed.text[:300]}")
        response = json.loads(data[1])
        if "answers" not in response:
            raise RuntimeError(f"unexpected reply from the Laya Space: {data[1][:300]}")
        return response

    @property
    def loaded(self) -> bool:
        return self._agent is not None

    @cleanup
    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())
