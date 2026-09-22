import asyncio
import json

import httpx
import pytest

from system_one_chess import retry
from system_one_chess.retry import post_with_retries


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(retry.asyncio, "sleep", lambda _: real_sleep(0))


def run(handler, url="https://gw.test/v1/x"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return asyncio.run(post_with_retries(client, url, json={"a": 1}, headers={}, timeout=5.0))


def test_rate_limits_server_errors_and_timeouts_are_retried_then_succeed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, text="slow down")
        if len(calls) == 2:
            raise httpx.ReadTimeout("slow", request=request)
        if len(calls) == 3:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"ok": True})

    response = run(handler)
    assert response.status_code == 200 and len(calls) == 4 and all(c == {"a": 1} for c in calls)


def test_the_last_failure_is_reported_and_other_statuses_come_back_at_once():
    def always_busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    assert run(always_busy).status_code == 503, (
        "after the last attempt the response is returned for the caller to raise"
    )
    calls = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, text="age check")

    assert run(forbidden).status_code == 403 and len(calls) == 1, "a 403 is final, not retried"

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(httpx.ConnectError):
        run(dead)
