import { Chessground } from './vendor/chessground/chessground.min.js';
import { DEPTHS, analyzeGame, decileLabel, renderChart, renderDeciles } from './analysis.js';

const $ = (id) => document.getElementById(id);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const DECISION_ROWS = 3;

let generation = 0;
let currentHuman = 'white';
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

function render(state) {
  ground.set({
    fen: state.fen,
    orientation: state.human === 'black' ? 'black' : 'white',
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
  renderUsage(state.usage);
  syncAnalysis(state);
  $('game-id').textContent = state.game_id;
  renderDecision(state.jev_top);
  renderMoves(state.history);
  if (state.over) setStatus(`Game over: ${state.result}`);
  else if (state.humans_turn) setStatus(`Your move (${state.turn})`);
  else askJev();
}

function renderUsage(usage) {
  $('usage-calls').textContent = usage.calls.toLocaleString('en-US');
  $('usage-input').textContent = usage.input_tokens.toLocaleString('en-US');
  $('usage-output').textContent = usage.output_tokens.toLocaleString('en-US');
  $('usage-latency').textContent = usage.calls ? `${Math.round((usage.seconds / usage.calls) * 1000)} ms` : '\u2013';
  $('usage-cost').textContent = `$${usage.cost_usd.toFixed(6)}`;
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
  const who = currentHuman === color ? 'You' : 'Jev';
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
  const current = generation;
  setStatus('Jev is deciding', { thinking: true });
  try {
    const state = await api('/api/jev', {});
    if (current === generation) render(state);
  } catch (error) {
    if (current === generation) setStatus(error.message, { error: true, retry: true });
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
    if (promotion === null) return render(await api('/api/state'));
  }
  try {
    const state = await api('/api/move', { from, to, promotion });
    if (current === generation) render(state);
  } catch (error) {
    if (current !== generation) return;
    render(await api('/api/state'));
    setStatus(error.message, { error: true });
  }
}

async function refresh() {
  const current = ++generation;
  try {
    const state = await api('/api/state');
    if (current === generation) render(state);
    return state;
  } catch (error) {
    if (current === generation) setStatus(error.message, { error: true, retry: true });
    return null;
  }
}

$('retry').addEventListener('click', refresh);

$('export-pgn').addEventListener('click', async () => {
  const text = $('pgn-text');
  text.textContent = 'Loading';
  text.classList.remove('error');
  $('pgn-dialog').showModal();
  try {
    const response = await fetch('api/pgn');
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
    none: 'No key set yet. Jev cannot move until you add one.',
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

function openSideDialog({ cancellable }) {
  $('side-cancel').hidden = !cancellable;
  dialog.showModal();
  dialog.querySelector(`button[value="${currentHuman}"]`).focus();
}

$('new-game').addEventListener('click', () => openSideDialog({ cancellable: true }));
$('side-cancel').addEventListener('click', () => dialog.close());

$('side-form').addEventListener('submit', async (event) => {
  const current = ++generation;
  try {
    const state = await api('/api/new', { human: event.submitter.value });
    if (current === generation) render(state);
  } catch (error) {
    if (current === generation) setStatus(error.message, { error: true, retry: true });
  }
});

renderDecision([]);
const initial = await refresh();
if (initial && initial.history.length === 0) openSideDialog({ cancellable: false });
