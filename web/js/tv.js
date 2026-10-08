// TV mode: LENTA with a remote control (Samsung TV app, or a TV's web browser).
//
//   arrows        move between pictures, buttons and menu items (the nearest one in that direction)
//   OK / Enter    open / press
//   Back          close a menu or dialog, leave the player, go back a page; on Home, leave the app
//   ▶ ❚❚ ⏯ ⏩ ⏪ ■  play, pause, play/pause, +10 s, −10 s, stop (in the player)
//
// In the player: OK plays or pauses, ◀ ▶ jump 10 s, ▲ ▼ show the controls and move onto them.
// Turned on by the LENTA TV app (?tvapp=1), by a TV browser, or by adding ?tv=1 to the address once.

const TV_UA = /Tizen|SMART-TV|SmartTV|Web0S|webOS|NetCast|BRAVIA|AFTS|AFTM|GoogleTV|CrKey|HbbTV/i;
const params = new URLSearchParams(location.search);
const store = (k, v) => { try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); } catch { return null; } };
if (params.get('tvapp') === '1') store('lenta-tvapp', '1');
if (params.get('home')) store('lenta-tvhome', params.get('home'));     // the TV app's own start page (to change server)
if (params.get('tv') === '1') store('lenta-tv', '1');
if (params.get('tv') === '0') store('lenta-tv', '0');

export const inTvApp = () => store('lenta-tvapp') === '1';
export const tvMode = () => inTvApp() || store('lenta-tv') === '1' || (store('lenta-tv') !== '0' && TV_UA.test(navigator.userAgent));

// key codes of TV remotes (Samsung Tizen, LG webOS) besides the standard keys
const KEYS = {
  10009: 'Back', 461: 'Back', 8: 'Back',
  415: 'Play', 19: 'Pause', 10252: 'PlayPause', 179: 'PlayPause',
  417: 'FastForward', 412: 'Rewind', 413: 'Stop',
};

function keyName(e) {
  if (KEYS[e.keyCode] && !(e.keyCode === 8 && e.target.closest?.('input, textarea'))) return KEYS[e.keyCode];
  return { MediaPlayPause: 'PlayPause', MediaPlay: 'Play', MediaPause: 'Pause', MediaFastForward: 'FastForward',
    MediaRewind: 'Rewind', MediaStop: 'Stop', BrowserBack: 'Back', GoBack: 'Back' }[e.key] || e.key;
}

// ---- the Samsung TV app ----------------------------------------------------------------------------
// The app opens LENTA as its page, so the TV's own functions (window.tizen) are here when the TV provides them.
const tizen = () => window.tizen;

function registerKeys() {
  try {
    const keys = ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaFastForward', 'MediaRewind'];
    const t = tizen();
    if (t?.tvinputdevice) {
      const have = new Set(t.tvinputdevice.getSupportedKeys().map(k => k.name));
      const want = keys.filter(k => have.has(k));
      if (t.tvinputdevice.registerKeyBatch) t.tvinputdevice.registerKeyBatch(want);
      else want.forEach(k => t.tvinputdevice.registerKey(k));
    }
  } catch { /* an older TV: OK still plays and pauses */ }
}

export function exitApp() {
  try { tizen().application.getCurrentApplication().exit(); return; } catch { /* not in the TV app */ }
  if (window.parent !== window) parent.postMessage({ lenta: 'exit' }, '*');
  else window.close();
}

export function changeServer() {
  const home = store('lenta-tvhome');
  if (home) { location.href = home + (home.includes('?') ? '&' : '?') + 'setup=1'; return; }
  parent.postMessage({ lenta: 'change-server' }, '*');
}

// ---- what can be focused, in the layer that is on top ------------------------------------------------
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex="-1"])';

function topLayer() {
  const menu = [...document.querySelectorAll('.menu')].pop();
  if (menu) return menu;
  const layers = [...document.querySelectorAll('.sheet-back, .modal-back, .trailer-modal, .lightbox')];
  if (layers.length) return layers[layers.length - 1];
  const player = document.querySelector('.player:not(.mini)');
  if (player) return player;
  return document.body;
}

function visible(el) {
  if (el.closest('[hidden], .player.mini')) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
}

function candidates(layer) {
  const vh = window.innerHeight;
  return [...layer.querySelectorAll(FOCUSABLE)].filter(el => {
    if (layer === document.body && el.closest('.player, .menu, .modal-back, .sheet-back')) return false;
    const r = el.getBoundingClientRect();
    return r.bottom > -vh * 2 && r.top < vh * 3 && visible(el);      // near the screen (rows scroll into view)
  });
}

// A card's picture and its name link both lead to the same place: only the picture takes part.
// Containers that are focusable only for screen readers, the row carousels' scroll arrows (the arrows on the remote
// scroll the row) and the row titles (the libraries are in the menu on the left) stay out of the way too.
const skip = (el) => el.matches('.card .caption a, .caption .t, .scroll-btn, .row > h2 a, main, #view, .page')
  || (el.getBoundingClientRect().width > window.innerWidth * 0.8 && el.getBoundingClientRect().height > window.innerHeight * 0.6);
const inRail = (el) => !!el.closest('.rail');

function centre(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }

function best(from, list, dir, strict) {
  const a = from.getBoundingClientRect(), ca = centre(a);
  let pick = null, score = Infinity;
  for (const el of list) {
    if (el === from || skip(el)) continue;
    const b = el.getBoundingClientRect(), cb = centre(b);
    let main, cross;
    if (dir === 'right') { main = b.left - a.right; cross = Math.abs(cb.y - ca.y); if (cb.x <= ca.x + 1) continue; }
    else if (dir === 'left') { main = a.left - b.right; cross = Math.abs(cb.y - ca.y); if (cb.x >= ca.x - 1) continue; }
    else if (dir === 'down') { main = b.top - a.bottom; cross = Math.abs(cb.x - ca.x); if (cb.y <= ca.y + 1) continue; }
    else { main = a.top - b.bottom; cross = Math.abs(cb.x - ca.x); if (cb.y >= ca.y - 1) continue; }
    // overlapping in the other direction (same row / column) is strongly preferred
    const overlap = (dir === 'left' || dir === 'right')
      ? Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
      : Math.min(a.right, b.right) - Math.max(a.left, b.left);
    if (strict && overlap <= 0) continue;
    const s = Math.max(main, -20) * 1 + cross * (overlap > 0 ? 0.4 : 2.5) + (overlap > 0 ? 0 : 400);
    if (s < score) { score = s; pick = el; }
  }
  return pick;
}

function focusEl(el) {
  if (!el) return;
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  const r = el.getBoundingClientRect();                       // keep it comfortably on screen (not under the edges)
  if (r.top < 90 || r.bottom > window.innerHeight - 60) el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
}

function firstIn(layer) {
  const list = candidates(layer).filter(el => !skip(el));
  const main = layer === document.body ? list.filter(el => !el.closest('.rail')) : list;
  return (main.find(el => el.matches('.btn.primary, .hero .btn, .card .frame-link')) || main[0] || list[0]);
}

function move(dir) {
  const layer = topLayer();
  let cur = document.activeElement;
  if (!cur || cur === document.body || !layer.contains(cur) || !visible(cur) || skip(cur)) { focusEl(firstIn(layer)); return; }
  const all = candidates(layer);
  let next;
  if (layer === document.body && inRail(cur)) {
    // in the menu: up and down move through it, right goes back to the page
    next = dir === 'right' ? (best(cur, all.filter(el => !inRail(el)), dir) || firstIn(layer)) : best(cur, all.filter(inRail), dir);
  } else {
    const page = all.filter(el => !inRail(el));
    // ◀ ▶ stay in the same row of pictures; at the end of a row nothing happens (◀ at the start opens the menu)
    const strict = layer === document.body && (dir === 'left' || dir === 'right') && !!cur.closest('.track');
    next = best(cur, page, dir, strict);
    if (!next && dir === 'left' && layer === document.body) {                                     // left edge: the menu,
      const rail = all.filter(inRail);                                                              // on the page you're on
      next = rail.find(el => el.matches('a.active')) || best(cur, rail, dir);
    }
  }
  if (next) focusEl(next);
}

// ---- Back --------------------------------------------------------------------------------------
// a key press made by LENTA itself (older TV engines ignore "key" in the event's settings, so it is set afterwards)
function keyEvent(key) {
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  if (ev.key !== key) Object.defineProperty(ev, 'key', { value: key });
  return ev;
}
function escape() { document.dispatchEvent(keyEvent('Escape')); }

function back() {
  const menu = document.querySelector('.menu');
  if (menu) { menu.remove(); return; }
  const layer = topLayer();
  if (layer !== document.body) {
    if (layer.classList.contains('player')) { layer.querySelector('[data-a="close"]')?.click(); return; }
    escape();
    if (document.contains(layer)) layer.querySelector('.close, [data-close], .modal-close, button[aria-label="Close"]')?.click();
    return;
  }
  const h = location.hash.replace(/^#/, '') || '/';
  if (h !== '/' && h !== '') { history.back(); return; }
  if (inTvApp()) exitApp();                                       // on Home: leave the app
}

// ---- player --------------------------------------------------------------------------------------
const playerKey = (key) => document.dispatchEvent(keyEvent(key));

function inPlayer() { return document.querySelector('.player:not(.mini)'); }

function showPlayerControls(p) {
  p.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));
  const play = p.querySelector('#pplay');
  if (play && visible(play)) focusEl(play);
}

function onKey(e) {
  const k = keyName(e);
  const p = inPlayer();
  const active = document.activeElement;
  const typing = active?.matches?.('input[type=text], input[type=search], input:not([type]), input[type=password], textarea');
  if (k === 'Back') {
    if (typing && e.keyCode === 8) return;
    e.preventDefault(); e.stopPropagation();
    back();
    return;
  }
  if (p) {
    const media = { Play: 'play', Pause: 'pause', PlayPause: ' ', FastForward: 'l', Rewind: 'j', Stop: 'Escape' }[k];
    if (media) {
      e.preventDefault(); e.stopPropagation();
      const v = p.querySelector('video');
      if (media === 'play') { if (v?.paused) playerKey(' '); }
      else if (media === 'pause') { if (v && !v.paused) playerKey(' '); }
      else playerKey(media);
      return;
    }
    const onControls = active && p.contains(active) && active !== p && !p.classList.contains('idle');
    if (!onControls) {
      // the picture: OK plays/pauses, ◀ ▶ seek (the player does that), ▲ ▼ bring up the controls
      if (k === 'Enter') { e.preventDefault(); e.stopPropagation(); playerKey(' '); return; }
      if (k === 'ArrowUp' || k === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); active?.blur?.(); showPlayerControls(p); return; }
      if (active && p.contains(active)) active.blur();       // controls had faded out: arrows seek again
      return;                                                 // ◀ ▶: the player seeks
    }
    if (k.startsWith('Arrow')) {
      if (active.matches('input[type=range]') && (k === 'ArrowLeft' || k === 'ArrowRight')) return;   // volume
      if (active.id === 'pseek' && (k === 'ArrowLeft' || k === 'ArrowRight')) return;               // seek bar
      e.preventDefault(); e.stopPropagation();
      p.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));                           // keep them visible
      move({ ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[k]);
    }
    return;
  }
  if (k.startsWith('Arrow')) {
    if (typing && (k === 'ArrowLeft' || k === 'ArrowRight') && active.value) return;              // move the caret
    if (active?.matches?.('select')) return;
    e.preventDefault(); e.stopPropagation();
    move({ ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[k]);
  }
}

// keys the TV app forwards (when the app frame, not LENTA, had the focus)
window.addEventListener('message', (e) => {
  const d = e.data;
  if (!d || d.lenta !== 'key') return;
  onKey({ keyCode: d.keyCode || 0, key: d.key || '', target: document.activeElement || document.body,
    preventDefault() {}, stopPropagation() {} });
});

// After a page change, put the focus somewhere sensible so the remote works straight away.
function settle() {
  setTimeout(() => {
    const layer = topLayer();
    const a = document.activeElement;
    if (a && a !== document.body && layer.contains(a) && visible(a) && !skip(a)) return;
    focusEl(firstIn(layer));
  }, 450);
}

export function initTv() {
  if (!tvMode()) return;
  document.documentElement.classList.add('tv');
  window.addEventListener('keydown', onKey, true);           // before the page's own key handlers
  window.addEventListener('hashchange', settle);
  document.addEventListener('focusin', (e) => { if (e.target.matches?.('main, #view, .page')) settle(); });   // a page was drawn
  new MutationObserver(() => {                                 // a menu or dialog opened: focus into it
    const layer = topLayer();
    if (layer !== document.body && !layer.contains(document.activeElement)) {
      const f = firstIn(layer);
      if (f && !layer.classList.contains('player')) focusEl(f);
    }
  }).observe(document.body, { childList: true });
  settle();
  if (inTvApp()) {
    registerKeys();
    if (window.parent !== window) parent.postMessage({ lenta: 'ready' }, '*');
  }
}
