"""One place for the transient failures of a gateway: rate limits, server errors, timeouts and dropped
connections are retried with a growing pause before they are reported. Everything else comes back at once."""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

RETRIED_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
ATTEMPTS = 4
PAUSES = (1.0, 3.0, 8.0)


async def post_with_retries(
    client: httpx.AsyncClient, url: str, *, json: dict, headers: dict, timeout: float
) -> httpx.Response:
    """POSTs and returns the response, retrying a transient failure up to ATTEMPTS times.

    A response with a status in RETRIED_STATUSES, a timeout or a network error is retried; the last failure
    is raised (an `httpx.HTTPStatusError` for a status, the transport error otherwise)."""
    for attempt in range(ATTEMPTS):
        try:
            response = await client.post(url, json=json, headers=headers, timeout=timeout)
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            if attempt == ATTEMPTS - 1:
                raise
            log.warning("retrying %s after %s (attempt %d)", url, type(error).__name__, attempt + 1)
        else:
            if response.status_code not in RETRIED_STATUSES or attempt == ATTEMPTS - 1:
                return response
            log.warning("retrying %s after HTTP %d (attempt %d)", url, response.status_code, attempt + 1)
        await asyncio.sleep(PAUSES[min(attempt, len(PAUSES) - 1)])
    raise AssertionError("unreachable")
