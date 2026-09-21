# Experiments on how Jev decides

[Jev](https://typesafe.ai) answers a Choice question by returning one option and a probability for every option. In this project the options are chess moves. Playing games shows how well it chooses; these experiments ask narrower questions about the mechanism. Each one has its own directory with a write-up, the code and the data.

| Experiment | Question | Answer |
|---|---|---|
| [Option order](option_order/README.md) | Does the answer depend on the order of the options? | Yes, when Jev is unsure: two runs pick the same move 89.1% of the time with a fixed order and 71.6% when the options are reordered. There is almost no bias towards a slot in the list |
| [Legality](legality/README.md) | Does Jev read the board, or recognise moves that sound right? | Offered eleven moves of which only one is legal, it finds it 19.7% of the time unprompted and 49.7% when told to look, against 9.1% by chance. Subtle illegal moves, such as leaving its own king in check, fool it most |

Both ran on 2026-09-21 and 2026-09-22 against `jev-latest` through OpenRouter. Together they made 5,040 calls and cost $0.24.

## The positions

Both experiments use the same 120 positions, in [`positions.json`](positions.json):

- 12 picked by hand to cover easy and hard cases: the starting position, five openings, the Petrov trap, a free queen, a mate in one, a pawn ending, a rook ending and a middlegame two pieces up.
- 108 taken from the six games of Kasparov against Deep Blue in 1997, 18 per game spread evenly from the opening to the end, skipping any position already in the set. Real games were used because random positions do not look like chess.

They have between 3 and 54 legal moves, with a median of 33. White is to move in 69 of them and the side to move is in check in 2.

## Running them

From the root of the repository, with a key set as for the game:

```sh
.venv/bin/python -m experiments.option_order
.venv/bin/python -m experiments.legality
```
