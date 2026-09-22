"""An LLM answering the same question as a System One model: pick one option from a labelled list.

The chat completion is asked for a JSON object with the chosen label. A reply that names no option is an
illegal move: the first earns one retry that quotes the mistake, the second forfeits the game."""

import json
import re
import time
from dataclasses import dataclass

import httpx
from pico_ioc import cleanup, component

from .provider import Gateway

SYSTEM_PROMPT = (
    "You are playing a game. You will receive the game state, the exact list of legal labels as a JSON array, "
    "and a description of each one. Only those labels are legal moves. "
    'Reply with a JSON object only: {"choice": "<label>"}, copying one label verbatim. No other text. '
    "A reply that is not one of the labels is an illegal move, and two illegal moves in one turn lose the game."
)
RETRY_PROMPT = (
    "{answer!r} is not one of the legal labels; that was an illegal move and a second one loses the game. "
    'Reply with a JSON object only, {{"choice": "<label>"}}, copying a label from the array exactly.'
)
MAX_TOKENS = 4000


@dataclass(frozen=True)
class LLMAnswer:
    choice: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float
    retried: bool


class LLMError(Exception):
    pass


class IllegalAnswers(LLMError):
    """Two replies in a row named no legal option; the game is forfeited and the attempts still cost."""

    def __init__(self, upstream: str, input_tokens: int, output_tokens: int, cost_usd: float, seconds: float):
        super().__init__(f"{upstream} did not name a legal option after two attempts")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.seconds = seconds


def parse_choice(text: str, labels: list[str]) -> str | None:
    """The label in a reply, by JSON first and by exact text second; None when nothing matches."""
    candidate = None
    try:
        candidate = json.loads(text).get("choice")
    except (ValueError, AttributeError):
        match = re.search(r'"choice"\s*:\s*"([^"]+)"', text)
        candidate = match.group(1) if match else None
    if isinstance(candidate, str) and candidate.strip() in labels:
        return candidate.strip()
    exact = [label for label in labels if re.search(rf"(?<![\w-]){re.escape(label)}(?![\w-])", text)]
    return exact[0] if len(exact) == 1 else None


def render(state: dict, instructions: str, criteria: dict[str, str | None]) -> str:
    options = "\n".join(f"- {label}" + (f": {text}" if text else "") for label, text in criteria.items())
    labels = json.dumps(list(criteria))
    return f"{instructions}\n\nState:\n{json.dumps(state, indent=1)}\n\nLegal labels:\n{labels}\n\nOptions:\n{options}"


@component
class LLMApi:
    def __init__(self):
        self._client = httpx.AsyncClient()

    async def complete(self, gateway: Gateway, upstream: str, messages: list[dict]) -> dict:
        body = {"model": upstream, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0}
        response = await self._client.post(
            f"{gateway.base_url}/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {gateway.api_key}", "X-Title": "system-one-chess"},
            timeout=max(gateway.timeout_seconds, 120),
        )
        response.raise_for_status()
        return response.json()

    async def choose(
        self, gateway: Gateway, upstream: str, state: dict, instructions: str, criteria: dict
    ) -> LLMAnswer:
        labels = list(criteria)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": render(state, instructions, criteria)},
        ]
        totals = {"input": 0, "output": 0, "cost": 0.0}
        started = time.perf_counter()
        for attempt in range(2):
            try:
                response = await self.complete(gateway, upstream, messages)
                text = response["choices"][0]["message"]["content"] or ""
            except httpx.HTTPStatusError as e:
                raise LLMError(f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as e:
                raise LLMError(f"request to {upstream} failed: {e}") from e
            usage = response.get("usage") or {}
            totals["input"] += int(usage.get("prompt_tokens") or 0)
            totals["output"] += int(usage.get("completion_tokens") or 0)
            totals["cost"] += float(usage.get("cost") or 0.0)
            choice = parse_choice(text, labels)
            if choice is not None:
                return LLMAnswer(
                    choice,
                    totals["input"],
                    totals["output"],
                    totals["cost"],
                    time.perf_counter() - started,
                    attempt > 0,
                )
            messages += [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": RETRY_PROMPT.format(answer=text.strip()[:200])},
            ]
        raise IllegalAnswers(upstream, totals["input"], totals["output"], totals["cost"], time.perf_counter() - started)

    @cleanup
    def close(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())
