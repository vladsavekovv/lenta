// TV mode: LENTA with a remote control (Samsung TV app, or a TV's web browser).
//
//   arrows        move between pictures, buttons and menu items (the nearest one in that direction)
//                 the menu is reached by pressing ▲ where it sits across the top, ◀ where it runs down
//                 the side, and you walk along it the same way round
//   OK / Enter    open / press
//   Back          close a menu or dialog, leave the player, go back a page; on Home, leave the app
//   ▶ ❚❚ ⏯ ⏩ ⏪ ■  play, pause, play/pause, +10 s, −10 s, stop (in the player)
//   CH ▲ ▼       the next or previous channel, while watching live TV
//
// In the player: OK plays or pauses, ◀ ▶ jump 10 s, ▲ ▼ show the controls and move onto them.
// Turned on by the LENTA TV app (?tvapp=1), by a TV browser, or by adding ?tv=1 to the address once.

import { pageScrollTo, popMenu } from './ui.js';

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
  // Back goes by many numbers. 10009 is Samsung's Return, 461 is LG's, 8 is a keyboard's Backspace,
  // 166 and 27 turn up on Android TV boxes and some browsers. None of them costs anything to accept,
  // and a remote whose Back key does nothing is the one thing that makes a TV app feel broken.
  10009: 'Back', 461: 'Back', 8: 'Back', 166: 'Back', 27: 'Back',
  415: 'Play', 19: 'Pause', 10252: 'PlayPause', 179: 'PlayPause',
  417: 'FastForward', 412: 'Rewind', 413: 'Stop',
  427: 'ChannelUp', 428: 'ChannelDown',
};

// The remote's own channel buttons. Live TV is the one place on a TV where they should mean something,
// and because the channel you came from is kept warm for half an hour, going back up is immediate.
let channelList = null;

async function stepChannel(delta) {
  const at = location.hash.match(/^#\/play\/live\/(\d+)/);
  if (!at) return;                                        // not watching live: the buttons mean nothing here
  if (!channelList) {
    try {
      const r = await fetch('/api/livetv/channels', { credentials: 'same-origin' });
      channelList = (await r.json()).channels || [];
    } catch { channelList = []; }
  }
  if (channelList.length < 2) return;
  const i = channelList.findIndex(c => String(c.id) === at[1]);
  if (i < 0) return;
  // Round the end, the way a television does: channel one is what follows the last one.
  const to = channelList[(i + delta + channelList.length) % channelList.length];
  location.hash = `#/play/live/${to.id}`;
}

function keyName(e) {
  if (KEYS[e.keyCode] && !(e.keyCode === 8 && e.target.closest?.('input, textarea'))) return KEYS[e.keyCode];
  return { MediaPlayPause: 'PlayPause', MediaPlay: 'Play', MediaPause: 'Pause', MediaFastForward: 'FastForward',
    MediaRewind: 'Rewind', MediaStop: 'Stop', BrowserBack: 'Back', GoBack: 'Back', XF86Back: 'Back',
    Back: 'Back', BrowserHome: 'Back', Cancel: 'Back' }[e.key] || e.key;
}

// ---- the Samsung TV app ----------------------------------------------------------------------------
// The app opens LENTA as its page, so the TV's own functions (window.tizen) are here when the TV provides them.
const tizen = () => window.tizen;

/** What the television said about itself, handed over by the app's own page.
 *
 *  Nothing here can ask the set directly. This page is served by LENTA, and Tizen refuses the device
 *  APIs to any page outside the app's own origin (Web Runtime compliance, requirement 0380), so
 *  `window.tizen` is undefined here however the app was opened. Until 1.17 this file called
 *  `registerKeys()` anyway, inside a try/catch, and it has never once done anything — which is why the
 *  remote's Play, Pause and Stop have never reached LENTA in the TV app.
 *
 *  The app's page keeps the origin now and sends this over, so the facts arrive instead of being
 *  guessed. Empty until it does, and empty for good in a TV's own browser, which has no app page.
 */
let platform = null;
export const tvPlatform = () => platform;

/** Ask the app's page to take a key, or give it back. The television delivers a registered key to the
 *  application instead of acting on it itself, so MediaPlayPause is only worth holding while a film is
 *  open — otherwise Samsung's own playback overlay stops working everywhere else. */
export function holdKey(name, on = true) {
  if (window.parent === window) return;
  parent.postMessage({ lenta: on ? 'register-key' : 'unregister-key', name }, '*');
}

/** A key the app's page saw and passed on. It forwards everything, because whether a registered key
 *  also reaches a frame on another origin is not documented and not worth guessing; so a forwarded key
 *  that already arrived here natively a moment ago is dropped, and the app works either way. */
const seenNatively = new Map();
function forwardedKey(d) {
  const last = seenNatively.get(d.keyCode) || 0;
  if (Date.now() - last < 120) return;                 // this page already had it: not twice
  document.dispatchEvent(new KeyboardEvent('keydown', { keyCode: d.keyCode, key: d.key, bubbles: true }));
}

function listenToHost() {
  if (window.parent === window) return;
  window.addEventListener('message', (e) => {
    if (e.source !== window.parent) return;      // only the window hosting this page may speak
    const d = e.data;
    if (!d || !d.lenta) return;
    if (d.lenta === 'platform') { platform = d.platform || null; return; }
    if (d.lenta === 'key') forwardedKey(d);
  });
  // remember what arrived here by itself, so the same key forwarded by the app is not acted on twice
  window.addEventListener('keydown', (e) => { seenNatively.set(e.keyCode, Date.now()); }, true);
  parent.postMessage({ lenta: 'ready' }, '*');
}

// ---- what is this remote actually sending? -------------------------------------------------------
// Open LENTA with ?keys=1 and every key press appears in the corner: the number the set sent, the name
// it gave, and what LENTA made of it. A remote whose Back key does nothing is otherwise unanswerable
// from here — the sets disagree about the number, and no amount of reasoning settles which one yours
// uses. Thirty seconds with this says it exactly.
let watching = false;
let forcedKeyWatch = false;
let noted = [];

export const keyWatching = () => watching;

/** Turn the watcher on or off from Settings › LENTA app.
 *
 *  The address bar is not a way in on a television — the app has none — and the TV's own browser would
 *  answer about itself rather than about the app, which is where the key is not working. So it is a
 *  switch you can reach with the remote you are trying to diagnose.
 */
export function setKeyWatch(on) {
  store('lenta-keys', on ? '1' : '0');
  applyKeyWatch(on);
}

/** Show or hide the panel. Called from Settings, and at startup from the saved preference.
 *
 *  Only ever on a television. The switch is deliberately reachable from a laptop — that is the whole
 *  point of it being a saved preference rather than something you hunt for with the broken remote —
 *  but a panel of key codes on the laptop itself would be clutter answering a question nobody asked
 *  there. */
export function applyKeyWatch(on) {
  // ?keys=1 on the address is someone saying "now, on this screen". It outranks the saved preference,
  // which is a standing arrangement — otherwise the preference, read a moment later when the session
  // starts, would quietly switch off the thing just asked for.
  if (!on && forcedKeyWatch) return;
  if (on && !tvMode()) return;
  if (on) {
    if (!watching) keyWatch();
    return;
  }
  watching = false;
  document.getElementById('lenta-keys')?.remove();
}

/** Add a line to the watcher from elsewhere in LENTA — silent unless the watcher is switched on.
 *
 *  A television has no console to open and no address bar to type in. When a film will not start there
 *  is nothing at all to look at, and "it doesn't play" is as far as anyone can get. This panel is the
 *  only window into the set, so the player writes into it too. */
export const tvNote = (kind, detail) => note(kind, detail);

function note(kind, detail) {
  if (!watching) return;
  noted.unshift(`${kind}: ${detail}`);
  noted = noted.slice(0, 12);
  const box = document.getElementById('lenta-keys');
  if (box) box.textContent = noted.join('\n');
}

function keyWatch() {
  watching = true;
  const box = document.createElement('pre');
  box.id = 'lenta-keys';
  box.style.cssText = 'position:fixed;right:12px;top:12px;z-index:99999;max-width:46vw;margin:0;'
    + 'padding:10px 12px;border-radius:10px;background:rgba(0,0,0,.82);color:#9fe870;'
    + 'font:13px/1.45 monospace;white-space:pre-wrap;pointer-events:none';
  box.textContent = 'Press a key on the remote…\n(?keys=1 — close the app to stop)';
  const put = () => (document.body || document.documentElement).appendChild(box);
  if (document.body) put(); else document.addEventListener('DOMContentLoaded', put);
  // Before everything, and not swallowed: this only watches.
  window.addEventListener('keydown', (e) => {
    note('keydown', `code ${e.keyCode}  key ${e.key || '(none)'}  \u2192 LENTA reads "${keyName(e)}"`);
  }, true);
}

// Some Tizen firmware delivers Back as its own event rather than a key press. Listening for both costs
// nothing, and whichever arrives, the same thing happens.
function wireHardwareKeys() {
  const onHw = (e) => {
    // The same event travels document -> window, so a listener on each sees it twice. Marking the
    // event itself is exact, where a stopwatch would only be a guess.
    if (e.__lenta) return;
    try { e.__lenta = true; } catch { /* a frozen event: the guard in backOnce still catches it */ }
    const name = (e.keyName || e.detail?.keyName || '').toLowerCase();
    if (name === 'back' || name === 'exit') {
      e.preventDefault?.();
      note('tizenhwkey', name);
      backOnce();
    }
  };
  document.addEventListener('tizenhwkey', onHw);
  window.addEventListener('tizenhwkey', onHw);
}

/** Leave the app, and say which way out actually worked.
 *
 *  Three ways, because which one exists depends on how LENTA was opened. The Tizen object belongs to
 *  the app's own page and a set that has navigated to the server may no longer have it; a frame can be
 *  asked by its parent; a plain browser tab can sometimes close itself. When none of them works there
 *  is nothing more to try, and saying so is better than a key that looks dead.
 */
export function exitApp() {
  try { tizen().application.getCurrentApplication().exit(); return 'closed the app'; } catch { /* not the app's own page */ }
  if (window.parent !== window) { parent.postMessage({ lenta: 'exit' }, '*'); return 'asked the app frame to close'; }
  try { window.close(); } catch { /* a tab that did not open itself */ }
  return 'nothing here can close it';
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
  // .ppanel is the player's own settings, subtitles and queue panel. It is drawn inside the player, so
  // without naming it here the layer on top was the player itself: the ring stayed on the controls
  // behind the panel, and the arrows went on meaning what they mean over the picture.
  const layers = [...document.querySelectorAll('.sheet-back, .modal-back, .trailer-modal, .lightbox, .ppanel')];
  if (layers.length) return layers[layers.length - 1];
  const player = document.querySelector('.player:not(.mini)');
  if (player) return player;
  return document.body;
}

// ---- the page's geometry, read once per key press ------------------------------------------------
// One arrow press compares the thing in focus against everything it could move to, and on Home that is
// several hundred cards. Asking the engine where each one is, is what makes a television feel
// unresponsive: after any change the engine must work the whole layout out again before it can answer,
// and the old code asked three times per card and asked its style as well. A few thousand of those is a
// visible wait between pressing the button and the ring moving — on a slow set, long enough that the
// next press arrives first and appears to have been ignored.
//
// Nothing on the page moves while a single press is being handled, so none of the answers can change:
// they are worked out once and kept until the press is finished.
let geom = null;

function rect(el) {
  if (!geom) return el.getBoundingClientRect();
  let r = geom.rects.get(el);
  if (r === undefined) geom.rects.set(el, r = el.getBoundingClientRect());
  return r;
}

function shown(el) {
  if (!geom) return visible(el);
  let v = geom.shown.get(el);
  if (v === undefined) geom.shown.set(el, v = visible(el));
  return v;
}

/** Run one key press with the geometry held still. Nested calls share the outermost one's answers. */
function press(fn) {
  const outer = geom;
  if (!outer) geom = { rects: new Map(), shown: new Map() };
  try { return fn(); } finally { if (!outer) geom = null; }
}

function visible(el) {
  if (el.closest('[hidden], .player.mini')) return false;
  const r = rect(el);
  if (r.width < 2 || r.height < 2) return false;
  const cs = getComputedStyle(el);
  return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
}

// The rows of Home, the lanes of the guide, the banner at the top: a block of the page holding many
// things, which stands or falls as one.
const BANDS = '.row, .guide-lane, .hero';
// The settings page's own column of pages, which behaves like the menu rather than like the page.
const SIDE_LIST = '.sset-nav';

function candidates(layer, cur, dir, want) {
  const vh = window.innerHeight;
  const near = (r) => r.bottom > -vh && r.top < vh * 2;              // on screen, or a row either side of it
  // Measure the rows first and throw away whole rows at a time. Home has four hundred focusable things
  // on it and a dozen rows; asking the engine where each card is, is the single biggest cost of one
  // press of the remote, and a card eight rows below the screen can never be the next one anyway. One
  // question per row answers it for every card in that row.
  const bands = [...layer.querySelectorAll(BANDS)];
  const dead = new Set();
  for (const band of bands) if (!near(rect(band))) dead.add(band);

  // Then narrow again by where the press is going. ◀ ▶ along a row of pictures can only end on
  // another picture in that row; ▲ ▼ can only end in a row near this one. Two rows either side
  // rather than one, because a short row can sit beside the next and an empty one has to be stepped
  // over. Everything that is not in a row at all — the menu, the buttons on the banner, the controls
  // in the corner — is always in the reckoning, and there are only ever a handful of those.
  let scope = null;
  if (cur && layer === document.body && !inRail(cur)) {
    const band = cur.closest(BANDS);
    if (band && !dead.has(band)) {
      const i = bands.indexOf(band);
      scope = dir === 'left' || dir === 'right' ? [band]
        : dir === 'down' ? bands.slice(i, i + 3)
        : dir === 'up' ? bands.slice(Math.max(0, i - 2), i + 1) : null;
    }
  }

  // The menu is only ever in the reckoning when the press is trying to reach it or walk along it,
  // and it is two dozen links. Asked for the page, do not measure it at all.
  return [...layer.querySelectorAll(FOCUSABLE)].filter(el => {
    if (layer === document.body && el.closest('.player, .menu, .modal-back, .sheet-back')) return false;
    if (want === 'page' && inRail(el)) return false;
    if (want === 'rail' && !inRail(el)) return false;
    const band = bands.length ? el.closest(BANDS) : null;
    if (band) {
      if (dead.has(band)) return false;                              // its row is nowhere near
      if (scope && scope.indexOf(band) < 0) return false;            // its row is not in this direction
    }
    return near(rect(el)) && shown(el);
  });
}

// A card's picture and its name link both lead to the same place: only the picture takes part.
// Containers that are focusable only for screen readers, the row carousels' scroll arrows (the arrows on the remote
// scroll the row) and the row titles (the libraries are in the menu on the left) stay out of the way too.
const skip = (el) => el.matches('.card .caption a, .caption .t, .scroll-btn, .row > h2 a, main, #view, .page')
  || (rect(el).width > window.innerWidth * 0.8 && rect(el).height > window.innerHeight * 0.6);
const inRail = (el) => !!el.closest('.rail');

function centre(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }

function best(from, list, dir, strict) {
  const a = rect(from), ca = centre(a);
  let pick = null, score = Infinity;
  for (const el of list) {
    if (el === from || skip(el)) continue;
    const b = rect(el), cb = centre(b);
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

// While a burst of presses is being worked through, the ring moves but the page does not: scrolling
// after every one of them would be a layout and a repaint each, and the last is the only one anybody
// sees. The element it ended on is remembered and brought into view once, at the end.
let holdScroll = false;
let scrollTo = null;

function focusEl(el) {
  if (!el) return;
  el.focus({ preventScroll: true });
  if (holdScroll) { scrollTo = el; return; }
  showEl(el);
}

function showEl(el) {
  // The menu across the top sits at the very top of the page and has nothing above it, so arriving
  // there with the page still half scrolled leaves the banner cut through the middle with the bar
  // lying over it. Coming back to the menu means coming back to the top.
  if (inRail(el) && document.documentElement.classList.contains('topnav')) { pageScrollTo(0); return; }
  // One scroll, not two. Asking for "nearest" and then measuring where that put it is a second layout
  // and a second repaint for every press; the same question can be answered before scrolling at all.
  // The band is the part of the screen that is neither under the menu nor behind the row of controls
  // along the bottom: anything landing outside it is put in the middle instead.
  const r = el.getBoundingClientRect();
  const out = r.top < 90 || r.bottom > window.innerHeight - 60;
  // Instantly, not smoothly. A smooth scroll is an animation, and on a television it is still running
  // when the next press arrives, so the ring trails behind the button by several rows.
  el.scrollIntoView({ block: out ? 'center' : 'nearest', inline: 'nearest' });
}

const firstIn = (layer) => press(() => firstInNow(layer));

function firstInNow(layer) {
  const list = candidates(layer).filter(el => !skip(el));
  const main = layer === document.body ? list.filter(el => !el.closest('.rail')) : list;
  return (main.find(el => el.matches('.btn.primary, .hero .btn, .card .frame-link')) || main[0] || list[0]);
}

/** Move the ring, one press or a dozen.
 *
 *  A remote held down sends twenty presses a second, and each one used to be a full round of
 *  questions to the engine about where everything is — four hundred of them on Home before the rows
 *  were ruled out, a hundred and twenty after. Twenty times a second is more work than a television
 *  can do, so the presses queue up behind frames that have not been drawn and the ring arrives
 *  somewhere unexpected a second after the thumb stopped.
 *
 *  Nothing about the page changes while the ring moves across it, so a burst of presses can be
 *  answered from one set of measurements: the queue is worked through on the next frame, sharing a
 *  single cache, and the page is scrolled once at the end. Eight presses then cost what one did.
 */
let queue = [];
let scheduled = 0;
const soon = window.requestAnimationFrame ? (fn) => window.requestAnimationFrame(fn) : (fn) => setTimeout(fn, 16);

function move(dir) {
  queue.push(dir);
  if (scheduled) return;
  scheduled = 1;
  soon(runQueue);
}

function runQueue() {
  scheduled = 0;
  const dirs = queue;
  queue = [];
  if (!dirs.length) return;
  holdScroll = true;
  scrollTo = null;
  try {
    press(() => { for (let i = 0; i < dirs.length; i++) moveNow(dirs[i]); });
  } finally {
    holdScroll = false;
  }
  if (scrollTo && document.contains(scrollTo)) showEl(scrollTo);
  scrollTo = null;
}

function moveNow(dir) {
  const layer = topLayer();
  let cur = document.activeElement;
  if (!cur || cur === document.body || !layer.contains(cur) || !visible(cur) || skip(cur)) { focusEl(firstIn(layer)); return; }
  let next;
  // Which way the menu runs. Two of the themes put it as a bar across the top rather than a column
  // down the side, and the difference is not cosmetic: on a bar you walk along it with ◀ ▶ and drop
  // into the page with ▼, where on a column you walk it with ▲ ▼ and leave it with ▶. Assuming the
  // column everywhere made Settings unreachable in those themes — the first ▶ in the menu jumped
  // straight back into the page, so you could never walk along to the gear at the end of it.
  const bar = document.documentElement.classList.contains('topnav');
  if (layer === document.body && inRail(cur)) {
    const rail = candidates(layer, cur, dir, 'rail');
    const page = candidates(layer, cur, dir, 'page');
    next = bar
      ? (dir === 'left' || dir === 'right' ? best(cur, rail, dir)
        : dir === 'down' ? (best(cur, page, dir) || firstIn(layer)) : null)   // nothing above a top bar
      : (dir === 'right' ? (best(cur, page, dir) || firstIn(layer)) : best(cur, rail, dir));
  } else {
    let page = candidates(layer, cur, dir, 'page');
    // Settings has a second column of its own: the list of settings pages down the left. It behaves
    // like the menu — ▲ ▼ walk it, ▶ leaves it for the panels, ◀ comes back to it — because what made
    // settings feel strange was a ▼ halfway down a panel slipping sideways into that list, simply
    // because the next thing below happened to be four pixels nearer over there.
    const col = cur.closest(SIDE_LIST);
    const updown = dir === 'up' || dir === 'down';
    if (layer === document.body) {
      if (col && updown) page = page.filter(el => el.closest(SIDE_LIST) === col);
      else if (col && dir === 'right') page = page.filter(el => !el.closest(SIDE_LIST));
      else if (!col && updown) page = page.filter(el => !el.closest(SIDE_LIST));
    }
    // ◀ ▶ stay in the same row of pictures; at the end of a row nothing happens. The way out of the
    // page and into the menu is whichever direction the menu actually lies in.
    const strict = layer === document.body && (dir === 'left' || dir === 'right') && !!cur.closest('.track');
    next = best(cur, page, dir, strict);
    if (!next && dir === (bar ? 'up' : 'left') && layer === document.body) {
      const rail = candidates(layer, cur, dir, 'rail');            // the item for the page you are on
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

// One press of Back arrives up to three times: twice as a Tizen hardware event (document and window)
// and once as an ordinary key press. Acting on each of them walked back three pages at once, which from
// anywhere shallow meant falling out of the app — the single likeliest reason Back "does nothing".
// Presses this close together are one press: no remote, and no thumb, sends two in a third of a second.
let lastBack = 0;
function backOnce() {
  const now = Date.now();
  if (now - lastBack < 350) { note('back', 'the same press again, ignored'); return; }
  lastBack = now;
  note('back', back());
}

function back() {
  const menu = document.querySelector('.menu');
  if (menu) { menu.remove(); return 'closed the menu'; }
  const layer = topLayer();
  if (layer !== document.body) {
    if (layer.classList.contains('player')) {
      // Ask the player, which is the only thing that knows whether a settings panel is open on top of
      // it. It calls preventDefault to say it dealt with the press; a button is only the fallback for
      // the error screen, which is the one place the player does have one.
      const ask = new CustomEvent('lenta:back', { cancelable: true, detail: {} });
      if (!window.dispatchEvent(ask)) return ask.detail.did || 'left the player';
      const b = layer.querySelector('[data-a="close"]');
      if (b) { b.click(); return 'left the player'; }
      return 'nothing in the player answered';
    }
    escape();
    if (document.contains(layer)) layer.querySelector('.close, [data-close], .modal-close, button[aria-label="Close"]')?.click();
    return 'closed what was on top';
  }
  const h = location.hash.replace(/^#/, '') || '/';
  if (h !== '/' && h !== '') { history.back(); return 'went back a page'; }
  // On Home there is nowhere left to go but out.
  return inTvApp() ? exitApp() : 'already on Home, and this is not the TV app';
}

// ---- player --------------------------------------------------------------------------------------
const playerKey = (key) => document.dispatchEvent(keyEvent(key));

function inPlayer() { return document.querySelector('.player:not(.mini)'); }

/** ▲ or ▼ over the picture: bring the controls up and put the ring on them.
 *
 *  This used to name one button by its id and give up if it was not there — and the id had been
 *  renamed, so it was never there. Nothing took the ring, so the player decided the ring was not on its
 *  controls, and every arrow afterwards fell through to seeking: ◀ ▶ jumped ten seconds and nothing
 *  else on the remote did anything at all.
 */
function showPlayerControls(p) {
  p.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));
  p.classList.remove('idle');                 // now, not after the fade: the ring is going there
  const pick = () => p.querySelector('#pplay2, #pplay, .bottom .pbtn, .pbtn') || firstIn(p);
  const now = pick();
  if (now) { focusEl(now); return; }
  // the controls are being drawn: try again once they are there
  requestAnimationFrame(() => {
    const later = pick();
    if (later && document.contains(later)) focusEl(later);
  });
}

function onKey(e) {
  const k = keyName(e);
  const p = inPlayer();
  const active = document.activeElement;
  const typing = active?.matches?.('input[type=text], input[type=search], input:not([type]), input[type=password], textarea');
  if (k === 'Back') {
    if (typing && e.keyCode === 8) return;
    e.preventDefault(); e.stopPropagation();
    backOnce();
    return;
  }
  if (k === 'ChannelUp' || k === 'ChannelDown') {
    e.preventDefault(); e.stopPropagation();
    stepChannel(k === 'ChannelUp' ? 1 : -1);
    return;
  }
  // Something drawn on top of the player — its own settings panel, or a menu — is a layer of its own,
  // and the arrows belong to it rather than to the picture behind it. Without this the panel opened,
  // the ring stayed on the controls underneath, and every press went to seeking the film.
  // The transport keys mean the same thing whatever is drawn over the picture.
  const media = p && { Play: 'play', Pause: 'pause', PlayPause: ' ', FastForward: 'l', Rewind: 'j', Stop: 'Escape' }[k];
  if (media) {
    e.preventDefault(); e.stopPropagation();
    const v = p.querySelector('video');
    if (media === 'play') { if (v?.paused) playerKey(' '); }
    else if (media === 'pause') { if (v && !v.paused) playerKey(' '); }
    else playerKey(media);
    return;
  }
  if (p && topLayer() === p) {
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
  // A drop-down, opened with OK rather than left to the engine. Handing the arrows to a <select> meant
  // they belonged to it for ever: the ring went in and could not come back out, and on a library page
  // — where the first thing above the pictures is the genre box — that put the whole menu out of
  // reach. Nothing on screen moved, however many times the button was pressed.
  if (k === 'Enter' && active?.matches?.('select')) {
    e.preventDefault(); e.stopPropagation();
    chooseFromSelect(active);
    return;
  }
  if (k.startsWith('Arrow')) {
    if (typing && (k === 'ArrowLeft' || k === 'ArrowRight') && active.value) return;              // move the caret
    e.preventDefault(); e.stopPropagation();
    move({ ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[k]);
  }
}

function chooseFromSelect(sel) {
  const items = [...sel.options].map((o, i) => ({
    label: `${o.textContent}${i === sel.selectedIndex ? '   ✓' : ''}`,
    action: () => {
      if (sel.selectedIndex === i) return;
      sel.selectedIndex = i;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    },
  }));
  popMenu(sel, [{ heading: sel.getAttribute('aria-label') || 'Choose' }, ...items]);
}

// Keys the TV app forwards, when the app's frame rather than LENTA's page had the focus.
//
// This used to be registered here, at module level, with **no check on where the message came from** —
// so it was live in every browser, not only on a television, and any page that had opened LENTA in a
// tab kept a handle to that window and could post into it. The opener cannot read anything back
// (same-origin policy), but it could drive the focus ring blind: walk it, press Back, change the live
// channel, and open a <select> and commit a choice, which is what `savePref` is wired to.
//
// Two conditions now. The listener only exists on a television (it is called from `initTv`), and a
// message is only acted on if it came from the window that is **hosting** this page. There is no
// legitimate sender other than that one: the TV app frames LENTA, so the sender is always the parent.
// `extras.js`'s YouTube receiver has always done this correctly (`e.source !== frame.contentWindow`)
// and is the model.
function listenForForwardedKeys() {
  window.addEventListener('message', (e) => {
    if (window.parent === window || e.source !== window.parent) return;
    const d = e.data;
    if (!d || d.lenta !== 'key') return;
    onKey({ keyCode: d.keyCode || 0, key: d.key || '', target: document.activeElement || document.body,
      preventDefault() {}, stopPropagation() {} });
  });
}

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
  wireHardwareKeys();
  listenForForwardedKeys();
  // ?keys=1 on the address, or the switch in Settings › LENTA app for a set with no address bar.
  if (params.get('keys') === '1') { forcedKeyWatch = true; store('lenta-keys', '1'); }
  if (store('lenta-keys') === '1') keyWatch();
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
  if (inTvApp()) listenToHost();
}
