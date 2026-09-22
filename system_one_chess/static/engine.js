const ENGINE_URL = new URL('./vendor/stockfish/stockfish-19-lite-single.js', import.meta.url);
const MATE = 100000;

export function isMate(score) {
  return Math.abs(score) > MATE / 2;
}

export function mateDistance(score) {
  return Math.sign(score) * (MATE - Math.abs(score));
}

function parseScore(line) {
  const match = line.match(/ score (cp|mate) (-?\d+)/);
  if (!match) return null;
  const value = Number(match[2]);
  if (match[1] === 'cp') return value;
  if (value === 0) return -MATE;
  return Math.sign(value) * (MATE - Math.abs(value));
}

export function createEngine() {
  const worker = new Worker(ENGINE_URL);
  let onLine = () => {};
  worker.onmessage = (event) => onLine(String(event.data));

  function until(command, isDone, onEach = () => {}) {
    return new Promise((resolve, reject) => {
      worker.onerror = (event) => reject(new Error(event.message || 'Stockfish failed to load'));
      onLine = (line) => {
        onEach(line);
        if (isDone(line)) resolve();
      };
      worker.postMessage(command);
    });
  }

  const ready = until('uci', (line) => line === 'uciok').then(() => until('isready', (line) => line === 'readyok'));

  const position = (movesUci) => `position startpos${movesUci.length ? ` moves ${movesUci.join(' ')}` : ''}`;

  return {
    async evaluateForWhite(movesUci, depth) {
      await ready;
      let score = 0;
      worker.postMessage('setoption name MultiPV value 1');
      worker.postMessage(position(movesUci));
      await until(`go depth ${depth}`, (line) => line.startsWith('bestmove'), (line) => {
        if (!line.startsWith('info') || line.includes('bound')) return;
        const parsed = parseScore(line);
        if (parsed !== null) score = parsed;
      });
      return movesUci.length % 2 === 0 ? score : -score;
    },
    async scoreEveryMove(movesUci, depth) {
      await ready;
      const scores = new Map();
      worker.postMessage('setoption name MultiPV value 256');
      worker.postMessage(position(movesUci));
      await until(`go depth ${depth}`, (line) => line.startsWith('bestmove'), (line) => {
        if (!line.startsWith('info') || line.includes('bound')) return;
        const move = line.match(/ pv (\S+)/);
        const parsed = parseScore(line);
        if (move && parsed !== null) scores.set(move[1], parsed);
      });
      return scores;
    },
    quit() {
      worker.terminate();
    },
  };
}
