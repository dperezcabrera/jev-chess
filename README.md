# How good is [Jev AI](https://typesafe.ai) at chess? Try to beat it

`jev-chess`: play chess in your browser against [Jev](https://typesafe.ai), TypeSafe AI's System One model, called through [OpenRouter](https://openrouter.ai/typesafe).

Jev does not generate text. It answers typed questions about a state with calibrated probabilities. That maps cleanly onto chess: every turn is **one Choice question whose options are the legal moves**. A chess position has at most 218 legal moves and a Choice accepts up to 255 options, so a single request always fits and Jev can never return an illegal move. A typical reply takes about 300 ms.

![Choosing a side](screenshots/choose-side.png)

![Jev playing both sides](screenshots/game.png)

## What you get

- **A real board.** [chessground](https://github.com/lichess-org/chessground), the open source board from lichess: drag or click, legal moves only.
- **Jev's confidence, every move.** The side panel shows the three options Jev weighed and the probability it gave each one.
- **Cost and latency, live.** The footer adds up Jev calls, tokens, average latency and dollars for the current game, straight from OpenRouter's usage data. Jev playing both sides costs about $0.00005 per move at 300 to 400 ms each: a 20-move game for $0.0009.
- **Engine analysis in your browser.** Stockfish 19 (WebAssembly, 1.8 MB) evaluates the game locally: evaluation chart, average centipawn loss (how many hundredths of a pawn each move gives away, see [Reading the numbers](#reading-the-numbers)), inaccuracies, mistakes and blunders per player. No server cost, no extra API calls. Depth is configurable.
- **Is Jev better than chance?** For every position, Stockfish scores all legal moves and ranks the one that was played. A random mover sits on the 50th percentile by definition, so anything above that is signal. Two breakdowns sit next to what a random mover would score. By distance: the share of moves within 10, 25, 50, 100 and 200 centipawns of the best one, the absolute reference. By percentile range: top move, top 3 moves, top 2%, 5%, 10%, 20%, 30% and 50%, each with its average and its worst loss, because a top range can still hold a terrible move when a position has only one good one.
- **PGN export**: a dialog shows the game in Portable Game Notation, ready to copy to the clipboard or download as a file.
- **One game per browser session**, so several people can play on the same server.

![Analysis of a Jev-versus-Jev game](screenshots/analysis.png)

In the game above Jev plays both sides and draws by repetition after 10 moves each. As Black it lands on the 80th percentile and loses 114 centipawns per move where a random mover would lose 402; as White, the 63rd percentile and 122 against 128. Black kept 70% of its moves within 50 centipawns of the best one (random: 20%); White 50% (random: 37%). They found the engine's top move 20% and 40% of the time (random: 4% and 15%). Better than chance, and still not a chess player. The top 3 moves in that game include one 1306 centipawns behind the best, which is why every range shows its worst loss. Jev is not fully deterministic, so your numbers will differ.

> Jev is a fast classifier, not a chess engine. Expect plausible moves, not strong ones. This project is a demo of the System One decision pattern.

## Quick start with Docker

You only need Docker and an [OpenRouter API key](https://openrouter.ai/settings/keys). A prebuilt image is published on the GitHub Container Registry, so there is nothing to build:

```sh
docker pull ghcr.io/dperezcabrera/jev-chess:latest
docker run --rm -p 127.0.0.1:8000:8000 -e OPENROUTER_API_KEY=sk-or-... ghcr.io/dperezcabrera/jev-chess:latest
```

Open http://localhost:8000.

If the key is already exported in your shell, pass it through without typing it:

```sh
docker run --rm -p 127.0.0.1:8000:8000 -e OPENROUTER_API_KEY ghcr.io/dperezcabrera/jev-chess:latest
```

Available tags: `latest` and the version number, such as `0.1.0`.

To build the image yourself instead:

```sh
docker build -t jev-chess .
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

Pick a side on the start screen: White, Black, or Spectator (Jev plays both sides). One click starts the game. Drag or click pieces; only legal moves are allowed. When a pawn reaches the last rank, pick the piece on the board; click elsewhere or press Escape to take the move back.

Analysis starts on its own when the game ends, or any time from **Analyze game**. Pick a depth first: Fast, Standard, Deep or Deepest. Deeper is slower and steadier; Standard analyzes a short game in a couple of seconds.

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
| `jev_chess/static/` | The UI: plain HTML, CSS and ES modules. `engine.js` hides the UCI protocol behind two questions (how good is this position, how good is every move), `analysis.js` holds the judgement and percentile math and draws the charts |

Rules, legality, game-over detection and PGN come from [python-chess](https://python-chess.readthedocs.io). The board is [chessground](https://github.com/lichess-org/chessground) and the engine is [Stockfish.js](https://github.com/nmrugg/stockfish.js) 19 (lite, single-threaded, so it needs no special HTTP headers). Both are vendored under `jev_chess/static/vendor/`, so the app works without any CDN.

### API

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/state` | | Current game of this session |
| POST | `/api/move` | `{"from": "e2", "to": "e4", "promotion": "q"}` | Play a human move |
| POST | `/api/jev` | | Ask Jev to move |
| GET | `/api/pgn` | | Download the current game as PGN |
| POST | `/api/new` | `{"human": "white" \| "black" \| "none"}` | Start a new game |

Illegal or out-of-turn moves return `409`, malformed bodies `422`, and Jev or OpenRouter failures `502` with an `error` message.

### Reading the numbers

**What a centipawn is.** Engines measure who is ahead in pawns. A centipawn (cp) is a hundredth of one, so 100 cp is one pawn. As a scale: a knight or bishop is worth about 300 cp, a rook about 500 and a queen about 900. An evaluation of +150 means White is a pawn and a half ahead; -300 means Black is a piece up.

**What centipawn loss is.** For one move, it is how much worse the move played is than the best move available, seen from the side that moved. Playing the engine's first choice loses 0 cp; leaving a knight to be captured for nothing loses about 300. Averaged over a player's moves it becomes the average centipawn loss, the usual one-number summary of how accurately someone played. Lower is better.

**What the numbers look like here.** Measured with this app: across a handful of games a random mover loses between 130 and 500 cp per move, Jev between 60 and 190, and a careful human game came out at 16. These depend on the engine and the depth, so compare numbers produced with the same settings and treat them as relative, not as a rating.

**Two loss figures, on purpose.** The summary table's *Avg. centipawn loss* compares the evaluation of the game before and after each move, searched at the game depth (12 on Standard). The *Avg. loss per move* in the comparison with a random mover compares the move played with the best of all legal moves, which needs every move scored and therefore runs at a lower depth (8 on Standard). They measure the same idea and usually land close, but they are different searches and will not match exactly.

**Why centipawns are not enough.** Dropping 300 cp in an equal position loses the game; dropping 300 cp when you are already a queen up changes nothing. So moves are not labelled by centipawns: a move is an inaccuracy, mistake or blunder when it lowers the mover's winning chances by 10, 20 or 30 points, using the same centipawn-to-winning-chances curve as lichess. Evaluations are capped at 1000 cp so that one forced mate does not swamp an average, which also means a single move can lose at most 2000 cp.

**Why the percentile is there.** Centipawn loss says how far a move is from perfect, not whether it beats guessing. The percentile answers that: it is the share of legal moves in the position that Stockfish scores strictly worse than the one played, with ties split evenly. A random mover sits on the 50th percentile by definition. The two are read together, because each hides something: a high percentile can still be a large loss when only one move in the position was good, and a small loss can be a low percentile when every move was fine. The metric is calibrated: in a long random-versus-random game both sides land on the 50th and 51st percentile.

## Development

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
node --test tests/analysis.test.mjs
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Tests run offline: the Jev HTTP client is backed by an `httpx.MockTransport`, so the full path from controller to request body is exercised without a network or a key. The Node test covers the analysis math (percentiles, deciles, top groups, judgements) and needs no dependencies.

### Publishing the image

The image is built and pushed from a developer machine. Log in once with a GitHub token that has the `write:packages` scope:

```sh
gh auth refresh -h github.com -s write:packages
gh auth token | docker login ghcr.io -u dperezcabrera --password-stdin
```

Then build, tag and push:

```sh
docker build -t ghcr.io/dperezcabrera/jev-chess:0.1.0 -t ghcr.io/dperezcabrera/jev-chess:latest .
docker push --all-tags ghcr.io/dperezcabrera/jev-chess
```

A new package on the registry starts private. Make it public once, from the package settings on GitHub, so that `docker pull` works without logging in.

## References

- [TypeSafe docs](https://docs.typesafe.ai): [Choice questions](https://docs.typesafe.ai/primitives/choice), [State](https://docs.typesafe.ai/concepts/state)
- [Using the TypeSafe API through OpenRouter](https://openrouter.ai/docs/guides/community/typesafe-sdk)

## License

[GPL-3.0-or-later](LICENSE), because the project bundles chessground and Stockfish.js and depends on python-chess, all GPL-3.0.
