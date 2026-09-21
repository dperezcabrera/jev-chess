# Does the order of the options matter?

Part of the [experiments on how Jev decides](../README.md). [Jev](https://typesafe.ai) answers a Choice question by returning one option and a probability for every option; in this project the options are chess moves. Run on 2026-09-21 against `jev-latest` through OpenRouter: 4,320 calls, $0.22.

**Short answer: yes, when Jev is unsure, and not because it favours a slot in the list.** Two runs pick the same move 89.1% of the time with a fixed order and 71.6% when the options are reordered.

## Why

Language models are known to favour an option because of where it sits in a list. A model whose whole job is to choose between options should be checked for that before anyone trusts its choices or its probabilities.

## The positions

The 120 positions of [`../positions.json`](../positions.json): 12 picked by hand (the starting position, five openings, the Petrov trap, a free queen, a mate in one, a pawn ending, a rook ending and a middlegame two pieces up) and 108 taken from the six games of Kasparov against Deep Blue in 1997, 18 per game spread from the opening to the end. They have between 3 and 54 legal moves, with a median of 33.

## Design

Jev is not fully deterministic: the same question can come back with a different answer. So the effect of the order can only be read against that background noise.

- **Same order, 6 runs per position.** The legal moves in the engine's usual order, asked six times. Whatever varies here is Jev's own run-to-run noise.
- **Rotated order, 30 runs per position.** In run *k* a different legal move is placed first and the remaining moves are shuffled. Every move gets its turn in the first slot, so exposure to that slot is balanced by design instead of being left to chance, and shuffling the rest keeps a move from always having the same neighbours.

That is 36 calls per position and 4,320 in total, six at a time, with three retries over the whole run.

## Measures

- **Pair agreement**: the chance that two runs of the same position picked the same move. It is used instead of the share of the most common answer because it does not depend on how many runs there are, which matters when comparing 6 runs with 30.
- **Distance**: total variation distance between the probability distributions of two runs, from 0 (identical) to 1 (no overlap), averaged over all pairs of runs.
- **Position effect**: the change in a move's probability when it goes from first to last in the list. It is a pooled slope with one intercept per move of each position, so how good a move is cancels out and only its place in the list remains.
- **Slot bonus**: the mean probability of a move when it sits in a given slot minus the mean probability of the same move everywhere else.
- **Intervals** are 95% bootstrap intervals that resample whole positions, because the 30 runs of one position are not independent of each other.

## Results

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

## Reading

- **The order changes the answer, and not by favouring a slot.** The slot effects are real, which 3,600 rotated runs are enough to show, and far too small to explain a 17.5 point drop in agreement. Reordering does not push the choice towards the top or the bottom of the list. It perturbs the whole distribution, almost four times as much as Jev's own noise, and that is enough to tip any close decision.
- **It is a property of close calls.** When Jev is confident the choice never changed in any order. When its two best moves are within five points of each other, reordering leaves two runs agreeing half of the time, which is a coin flip between them. The number of options barely matters.
- **A single probability is a rough reading.** The same move in the same position came back at 0.24 in one order and at 0.63 in another. Anything that compares Jev's confidence with a threshold should average a few calls with the options in different orders, and should not trust a gap of less than about 15 points between the top two options.
- **Consistent is not correct.** Jev found the mate in one and took the free queen in all 30 orders, and it also took the poisoned pawn in the Petrov in all 30.

## A correction along the way

The first analysis compared the share of the most common answer over 6 same-order runs with the same share over 30 rotated runs, and counted the positions where the move ever changed: 27 against 77. Both comparisons flatter the fixed order, because more runs give a rare answer more chances to appear. Pair agreement and the resampled comparison replaced them. A 12 position pilot with 8 plain shuffles had also suggested the choice was almost untouched by the order (96% against 95%); it was too small and too easy to show the effect.

## Limits

- One model version, `jev-latest` on the dates above, through one gateway. Jev is not fully deterministic, so a rerun will not reproduce these numbers to the last digit.
- 120 positions, 108 of them from six games between the same two players. They cover every phase of the game but not every kind of position.

## Reproduce

It needs a key, as the game does (see [Getting a key](../../README.md#getting-a-key)). From the root of the repository:

```sh
.venv/bin/python -m experiments.option_order     # 4,320 calls, about $0.22, about 15 minutes
```

`--positions N` runs on the first N positions only, `--workers` sets how many calls are in flight and `--seed` fixes the orders. The analysis can be redone from the stored runs with no key and at no cost; it takes about a minute of processor time for the bootstrap intervals:

```sh
.venv/bin/python -m experiments.option_order --reanalyze
```

| File | Contents |
|---|---|
| [`experiment.py`](experiment.py) | Design, measures and report |
| [`report.json`](report.json) | Overall figures and one entry per position |
| `runs.json.gz` | Every run: the order asked, the choice and all probabilities |

The measures are covered by `tests/test_option_order.py`, including a synthetic model biased towards the first option that the slot measures must catch.
