import { createEngine, isMate, mateDistance } from './engine.js';

export const DEPTHS = {
  fast: { label: 'Fast', game: 10, everyMove: 6 },
  standard: { label: 'Standard', game: 12, everyMove: 8 },
  deep: { label: 'Deep', game: 16, everyMove: 11 },
  deepest: { label: 'Deepest', game: 20, everyMove: 14 },
};
const percentileRange = (share) => ({ key: `p${share}`, label: `Top ${share}%`, holds: (move) => move.percentile >= 100 - share });
const withinLoss = (limit) => ({ key: `cp${limit}`, label: `Within ${limit} cp of the best`, holds: (move) => move.loss <= limit });
const GROUPS = {
  percentileRanges: [
    { key: 'best', label: 'Top move', holds: (move) => move.better === 0 },
    { key: 'top3', label: 'Top 3 moves', holds: (move) => move.better < 3 },
    ...[2, 5, 10, 20, 30, 50].map(percentileRange),
  ],
  distanceBands: [...[10, 25, 50, 100, 200].map(withinLoss), { key: 'far', label: 'More than 200 cp behind', holds: (move) => move.loss > 200 }],
};
const ALL_GROUPS = [...GROUPS.percentileRanges, ...GROUPS.distanceBands];
const CP_CAP = 1000;
const JUDGEMENTS = [
  { key: 'blunder', glyph: '??', label: 'Blunder', threshold: 0.3 },
  { key: 'mistake', glyph: '?', label: 'Mistake', threshold: 0.2 },
  { key: 'inaccuracy', glyph: '?!', label: 'Inaccuracy', threshold: 0.1 },
];
const SVG = 'http://www.w3.org/2000/svg';

const clamp = (value, limit) => Math.max(-limit, Math.min(limit, value));
const winningChances = (score) => 2 / (1 + Math.exp(-0.00368208 * clamp(score, CP_CAP))) - 1;

export function formatScore(score) {
  if (isMate(score)) return `#${mateDistance(score)}`;
  const pawns = score / 100;
  return `${pawns > 0 ? '+' : ''}${pawns.toFixed(1)}`;
}

export function judge(scores, sans) {
  const moves = sans.map((san, ply) => {
    const sign = ply % 2 === 0 ? 1 : -1;
    const before = scores[ply];
    const after = scores[ply + 1];
    const loss = Math.max(0, sign * (clamp(before, CP_CAP) - clamp(after, CP_CAP)));
    const drop = sign * (winningChances(before) - winningChances(after));
    const judgement = JUDGEMENTS.find((j) => drop >= j.threshold) || null;
    return { ply, san, color: ply % 2 === 0 ? 'white' : 'black', score: after, loss, judgement };
  });
  const summary = {};
  for (const color of ['white', 'black']) {
    const own = moves.filter((move) => move.color === color);
    summary[color] = {
      moves: own.length,
      acpl: own.length ? Math.round(own.reduce((sum, move) => sum + move.loss, 0) / own.length) : 0,
    };
    for (const j of JUDGEMENTS) summary[color][j.key] = own.filter((move) => move.judgement === j).length;
  }
  return { moves, summary };
}

const percentileOf = (values, own) => {
  if (values.length < 2) return 100;
  const worse = values.filter((value) => value < own).length;
  const tied = values.filter((value) => value === own).length;
  return (100 * (worse + (tied - 1) / 2)) / (values.length - 1);
};
const decileOf = (percentile) => Math.min(9, Math.floor((100 - percentile) / 10));

export function rankAgainstRandom(scoresByMove, chosen) {
  const values = [...scoresByMove.values()].map((score) => clamp(score, CP_CAP));
  const best = Math.max(...values);
  const describe = (value) => {
    const percentile = percentileOf(values, value);
    return { loss: best - value, percentile, decile: decileOf(percentile), better: values.filter((other) => other > value).length };
  };
  const moves = values.map(describe);
  const own = describe(clamp(scoresByMove.get(chosen), CP_CAP));
  const bucket = (members) => ({
    lossSum: members.reduce((sum, move) => sum + move.loss, 0),
    worstLoss: members.reduce((worst, move) => Math.max(worst, move.loss), 0),
    moves: members.length,
  });
  return {
    options: values.length,
    percentile: own.percentile,
    decile: own.decile,
    isBest: own.better === 0,
    loss: own.loss,
    randomLoss: moves.reduce((sum, move) => sum + move.loss, 0) / moves.length,
    deciles: Array.from({ length: 10 }, (_, index) => bucket(moves.filter((move) => move.decile === index))),
    groups: Object.fromEntries(ALL_GROUPS.map((group) => [group.key, { ...bucket(moves.filter(group.holds)), picked: group.holds(own) }])),
  };
}

export function summarizeRanks(ranks) {
  const mean = (pick) => (ranks.length ? ranks.reduce((sum, rank) => sum + pick(rank), 0) / ranks.length : 0);
  const pooled = (pick) => {
    const lossSum = ranks.reduce((sum, rank) => sum + pick(rank).lossSum, 0);
    const moves = ranks.reduce((sum, rank) => sum + pick(rank).moves, 0);
    return moves ? lossSum / moves : null;
  };
  return {
    decisions: ranks.length,
    percentile: mean((rank) => rank.percentile),
    bestShare: 100 * mean((rank) => (rank.isBest ? 1 : 0)),
    loss: mean((rank) => rank.loss),
    randomLoss: mean((rank) => rank.randomLoss),
    deciles: Array.from({ length: 10 }, (_, index) => {
      const picks = ranks.filter((rank) => rank.decile === index).length;
      return {
        decile: index + 1,
        averageLoss: pooled((rank) => rank.deciles[index]),
        picks,
        share: ranks.length ? (100 * picks) / ranks.length : 0,
        randomShare: 100 * mean((rank) => rank.deciles[index].moves / rank.options),
      };
    }),
    ...Object.fromEntries(Object.entries(GROUPS).map(([name, groups]) => [name, groups.map((group) => {
      const picks = ranks.filter((rank) => rank.groups[group.key].picked).length;
      return {
        key: group.key,
        label: group.label,
        averageLoss: pooled((rank) => rank.groups[group.key]),
        worstLoss: ranks.reduce((worst, rank) => Math.max(worst, rank.groups[group.key].worstLoss), 0),
        picks,
        share: ranks.length ? (100 * picks) / ranks.length : 0,
        randomShare: 100 * mean((rank) => rank.groups[group.key].moves / rank.options),
      };
    })])),
  };
}

export async function analyzeGame(movesUci, sans, depth, { onProgress, signal }) {
  const engine = createEngine();
  try {
    const total = 2 * movesUci.length + 1;
    const scores = [];
    for (let ply = 0; ply <= movesUci.length; ply++) {
      if (signal.aborted) return null;
      scores.push(await engine.evaluateForWhite(movesUci.slice(0, ply), depth.game));
      onProgress(scores.length, total);
    }
    const ranks = [];
    for (let ply = 0; ply < movesUci.length; ply++) {
      if (signal.aborted) return null;
      const scoresByMove = await engine.scoreEveryMove(movesUci.slice(0, ply), depth.everyMove);
      if (scoresByMove.has(movesUci[ply])) ranks.push({ ply, san: sans[ply], color: ply % 2 === 0 ? 'white' : 'black', ...rankAgainstRandom(scoresByMove, movesUci[ply]) });
      onProgress(scores.length + ply + 1, total);
    }
    const versusRandom = { all: summarizeRanks(ranks) };
    for (const color of ['white', 'black']) versusRandom[color] = summarizeRanks(ranks.filter((rank) => rank.color === color));
    return { scores, ranks, versusRandom, ...judge(scores, sans) };
  } finally {
    engine.quit();
  }
}

function el(name, attributes = {}, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function moveLabel(move) {
  const number = Math.floor(move.ply / 2) + 1;
  return `${number}${move.color === 'white' ? '.' : '...'} ${move.san}${move.judgement ? move.judgement.glyph : ''}`;
}

export function renderChart(container, tooltip, analysis) {
  const width = 960;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 28, left: 52 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const points = analysis.scores.map((score, index) => ({ index, score, y: winningChances(score) }));
  const last = Math.max(1, points.length - 1);
  const x = (index) => pad.left + (index / last) * innerWidth;
  const y = (value) => pad.top + ((1 - value) / 2) * innerHeight;
  const zero = y(0);

  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Engine evaluation after every move, from White\'s point of view' });

  for (const [value, label] of [[1, 'White'], [0, '0'], [-1, 'Black']]) {
    svg.append(el('line', { x1: pad.left, x2: width - pad.right, y1: y(value), y2: y(value), class: value === 0 ? 'chart-baseline' : 'chart-grid' }));
    svg.append(el('text', { x: pad.left - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'chart-tick' }, label));
  }
  const step = Math.max(1, Math.ceil(last / 2 / 10)) * 2;
  for (let index = 0; index <= last; index += step) {
    svg.append(el('text', { x: x(index), y: height - 8, 'text-anchor': 'middle', class: 'chart-tick' }, index === 0 ? 'Start' : String(index / 2)));
  }

  const line = points.map((p) => `${x(p.index)},${y(p.y)}`).join(' L');
  svg.append(el('path', { d: `M${x(0)},${zero} L${line} L${x(last)},${zero} Z`, class: 'chart-area' }));
  svg.append(el('path', { d: `M${line}`, class: 'chart-line' }));

  for (const move of analysis.moves) {
    if (!move.judgement || move.judgement.key === 'inaccuracy') continue;
    svg.append(el('circle', { cx: x(move.ply + 1), cy: y(winningChances(move.score)), r: 5, class: `chart-marker chart-marker-${move.judgement.key}` }));
  }

  const crosshair = el('line', { y1: pad.top, y2: height - pad.bottom, class: 'chart-crosshair', visibility: 'hidden' });
  const focusDot = el('circle', { r: 5, class: 'chart-focus', visibility: 'hidden' });
  const hit = el('rect', { x: pad.left, y: pad.top, width: innerWidth, height: innerHeight, fill: 'transparent', tabindex: 0, 'aria-label': 'Evaluation chart. Use left and right arrows to step through moves.' });
  svg.append(crosshair, focusDot, hit);

  let focused = 0;
  function show(index) {
    focused = Math.max(0, Math.min(last, index));
    const point = points[focused];
    const move = analysis.moves[focused - 1];
    crosshair.setAttribute('x1', x(focused));
    crosshair.setAttribute('x2', x(focused));
    focusDot.setAttribute('cx', x(focused));
    focusDot.setAttribute('cy', y(point.y));
    crosshair.setAttribute('visibility', 'visible');
    focusDot.setAttribute('visibility', 'visible');
    tooltip.replaceChildren();
    const value = document.createElement('strong');
    value.textContent = formatScore(point.score);
    const label = document.createElement('span');
    label.textContent = move ? moveLabel(move) : 'Starting position';
    tooltip.append(value, label);
    if (move && move.judgement) {
      const note = document.createElement('span');
      note.className = 'chart-tooltip-note';
      note.textContent = `${move.judgement.label}, ${Math.round(move.loss)} centipawns lost`;
      tooltip.append(note);
    }
    const box = container.getBoundingClientRect();
    const left = (x(focused) / width) * box.width;
    tooltip.style.transform = `translate(${Math.min(Math.max(left - 80, 0), box.width - 176)}px, 0)`;
    tooltip.hidden = false;
  }
  function hide() {
    crosshair.setAttribute('visibility', 'hidden');
    focusDot.setAttribute('visibility', 'hidden');
    tooltip.hidden = true;
  }

  hit.addEventListener('pointermove', (event) => {
    const box = svg.getBoundingClientRect();
    const ratio = ((event.clientX - box.left) / box.width * width - pad.left) / innerWidth;
    show(Math.round(ratio * last));
  });
  hit.addEventListener('pointerleave', hide);
  hit.addEventListener('focus', () => show(focused));
  hit.addEventListener('blur', hide);
  hit.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowRight') show(focused + 1);
    else if (event.key === 'ArrowLeft') show(focused - 1);
    else return;
    event.preventDefault();
  });

  container.replaceChildren(svg);
}

export function decileLabel(decile) {
  if (decile === 1) return 'Best 10%';
  if (decile === 10) return 'Worst 10%';
  return `${(decile - 1) * 10}-${decile * 10}%`;
}

export function renderDeciles(container, tooltip, series) {
  const width = 960;
  const height = 220;
  const pad = { top: 16, right: 16, bottom: 28, left: 64 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const shares = series.flatMap((entry) => entry.deciles.map((d) => d.share));
  const top = Math.max(20, Math.ceil(Math.max(...shares) / 10) * 10);
  const slot = innerWidth / 10;
  const barWidth = 20;
  const gap = 2;
  const y = (share) => pad.top + (1 - share / top) * innerHeight;

  const svg = el('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Share of each player\'s moves in each decile of move quality; a random mover puts 10% in every decile' });
  for (const [value, label] of [[top, `${top}%`], [10, 'Random 10%'], [0, '0%']]) {
    svg.append(el('line', { x1: pad.left, x2: width - pad.right, y1: y(value), y2: y(value), class: value === 10 ? 'chart-reference' : value === 0 ? 'chart-baseline' : 'chart-grid' }));
    svg.append(el('text', { x: pad.left - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'chart-tick' }, label));
  }

  for (let index = 0; index < 10; index++) {
    const center = pad.left + slot * (index + 0.5);
    const start = center - (series.length * barWidth + (series.length - 1) * gap) / 2;
    series.forEach((entry, position) => {
      const share = entry.deciles[index].share;
      const size = y(0) - y(share);
      if (size <= 0) return;
      const left = start + position * (barWidth + gap);
      const radius = Math.min(4, size);
      svg.append(el('path', {
        d: `M${left},${y(0)} V${y(share) + radius} Q${left},${y(share)} ${left + radius},${y(share)} H${left + barWidth - radius} Q${left + barWidth},${y(share)} ${left + barWidth},${y(share) + radius} V${y(0)} Z`,
        class: `chart-bar chart-series-${entry.color}`,
      }));
    });
    svg.append(el('text', { x: center, y: height - 8, 'text-anchor': 'middle', class: 'chart-tick' }, decileLabel(index + 1)));

    const summary = series.map((entry) => `${entry.name} ${Math.round(entry.deciles[index].share)}%`).join(', ');
    const hit = el('rect', { x: center - slot / 2, y: pad.top, width: slot, height: innerHeight, fill: 'transparent', tabindex: 0, 'aria-label': `${decileLabel(index + 1)}: ${summary}` });
    const show = () => {
      tooltip.replaceChildren();
      const title = document.createElement('span');
      title.className = 'chart-tooltip-note';
      title.textContent = `${decileLabel(index + 1)} of legal moves`;
      tooltip.append(title);
      for (const entry of series) {
        const row = document.createElement('span');
        row.className = 'chart-tooltip-row';
        const key = document.createElement('i');
        key.className = `legend-line legend-${entry.color}`;
        const value = document.createElement('strong');
        value.textContent = `${Math.round(entry.deciles[index].share)}%`;
        row.append(key, value, document.createTextNode(` ${entry.name}`));
        tooltip.append(row);
      }
      const box = container.getBoundingClientRect();
      tooltip.style.transform = `translate(${Math.min(Math.max((center / width) * box.width - 88, 0), box.width - 176)}px, 0)`;
      tooltip.hidden = false;
    };
    const hide = () => { tooltip.hidden = true; };
    hit.addEventListener('pointerenter', show);
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('focus', show);
    hit.addEventListener('blur', hide);
    svg.append(hit);
  }
  container.replaceChildren(svg);
}

export { JUDGEMENTS };
