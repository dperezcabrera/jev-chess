import { Chessground } from './vendor/chessground/chessground.min.js';
import { DEPTHS, analyzeGame, decileLabel, renderChart, renderDeciles } from './analysis.js';

const $ = (id) => document.getElementById(id);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const DECISION_ROWS = 3;

let generation = 0;
let currentHuman = 'white';
let selectedBoard = null;
const stateUrl = () => (selectedBoard ? `/api/tournament/board/${selectedBoard}` : '/api/state');
const moveUrl = () => (selectedBoard ? `/api/tournament/board/${selectedBoard}/move` : '/api/move');
const pgnUrl = () => (selectedBoard ? `api/tournament/board/${selectedBoard}/pgn` : 'api/pgn');
let current = null;
let analysis = { gameId: null, done: '', glyphs: [], abort: null };

const ground = Chessground($('board'), {
  animation: { enabled: !reducedMotion },
  movable: { free: false, events: { after: onHumanMove } },
  premovable: { enabled: false },
  draggable: { showGhost: true },
});
new ResizeObserver(() => ground.redrawAll()).observe($('board'));

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setStatus(text, { thinking = false, error = false, retry = false } = {}) {
  $('status-text').textContent = text;
  $('status').classList.toggle('error', error);
  $('spinner').hidden = !thinking;
  $('retry').hidden = !retry;
}

function renderDecision(top) {
  const body = $('decision');
  body.replaceChildren();
  for (let i = 0; i < DECISION_ROWS; i++) {
    const row = body.insertRow();
    const entry = top[i];
    row.className = entry ? '' : 'placeholder';
    const move = row.insertCell();
    move.className = 'col-move';
    move.textContent = entry ? entry.san : '–';
    const probability = row.insertCell();
    probability.className = 'col-prob';
    probability.textContent = entry ? `${(entry.probability * 100).toFixed(1)}%` : '–';
  }
}

function renderMoves(history) {
  const list = $('moves');
  list.replaceChildren();
  $('moves-empty').hidden = history.length > 0;
  for (let i = 0; i < history.length; i += 2) {
    const item = document.createElement('li');
    const glyph = (ply) => (history[ply] ? history[ply] + (analysis.glyphs[ply] || '') : '');
    for (const [className, text] of [['number', `${i / 2 + 1}.`], ['', glyph(i)], ['', glyph(i + 1)]]) {
      const span = document.createElement('span');
      span.className = className;
      span.textContent = text;
      item.append(span);
    }
    list.append(item);
  }
  $('moves-box').scrollTop = $('moves-box').scrollHeight;
}

function playerOf(state, colour) {
  if (state.human === colour) return { id: 'human', name: 'You', logo: '' };
  const id = state.models[colour];
  return models.find((model) => model.id === id) || { id, name: id.replace(/^llm:[^/]*\//, ''), logo: '' };
}

let flipped = false;

function orientationOf(state) {
  const natural = state.human === 'black' ? 'black' : 'white';
  return flipped ? (natural === 'white' ? 'black' : 'white') : natural;
}

let thinkingSince = null;

function clockSeconds(state, colour) {
  const used = state.usage_by_colour ? state.usage_by_colour[colour].seconds : 0;
  const live = !state.over && state.turn === colour && state.human !== colour && thinkingSince ? (Date.now() - thinkingSince) / 1000 : 0;
  return used + live;
}

const clockText = (state, colour) => {
  const total = Math.round(clockSeconds(state, colour));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

function tickClocks() {
  if (!current) return;
  for (const clock of document.querySelectorAll('.player-clock')) clock.textContent = clockText(current, clock.dataset.colour);
}

setInterval(tickClocks, 1000);

function renderPlayers(state) {
  const bottom = orientationOf(state);
  const top = bottom === 'white' ? 'black' : 'white';
  for (const [slot, colour] of [['player-top', top], ['player-bottom', bottom]]) {
    const bar = $(slot);
    const player = playerOf(state, colour);
    const toMove = !state.over && state.turn === colour;
    bar.classList.toggle('to-move', toMove);
    const swatch = Object.assign(document.createElement('span'), { className: `player-colour ${colour}` });
    swatch.setAttribute('aria-hidden', 'true');
    const note = Object.assign(document.createElement('span'), { className: 'player-note', textContent: state.over ? (state.result || '').split(' ')[0] : toMove ? 'to move' : colour });
    const clock = Object.assign(document.createElement('span'), { className: 'player-clock', textContent: clockText(state, colour) });
    clock.dataset.colour = colour;
    clock.title = 'Time spent deciding in this game';
    bar.replaceChildren(swatch, logoNode(player), Object.assign(document.createElement('span'), { className: 'player-name', textContent: player.name, title: player.id }), clock, note);
  }
}

function render(state) {
  renderPlayers(state);
  ground.set({
    fen: state.fen,
    orientation: orientationOf(state),
    turnColor: state.turn,
    lastMove: state.last_move || undefined,
    check: state.check,
    movable: {
      color: state.humans_turn ? state.turn : undefined,
      dests: new Map(Object.entries(state.dests)),
    },
  });
  current = state;
  currentHuman = state.human;
  renderUsage(state);
  syncAnalysis(state);
  $('game-id').textContent = state.game_id;
  renderDecision(state.jev_top);
  const lastMover = state.history.length ? (state.turn === 'white' ? 'black' : 'white') : null;
  $('decision-title').textContent = lastMover && state.human !== lastMover ? `${playerOf(state, lastMover).name}'s last decision` : 'Last decision';
  if (state.over && standingsKey !== state.game_id) {
    standingsKey = state.game_id;
    loadStandings();
  }
  renderMoves(state.history);
  if (state.over) setStatus(`Game over: ${state.result}`);
  else if (state.humans_turn) setStatus(`Your move (${state.turn})`);
  else if (PAGE === 'tournament') setStatus(`${playerOf(state, state.turn).name} is deciding`, { thinking: true });
  else askJev();
}

function usageCells(row, usage) {
  const latency = usage.calls ? `${Math.round((usage.seconds / usage.calls) * 1000)} ms` : '\u2013';
  for (const value of [usage.calls.toLocaleString('en-US'), usage.input_tokens.toLocaleString('en-US'), usage.output_tokens.toLocaleString('en-US'), latency, usage.illegal, `$${usage.cost_usd.toFixed(6)}`]) {
    Object.assign(row.insertCell(), { className: 'col-num', textContent: value });
  }
}

function renderUsage(state) {
  const body = $('usage-rows');
  body.replaceChildren();
  const sides = ['white', 'black'].filter((colour) => state.human !== colour);
  for (const colour of sides) {
    const row = body.insertRow();
    const cell = row.insertCell();
    cell.className = 'col-text';
    const player = playerOf(state, colour);
    const head = Object.assign(document.createElement('span'), { className: 'model-head' });
    head.append(logoNode(player), Object.assign(document.createElement('span'), { className: 'model-head-name', textContent: `${player.name} (${colour})`, title: player.id }));
    cell.append(head);
    usageCells(row, state.usage_by_colour[colour]);
  }
  if (sides.length > 1) {
    const row = body.insertRow();
    row.className = 'total';
    Object.assign(row.insertCell(), { className: 'col-text', textContent: 'Both' });
    usageCells(row, state.usage);
  }
}

function setAnalysisMessage(text, { error = false } = {}) {
  $('analysis-message').textContent = text;
  $('analysis-message').hidden = !text;
  $('analysis-message').classList.toggle('error', error);
}

function syncAnalysis(state) {
  const stale = analysis.gameId !== state.game_id || parseInt(analysis.done, 10) > state.history.length;
  if (stale) {
    if (analysis.abort) analysis.abort.abort();
    analysis = { gameId: state.game_id, done: '', glyphs: [], abort: null };
    $('analysis-result').hidden = true;
    $('analysis-progress').hidden = true;
    setAnalysisMessage('Play a few moves, then analyze the game. Analysis starts on its own when the game ends.');
  }
  $('analyze').disabled = state.history.length < 2 || analysis.abort !== null;
  if (state.over && analysis.done !== analysisKey(state) && !analysis.abort) runAnalysis();
}

const analysisKey = (state) => `${state.history.length}:${$('depth').value}`;

async function runAnalysis() {
  const state = current;
  const key = analysisKey(state);
  const abort = new AbortController();
  analysis.abort = abort;
  $('analyze').disabled = true;
  $('analyze-label').textContent = 'Analyzing';
  $('analysis-result').hidden = true;
  $('analysis-progress').hidden = false;
  $('analysis-progress').value = 0;
  setAnalysisMessage(`Analyzing ${state.history.length} moves at ${DEPTHS[$('depth').value].label.toLowerCase()} depth`);
  try {
    const result = await analyzeGame(state.moves_uci, state.history, DEPTHS[$('depth').value], {
      signal: abort.signal,
      onProgress: (done, total) => { $('analysis-progress').value = done / total; },
    });
    if (!result || abort.signal.aborted) return;
    analysis.done = key;
    analysis.glyphs = result.moves.map((move) => (move.judgement ? move.judgement.glyph : ''));
    setAnalysisMessage('');
    $('analysis-result').hidden = false;
    renderChart($('chart'), $('chart-tooltip'), result);
    renderSummary(result.summary);
    renderVersusRandom(result);
    renderMoves(current.history);
  } catch (error) {
    if (!abort.signal.aborted) setAnalysisMessage(`Analysis failed: ${error.message}`, { error: true });
  } finally {
    if (analysis.abort === abort) analysis.abort = null;
    $('analysis-progress').hidden = true;
    $('analyze-label').textContent = 'Analyze game';
    $('analyze').disabled = !current || current.history.length < 2 || analysis.abort !== null;
  }
}

function playerName(color) {
  const who = currentHuman === color ? 'You' : modelName(current && current.models ? current.models[color] : 'jev');
  return `${color === 'white' ? 'White' : 'Black'} (${who})`;
}

function fillRows(body, rows) {
  body.replaceChildren();
  for (const [name, ...values] of rows) {
    const row = body.insertRow();
    const first = row.insertCell();
    first.className = 'col-text';
    first.textContent = name;
    for (const value of values) {
      const cell = row.insertCell();
      cell.className = 'col-num';
      cell.textContent = value;
    }
  }
}

function renderVersusRandom(result) {
  const versus = result.versusRandom;
  const colors = ['white', 'black'];
  const cp = (value) => (value === null ? '\u2013' : `${Math.round(value)} cp`);
  const share = (entry) => `${Math.round(entry.share)}% (${entry.picks})`;
  for (const color of colors) {
    for (const prefix of ['percentile', 'distance', 'decile', 'legend']) $(`${prefix}-${color}`).textContent = playerName(color);
  }
  fillRows($('versus-rows'), colors.map((color) => [
    playerName(color),
    versus[color].decisions ? String(Math.round(versus[color].percentile)) : '\u2013',
    cp(versus[color].decisions ? versus[color].loss : null),
    cp(versus[color].decisions ? versus[color].randomLoss : null),
  ]));
  const versusRandomCells = (name, index) => colors.flatMap((color) => [share(versus[color][name][index]), `${Math.round(versus[color][name][index].randomShare)}%`]);
  fillRows($('percentile-rows'), versus.all.percentileRanges.map((group, index) => [group.label, cp(group.averageLoss), cp(group.worstLoss), ...versusRandomCells('percentileRanges', index)]));
  fillRows($('distance-rows'), versus.all.distanceBands.map((group, index) => [group.label, ...versusRandomCells('distanceBands', index)]));
  const insideBest = ['best', 'top3', 'p5'].map((key) => versus.all.percentileRanges.findIndex((group) => group.key === key));
  fillRows($('decile-rows'), [
    ...insideBest.map((index) => [`Best 10% \u203a ${versus.all.percentileRanges[index].label.toLowerCase()}`, cp(versus.all.percentileRanges[index].averageLoss), ...versusRandomCells('percentileRanges', index)]),
    ...versus.all.deciles.map((decile, index) => [decileLabel(decile.decile), cp(decile.averageLoss), ...versusRandomCells('deciles', index)]),
  ]);
  [...$('decile-rows').rows].slice(0, insideBest.length).forEach((row) => row.classList.add('detail-row'));
  renderDeciles($('deciles'), $('decile-tooltip'), colors.map((color) => ({ color, name: playerName(color), deciles: versus[color].deciles })));
}

function renderSummary(summary) {
  fillRows($('summary'), ['white', 'black'].map((color) => {
    const stats = summary[color];
    return [playerName(color), ...[stats.moves, stats.acpl, stats.inaccuracy, stats.mistake, stats.blunder].map(String)];
  }));
}

for (const [key, depth] of Object.entries(DEPTHS)) $('depth').add(new Option(`${depth.label} (depth ${depth.game})`, key, key === 'standard', key === 'standard'));
$('analyze').addEventListener('click', runAnalysis);

async function askJev() {
  const turn = generation;
  const mover = current ? playerOf(current, current.turn).name : 'The model';
  setStatus(`${mover} is deciding`, { thinking: true });
  thinkingSince = Date.now();
  try {
    const state = await api('/api/jev', {});
    thinkingSince = null;
    if (turn === generation) render(state);
  } catch (error) {
    if (turn === generation) setStatus(error.message, { error: true, retry: true });
  }
}

function askPromotion(to, color) {
  const overlay = $('promotion');
  const choices = $('promotion-choices');
  const buttons = [...choices.querySelectorAll('button')];
  const whiteView = ground.state.orientation === 'white';
  const file = to.charCodeAt(0) - 97;
  choices.style.setProperty('--promotion-column', whiteView ? file : 7 - file);
  choices.classList.toggle('from-bottom', (to[1] === '8') !== whiteView);
  for (const button of buttons) button.querySelector('piece').className = `${button.getAttribute('aria-label').split(' ').pop()} ${color}`;
  overlay.hidden = false;
  buttons[0].focus();

  return new Promise((resolve) => {
    const finish = (piece) => {
      overlay.hidden = true;
      overlay.removeEventListener('click', onClick);
      overlay.removeEventListener('keydown', onKey);
      resolve(piece);
    };
    const onClick = (event) => {
      const button = event.target.closest('button');
      finish(button ? button.dataset.piece : null);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') finish(null);
      if (event.key !== 'Tab') return;
      event.preventDefault();
      const next = buttons.indexOf(document.activeElement) + (event.shiftKey ? -1 : 1);
      buttons[(next + buttons.length) % buttons.length].focus();
    };
    overlay.addEventListener('click', onClick);
    overlay.addEventListener('keydown', onKey);
  });
}

async function onHumanMove(from, to) {
  const current = generation;
  const moved = ground.state.pieces.get(to);
  let promotion = 'q';
  if (moved && moved.role === 'pawn' && (to[1] === '8' || to[1] === '1')) {
    promotion = await askPromotion(to, moved.color);
    if (current !== generation) return;
    if (promotion === null) return render(await api(stateUrl()));
  }
  try {
    const state = await api(moveUrl(), { from, to, promotion });
    if (current === generation) render(state);
  } catch (error) {
    if (current !== generation) return;
    render(await api(stateUrl()));
    setStatus(error.message, { error: true });
  }
}

async function refresh() {
  const current = ++generation;
  try {
    const state = await api(stateUrl());
    if (current === generation) render(state);
    return state;
  } catch (error) {
    if (current === generation) setStatus(error.message, { error: true, retry: true });
    return null;
  }
}

$('retry').addEventListener('click', refresh);
$('flip-board').addEventListener('click', () => {
  flipped = !flipped;
  $('flip-board').setAttribute('aria-pressed', String(flipped));
  if (current) {
    ground.set({ orientation: orientationOf(current) });
    renderPlayers(current);
  }
});

$('export-pgn').addEventListener('click', async () => {
  const text = $('pgn-text');
  text.textContent = 'Loading';
  text.classList.remove('error');
  $('pgn-dialog').showModal();
  try {
    const response = await fetch(pgnUrl());
    $('pgn-download').href = pgnUrl();
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    text.textContent = await response.text();
  } catch (error) {
    text.textContent = `Could not load the PGN: ${error.message}`;
    text.classList.add('error');
  }
});

const KEY_LINKS = {
  vercel: ['https://vercel.com/ai-gateway', 'vercel.com/ai-gateway'],
  openrouter: ['https://openrouter.ai/settings/keys', 'openrouter.ai/settings/keys'],
};

let settingsView = null;

function renderSettings(settings) {
  settingsView = settings;
  const select = $('settings-provider');
  select.replaceChildren(...settings.providers.map((provider) => new Option(provider.label, provider.id)));
  select.value = settings.provider;
  $('settings-key').value = '';
  $('settings-forget').hidden = settings.key_source !== 'session';
  $('settings-state').textContent = {
    local: `Runs on this server, no key and no cost. Model: ${settings.model}.`,
    session: `Using your key ending in ${settings.key_hint}. Model: ${settings.model}.`,
    environment: `Using the server's key. Model: ${settings.model}.`,
    none: 'No key set yet. The cloud models cannot move until you add one.',
  }[settings.key_source];
  syncProviderFields();
}

function syncProviderFields() {
  const provider = $('settings-provider').value;
  const local = provider === 'laya';
  $('settings-key-field').hidden = local;
  $('settings-key-note').hidden = local;
  const hint = $('settings-provider-hint');
  if (!local) {
    const [href, text] = KEY_LINKS[provider] || KEY_LINKS.vercel;
    hint.replaceChildren('Get a key at ', Object.assign(document.createElement('a'), { id: 'settings-key-link', href, target: '_blank', rel: 'noopener', textContent: text }), '.');
    return;
  }
  const installed = settingsView && settingsView.laya_installed;
  hint.textContent = installed
    ? 'Laya is an open-source decision model (Apache-2.0) that runs on this server. The first move loads it, which takes a while.'
    : "Laya is not installed on this server. Install it with pip install 'system-one-chess[laya]' and restart.";
}

async function settingsRequest(method, body) {
  $('settings-error').textContent = '';
  $('settings-save').disabled = true;
  try {
    const response = await fetch('api/settings', {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The server rejected these settings.');
    renderSettings(data);
    return true;
  } catch (error) {
    $('settings-error').textContent = error.message;
    return false;
  } finally {
    $('settings-save').disabled = false;
  }
}

$('open-settings').addEventListener('click', () => {
  $('settings-dialog').showModal();
  settingsRequest('GET');
});
$('settings-close').addEventListener('click', () => $('settings-dialog').close());
$('settings-provider').addEventListener('change', syncProviderFields);
$('settings-forget').addEventListener('click', () => settingsRequest('DELETE'));
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const saved = await settingsRequest('POST', { provider: $('settings-provider').value, api_key: $('settings-key').value });
  if (!saved) return;
  $('settings-dialog').close();
  if (current && current.jevs_turn) refresh();
});

async function copyText(text, source) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const range = document.createRange();
  range.selectNodeContents(source);
  const selection = getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  if (!document.execCommand('copy')) throw new Error('copy is not available');
}

$('pgn-copy').addEventListener('click', async () => {
  const button = $('pgn-copy');
  try {
    await copyText($('pgn-text').textContent, $('pgn-text'));
    button.classList.add('done');
    $('pgn-copied').textContent = 'PGN copied to clipboard';
    setTimeout(() => {
      button.classList.remove('done');
      $('pgn-copied').textContent = '';
    }, 2000);
  } catch (error) {
    $('pgn-copied').textContent = `Could not copy: ${error.message}`;
  }
});

const dialog = $('side-dialog');
const sideForm = $('side-form');
let models = [];
const chosen = { opponent: 'jev', white: 'jev', black: 'jev' };
const modelName = (id) => (models.find((model) => model.id === id) || { name: id }).name;

function logoNode(model) {
  const fallback = Object.assign(document.createElement('span'), { className: 'model-logo model-logo-fallback', textContent: (model.name || '?').slice(0, 1).toUpperCase() });
  fallback.setAttribute('aria-hidden', 'true');
  if (!model.logo) return fallback;
  const img = Object.assign(document.createElement('img'), { className: 'model-logo', src: model.logo, alt: '', width: 22, height: 22, loading: 'lazy' });
  img.addEventListener('error', () => img.replaceWith(fallback));
  return img;
}

let standingsKey = null;

async function loadStandings() {
  try {
    renderStandings((await api('/api/standings')).rows);
  } catch (error) {
    $('standings-empty').textContent = error.message;
  }
}

const compactTokens = (n) => (n < 1000 ? String(n) : n < 10000 ? `${(n / 1000).toFixed(1)}k` : n < 1000000 ? `${Math.round(n / 1000)}k` : `${(n / 1000000).toFixed(1)}M`);
const compactTime = (seconds) => (seconds < 60 ? `${Math.round(seconds)}s` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s` : `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`);
const spendCells = (row, withTime = true) => [compactTokens(row.input_tokens + row.output_tokens), ...(withTime ? [compactTime(row.seconds)] : []), `$${row.cost_usd.toFixed(4)}`];

function renderStandings(rows) {
  const body = $('standings');
  body.replaceChildren();
  $('standings-empty').hidden = rows.length > 0;
  for (const row of rows) {
    const tr = body.insertRow();
    const rank = tr.insertCell();
    rank.className = 'col-rank';
    rank.textContent = row.rank;
    const model = tr.insertCell();
    model.className = 'col-model';
    const head = Object.assign(document.createElement('span'), { className: 'model-head' });
    head.append(logoNode(row), Object.assign(document.createElement('span'), { className: 'model-head-name', textContent: row.name, title: row.id }));
    model.append(head);
    for (const value of [row.games, row.wins, row.draws, row.losses, row.points % 1 ? row.points.toFixed(1) : row.points, ...spendCells(row, false)]) {
      const cell = tr.insertCell();
      cell.className = 'col-num';
      cell.textContent = value;
    }
  }
}

const PAGE = /\/tournament\/?$/.test(location.pathname) ? 'tournament' : 'play';
let tournament = null;

function playerNode(player, extraClass = '') {
  const head = Object.assign(document.createElement('span'), { className: `model-head ${extraClass}`.trim() });
  head.append(logoNode(player), Object.assign(document.createElement('span'), { className: 'model-head-name', textContent: player.name, title: player.id }));
  return head;
}

function colourSwatch(colour) {
  const swatch = Object.assign(document.createElement('span'), { className: `player-colour ${colour}` });
  swatch.setAttribute('aria-hidden', 'true');
  return swatch;
}

function boardNode(pairing, colour, extraClass) {
  const head = playerNode(pairing[colour], extraClass);
  if (colour === 'white') head.prepend(colourSwatch('white'));
  else head.append(colourSwatch('black'));
  return head;
}

function renderRoundsDialog(view) {
  const body = $('rounds-body');
  body.replaceChildren();
  $('rounds-empty').hidden = view.rounds.length > 0;
  view.rounds.forEach((round, roundIndex) => {
    const block = Object.assign(document.createElement('section'), { className: 'round-block' });
    block.append(Object.assign(document.createElement('h3'), { textContent: `Round ${roundIndex + 1} of ${view.rounds_total}` }));
    const list = Object.assign(document.createElement('ol'), { className: 'boards' });
    round.pairings.forEach((pairing, index) => {
      const li = document.createElement('li');
      if (view.active && roundIndex === view.round - 1 && index === view.game - 1) li.classList.add('current');
      li.append(
        Object.assign(document.createElement('span'), { className: 'board-number', textContent: `${index + 1}.` }),
        boardNode(pairing, 'white', 'board-white'),
        Object.assign(document.createElement('span'), { className: 'board-result', textContent: pairing.result || '\u2013' }),
        boardNode(pairing, 'black', 'board-black'),
      );
      list.append(li);
    });
    if (round.bye) {
      const li = document.createElement('li');
      li.append(Object.assign(document.createElement('span'), { className: 'board-number', textContent: '' }), Object.assign(document.createElement('span'), { className: 'board-bye', textContent: `${round.bye.name} has the bye and scores a point` }));
      list.append(li);
    }
    block.append(list);
    body.append(block);
  });
}

function renderStandingsDialog(view) {
  const body = $('standings-rows');
  body.replaceChildren();
  $('standings-dialog-empty').hidden = view.standings.length > 0;
  for (const row of view.standings) {
    const tr = body.insertRow();
    Object.assign(tr.insertCell(), { className: 'col-num', textContent: row.rank });
    const cell = tr.insertCell();
    cell.className = 'col-text';
    cell.append(playerNode(row));
    const half = (n) => (n % 1 ? n.toFixed(1) : n);
    const values = [row.games, row.wins, row.draws, row.losses, half(row.points), half(row.buchholz), half(row.sonneborn_berger), row.calls, `${compactTokens(row.input_tokens)} / ${compactTokens(row.output_tokens)}`, compactTime(row.seconds), row.forfeits ? `${row.illegal} (${row.forfeits} lost)` : row.illegal, `$${row.cost_usd.toFixed(4)}`];
    for (const value of values) Object.assign(tr.insertCell(), { className: 'col-num', textContent: value });
  }
}

function renderTournament(view) {
  tournament = view;
  const status = $('tournament-status');
  $('tournament-stop').hidden = !view.active;
  $('open-standings').disabled = !view.rounds.length;
  $('open-rounds').disabled = !view.rounds.length;
  for (const [id, what] of [['tournament-pgn', 'as PGN'], ['tournament-export', 'with every statistic, as JSON']]) {
    const link = $(id);
    link.setAttribute('aria-disabled', String(!view.finished_games));
    link.classList.toggle('is-disabled', !view.finished_games);
    link.title = view.finished_games ? `Download the ${view.finished_games} finished game${view.finished_games === 1 ? '' : 's'} ${what}` : 'No finished game yet';
  }
  if (!view.rounds.length) status.textContent = 'No tournament yet';
  else if (view.done) {
    const leader = view.standings[0];
    status.replaceChildren(Object.assign(document.createElement('strong'), { textContent: 'Finished' }), ` after ${view.rounds_total} round${view.rounds_total === 1 ? '' : 's'} in ${compactTime(view.elapsed)}: ${leader.name} wins with ${leader.points % 1 ? leader.points.toFixed(1) : leader.points} points`);
  } else {
    status.replaceChildren(Object.assign(document.createElement('strong'), { textContent: `Round ${view.round} of ${view.rounds_total}` }), ` \u00b7 ${view.boards_finished} of ${view.boards_total} board${view.boards_total === 1 ? '' : 's'} finished \u00b7 ${compactTime(view.elapsed)}`);
  }
  const progress = $('tournament-progress');
  const total = view.rounds_total * Math.max(1, view.boards_total);
  progress.value = view.rounds.length ? (view.round - 1) * Math.max(1, view.boards_total) + view.boards_finished : 0;
  progress.max = total || 1;
  const top = $('tournament-top');
  top.replaceChildren();
  for (const row of view.standings) {
    const li = document.createElement('li');
    li.append(Object.assign(document.createElement('span'), { className: 'top-rank', textContent: row.rank }), playerNode(row), Object.assign(document.createElement('span'), { className: 'top-points', textContent: row.points % 1 ? row.points.toFixed(1) : row.points }));
    top.append(li);
  }
  renderBoards(view);
  renderStandingsDialog(view);
  renderRoundsDialog(view);
}

$('open-standings').addEventListener('click', () => $('standings-dialog').showModal());
$('standings-close').addEventListener('click', () => $('standings-dialog').close());
$('open-rounds').addEventListener('click', () => $('rounds-dialog').showModal());
$('rounds-close').addEventListener('click', () => $('rounds-dialog').close());

async function loadTournament() {
  try {
    renderTournament(await api('/api/tournament'));
  } catch (error) {
    $('tournament-status').textContent = error.message;
  }
  return tournament;
}

const minis = new Map();
let pollTimer = null;

function miniNode(board, view) {
  let mini = minis.get(board.board);
  if (!mini) {
    const root = Object.assign(document.createElement('button'), { className: 'mini', type: 'button' });
    const boardEl = Object.assign(document.createElement('div'), { className: 'mini-board' });
    const top = Object.assign(document.createElement('div'), { className: 'mini-row' });
    const bottom = Object.assign(document.createElement('div'), { className: 'mini-row' });
    const status = Object.assign(document.createElement('span'), { className: 'mini-status' });
    root.append(top, boardEl, bottom, status);
    const ground = Chessground(boardEl, { fen: board.fen, viewOnly: true, coordinates: false, animation: { enabled: false }, drawable: { enabled: false } });
    mini = { root, ground, top, bottom, status };
    root.addEventListener('click', () => selectBoard(board.board));
    minis.set(board.board, mini);
  }
  const orientation = board.human === 'black' ? 'black' : 'white';
  mini.ground.set({ fen: board.fen, lastMove: board.last_move || undefined, orientation, check: board.check });
  const topColour = orientation === 'white' ? 'black' : 'white';
  const row = (colour, el) => {
    const player = board[colour];
    const live = board.thinking_since && !board.over && board.turn === colour ? Math.max(0, view.now - board.thinking_since) : 0;
    const total = Math.round(board.clock[colour] + live);
    el.replaceChildren(playerNode(player), Object.assign(document.createElement('span'), { className: `mini-clock${!board.over && board.turn === colour ? ' active' : ''}`, textContent: `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}` }));
  };
  row(topColour, mini.top);
  row(orientation, mini.bottom);
  const text = board.error ? 'stopped, click to retry' : board.over ? board.result : board.humans_turn ? 'your move' : `move ${Math.floor(board.ply / 2) + 1}, ${board[board.turn].name} thinking`;
  mini.status.textContent = text;
  mini.status.className = `mini-status${board.error ? ' is-error' : board.humans_turn ? ' is-yours' : board.over ? ' is-over' : ''}`;
  mini.root.classList.toggle('selected', board.board === selectedBoard);
  mini.root.setAttribute('aria-pressed', String(board.board === selectedBoard));
  mini.root.setAttribute('aria-label', `Board ${board.board}: ${board.white.name} against ${board.black.name}, ${text}`);
  return mini.root;
}

function renderBoards(view) {
  const grid = $('boards');
  const round = view.rounds[view.rounds.length - 1];
  grid.hidden = !round;
  if (!round) return;
  const numbers = new Set(round.pairings.map((board) => board.board));
  for (const [number, mini] of minis) if (!numbers.has(number) || mini.gameId !== round.pairings[number - 1].game_id) { mini.root.remove(); minis.delete(number); }
  for (const board of round.pairings) {
    const node = miniNode(board, view);
    minis.get(board.board).gameId = board.game_id;
    if (node.parentElement !== grid) grid.append(node);
  }
}

async function selectBoard(number) {
  if (selectedBoard === number) {
    const board = tournament && tournament.rounds.length ? tournament.rounds[tournament.rounds.length - 1].pairings[number - 1] : null;
    if (board && board.error) await api(`/api/tournament/board/${number}/retry`, {});
    return;
  }
  selectedBoard = number;
  await refresh();
  if (tournament) renderBoards(tournament);
}

async function pollTournament() {
  if (PAGE !== 'tournament') return;
  try {
    const view = await api('/api/tournament');
    const previousRound = tournament ? tournament.round : 0;
    renderTournament(view);
    if (view.rounds.length) {
      const boards = view.rounds[view.rounds.length - 1].pairings;
      if (view.round !== previousRound || !selectedBoard || selectedBoard > boards.length) selectedBoard = view.human_board || 1;
      const board = boards[selectedBoard - 1];
      thinkingSince = board.thinking_since ? Date.now() - Math.max(0, view.now - board.thinking_since) * 1000 : null;
      const state = await api(stateUrl());
      if (!current || current.game_id !== state.game_id || current.history.length !== state.history.length || current.over !== state.over) render(state);
      else tickClocks();
    }
    clearTimeout(pollTimer);
    if (view.active) pollTimer = setTimeout(pollTournament, 1500);
  } catch (error) {
    setStatus(error.message, { error: true });
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollTournament, 4000);
  }
}

const tournamentDialog = $('tournament-dialog');
const tournamentForm = $('tournament-form');
const ROUND_CHOICES = [1, 2, 3, 4, 5, 6, 7];

function renderParticipants() {
  const box = $('participants');
  const picked = new Set([...tournamentForm.querySelectorAll('input[name="participant"]:checked')].map((input) => input.value));
  box.replaceChildren(...models.map((model) => {
    const chip = document.createElement('label');
    chip.className = 'chip';
    const input = Object.assign(document.createElement('input'), { type: 'checkbox', name: 'participant', value: model.id, disabled: !model.ready, checked: model.ready && (picked.size ? picked.has(model.id) : model.kind === 'system_one') });
    chip.append(input, logoNode(model), Object.assign(document.createElement('span'), { textContent: model.name }));
    if (!model.ready) chip.append(Object.assign(document.createElement('small'), { textContent: model.note }));
    return chip;
  }));
  if (!$('rounds-options').children.length) {
    $('rounds-options').replaceChildren(...ROUND_CHOICES.map((n) => {
      const label = document.createElement('label');
      label.className = 'segment-option';
      label.append(Object.assign(document.createElement('input'), { type: 'radio', name: 'rounds', value: n, checked: n === 3 }), Object.assign(document.createElement('span'), { textContent: n }));
      return label;
    }));
  }
  syncTournamentDialog();
}

function tournamentChoice() {
  const participants = [...tournamentForm.querySelectorAll('input[name="participant"]:checked')].map((input) => input.value);
  const human = tournamentForm.elements.human.value === 'yes';
  const rounds = parseInt(tournamentForm.elements.rounds.value, 10);
  return { participants, human, rounds };
}

function syncTournamentDialog() {
  const { participants, human, rounds } = tournamentChoice();
  const players = participants.length + (human ? 1 : 0);
  const games = Math.floor(players / 2) * rounds;
  $('tournament-start').disabled = players < 2;
  $('tournament-hint').textContent = players < 2 ? 'Pick at least two players' : `${players} players, ${rounds} round${rounds === 1 ? '' : 's'}, ${games} game${games === 1 ? '' : 's'}${players % 2 ? ', one bye per round' : ''}`;
}

function openTournamentDialog() {
  $('tournament-error').textContent = '';
  renderParticipants();
  loadModels();
  tournamentDialog.showModal();
}

tournamentForm.addEventListener('change', syncTournamentDialog);
$('tournament-cancel').addEventListener('click', () => tournamentDialog.close());
$('tournament-manage-models').addEventListener('click', () => {
  $('models-error').textContent = '';
  $('models-dialog').showModal();
  $('models-upstream').focus();
});
tournamentForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const current = ++generation;
  try {
    const view = await api('/api/tournament', tournamentChoice());
    tournamentDialog.close();
    selectedBoard = view.human_board || 1;
    renderTournament(view);
    clearTimeout(pollTimer);
    await pollTournament();
  } catch (error) {
    $('tournament-error').textContent = error.message;
  }
});
$('tournament-stop').addEventListener('click', async () => {
  try {
    const response = await fetch('api/tournament', { method: 'DELETE' });
    clearTimeout(pollTimer);
    selectedBoard = null;
    renderTournament(await response.json());
  } catch (error) {
    setStatus(error.message, { error: true });
  }
});

function renderSegments() {
  for (const box of sideForm.querySelectorAll('[data-segment]')) {
    const name = box.dataset.segment;
    if (!models.some((model) => model.id === chosen[name] && model.ready)) chosen[name] = (models.find((model) => model.ready) || models[0] || { id: 'jev' }).id;
    box.replaceChildren(...models.map((model) => {
      const label = document.createElement('label');
      label.className = `segment-option${model.ready ? '' : ' segment-unavailable'}`;
      const input = Object.assign(document.createElement('input'), { type: 'radio', name, value: model.id, checked: model.id === chosen[name], disabled: !model.ready });
      const title = Object.assign(document.createElement('span'), { className: 'model-head' });
      title.append(logoNode(model), Object.assign(document.createElement('span'), { className: 'model-head-name', textContent: model.name }));
      const note = Object.assign(document.createElement('small'), { textContent: model.ready ? (model.kind === 'llm' ? 'LLM' : model.provider === 'laya' ? 'local' : 'cloud') : model.note });
      label.append(input, title, note);
      return label;
    }));
  }
}

function syncSideDialog() {
  const play = sideForm.elements.mode.value === 'play';
  for (const name of ['opponent', 'white', 'black']) {
    const picked = sideForm.querySelector(`input[name="${name}"]:checked`);
    if (picked) chosen[name] = picked.value;
  }
  $('opponent-segment').hidden = !play;
  $('side-cards').hidden = !play;
  $('white-segment').hidden = play;
  $('black-segment').hidden = play;
  $('watch-cards').hidden = play;
  $('black-hint').textContent = `${modelName(chosen.opponent)} opens the game`;
  $('watch-name').textContent = `${modelName(chosen.white)} vs ${modelName(chosen.black)}`;
  $('side-footnote').textContent = play ? 'Pick your side to start playing right away.' : 'Both sides are decided by a model; you watch.';
}

async function loadModels() {
  try {
    const data = await api('/api/models');
    models = data.models;
    renderSegments();
    syncSideDialog();
    renderModels(data);
    if (PAGE === 'tournament') renderParticipants();
  } catch (error) {
    $('side-error').textContent = error.message;
  }
}

function openSideDialog() {
  $('side-error').textContent = '';
  renderSegments();
  syncSideDialog();
  loadModels();
  dialog.showModal();
  sideForm.elements.mode[0].focus();
}

$('new-game').addEventListener('click', () => (PAGE === 'tournament' ? openTournamentDialog() : openSideDialog()));
$('side-cancel').addEventListener('click', () => dialog.close());
sideForm.addEventListener('change', syncSideDialog);

sideForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const play = sideForm.elements.mode.value === 'play';
  const human = play ? event.submitter.value : 'none';
  const body = play
    ? { human, white: human === 'white' ? 'jev' : chosen.opponent, black: human === 'black' ? 'jev' : chosen.opponent }
    : { human, white: chosen.white, black: chosen.black };
  const current = ++generation;
  try {
    const state = await api('/api/new', body);
    dialog.close();
    if (current === generation) render(state);
  } catch (error) {
    $('side-error').textContent = error.message;
  }
});

function renderModels(data) {
  const body = $('models-rows');
  body.replaceChildren();
  for (const model of data.models) {
    const row = body.insertRow();
    const name = row.insertCell();
    name.className = 'col-text';
    const text = Object.assign(document.createElement('span'), { className: 'model-text' });
    text.append(Object.assign(document.createElement('strong'), { textContent: model.name }), Object.assign(document.createElement('span'), { className: 'model-upstream', textContent: model.upstream }));
    const head = Object.assign(document.createElement('span'), { className: 'model-head' });
    head.append(logoNode(model), text);
    name.append(head);
    const kind = row.insertCell();
    kind.className = 'col-badge';
    kind.append(Object.assign(document.createElement('span'), { className: `kind-badge kind-${model.kind}`, textContent: model.kind === 'llm' ? 'LLM' : 'System One' }));
    const runs = row.insertCell();
    runs.className = 'col-text';
    runs.textContent = { openrouter: 'OpenRouter', vercel: 'Vercel AI Gateway', laya: 'this server' }[model.provider] || model.provider;
    const ready = row.insertCell();
    ready.className = 'col-badge';
    ready.append(Object.assign(document.createElement('span'), { className: model.ready ? 'ready-yes' : 'ready-no', textContent: model.ready ? 'yes' : model.note }));
    const remove = row.insertCell();
    remove.className = 'col-badge';
    if (model.removable) {
      const button = Object.assign(document.createElement('button'), { className: 'icon-button', type: 'button', title: 'Remove' });
      button.setAttribute('aria-label', `Remove ${model.name}`);
      button.innerHTML = '<svg viewBox="0 0 24 24" width="1.25em" height="1.25em" fill="currentColor" aria-hidden="true"><path d="M18.3 5.7 12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7l-1.4-1.4L9.2 12 2.9 5.7l1.4-1.4 6.3 6.3 6.3-6.3z" transform="translate(1.4 0)"/></svg>';
      button.addEventListener('click', async () => {
        await fetch(`api/models/${model.upstream}`, { method: 'DELETE' });
        loadModels();
      });
      remove.append(button);
    }
  }
  $('models-suggested').replaceChildren(...data.suggested.map((m) => new Option(m.tier, m.upstream)));
}

$('manage-models').addEventListener('click', () => {
  $('models-error').textContent = '';
  $('models-dialog').showModal();
  $('models-upstream').focus();
});
$('models-close').addEventListener('click', () => $('models-dialog').close());
$('models-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('models-error').textContent = '';
  try {
    const response = await fetch('api/models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ upstream: $('models-upstream').value }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || (data.detail && data.detail[0] && data.detail[0].msg) || 'The server rejected that model.');
    $('models-upstream').value = '';
    models = data.models;
    renderSegments();
    syncSideDialog();
    renderModels(data);
    if (PAGE === 'tournament') renderParticipants();
  } catch (error) {
    $('models-error').textContent = error.message;
  }
});

renderDecision([]);
if (PAGE === 'tournament') {
  $('tournament-section').hidden = false;
  $('ranking-section').hidden = true;
  $('subtitle-play').hidden = true;
  $('subtitle-tournament').hidden = false;
  $('nav-tournament').hidden = true;
  $('nav-play').hidden = false;
  $('new-game').lastChild.textContent = ' New tournament';
  await loadModels();
  const view = await loadTournament();
  if (view && view.rounds.length) {
    selectedBoard = view.human_board || 1;
    await pollTournament();
  }
  if (!view || !view.active) openTournamentDialog();
} else {
  loadStandings();
  await loadModels();
  const initial = await refresh();
  if (initial && initial.history.length === 0) openSideDialog();
}
