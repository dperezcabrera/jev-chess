# jev-chess

Play chess in your browser against [Jev](https://typesafe.ai), TypeSafe AI's System One model, called through [OpenRouter](https://openrouter.ai/typesafe).

Jev does not generate text. It answers typed questions about a state with calibrated probabilities. That maps cleanly onto chess: every turn is **one Choice question whose options are the legal moves**. A chess position has at most 218 legal moves and a Choice accepts up to 255 options, so a single request always fits and Jev can never return an illegal move. A typical reply takes about half a second.

The side panel shows how sure Jev was about its last decision:

| Move | Probability |
|---|---:|
| e5 | 45.0% |
| Nf6 | 42.0% |
| Nc6 | 5.0% |

> Jev is a fast classifier, not a chess engine. Expect plausible moves, not strong ones. This project is a demo of the System One decision pattern.

## Quick start with Docker

You only need Docker and an [OpenRouter API key](https://openrouter.ai/settings/keys).

```sh
docker build -t jev-chess .
docker run --rm -p 127.0.0.1:8000:8000 -e OPENROUTER_API_KEY=sk-or-... jev-chess
```

Open http://localhost:8000.

If the key is already exported in your shell, pass it through without typing it:

```sh
docker run --rm -p 127.0.0.1:8000:8000 -e OPENROUTER_API_KEY jev-chess
```

Or keep it in a `.env` file (see below) and use `--env-file .env`.

The key is only read at run time. It is never baked into the image.

## Local setup

Requires Python 3.11+.

```sh
git clone https://github.com/dperezcabrera/jev-chess.git
cd jev-chess
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
```

Open `.env` and paste your key:

```sh
OPENROUTER_API_KEY=sk-or-...
```

Then run:

```sh
.venv/bin/jev-chess
```

`.env` is git-ignored, so the key never ends up in the repository. Variables already set in your shell take precedence over `.env`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | required | Your OpenRouter key |
| `JEV_MODEL` | `jev-latest` | Model ID, for example `jev-1.13` to pin a version |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api` | System One API base URL |
| `OPENROUTER_TIMEOUT_SECONDS` | `30` | Request timeout |
| `SESSION_SECRET` | random per process | Signs the session cookie; set it to keep sessions across restarts of a multi-worker setup |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | Bind port |

## Playing

Pick a side on the start screen: White, Black, or Spectator (Jev plays both sides). Drag or click pieces; only legal moves are allowed. Use **Promote to** before pushing a pawn to the last rank if you want something other than a queen.

Every browser session gets its own game with its own id, so several people can play against the same server at once. Games live in memory and are lost when the server restarts.

## How it works

Each Jev turn sends one request to `POST https://openrouter.ai/api/v1/systemone`:

```json
{
  "model": "jev-latest",
  "state": {
    "game": "chess",
    "side_to_move": "black",
    "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "board": "r n b q k b n r\n...",
    "moves_so_far": "1. e4"
  },
  "questions": {
    "move": {
      "type": "choice",
      "instructions": "You are a strong chess player playing black. Which move is best? ...",
      "criteria": {
        "Nf6": "knight g8 to f6",
        "e5": "pawn e7 to e5",
        "...": "..."
      }
    }
  }
}
```

Each option carries a short description (captures, checks, checkmate, whether the moved piece can be recaptured) so the model has tactical hints, not just move names. The response contains the selected `choice` plus a probability for every option.

### Architecture

The backend is built with the [pico framework](https://github.com/dperezcabrera/pico-ioc), a Spring Boot style stack for Python: constructor injection, controllers, declarative HTTP clients and typed settings.

| Module | Role |
|---|---|
| `jev_chess/settings.py` | `@configured` dataclasses bound to environment variables |
| `jev_chess/jev.py` | `JevApi`, a declarative `@http_client` for the System One API, and `JevMoveChooser`, which turns a position into a Choice question |
| `jev_chess/game.py` | `Game`, a session-scoped `@component` holding one board per browser session |
| `jev_chess/api.py` | `@controller` classes for the JSON API and the page, plus FastAPI configurers (sessions, static files, error mapping) |
| `jev_chess/main.py` | App factory: loads `.env`, boots the container with `pico_boot.init` |
| `jev_chess/static/` | The UI: plain HTML, CSS and an ES module |

Rules, legality and game-over detection come from [python-chess](https://python-chess.readthedocs.io). The board is [chessground](https://github.com/lichess-org/chessground), the open source board used by lichess, vendored under `jev_chess/static/vendor/`.

### API

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/state` | | Current game of this session |
| POST | `/api/move` | `{"from": "e2", "to": "e4", "promotion": "q"}` | Play a human move |
| POST | `/api/jev` | | Ask Jev to move |
| POST | `/api/new` | `{"human": "white" \| "black" \| "none"}` | Start a new game |

Illegal or out-of-turn moves return `409`, malformed bodies `422`, and Jev or OpenRouter failures `502` with an `error` message.

## Development

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests run offline: the Jev HTTP client is backed by an `httpx.MockTransport`, so the full path from controller to request body is exercised without a network or a key.

## References

- [TypeSafe docs](https://docs.typesafe.ai): [Choice questions](https://docs.typesafe.ai/primitives/choice), [State](https://docs.typesafe.ai/concepts/state)
- [Using the TypeSafe API through OpenRouter](https://openrouter.ai/docs/guides/community/typesafe-sdk)

## License

[GPL-3.0-or-later](LICENSE), because the project bundles chessground and depends on python-chess, both GPL-3.0.
