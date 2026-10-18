// Detail-page atmosphere: the fixed backdrop stage, trailers (background or full screen) and theme music.
// Everything registers a cleanup so leaving the page, signing out or pressing Play stops it.
import { api } from './api.js';
import { state } from './app.js';
import { musicPlaying } from './music.js';
import { dockPlaying } from './dock.js';
import { $, html, icon, img, mount, onPageScroll, pageY, safeUrl } from './ui.js';
import { tvMode } from './tv.js';

const cleanups = [];
let pageToken = 0;
export function onCleanup(fn) { cleanups.push(fn); }
export function runCleanups() {
  pageToken++;
  while (cleanups.length) { try { cleanups.pop()(); } catch { /* keep going */ } }
}

// ---------------------------------------------------------------- stage
export function stageHtml(art, { soft = false } = {}) {
  return html`<div class="stage${soft ? ' soft' : ''}" id="stage" aria-hidden="true">
    ${art ? html`<div class="stage-art" style="background-image:url('${img(art, 1920)}')"></div>` : ''}
    <div class="stage-trailer" id="stage-trailer"></div>
    <div class="stage-shade"></div><div class="stage-dim"></div></div>`;
}

let trailerBg = null;    // the background trailer controller of the current page, if any
let theme = null;        // the theme-music controller of the current page, if any

export function wireStage() {
  const st = $('#stage');
  if (!st) return;
  let raf = 0;
  const onScroll = () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const dim = Math.min(1, pageY() / (window.innerHeight * 0.7));
      st.style.setProperty('--dim', dim.toFixed(3));
      trailerBg?.visible(dim < 0.85);
    });
  };
  const stop = onPageScroll(onScroll);
  onScroll();
  onCleanup(() => { stop(); cancelAnimationFrame(raf); });
}

// ---------------------------------------------------------------- YouTube helpers
const YT = 'https://www.youtube-nocookie.com/embed/';
export function ytSrc(key, { muted, controls, start = 0 }) {
  // cc_load_policy 0 + iv_load_policy 3: no captions or annotations asked for (the captions module is also
  // unloaded once it plays, see trailerPlayer: some videos switch captions on by default)
  const p = new URLSearchParams({ autoplay: '1', mute: muted ? '1' : '0', controls: controls ? '1' : '0', rel: '0', cc_load_policy: '0',
    playsinline: '1', modestbranding: '1', iv_load_policy: '3', enablejsapi: '1', origin: location.origin });
  if (!controls) p.set('disablekb', '1');
  if (start >= 1) p.set('start', String(Math.floor(start)));
  return `${YT}${encodeURIComponent(key)}?${p}`;
}
export function ytCommand(frame, func, args = []) {
  frame?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args }), '*');
}
export function ytListen(frame, onState, onInfo, onError, onAny) {
  const onMsg = (e) => {
    if (e.source !== frame.contentWindow) return;
    let d;
    try { d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data; } catch { return; }
    onAny?.();            // anything at all from the embed proves it is listening to us as well
    if (d?.event === 'onError') { onError?.(Number(d.info)); return; }
    const s = d?.event === 'onStateChange' ? d.info : d?.info?.playerState;
    if (typeof s === 'number') onState(s);
    if (onInfo && d?.event === 'infoDelivery' && d.info && typeof d.info === 'object') onInfo(d.info);
  };
  window.addEventListener('message', onMsg);
  frame.addEventListener('load', () => {
    frame.contentWindow.postMessage(JSON.stringify({ event: 'listening', id: 'lenta', channel: 'widget' }), '*');
  });
  return () => window.removeEventListener('message', onMsg);
}

/** "With sound" trailers start muted (browsers only allow sound once you've clicked or pressed a key on the
 *  page; after a refresh you haven't). This calls go() as soon as sound is allowed: at once if it already is
 *  (you've clicked, or the browser trusts this site), otherwise at your first click, tap or key press
 *  anywhere. Pressing a sound button yourself cancels it. Returns a function that cancels the wait. */
export function whenSoundAllowed(go) {
  if (navigator.userActivation ? navigator.userActivation.hasBeenActive : true) { go(); return () => {}; }
  let done = false;
  const evs = ['pointerdown', 'keydown', 'touchend'];
  const off = () => evs.forEach(ev => document.removeEventListener(ev, fire, true));
  function fire(e) {
    if (done) return;
    done = true; off();
    if (e && e.target && e.target.closest && e.target.closest('[data-t=sound], .hc-sound')) return;   // you're pressing it yourself
    go();
  }
  evs.forEach(ev => document.addEventListener(ev, fire, true));
  try {         // a site you often play media on may play sound without a click (Chrome's media engagement)
    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC) { const ac = new AC(); const ok = ac.state === 'running'; if (ac.close) ac.close(); if (ok) setTimeout(() => fire(), 0); }
  } catch (_) { /* no Web Audio */ }
  return () => { done = true; off(); };
}

/** Tell the server a YouTube trailer can't be played here; returns the key to play instead (or null). */
const replaced = new Map();
export async function trailerUnavailable(key) {
  if (replaced.has(key)) return replaced.get(key);
  let next = null;
  try { next = (await api.post('/api/trailers/unavailable', { key })).key || null; } catch { /* offline */ }
  replaced.set(key, next);
  return next;
}

// ---- one player for every kind of trailer -------------------------------------------------------
// A trailer key is a YouTube id, or "v:<address>" for a video file (a local trailer file, Apple TV).
export const isFileTrailer = (key) => typeof key === 'string' && key.startsWith('v:');

/** Moves a volume (0-100) to `target` over `ms`, smoothly. A newer call cancels the running one (it resolves
 *  false). Timers, not animation frames, so a fade in a background tab still finishes. */
function fader(get, set) {
  let run = 0;
  return (target, ms = 700) => new Promise((done) => {
    const id = ++run, from = get(), t0 = performance.now();
    if (ms <= 0 || Math.abs(target - from) < 0.5) { set(target); done(true); return; }
    const step = () => {
      if (id !== run) { done(false); return; }
      const k = Math.min(1, (performance.now() - t0) / ms);
      set(from + (target - from) * k * k * (3 - 2 * k));
      if (k < 1) setTimeout(step, 40); else done(true);
    };
    step();
  });
}

/** { el, play, pause, setMuted, fadeTo, time, destroy } — onState gets 1 playing, 2 paused, 0 ended (YouTube's codes). */
/** Whether a trailer should play behind the page at all.
 *
 *  Not on a television. A background trailer is a second video decoding while you browse, on a machine
 *  that is working hard enough to decode one — it is the single most expensive thing Home does. And
 *  when it fails, which a YouTube embed on a TV often does, the artwork it was meant to replace has
 *  already been hidden: what is left is an empty panel where the banner should be.
 *
 *  A trailer the viewer actually asked for — the Trailer button, which has controls — still plays.
 */
export const backgroundTrailers = () => !tvMode();

export function trailerPlayer(key, { muted = true, controls = false, start = 0, onState, onTime, replace = true, onUnavailable, skipLead = true } = {}) {
  if (!controls && !backgroundTrailers()) return null;
  // Settings › Playback › Media pages › Start trailers after: skip the green "approved for appropriate audiences" card
  if (skipLead) start = Math.max(start, Number(state.prefs?.trailer_skip || 0));
  if (isFileTrailer(key)) {
    const v = document.createElement('video');
    v.src = key.slice(2);
    v.muted = muted;
    v.playsInline = true;
    v.setAttribute('playsinline', '');
    v.preload = 'auto';
    v.controls = !!controls;
    v.tabIndex = -1;
    if (start > 0.5) v.addEventListener('loadedmetadata', () => { try { v.currentTime = Math.min(start, (v.duration || start + 1) - 0.5); } catch { /* not seekable */ } }, { once: true });
    v.addEventListener('playing', () => onState?.(1));
    v.addEventListener('pause', () => { if (!v.ended) onState?.(2); });
    v.addEventListener('ended', () => onState?.(0));
    v.addEventListener('error', () => onState?.(0));      // can't be played here: behave as if it ended
    v.addEventListener('timeupdate', () => onTime?.(v.currentTime));
    const go = () => v.play().catch(() => { if (!v.muted) { v.muted = true; v.play().catch(() => {}); } });
    queueMicrotask(go);
    let vol = 80;
    const setVol = (x) => { vol = Math.max(0, Math.min(100, x)); v.volume = vol / 100; };
    return {
      el: v, file: true, play: go, pause: () => v.pause(),
      setMuted: (m) => { v.muted = m; if (!m) setVol(vol); },
      fadeTo: fader(() => vol, setVol),
      time: () => v.currentTime,
      destroy: () => { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); },
    };
  }
  const f = document.createElement('iframe');
  let current = key, isMuted = muted, swaps = 0;
  f.src = ytSrc(key, { muted, controls, start });
  f.allow = `autoplay; encrypted-media; picture-in-picture${controls ? '; fullscreen' : ''}`;
  if (controls) f.allowFullscreen = true;
  else f.tabIndex = -1;
  f.title = 'Trailer';
  let t = start, at = 0, vol = 80, sent = -1;
  // A command to a YouTube embed is a postMessage into an iframe that may not be listening yet, and one
  // sent too early is dropped with no error at all. That is how a trailer ends up playing at YouTube's own
  // volume instead of ours — we said setVolume(0), nothing heard it, and the cache below then recorded it
  // as said. So commands wait here until the embed has spoken to us, which is the only proof it is
  // listening; and the mute and the volume are said again at that moment.
  let ready = false;
  const queued = [];
  const cmd = (func, args = []) => { if (ready) ytCommand(f, func, args); else queued.push([func, args]); };
  const flush = () => {
    if (ready) return;
    ready = true;
    for (const [func, args] of queued.splice(0)) ytCommand(f, func, args);
    ytCommand(f, isMuted ? 'mute' : 'unMute');
    sent = -1;
    setVol(vol);
  };
  const setVol = (x) => {
    vol = Math.max(0, Math.min(100, x));
    const r = Math.round(vol);
    if (r !== sent) { sent = r; cmd('setVolume', [r]); }
  };
  // YouTube shows captions when the viewer's YouTube settings or the uploader ask for them: switch them off
  // as the trailer starts (a few times, the captions module loads a moment after playback begins).
  // With controls (the Trailer button) the CC button can still turn them back on.
  const noCaptions = () => {
    for (const ms of [0, 600, 2000]) setTimeout(() => {
      if (!f.isConnected) return;
      cmd('unloadModule', ['captions']);
      cmd('unloadModule', ['cc']);
    }, ms);
  };
  let capsOff = '';
  const stop = ytListen(f, (s) => {
    flush();                                            // it answered: whatever we said too early, say again
    if (s === 1 && capsOff !== current) { capsOff = current; noCaptions(); }
    onState?.(s);
  }, (info) => {
    flush();
    if (typeof info.currentTime === 'number') { t = info.currentTime; at = performance.now(); onTime?.(t); }
  }, async (code) => {
    // 100 removed/private, 101/150 the uploader blocks embedding or this country, 153 embedding refused
    if (![100, 101, 150, 153].includes(code)) { onState?.(0); return; }
    const next = swaps < 3 ? await trailerUnavailable(current) : null;
    swaps++;
    onUnavailable?.(current, next);
    if (!replace) return;
    if (next && !isFileTrailer(next) && f.isConnected) {
      current = next; t = 0; at = 0;
      ready = false; queued.length = 0; sent = -1;      // a new load: nothing is listening again until it says so
      f.src = ytSrc(next, { muted: isMuted, controls, start: 0 });      // same element: callers don't notice
    } else {
      onState?.(0);                                                     // nothing else to play: as if it ended
    }
  }, flush);
  return {
    el: f, file: false, key: () => current,
    play: () => cmd('playVideo'), pause: () => cmd('pauseVideo'),
    setMuted: (m) => { isMuted = m; cmd(m ? 'mute' : 'unMute'); if (!m) setVol(vol); },
    fadeTo: fader(() => vol, setVol),
    time: () => (at ? t + (performance.now() - at) / 1000 : t),
    destroy: () => { stop(); f.remove(); },
  };
}

// ---------------------------------------------------------------- theme music
class ThemeMusic {
  constructor(t, volume) {
    this.t = t;
    this.target = volume;
    this.audio = new Audio(t.url);
    this.audio.loop = true;
    this.audio.volume = 0;
    this.timer = 0;
    this.userPaused = false;
  }
  fade(to, ms, done) {
    clearInterval(this.timer);
    const from = this.audio.volume, steps = Math.max(1, Math.round(ms / 50));
    let i = 0;
    this.timer = setInterval(() => {
      i++;
      this.audio.volume = Math.min(1, Math.max(0, from + (to - from) * (i / steps)));
      if (i >= steps) { clearInterval(this.timer); done?.(); }
    }, 50);
  }
  play() {
    if (mediaOn) return;                 // a film is playing: it has the sound
    if (this.userPaused) return;
    this.audio.play().then(() => { this.fade(this.target, 2600); this.render(); })
      .catch(() => this.render());          // autoplay blocked: the chip offers a play button
  }
  pause(ms = 900) { this.fade(0, ms, () => { this.audio.pause(); this.render(); }); }
  destroy() { clearInterval(this.timer); this.audio.pause(); this.audio.removeAttribute('src'); this.audio.load(); }
  render() {
    const host = $('#d-aside');
    if (!host) return;
    let chip = $('#theme-chip');
    if (!chip) { chip = document.createElement('div'); chip.id = 'theme-chip'; chip.className = 'pill-ctl'; host.append(chip); }
    const playing = !this.audio.paused;
    mount(chip, html`<span class="eq ${playing ? '' : 'paused'}"><i></i><i></i><i></i></span>
      <span class="lbl">${this.t.title || 'Theme'}${this.t.artist ? html`<small> · ${this.t.artist}</small>` : ''}</span>
      ${safeUrl(this.t.link) ? html`<a href="${safeUrl(this.t.link)}" target="_blank" rel="noopener noreferrer" title="Preview from Apple Music">Apple Music</a>` : ''}
      <button type="button" aria-label="${playing ? 'Stop theme music' : 'Play theme music'}">${icon(playing ? 'pause' : 'play')}</button>`);
    chip.querySelector('button').onclick = () => {
      if (playing) { this.userPaused = true; this.pause(500); } else { this.userPaused = false; this.play(); }
    };
  }
}

// ---------------------------------------------------------------- background trailer
// ---- a hover preview's trailer carries on into the title's page (Netflix style)
let handoff = null;
/** Called by the preview as you click into a title: { ids, key, time, playing, muted } */
export function handTrailer(h) { handoff = { ...h, at: performance.now() }; }
function takeHandoff(item) {
  const h = handoff;
  handoff = null;
  if (!h || performance.now() - h.at > 15000) return null;
  if (!h.ids.includes(item.id)) return null;
  return { ...h, time: h.time + (h.playing ? (performance.now() - h.at) / 1000 : 0) };
}

class BackgroundTrailer {
  constructor(key, label, { sound = false, startAt = 0 } = {}) {
    this.startAt = startAt;
    this.key = key;
    this.label = label;
    this.frame = null;
    this.muted = true;
    this.wantSound = sound;
    this.shown = false;
    this.stopListen = null;
  }
  start(delay = 0) {          // loads at once; it appears the moment it is actually playing
    const token = pageToken;
    this.timer = setTimeout(() => {
      if (token !== pageToken) return;
      const host = $('#stage-trailer');
      if (!host) return;
      this.player = trailerPlayer(this.key, { muted: true, start: this.startAt, onState: (s) => {
        if (s === 1) this.reveal();
        if (s === 0) this.end();
      } });
      if (!this.player) return;            // a television: the backdrop stays a picture
      host.replaceChildren(this.player.el);
      this.frame = this.player.el;
      this.fallback = setTimeout(() => this.reveal(), 4500);   // in case the player sends no events
    }, delay);
  }
  reveal() {
    if (this.shown || !this.frame) return;
    this.shown = true;
    clearTimeout(this.fallback);
    $('#stage-trailer')?.classList.add('on');
    $('#stage')?.classList.add('trailer-on');
    // "With sound": it starts muted (always allowed) and turns the sound on once it plays. Browsers
    // only allow that after you've clicked something on the page; otherwise the sound button stays.
    this.renderPill();
    if (this.wantSound && this.muted) this.soundWait = whenSoundAllowed(() => { if (this.muted && this.shown) this.toggleSound(); });
  }
  end() {
    clearTimeout(this.fallback);
    this.shown = false;
    $('#stage-trailer')?.classList.remove('on');
    $('#stage')?.classList.remove('trailer-on');
    $('#trailer-chip')?.remove();
    setTimeout(() => { if (!this.shown) { this.player?.destroy(); this.frame = null; } }, 1700);
    if (!this.muted) theme?.play();
  }
  visible(on) {
    if (!this.frame || !this.shown || (on && mediaOn)) return;
    if (on) this.player.play(); else this.player.pause();
  }
  toggleSound() {
    this.muted = !this.muted;
    this.player?.setMuted(this.muted);
    if (!this.muted) theme?.pause(); else theme?.play();
    this.renderPill();
  }
  pause() { this.player?.pause(); }
  resume() { if (this.shown && !mediaOn) this.player?.play(); }
  renderPill() {
    const host = $('#d-aside');
    if (!host) return;
    let chip = $('#trailer-chip');
    if (!chip) { chip = document.createElement('div'); chip.id = 'trailer-chip'; chip.className = 'pill-ctl'; host.prepend(chip); }
    mount(chip, html`<span class="lbl">Trailer<small>${this.label ? ` · ${this.label}` : ''}</small></span>
      <button type="button" data-t="sound" aria-label="${this.muted ? 'Turn trailer sound on' : 'Mute trailer'}">${icon(this.muted ? 'mute' : 'volume')}</button>
      <button type="button" data-t="stop" aria-label="Stop trailer">${icon('close')}</button>`);
    chip.querySelector('[data-t=sound]').onclick = () => { this.soundWait?.(); this.toggleSound(); };
    chip.querySelector('[data-t=stop]').onclick = () => { this.pause(); this.end(); };
  }
  destroy() {
    clearTimeout(this.timer); clearTimeout(this.fallback); clearInterval(this.clock);
    this.soundWait?.();
    this.player?.destroy();
  }
}

// ---------------------------------------------------------------- full-screen trailer
export function openTrailer(key) {
  theme?.pause(400);
  trailerBg?.pause();
  const el = document.createElement('div');
  el.className = 'trailer-modal';
  el.setAttribute('role', 'dialog');
  el.innerHTML = `<button class="pbtn close" aria-label="Close trailer">${icon('close').s}</button>
    <div class="frame"></div>`;
  const player = trailerPlayer(key, { muted: false, controls: true });
  el.querySelector('.frame').append(player.el);
  document.body.append(el);
  const close = () => {
    player.destroy();
    el.remove();
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', close);
    trailerBg?.resume();
    theme?.play();
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  el.addEventListener('click', (e) => { if (e.target === el || e.target.closest('.close')) close(); });
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close);
  el.querySelector('.close').focus();
}

// ---------------------------------------------------------------- Home banner trailer
// Its own setting (prefs.home_trailer) and its own controller: nothing here touches the media-page
// trailer or theme music.
let homeTrailer = null;

class HomeTrailer {
  constructor(hero, key, label, { sound, limit = 0, itemId }) {
    this.hero = hero; this.key = key; this.label = label; this.wantSound = sound; this.limit = limit;
    // Not "in view until told otherwise": the banner may be drawn while you are scrolled half way down
    // the page, and sync() works out for itself where it is.
    this.muted = true; this.shown = false; this.inView = false; this.stopped = false;
    // More info (or the title) while the trailer plays: it carries on in the title's page from the same moment
    this.onOpen = (e) => {
      const a = e.target.closest('a[href^="#/item/"]');
      if (!a || !this.player || !this.shown || this.stopped) return;
      handTrailer({ ids: [itemId].filter(Boolean), key: this.player.key?.() || this.key, time: this.player.time(),
        playing: !!this.playing, muted: this.muted });
    };
    hero.addEventListener('click', this.onOpen, true);
  }
  start(delay = 0) {
    const token = pageToken;
    this.timer = setTimeout(() => {
      if (token !== pageToken || !this.hero.isConnected) return;
      const layer = document.createElement('div');
      layer.className = 'hero-trailer';
      this.player = trailerPlayer(this.key, { muted: true, onState: (s) => {
        if (s === 1) this.reveal();
        if (s === 0) this.end();
      } });
      if (!this.player) return;            // a television: the banner stays a picture
      layer.append(this.player.el);
      this.hero.querySelector('.art').after(layer);
      this.layer = layer;
      this.frame = this.player.el;
      this.fallback = setTimeout(() => this.reveal(), 5000);
    }, delay);
    // The trailer plays while the banner is on screen and stops once it is mostly gone (or another tab is
    // in front). sync() measures the banner itself; the observer is only what tells it to look again.
    this.io = new IntersectionObserver(() => this.sync(),
      { threshold: Array.from({ length: 21 }, (_, i) => i / 20) });
    this.io.observe(this.hero);
    this.onVis = () => this.sync();
    document.addEventListener('visibilitychange', this.onVis);
  }
  /** How much of the banner is on screen, 0 to 1 — asked of the page, not remembered from the last
   *  observation, because the banner can be redrawn in a place nothing has observed yet. */
  onScreen() {
    const h = this.hero;
    if (!h.isConnected || !h.offsetParent) return 0;
    const r = h.getBoundingClientRect();
    const full = Math.min(r.height, window.innerHeight);
    if (full <= 0) return 0;
    return Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0)) / full;
  }
  /** Full volume while the banner is in view — it fades in and out, it does not follow the scroll
   *  half way. Silent for the last seconds before the 30 seconds limit. */
  level() {
    if (this.limit && (this.played || 0) >= this.limit - FADE_END_S) return 0;
    return this.inView ? 80 : 0;
  }
  loudness(ms) { if (this.player && !this.muted && !this.stopped && this.playing) this.player.fadeTo(this.level(), ms); }
  sync() {
    if (!this.frame || !this.shown || this.stopped) return;
    this.seen = this.onScreen();
    this.inView = this.seen > 0.35;
    const play = this.inView && !document.hidden && !previewOn && !mediaOn;
    if (play) {
      this.player.setMuted(this.muted || dockPlaying());    // the player bar has the sound: the banner plays muted
      if (!this.playing) {
        this.playing = true;
        this.player.play();                   // the volume is 0 here (faded out, or set to 0 below)
      }
      this.loudness(700);                     // comes back in gently
    } else {
      // Not "else if (this.playing)": a player starts playing the moment it is created, so a banner drawn
      // while you are scrolled down the page had one running that nothing here had asked for — off screen,
      // and audible on an engine whose volume had not been set yet. Drive it to silence either way.
      const was = this.playing;
      this.playing = false;
      const p = this.player;
      // fade the sound out, then pause (straight away when muted, hidden, or never ours to fade)
      if (!was || this.muted || document.hidden || mediaOn) { p.fadeTo(0, 0); p.pause(); }   // a film starting: at once
      else p.fadeTo(0, 700).then((done) => { if (done && !this.playing && this.player === p) p.pause(); });
    }
    // 30 seconds limit (Settings): count only the time it really plays, fade the sound over the last
    // seconds, then show the picture again
    clearInterval(this.clock);
    if (play && this.limit) {
      this.clock = setInterval(() => {
        this.played = (this.played || 0) + 1;
        if (this.played === this.limit - FADE_END_S) this.loudness(FADE_END_S * 1000);
        if (this.played >= this.limit) { clearInterval(this.clock); this.end(); }
      }, 1000);
    }
    this.hero.dataset.trailer = play ? 'playing' : 'paused';
  }
  reveal() {
    if (this.shown || !this.frame || this.stopped) return;
    this.shown = true;
    clearTimeout(this.fallback);
    this.hero.classList.add('trailer-on');
    this.renderPill();
    this.sync();
    // with sound: on now if the browser allows it, otherwise at your first click or key press
    if (this.wantSound) this.soundWait = whenSoundAllowed(() => { if (this.muted && !this.stopped && this.player) this.setMuted(false); });
  }
  setMuted(m) {
    this.muted = m;
    if (!m && this.player) this.player.fadeTo(0, 0);          // the sound fades in rather than jumping on
    this.player?.setMuted(m || dockPlaying());
    if (!m) this.loudness(600);
    this.renderPill();
  }
  end() {
    clearTimeout(this.fallback); clearInterval(this.clock);
    this.soundWait?.();
    const p = this.player, layer = this.layer;
    this.stopped = true; this.playing = false;
    this.hero.classList.remove('trailer-on');
    // the sound fades out with the picture (when the trailer ends by itself there's nothing left to fade)
    if (p && !this.muted) p.fadeTo(0, 1100).then(() => p.pause());
    setTimeout(() => { p?.destroy(); if (this.player === p) this.player = null; layer?.remove(); }, 1250);
    // the picture comes back with the description, and a button to watch the trailer again
    if (!this.pill) { this.pill = document.createElement('div'); this.pill.className = 'pill-ctl hero-pill'; this.hero.append(this.pill); }
    this.pill.title = 'Play the trailer again';
    mount(this.pill, html`<button type="button" data-t="replay" aria-label="Play the trailer again">${icon('restart')}</button>`);
    this.pill.querySelector('[data-t=replay]').onclick = () => this.replay();
  }
  replay() {
    this.destroy();
    this.played = 0;
    this.pill = null; this.player = null; this.layer = null; this.frame = null;
    this.stopped = false; this.shown = false;
    this.hero.addEventListener('click', this.onOpen, true);
    this.start();
  }
  pause() { this.sync(); }               // (only while a film plays: see mediaOn)
  renderPill() {
    if (!this.pill) {
      this.pill = document.createElement('div');
      this.pill.className = 'pill-ctl hero-pill';
      this.hero.append(this.pill);
    }
    this.pill.title = `Trailer${this.label ? ` · ${this.label}` : ''}`;
    // only the sound button (no stop button on the Home banner)
    mount(this.pill, html`<button type="button" data-t="sound" aria-label="${this.muted ? 'Turn trailer sound on' : 'Mute trailer'}">${icon(this.muted ? 'mute' : 'volume')}</button>`);
    this.pill.querySelector('[data-t=sound]').onclick = () => { this.soundWait?.(); this.setMuted(!this.muted); };
  }
  /** fadeMs: let the sound fade out first (the banner moving to another title) */
  destroy(fadeMs = 0) {
    clearTimeout(this.timer); clearTimeout(this.fallback); clearInterval(this.clock);
    this.soundWait?.();
    this.hero.removeEventListener('click', this.onOpen, true);
    this.io?.disconnect();
    document.removeEventListener('visibilitychange', this.onVis);
    this.pill?.remove();
    const p = this.player, layer = this.layer;
    const gone = () => { p?.destroy(); layer?.remove(); };
    if (fadeMs && p && !this.muted && this.playing && !this.stopped) p.fadeTo(0, fadeMs).then(gone);
    else gone();
    this.playing = false;
  }
}
const FADE_END_S = 3;      // the 30 seconds limit: the sound fades over the last 3 seconds

// While a hover preview plays its trailer, that is the one playing: the Home banner trailer and a media
// page's background trailer pause (and stay paused if the banner moves to another title meanwhile),
// and theme music pauses too when the preview has sound. All of it resumes when the preview closes.
let previewOn = false, previewLoud = false;
export function previewTrailerPlaying(on, { sound = false } = {}) {
  const loud = on && sound;
  if (on !== previewOn) {
    previewOn = on;
    homeTrailer?.sync();
    if (on) trailerBg?.pause(); else trailerBg?.resume();
  }
  if (loud !== previewLoud) {
    previewLoud = loud;
    if (loud) theme?.pause(300); else theme?.play();
  }
}

/** A video is playing in the mini player: pages stay quiet (no trailers, no theme music). */
export const miniPlaying = () => document.body.classList.contains('mini-player');

export function stopHomeTrailer(fadeMs = 0) { homeTrailer?.destroy(fadeMs); homeTrailer = null; }

export async function startHomeTrailer(item) {
  const mode = state.prefs?.home_trailer || 'muted';
  const why = (r) => console.info(`[LENTA banner] ${item?.title || ''}: ${r}`);   // F12 › Console says why nothing plays
  if (!item) return;
  if (mode === 'off') return why('trailers are off in Settings › Home screen');
  if (!item.home_trailer) return why('not ticked in Edit › General (picture only)');
  const hero = document.querySelector('.hero');
  const token = pageToken;
  let data = item.trailer ? { trailer: { key: item.trailer } } : null;     // indexed: no lookup needed
  if (!data) {
    try { data = await api.get(`/api/items/${item.id}/extras?only=trailer`); } catch { return; }
  }
  if (token !== pageToken || !hero?.isConnected) return;
  if (!data.trailer) return why('no trailer found for this title');
  why(`playing trailer ${data.trailer.key}`);
  if (!backgroundTrailers()) return why('a television plays the banner as a picture, not a trailer');
  homeTrailer?.destroy();                 // the banner moved on: the previous title's trailer goes
  homeTrailer = new HomeTrailer(hero, data.trailer.key, data.trailer.name, { sound: mode === 'sound', limit: state.prefs?.home_trailer_30 === '1' ? 30 : 0, itemId: item.id });
  homeTrailer.start();
  onCleanup(() => { homeTrailer?.destroy(); homeTrailer = null; });
}

// ---------------------------------------------------------------- per page entry point
export async function startExtras(item, { onTrailer } = {}) {
  const prefs = state.prefs || {};
  const trailerMode = prefs.trailer_mode || 'off';
  const wantMusic = prefs.theme_music === '1';
  const token = pageToken;
  const quiet = miniPlaying();              // the mini player has the sound: only offer the Trailer button
  // Clicked in from a hover preview that was playing this trailer: go on from the same moment, same sound.
  const h = takeHandoff(item);
  if (h) {
    onTrailer?.({ key: h.key, name: item.extra?.trailer_name });
    trailerBg = new BackgroundTrailer(h.key, item.extra?.trailer_name, { sound: !h.muted, startAt: h.time });
    trailerBg.start();
    onCleanup(() => { trailerBg?.destroy(); trailerBg = null; });
    if (!wantMusic) return;
  } else if (trailerMode === 'off' && !wantMusic) return;
  const startTrailer = (trailer) => {
    if (h) return;
    if (!trailer || trailerMode === 'off' || token !== pageToken) return;
    onTrailer?.(trailer);
    if (quiet) return;
    if (trailerMode === 'background' || trailerMode === 'background_sound') {
      trailerBg = new BackgroundTrailer(trailer.key, trailer.name, { sound: trailerMode === 'background_sound' });
      trailerBg.start();
      onCleanup(() => { trailerBg?.destroy(); trailerBg = null; });
    }
  };
  // The trailer is indexed with the title (trailers.py): start it now, while theme music is looked up.
  const known = item.trailer || item.extra?.trailer;
  if (known) startTrailer({ key: known, name: item.extra?.trailer_name });
  if (known && !wantMusic) return;
  let data;
  try { data = await api.get(`/api/items/${item.id}/extras`); } catch { return; }
  if (token !== pageToken) return;          // the user already left this page
  if (!known) startTrailer(data.trailer);
  if (data.theme && wantMusic && !quiet && !musicPlaying() && !(trailerBg && trailerBg.wantSound)) {
    theme = new ThemeMusic(data.theme, Number(prefs.theme_music_volume || 0.35));
    theme.play();
    onCleanup(() => { theme?.destroy(); theme = null; });
  }
}

// Pressing Play: the film takes over the sound and the screen until the player closes (also as a mini
// player). Nothing behind it may start again meanwhile: the Home banner looks "in view" again when the page
// under the player scrolls back, a hover preview closing asks the others to resume, a click allows sound...
let mediaOn = false;
window.addEventListener('lenta:playback', () => {
  mediaOn = true;
  theme?.pause(300); trailerBg?.pause(); homeTrailer?.pause();
});
window.addEventListener('lenta:playback-ended', () => {
  mediaOn = false;
  homeTrailer?.sync();                      // the Home banner carries on if it's in view
});
// The video went into the player bar: the page may play its trailers again, muted while the bar plays.
window.addEventListener('lenta:player-mini', () => {
  mediaOn = false;
  homeTrailer?.sync();
});
// Something in the player bar started, paused or stopped: the banner's sound follows (muted while it plays).
window.addEventListener('lenta:dock', () => { homeTrailer?.sync(); });

