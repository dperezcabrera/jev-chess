# Does Jev read the board?

Part of the [experiments on how Jev decides](../README.md). [Jev](https://typesafe.ai) answers a Choice question by returning one option and a probability for every option; in this project the options are chess moves. Run on 2026-09-22 against `jev-latest` through OpenRouter: 720 calls, $0.02.

**Short answer: not reliably.** Offered eleven moves of which only one is legal, Jev finds it 19.7% of the time when asked for the best move and 49.7% when told that exactly one is legal. Chance is 9.1%.

## Why

In a normal game every option is legal, so choosing well can be done by recognising what good moves look like. To separate that from reading the position, remove the clue: offer moves of which only one can be played at all, and make that one a random legal move, so its quality says nothing.

## The positions

The 120 positions of [`../positions.json`](../positions.json): 12 picked by hand (the starting position, five openings, the Petrov trap, a free queen, a mate in one, a pawn ending, a rook ending and a middlegame two pieces up) and 108 taken from the six games of Kasparov against Deep Blue in 1997, 18 per game spread from the opening to the end. They have between 3 and 54 legal moves, with a median of 33.

## Design

For each position, eleven options in random order: **one legal move chosen at random and ten illegal ones**. The illegal moves are generated from the position in six kinds, from obvious to subtle, and every question mixes at least four kinds:

| Kind | Example |
|---|---|
| Empty origin | a move starting from a square with no piece on it |
| Opponent piece | a move that would be legal for the other side |
| Wrong geometry | a knight moving like a bishop |
| Blocked path | a rook jumping over a pawn |
| Own piece on target | capturing a piece of the same colour |
| Leaves king in check | moving a pinned piece away, or ignoring a check. Legal in every other respect |

Every option is labelled and described in the same neutral way, for example `g8-f6` and "knight from g8 to f6". There is no capture mark, no check mark and none of the tactical hints the game normally adds, so nothing but the board tells the legal move apart. A test checks, for all 120 positions, that no decoy is ever legal and that exactly one option is.

Each set of eleven options is asked twice:

- **Implicit**: the usual instruction of the game, "Which move is best?". It never says that illegal moves are present.
- **Explicit**: "Exactly one of these moves is legal in this position; every other one breaks the rules of chess. Which move is the legal one?"

Three sets of options per position, asked both ways, make 720 calls. Chance is 1 in 11, or 9.1%.

## Results

| | Picked the legal move | 95% interval | Mean probability on it |
|---|---:|---:|---:|
| Implicit | 19.7% | 15.6% to 24.2% | 0.152 |
| Explicit | 49.7% | 44.2% to 55.6% | 0.326 |
| Chance | 9.1% | | 0.091 |

How often each kind of illegal move was chosen, out of the times it was offered:

| Kind | Offered | Chosen, implicit | Chosen, explicit |
|---|---:|---:|---:|
| Empty origin | 741 | 1.8% | 0.4% |
| Opponent piece | 726 | 3.9% | 1.4% |
| Wrong geometry | 720 | 13.1% | 0.7% |
| Blocked path | 678 | 17.3% | 1.9% |
| Own piece on target | 642 | 5.3% | 18.4% |
| Leaves king in check | 93 | 3.2% | 34.4% |

The same eleven options asked both ways, 360 pairs: right both times 39, right only when told 140, right only when not told 32, wrong both times 149.

## Reading

- **Jev does not check legality on its own.** Asked for the best move, it picks the only playable one twice as often as chance and no more. Its mistakes are moves that sound like chess: a piece sliding along a file through another piece, a piece going somewhere plausible it cannot reach. It almost never picks a move from an empty square or with the opponent's piece, so it does see where the pieces are.
- **Told what to look for, it improves a lot and still fails half of the time.** The obvious kinds of illegality nearly vanish, which shows the information is there to be used when the question asks for it. What remains are the rules that need a second step of reasoning. A move that leaves its own king in check is chosen 34% of the time it is offered, the worst figure in the table, and capturing its own piece 18%.
- **The rise of those two kinds under the explicit instruction is partly a side effect.** Once the obvious decoys are rejected, their probability has to go somewhere, and it goes to the decoys that look most like legal moves. Those two kinds are geometrically perfect moves.
- **32 pairs were right only when Jev was not told.** That is more likely noise, of the size measured in the [option-order experiment](../option_order/README.md), than understanding.
- Taken together with the games, where Jev finds sound positional moves and then leaves a piece hanging, this fits a model that recognises what good chess moves look like much better than it tracks what is true on the board.

## Limits

- One model version, `jev-latest` on the dates above, through one gateway. Jev is not fully deterministic, so a rerun will not reproduce these numbers to the last digit.
- 120 positions, 108 of them from six games between the same two players. They cover every phase of the game but not every kind of position.
- The state sent to Jev is the one the game uses: the position as FEN, an ASCII board and the side to move. A different way of presenting the board could change the second experiment a great deal, and that was not tested.
- "Leaves king in check" is rare in real positions: 93 decoys against about 700 for every other kind, so its rate is the least precise in the table.
- The legal move in the second experiment is random and often bad. That is deliberate, but it means the implicit instruction asks for the best move when the only playable one may be poor.

## Reproduce

It needs a key, as the game does (see [Getting a key](../../README.md#getting-a-key)). From the root of the repository:

```sh
.venv/bin/python -m experiments.legality     # 720 calls, about $0.02, about 3 minutes
```

`--positions N` runs on the first N positions only, `--draws` sets how many sets of eleven options each position gets, `--workers` how many calls are in flight and `--seed` fixes the decoys.

| File | Contents |
|---|---|
| [`experiment.py`](experiment.py) | The generator of illegal moves, measures and report |
| [`report.json`](report.json) | Figures per instruction and per kind of illegal move |
| `trials.json.gz` | Every trial: the eleven options, which was legal, the kind of each decoy, the choice and all probabilities |

`tests/test_legality.py` checks, for all 120 positions, that no decoy is ever legal and that exactly one option is.
