# Two experiments on how Jev decides

[Jev](https://typesafe.ai) answers a Choice question by returning one option and a probability for every option. In this project the options are chess moves. Playing games shows how well it chooses; these experiments ask two narrower questions about the mechanism:

1. **Does the answer depend on the order of the options?** It should not.
2. **Does Jev read the board, or does it recognise moves that sound right?** Offer it eleven moves of which only one is legal and see whether it finds it.

Both ran on 2026-09-21 and 2026-09-22 against `jev-latest` through OpenRouter. Together they made 5,040 calls and cost $0.24.

| | Answer |
|---|---|
| Does the order of the options change the chosen move? | Yes, when Jev is unsure. Two runs pick the same move 89.1% of the time with a fixed order and 71.6% when the options are reordered |
| Is there a bias towards a slot in the list? | Almost none. Being listed first costs a move 0.65 points of probability, being listed last gains it 0.41 |
| Does Jev find the only legal move when it is not told to look? | Rarely: 19.7% of the time, against 9.1% by chance |
| And when told exactly one move is legal? | Half the time: 49.7% |
| Which illegal moves fool it most? | The subtle ones: a move that leaves its own king in check, and capturing its own piece |

## The positions

Both experiments use the same 120 positions, in [`positions.json`](positions.json):

- 12 picked by hand to cover easy and hard cases: the starting position, five openings, the Petrov trap, a free queen, a mate in one, a pawn ending, a rook ending and a middlegame two pieces up.
- 108 taken from the six games of Kasparov against Deep Blue in 1997, 18 per game spread evenly from the opening to the end, skipping any position already in the set. Real games were used because random positions do not look like chess.

They have between 3 and 54 legal moves, with a median of 33. White is to move in 69 of them and the side to move is in check in 2.

## Experiment 1: does the order of the options matter?

### Why

Language models are known to favour an option because of where it sits in a list. A model whose whole job is to choose between options should be checked for that before anyone trusts its choices or its probabilities.

### Design

Jev is not fully deterministic: the same question can come back with a different answer. So the effect of the order can only be read against that background noise.

- **Same order, 6 runs per position.** The legal moves in the engine's usual order, asked six times. Whatever varies here is Jev's own run-to-run noise.
- **Rotated order, 30 runs per position.** In run *k* a different legal move is placed first and the remaining moves are shuffled. Every move gets its turn in the first slot, so exposure to that slot is balanced by design instead of being left to chance, and shuffling the rest keeps a move from always having the same neighbours.

That is 36 calls per position and 4,320 in total, six at a time, with three retries over the whole run.

### Measures

- **Pair agreement**: the chance that two runs of the same position picked the same move. It is used instead of the share of the most common answer because it does not depend on how many runs there are, which matters when comparing 6 runs with 30.
- **Distance**: total variation distance between the probability distributions of two runs, from 0 (identical) to 1 (no overlap), averaged over all pairs of runs.
- **Position effect**: the change in a move's probability when it goes from first to last in the list. It is a pooled slope with one intercept per move of each position, so how good a move is cancels out and only its place in the list remains.
- **Slot bonus**: the mean probability of a move when it sits in a given slot minus the mean probability of the same move everywhere else.
- **Intervals** are 95% bootstrap intervals that resample whole positions, because the 30 runs of one position are not independent of each other.

### Results

| | Same order | Rotated order |
|---|---:|---:|
| Two runs pick the same move | 89.1% | 71.6% |
| Distance between probability distributions | 0.043 | 0.163 |

Reordering the options lowers pair agreement by **17.5 points** (95% interval 12.6 to 22.5).

| Bias towards a slot | Effect on a move's probability | 95% interval |
|---|---:|---:|
| Listed first | -0.65 points | -0.77 to -0.54 |
| Listed last | +0.41 points | +0.30 to +0.51 |
| From first to last, slope | +1.09 points | +0.97 to +1.22 |

The move listed first was chosen in 3.2% of the rotated runs; if the order did not matter at all it would be 3.9%.

Where the instability lives:

| Positions | n | Pair agreement, same order | Rotated | Distance, same order | Rotated |
|---|---:|---:|---:|---:|---:|
| Jev is confident, top move at 60% or more | 16 | 100.0% | 100.0% | 0.026 | 0.096 |
| Jev is unsure, top move under 60% | 104 | 87.4% | 67.2% | 0.046 | 0.173 |
| Top two moves within 5 points | 50 | 75.9% | 50.5% | 0.046 | 0.182 |
| Top two moves 5 to 15 points apart | 26 | 95.9% | 70.5% | 0.045 | 0.170 |
| Top two moves 15 to 30 points apart | 18 | 100.0% | 92.4% | 0.050 | 0.176 |
| Top move ahead by 30 points or more | 26 | 100.0% | 99.0% | 0.032 | 0.108 |
| Up to 20 legal moves | 13 | 85.1% | 73.8% | 0.034 | 0.099 |
| 21 to 35 legal moves | 64 | 90.2% | 71.6% | 0.042 | 0.159 |
| More than 35 legal moves | 43 | 88.5% | 70.9% | 0.048 | 0.188 |

In 43 of the 120 positions Jev chose the same move in all 30 orders; in 50 it chose three or more different moves. The probability it gave to its most frequent move swung by 0.195 on average across the 30 orders of a position, and by 0.390 in the worst case.

### Reading

- **The order changes the answer, and not by favouring a slot.** The slot effects are real, which 3,600 rotated runs are enough to show, and far too small to explain a 17.5 point drop in agreement. Reordering does not push the choice towards the top or the bottom of the list. It perturbs the whole distribution, almost four times as much as Jev's own noise, and that is enough to tip any close decision.
- **It is a property of close calls.** When Jev is confident the choice never changed in any order. When its two best moves are within five points of each other, reordering leaves two runs agreeing half of the time, which is a coin flip between them. The number of options barely matters.
- **A single probability is a rough reading.** The same move in the same position came back at 0.24 in one order and at 0.63 in another. Anything that compares Jev's confidence with a threshold should average a few calls with the options in different orders, and should not trust a gap of less than about 15 points between the top two options.
- **Consistent is not correct.** Jev found the mate in one and took the free queen in all 30 orders, and it also took the poisoned pawn in the Petrov in all 30.

### A correction along the way

The first analysis compared the share of the most common answer over 6 same-order runs with the same share over 30 rotated runs, and counted the positions where the move ever changed: 27 against 77. Both comparisons flatter the fixed order, because more runs give a rare answer more chances to appear. Pair agreement and the resampled comparison replaced them. A 12 position pilot with 8 plain shuffles had also suggested the choice was almost untouched by the order (96% against 95%); it was too small and too easy to show the effect.

## Experiment 2: does Jev read the board?

### Why

In a normal game every option is legal, so choosing well can be done by recognising what good moves look like. To separate that from reading the position, remove the clue: offer moves of which only one can be played at all, and make that one a random legal move, so its quality says nothing.

### Design

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

### Results

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

### Reading

- **Jev does not check legality on its own.** Asked for the best move, it picks the only playable one twice as often as chance and no more. Its mistakes are moves that sound like chess: a piece sliding along a file through another piece, a piece going somewhere plausible it cannot reach. It almost never picks a move from an empty square or with the opponent's piece, so it does see where the pieces are.
- **Told what to look for, it improves a lot and still fails half of the time.** The obvious kinds of illegality nearly vanish, which shows the information is there to be used when the question asks for it. What remains are the rules that need a second step of reasoning. A move that leaves its own king in check is chosen 34% of the time it is offered, the worst figure in the table, and capturing its own piece 18%.
- **The rise of those two kinds under the explicit instruction is partly a side effect.** Once the obvious decoys are rejected, their probability has to go somewhere, and it goes to the decoys that look most like legal moves. Those two kinds are geometrically perfect moves.
- **32 pairs were right only when Jev was not told.** That is more likely noise, of the size measured in the first experiment, than understanding.
- Taken together with the games, where Jev finds sound positional moves and then leaves a piece hanging, this fits a model that recognises what good chess moves look like much better than it tracks what is true on the board.

## Limits

- One model version, `jev-latest` on the dates above, through one gateway. Jev is not fully deterministic, so a rerun will not reproduce these numbers to the last digit.
- 120 positions, 108 of them from six games between the same two players. They cover every phase of the game but not every kind of position.
- The state sent to Jev is the one the game uses: the position as FEN, an ASCII board and the side to move. A different way of presenting the board could change the second experiment a great deal, and that was not tested.
- "Leaves king in check" is rare in real positions: 93 decoys against about 700 for every other kind, so its rate is the least precise in the table.
- The legal move in the second experiment is random and often bad. That is deliberate, but it means the implicit instruction asks for the best move when the only playable one may be poor.

## Reproduce

Both experiments need a key, as the game does (see the main [README](../README.md#getting-a-key)):

```sh
.venv/bin/python -m experiments.option_order     # 4,320 calls, about $0.22, about 15 minutes
.venv/bin/python -m experiments.legality         # 720 calls, about $0.02, about 3 minutes
```

`--positions N` runs on the first N positions only, `--workers` sets how many calls are in flight, and `--seed` fixes the orders and the decoys. The analysis of the first experiment can be redone from the stored runs with no key and at no cost:

```sh
.venv/bin/python -m experiments.option_order --reanalyze
```

| File | Contents |
|---|---|
| [`positions.json`](positions.json) | The 120 positions with their source |
| [`option_order.py`](option_order.py) | Experiment 1: design, measures and report |
| [`legality.py`](legality.py) | Experiment 2: the generator of illegal moves, measures and report |
| [`results/option-order.json`](results/option-order.json) | Overall figures and one entry per position |
| `results/option-order.runs.json.gz` | Every run: the order asked, the choice and all probabilities |
| [`results/legality.json`](results/legality.json) | Figures per instruction and per kind of illegal move |
| `results/legality.trials.json.gz` | Every trial: the eleven options, which was legal, the kind of each decoy, the choice and all probabilities |

The measures are covered by `tests/test_option_order.py` and `tests/test_legality.py`, including a synthetic model biased towards the first option that the slot measures must catch.
