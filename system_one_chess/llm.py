"""An LLM answering the same question as a System One model: pick one option from a labelled list.

The chat completion is asked for a JSON object with the chosen label. A reply that names no option is an
illegal move; the caller says how many the model may still make, and one more than that forfeits. An empty
reply, or one the gateway flags as an error, is no answer at all: it is asked again and never counts as
illegal, and after a few in a row the call fails like any other gateway error. A thinking model that ran out
of tokens before answering is asked once more with a far larger budget and little reasoning, and only a second
truncated reply counts as an illegal answer."""

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx
from pico_ioc import cleanup, component

from .provider import Gateway
from .retry import post_with_retries

SYSTEM_PROMPT = (
    "You are playing a game. You will receive the game state, the exact list of legal labels as a JSON array, "
    "and a description of each one. Only those labels are legal moves. "
    'Reply with a JSON object only: {"choice": "<label>"}, copying one label verbatim. No other text. '
    "A reply that is not one of the labels is an illegal move, and two illegal moves in a game lose it, as in chess."
)
RETRY_PROMPT = (
    "{answer!r} is not one of the legal labels; that was an illegal move and one more loses the game. "
    'Reply with a JSON object only, {{"choice": "<label>"}}, copying one label exactly from this array: {labels}'
)
MAX_TOKENS = 8000
TRUNCATED_TOKENS = 24000
TRIM = ".,;:!?*'\"`"
BLANK_RETRIES = 3


@dataclass(frozen=True)
class LLMAnswer:
    choice: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    seconds: float
    illegal: int
    illegal_answers: tuple[str, ...] = ()


class LLMError(Exception):
    pass


class IllegalAnswers(LLMError):
    """The model used up its illegal answers without naming a legal option; the attempts still cost."""

    def __init__(
        self,
        upstream: str,
        illegal: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        seconds: float,
        answers: tuple[str, ...] = (),
    ):
        super().__init__(f"{upstream} did not name a legal option after {illegal} illegal answers")
        self.illegal = illegal
        self.answers = answers
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.seconds = seconds


def parse_choice(text: str, labels: list[str], aliases: dict[str, str] | None = None) -> str | None:
    """The label in a reply: the JSON `choice` first, then a single label mentioned in the text.

    `aliases` maps other spellings of a label to it (the caller knows its notation: `e2e4` for `e4`, `Nf3`
    for `Nf3+`); matching ignores case and trailing punctuation, so only a genuinely different answer fails."""
    known = {label.lower(): label for label in labels}
    known.update({alias.lower(): label for alias, label in (aliases or {}).items() if label in labels})
    candidate = None
    try:
        candidate = json.loads(text).get("choice")
    except (ValueError, AttributeError):
        match = re.search(r'"choice"\s*:\s*"([^"]+)"', text)
        candidate = match.group(1) if match else None
    if isinstance(candidate, str):
        cleaned = candidate.strip().strip(TRIM).lower()
        if cleaned in known:
            return known[cleaned]
    found = {
        known[token.lower()]
        for token in re.findall(r"[^\s\"',;()]+", text)
        if token.strip(TRIM).lower() in known
        for token in [token.strip(TRIM)]
    }
    return found.pop() if len(found) == 1 else None


def choice_schema(labels: list[str]) -> dict:
    """Structured output that only admits one of the labels, for the models that honour a JSON schema."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "choice",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"choice": {"type": "string", "enum": labels}},
                "required": ["choice"],
                "additionalProperties": False,
            },
        },
    }


def reply_text(response: dict) -> str:
    """The reply, falling back to the reasoning when a thinking model spent its tokens there."""
    message = response["choices"][0]["message"]
    return (message.get("content") or message.get("reasoning") or message.get("reasoning_content") or "")[:20000]


def render(state: dict, instructions: str, criteria: dict[str, str | None]) -> str:
    options = "\n".join(f"- {label}" + (f": {text}" if text else "") for label, text in criteria.items())
    labels = json.dumps(list(criteria))
    return f"{instructions}\n\nState:\n{json.dumps(state, indent=1)}\n\nLegal labels:\n{labels}\n\nOptions:\n{options}"


@component
class LLMApi:
    def __init__(self):
        self._client = httpx.AsyncClient()
        self._no_schema: set[str] = set()

    async def complete(
        self,
        gateway: Gateway,
        upstream: str,
        messages: list[dict],
        labels: list[str],
        brief: bool = False,
        reasoning: dict | None = None,
    ) -> dict:
        """One chat completion, with a JSON schema that only admits the labels; a model that rejects the
        schema (HTTP 400) is asked again without it and remembered. `reasoning` is OpenRouter's parameter
        for how much a thinking model may think; `brief` overrides it with a much larger token budget and
        little reasoning, for when the model ran out of tokens while thinking."""
        body = {"model": upstream, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": 0}
        if reasoning:
            body["reasoning"] = dict(reasoning)
        if brief:
            body["max_tokens"] = TRUNCATED_TOKENS
            body["reasoning"] = {"effort": "low"}
        if upstream not in self._no_schema:
            body["response_format"] = choice_schema(labels)
        response = await self._post(gateway, body)
        if response.status_code == 400 and "response_format" in body:
            self._no_schema.add(upstream)
            del body["response_format"]
            response = await self._post(gateway, body)
        response.raise_for_status()
        return response.json()

    async def _post(self, gateway: Gateway, body: dict) -> httpx.Response:
        return await post_with_retries(
            self._client,
            f"{gateway.base_url}/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {gateway.api_key}", "X-Title": "system-one-chess"},
            timeout=max(gateway.timeout_seconds, 120),
        )

    async def choose(
        self,
        gateway: Gateway,
        upstream: str,
        state: dict,
        instructions: str,
        criteria: dict,
        attempts: int = 2,
        aliases: dict[str, str] | None = None,
        reasoning: dict | None = None,
    ) -> LLMAnswer:
        """`attempts` is how many replies the model gets; every one that names no label is an illegal answer."""
        labels = list(criteria)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": render(state, instructions, criteria)},
        ]
        totals = {"input": 0, "output": 0, "cost": 0.0}
        wrong: list[str] = []
        blanks = 0
        truncated = 0
        started = time.perf_counter()
        attempt = 0
        while attempt < max(1, attempts):
            try:
                response = await self.complete(
                    gateway, upstream, messages, labels, brief=truncated > 0, reasoning=reasoning
                )
                text = reply_text(response)
                finish = response["choices"][0].get("finish_reason")
            except httpx.HTTPStatusError as e:
                raise LLMError(f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as e:
                raise LLMError(f"request to {upstream} failed: {e}") from e
            usage = response.get("usage") or {}
            totals["input"] += int(usage.get("prompt_tokens") or 0)
            totals["output"] += int(usage.get("completion_tokens") or 0)
            totals["cost"] += float(usage.get("cost") or 0.0)
            if finish == "error" or not text.strip():
                blanks += 1
                if blanks > BLANK_RETRIES:
                    raise LLMError(f"{upstream} returned no answer {blanks} times in a row (finish_reason {finish!r})")
                await asyncio.sleep(1.0)
                continue
            choice = parse_choice(text, labels, aliases)
            if choice is None and finish == "length" and truncated == 0:
                truncated += 1
                continue
            attempt += 1
            if choice is not None:
                return LLMAnswer(
                    choice,
                    totals["input"],
                    totals["output"],
                    totals["cost"],
                    time.perf_counter() - started,
                    attempt - 1,
                    tuple(wrong),
                )
            wrong.append(("[ran out of tokens while thinking] " if finish == "length" else "") + text.strip()[:200])
            messages += [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": RETRY_PROMPT.format(answer=text.strip()[:200], labels=json.dumps(labels))},
            ]
        raise IllegalAnswers(
            upstream,
            max(1, attempts),
            totals["input"],
            totals["output"],
            totals["cost"],
            time.perf_counter() - started,
            tuple(wrong),
        )

    @cleanup
    def close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())
