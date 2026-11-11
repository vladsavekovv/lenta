// Remote control of this page's player (1.25.0). The server half, and why, is server/lenta/players.py.
//
// Every signed-in LENTA page checks in with the server: every 2 s while something plays, every 5 s
// while nothing does. It says which device it is and what it is doing, and collects any command another
// device (Jarvis) left for it — pause, play, seek, stop, open a title, a channel or an album, and (1.26.0)
// subtitles, audio, next, skip intro, volume, size and quality.
//
// It runs on its own timer, never on the player's drawing loop: browsers stop drawing in a hidden tab
// and in an app sent to the background, and that is exactly where a film keeps playing and where a
// pause has to arrive. Nothing here may break the page: every failure is swallowed and retried later.
import { remoteStatus, remoteCommand, playerOpen } from './player.js';
import { photoStatus } from './views.js';

const FAST = 2000, SLOW = 5000;
let timer = null, running = false, done = [], inflight = false, again = false;

function store(key, fallback) {
  try { return localStorage.getItem(key) || fallback(); } catch { return fallback(); }
}

let memoryId = null;
// One id per browser or app, made once and kept — unlike a film's play id, it survives restarts, which
// is what lets "the tablet" mean the same thing tomorrow.
export function deviceId() {
  return store('lenta.device', () => {
    const id = memoryId || (memoryId = Array.from({ length: 16 }, () => Math.random().toString(36)[2] || '0').join(''));
    try { localStorage.setItem('lenta.device', id); } catch { /* private window: this visit only */ }
    return id;
  });
}

export function deviceKind() {
  if (/LENTA-Android\//.test(navigator.userAgent)) return 'android';
  if (document.documentElement.classList.contains('tv')) return 'tv';
  return 'pc';
}

export function defaultDeviceName() {
  const kind = deviceKind();
  if (kind === 'android') return /Mobile/.test(navigator.userAgent) ? 'Android phone' : 'Android tablet';
  return kind === 'tv' ? 'TV' : 'PC';
}

// The name Vlad gave this device in Settings › Playback; the guess above until he does.
export function deviceName() {
  return store('lenta.deviceName', defaultDeviceName).slice(0, 40);
}

export function setDeviceName(name) {
  const n = (name || '').trim().slice(0, 40);
  try { n ? localStorage.setItem('lenta.deviceName', n) : localStorage.removeItem('lenta.deviceName'); } catch { /* kept for this visit only */ }
}

// 1.26.2: a command never holds up a check-in. Until then each command ran *inside* the check-in that
// collected it, and a slow one — a subtitle track the server extracts on demand, or a picture subtitle
// burned into a new stream — stopped every check-in after it: the outcome was never reported, and after
// 30 s the device dropped off the list while the film played on (Jarvis, 9 Nov, English subtitles).
// Now a command runs beside the check-ins, which go on every 2 s and say it is `running`; its outcome
// goes out in the first check-in after it ends; and after LIMIT it is reported failed ("timeout"), with
// the second the film was at, so whoever sent it always hears how it ended.
const LIMIT = 20000;
const working = new Set();                 // ids of commands being carried out now
const BEAT_WAIT = 10000;                   // a check-in the server has not answered in 10 s is given up

function finish(c, res) {
  working.delete(c.id);
  done.push({ id: c.id, ok: !!res.ok, position_s: res.position_s ?? null, ...(res.reason ? { reason: res.reason } : {}) });
  kick();                                  // report it now, not at the next beat
}

function run(c) {
  working.add(c.id);
  return new Promise((resolve) => {
    let over = false;
    const limit = setTimeout(() => {
      over = true;
      const at = remoteStatus();
      finish(c, { ok: false, reason: 'timeout', position_s: at.item_id ? at.position_s : null });
      resolve();
    }, LIMIT);
    Promise.resolve().then(() => remoteCommand(c)).catch(() => ({ ok: false, position_s: null })).then((res) => {
      if (over) return;                    // already reported as timed out; what it did late stays done
      clearTimeout(limit);
      finish(c, res || { ok: false });
      resolve();
    });
  });
}

async function beat() {
  timer = null;
  if (!running) return;
  if (inflight) { again = true; return; }
  inflight = true;
  const s = { ...remoteStatus(), ...(photoStatus() || {}) };     // photos: which album, which picture
  const body = { device_id: deviceId(), device_kind: deviceKind(), device_name: deviceName(), ...s, done,
                 running: [...working] };
  const sending = done;
  done = [];
  let stop = null;
  try {
    const ctl = typeof AbortController === 'function' ? new AbortController() : null;
    if (ctl) stop = setTimeout(() => ctl.abort(), BEAT_WAIT);
    const r = await fetch('/api/players/beat', { method: 'POST', credentials: 'same-origin', signal: ctl ? ctl.signal : undefined,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (r.status === 401) { clearTimeout(stop); running = false; inflight = false; syncStream(); return; }   // signed out: startRemote() begins again
    if (!r.ok) throw new Error(String(r.status));
    const { commands = [] } = await r.json();
    // Commands that came together keep their order; separate check-ins' commands do not wait for each other,
    // so a pause is not stuck behind a subtitle track that is still loading.
    commands.reduce((prev, c) => prev.then(() => run(c)), Promise.resolve());
    if (commands.length) { clearTimeout(stop); inflight = false; schedule(150); return; }   // say "running" straight away
  } catch {
    done = sending.concat(done);                              // not delivered: say it again next time
  }
  clearTimeout(stop);
  inflight = false;
  if (again) { again = false; schedule(0); return; }
  schedule();
}

const busy = () => (playerOpen() && remoteStatus().state !== 'idle') || !!photoStatus();

function schedule(ms) {
  syncStream();
  if (!running || timer) return;
  timer = setTimeout(beat, ms ?? (busy() ? FAST : SLOW));
}

// 1.26.0: a command should not wait for the next check-in. While this page is in front, or playing,
// it listens on the event stream (events.py), and a command queued for this device makes it check in
// at once. The check-in above stays exactly as it was and is what still works when the stream does
// not: a proxy that holds it back, an engine without EventSource, a page in the background.
//
// Only in front or busy, because a browser gives a server six connections in all: every LENTA tab
// holding a stream open would use one, and six forgotten tabs would leave none for the page itself.
let stream = null;
function wantStream() {
  return running && typeof EventSource === 'function'
    && (document.visibilityState === 'visible' || busy());
}

function syncStream() {
  try {
    if (wantStream() && !stream) {
      stream = new EventSource('/api/events');
      stream.addEventListener('command', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (d.device_id === deviceId() && d.status === 'queued') kick();
        } catch { /* not for us */ }
      });
      stream.onerror = () => {
        if (stream && stream.readyState === 2) { stream = null; }   // closed for good (signed out): the beat goes on
      };
    } else if (!wantStream() && stream) {
      stream.close();
      stream = null;
    }
  } catch { stream = null; }
}

function kick() {
  if (!running) return;
  clearTimeout(timer);
  timer = null;
  schedule(0);
}

try { document.addEventListener('visibilitychange', () => { if (running) syncStream(); }); } catch { /* fine */ }

export function startRemote() {
  if (running) return;
  running = true;
  deviceId();                                   // made now, so the page has its id before the first check-in
  schedule(500);
}

export function stopRemote() {
  running = false;
  clearTimeout(timer);
  timer = null;
  syncStream();
}
