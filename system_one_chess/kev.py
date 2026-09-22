"""Kev (github.com/jaredpalmer/kev, Apache-2.0): a family of open System One models built on Qwen3.5 that
speak TypeSafe's `/v1/systemone` protocol. A local `kev.serve` is used when `KEV_BASE_URL` points at one;
otherwise the public demo Space on Hugging Face answers, on a shared GPU, with the size `KEV_SIZE` names."""

import asyncio
import json

import httpx
from pico_ioc import cleanup, component

from .retry import post_with_retries
from .settings import KevSettings

NOT_CONFIGURED = (
    "Kev is not configured on this server: set KEV_BASE_URL to a running `kev.serve`, or KEV_ENDPOINT to its "
    "demo Space."
)
REMOTE_NOTE = "Hugging Face Space, shared GPU"


@component
class KevModel:
    def __init__(self, settings: KevSettings):
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=max(settings.timeout_seconds, 30.0), follow_redirects=True)

    @property
    def mode(self) -> str:
        """`local` (a kev.serve at KEV_BASE_URL), `remote` (the Space) or `none`."""
        if self._settings.base_url.strip():
            return "local"
        return "remote" if self._settings.endpoint.strip() else "none"

    @property
    def ready(self) -> bool:
        return self.mode != "none"

    @property
    def upstream(self) -> str:
        return self._settings.model if self.mode == "local" else f"jaredpalmer/{self._settings.size.lower()}"

    async def system_one(self, body: dict) -> dict:
        mode = self.mode
        if mode == "local":
            headers = {"Authorization": f"Bearer {self._settings.api_key}"} if self._settings.api_key else {}
            response = await post_with_retries(
                self._client,
                f"{self._settings.base_url.rstrip('/')}/v1/systemone",
                json={**body, "model": self._settings.model},
                headers=headers,
                timeout=self._settings.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        if mode == "remote":
            return await self._remote(body)
        raise RuntimeError(NOT_CONFIGURED)

    async def _remote(self, body: dict) -> dict:
        """The Space's Gradio API: `decide` takes the state, the questions, the size and the demo's toggles
        (calibrated on, date facts off, no stability check, two permutations) and streams an HTML view, the
        System One JSON and a note; the JSON is what we keep."""
        base = self._settings.endpoint.rstrip("/")
        data = [json.dumps(body["state"]), json.dumps(body["questions"]), self._settings.size, True, False, False, 2]
        submitted = await self._client.post(f"{base}/gradio_api/call/decide", json={"data": data})
        submitted.raise_for_status()
        event = submitted.json()["event_id"]
        streamed = await self._client.get(f"{base}/gradio_api/call/decide/{event}")
        streamed.raise_for_status()
        payload = None
        for line in streamed.text.splitlines():
            if line.startswith("event: error"):
                raise RuntimeError(f"the Kev Space reported an error: {streamed.text[:300]}")
            if line.startswith("data: "):
                payload = json.loads(line[6:])
        if not payload or len(payload) < 2 or not isinstance(payload[1], (str, dict)):
            raise RuntimeError(f"unexpected reply from the Kev Space: {streamed.text[:300]}")
        response = json.loads(payload[1]) if isinstance(payload[1], str) else payload[1]
        if "answers" not in response:
            raise RuntimeError(f"unexpected reply from the Kev Space: {str(payload[1])[:300]}")
        return response

    @cleanup
    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())
