import assert from 'node:assert/strict';
import { test } from 'node:test';

globalThis.Worker = class {};
const { judge, rankAgainstRandom, summarizeRanks } = await import('../system_one_chess/static/analysis.js');

const spread = new Map([['a', 100], ['b', 50], ['c', 0], ['d', -50], ['e', -100]]);
const ten = new Map(Array.from({ length: 10 }, (_, i) => [`m${i}`, -i * 10]));

test('percentile runs from 100 for the best move to 0 for the worst', () => {
  assert.equal(rankAgainstRandom(spread, 'a').percentile, 100);
  assert.equal(rankAgainstRandom(spread, 'c').percentile, 50);
  assert.equal(rankAgainstRandom(spread, 'e').percentile, 0);
});

test('tied moves share the middle of their range and a forced move is perfect', () => {
  assert.equal(rankAgainstRandom(new Map([['a', 10], ['b', 10]]), 'a').percentile, 50);
  assert.equal(rankAgainstRandom(new Map([['a', 10]]), 'a').percentile, 100);
});

test('loss is measured in centipawns against the best move', () => {
  const rank = rankAgainstRandom(spread, 'c');
  assert.equal(rank.loss, 100);
  assert.equal(rank.randomLoss, 100);
  assert.equal(rankAgainstRandom(spread, 'a').isBest, true);
});

test('ten moves fill ten deciles, each with its own centipawn loss', () => {
  const top = rankAgainstRandom(ten, 'm0');
  assert.equal(top.decile, 0);
  assert.equal(rankAgainstRandom(ten, 'm9').decile, 9);
  assert.deepEqual(top.deciles.map((d) => d.moves), Array(10).fill(1));
  assert.equal(top.deciles[9].lossSum, 90);
});

test('percentile ranges widen from the top move down to the top half', () => {
  const forty = new Map(Array.from({ length: 40 }, (_, i) => [`m${i}`, -i * 5]));
  const second = rankAgainstRandom(forty, 'm1');
  const picked = (key) => second.groups[key].picked;
  assert.deepEqual(['best', 'top3', 'p2', 'p5', 'p10', 'p50'].map(picked), [false, true, false, true, true, true]);
  assert.equal(second.groups.top3.moves, 3);
  assert.deepEqual(['best', 'p2', 'p5', 'p10', 'p20', 'p50'].map((key) => second.groups[key].moves), [1, 1, 2, 4, 8, 20]);

  const summary = summarizeRanks([rankAgainstRandom(forty, 'm0'), second, rankAgainstRandom(forty, 'm20')]);
  const byKey = Object.fromEntries(summary.percentileRanges.map((group) => [group.key, group]));
  assert.ok(Math.abs(summary.percentile - (100 + 100 * (38 / 39) + 100 * (19 / 39)) / 3) < 1e-9);
  assert.equal(byKey.best.picks, 1);
  assert.equal(byKey.p5.picks, 2);
  assert.equal(Math.round(byKey.p5.share), 67);
  assert.ok(Math.abs(byKey.best.randomShare - 2.5) < 1e-9);
  assert.ok(Math.abs(byKey.p10.randomShare - 10) < 1e-9);
  assert.equal(byKey.p10.worstLoss, 15);
  assert.ok(Math.abs(byKey.top3.randomShare - 7.5) < 1e-9);
  assert.ok(Math.abs(summary.deciles[0].randomShare - 10) < 1e-9);
});

test('a top range exposes the terrible second-best move through its worst loss', () => {
  const onlyOneGood = new Map([['good', 0], ['bad', -900], ['worse', -950], ['awful', -990]]);
  const summary = summarizeRanks([rankAgainstRandom(onlyOneGood, 'bad')]);
  const top50 = summary.percentileRanges.find((group) => group.key === 'p50');
  assert.equal(top50.picks, 1);
  assert.equal(top50.worstLoss, 900);
  const distance = Object.fromEntries(summary.distanceBands.map((group) => [group.key, group]));
  assert.equal(distance.cp200.picks, 0);
  assert.equal(distance.far.picks, 1);
  assert.equal(distance.cp10.randomShare, 25);
  assert.equal(distance.far.randomShare, 75);
});

test('summary aggregates picks and average loss per decile', () => {
  const summary = summarizeRanks([rankAgainstRandom(ten, 'm0'), rankAgainstRandom(ten, 'm9')]);
  assert.equal(summary.deciles[0].share, 50);
  assert.equal(summary.deciles[9].averageLoss, 90);
  assert.equal(summary.deciles[4].picks, 0);
  assert.equal(summarizeRanks([]).decisions, 0);
});

test('a black move that hands White 380 centipawns is a blunder', () => {
  const judged = judge([20, 20, 400, 400], ['e4', 'f6', 'd4']);
  assert.equal(judged.moves[1].judgement.key, 'blunder');
  assert.equal(judged.moves[1].loss, 380);
  assert.equal(judged.summary.black.blunder, 1);
  assert.equal(judged.summary.white.acpl, 0);
});
