import { Chessground } from './vendor/chessground/chessground.min.js';

const $ = (id) => document.getElementById(id);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const DECISION_ROWS = 3;

let generation = 0;
let currentHuman = 'white';

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
    for (const [className, text] of [['number', `${i / 2 + 1}.`], ['', history[i]], ['', history[i + 1] || '']]) {
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
  currentHuman = state.human;
  $('game-id').textContent = state.game_id;
  renderDecision(state.jev_top);
  renderMoves(state.history);
  if (state.over) setStatus(`Game over: ${state.result}`);
  else if (state.humans_turn) setStatus(`Your move (${state.turn})`);
  else askJev();
}

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

async function onHumanMove(from, to) {
  const current = generation;
  try {
    const state = await api('/api/move', { from, to, promotion: $('promotion').value });
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

const dialog = $('side-dialog');

function openSideDialog({ cancellable }) {
  $('side-cancel').hidden = !cancellable;
  dialog.showModal();
  $('side-form').elements.human.value = currentHuman;
  dialog.querySelector('input:checked').focus();
}

$('new-game').addEventListener('click', () => openSideDialog({ cancellable: true }));
$('side-cancel').addEventListener('click', () => dialog.close());

$('side-form').addEventListener('submit', async () => {
  const current = ++generation;
  try {
    const state = await api('/api/new', { human: $('side-form').elements.human.value });
    if (current === generation) render(state);
  } catch (error) {
    if (current === generation) setStatus(error.message, { error: true, retry: true });
  }
});

renderDecision([]);
const initial = await refresh();
if (initial && initial.history.length === 0) openSideDialog({ cancellable: false });
