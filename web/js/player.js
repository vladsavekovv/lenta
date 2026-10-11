// Video player: direct play or HLS (hls.js), custom controls, subtitles, audio tracks, quality, up next.
import { api } from './api.js';
import { pauseMusic } from './music.js';
import { dockMount, dockRefresh, dockRegister, dockUnmount, dockUpdate } from './dock.js';
import { state } from './app.js';
import { langName, subtitleSearch } from './subtitles.js';
import { $, channels, fmtTime, html, icon, img, isTouch, mount, popMenu, toast } from './ui.js';

const sameLang = (a, b) => !!a && !!b && (a === b || a.split('-')[0] === b.split('-')[0]);

let P = null;
const QUALITIES = [['original', 'Original'], ['1080p', '1080p · 10 Mbps'], ['720p', '720p · 4 Mbps'], ['480p', '480p · 1.5 Mbps']];
const store = {
  get: (k, d) => { try { return localStorage.getItem(`lenta.${k}`) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(`lenta.${k}`, v); } catch { /* private mode */ } },
};

export const playerOpen = () => !!P;

function capabilities(forceHls) {
  const v = document.createElement('video');
  const can = (t) => v.canPlayType(t) !== '';
  const caps = { video: [], audio: [], containers: [], hdr: false };
  if (can('video/mp4; codecs="avc1.640028"')) caps.video.push('h264');
  if (can('video/mp4; codecs="hvc1.2.4.L153.B0"') || can('video/mp4; codecs="hev1.1.6.L93.B0"')) caps.video.push('hevc');
  if (can('video/webm; codecs="vp9"')) caps.video.push('vp9');
  if (can('video/mp4; codecs="av01.0.08M.08"')) caps.video.push('av1');
  if (can('audio/mp4; codecs="mp4a.40.2"')) caps.audio.push('aac');
  if (can('audio/mpeg')) caps.audio.push('mp3');
  if (can('audio/webm; codecs="opus"')) caps.audio.push('opus');
  if (can('audio/webm; codecs="vorbis"')) caps.audio.push('vorbis');
  if (can('audio/flac')) caps.audio.push('flac');
  if (can('audio/mp4; codecs="ac-3"')) caps.audio.push('ac3');
  if (can('audio/mp4; codecs="ec-3"')) caps.audio.push('eac3');
  if (!forceHls) {
    caps.containers.push('mp4');
    if (can('video/webm')) caps.containers.push('webm');
  }
  caps.hdr = window.matchMedia?.('(dynamic-range: high)').matches && caps.video.includes('hevc');
  return caps;
}

// ---------------------------------------------------------------- open / close
export async function openPlayer(itemId, opts = {}) {
  if (P) teardown('stopped', { quiet: true });      // another film replaces this one: still playing
  pauseMusic();
  window.dispatchEvent(new CustomEvent('lenta:playback'));
  const root = $('#player-root');
  const returnTo = opts.returnTo;
  P = {
    itemId, root, info: null, hls: null, offset: 0, sessionId: null, cues: [], subtitleKey: null, burnedKey: null, subDelay: 0,
    audioIndex: null, quality: store.get('quality', 'original'), fileId: opts.file ? Number(opts.file) : null,
    playId: Math.random().toString(36).slice(2), lastReport: 0, idleTimer: null, panel: null, stats: false,
    upNextDismissed: false, forceHls: false, returnTo, raf: 0, seeking: false, loadSeq: 0,
  };
  const touch = isTouch();
  mount(root, html`<div class="player ${touch ? 'touch' : ''}" id="player">
    <video id="pv" playsinline preload="auto"></video>
    <div class="subtitle-layer" id="psub"></div>
    <div class="seek-flash left" id="pflash-l"></div><div class="seek-flash right" id="pflash-r"></div>
    <div class="chrome">
      ${touch ? html`<div class="transport" id="ptransport">
        <button class="pbtn" data-a="back10" aria-label="Back 10 seconds">${icon('replay10')}</button>
        <button class="pbtn huge" data-a="toggle" id="pplay2" aria-label="Play">${icon('play')}</button>
        <button class="pbtn" data-a="fwd10" aria-label="Forward 10 seconds">${icon('fwd10')}</button></div>` : ''}
      <div class="top">
        <button class="pbtn" data-a="minimize" aria-label="Minimize player" title="Minimize: keep watching while you browse">${icon('chevron')}</button>
        <span class="grow"></span>
        <button class="pbtn hide-sm" data-a="stats" aria-label="Playback info" title="Playback info">${icon('stats')}</button>
        <button class="pbtn" data-a="fullscreen" id="pfs" aria-label="Full screen" title="Full screen">${icon('fullscreen')}</button></div>
      <div class="center" id="pc"><div class="spinner"></div></div>
      <div class="bottom" id="pdock"></div>
    </div>
    <div id="ppanel-root"></div><div id="pstats-root"></div><div id="pupnext-root"></div>
    <button type="button" class="skip-intro" id="pskip" hidden></button>
  </div>`);
  const video = $('#pv');
  P.video = video;
  // iPhone Safari can't put a page element in full screen; the player already fills the screen there.
  if (!document.fullscreenEnabled && !document.webkitFullscreenEnabled) $('#pfs').hidden = true;
  video.volume = Number(store.get('volume', '1'));
  video.muted = store.get('muted', '0') === '1';
  wire();
  P.dock = dockMount($('#pdock'), VIDEO);
  applySubStyle();      // the controls: the same bar as the one at the bottom of the screen
  document.documentElement.classList.add('player-open');      // the page behind can't scroll (no scrollbar)
  if (opts.mini) minimizePlayer({ navigate: false });

  let detail;
  try {
    detail = await api.get(`/api/items/${itemId}`);
  } catch (err) { return showMessage("This title can't be played", err.message); }
  if (!P || P.itemId !== itemId) return;
  P.detail = detail;
  const file = (detail.files || []).find(f => f.id === P.fileId) || detail.files?.[0];
  if (!file) return showMessage('No playable file', 'This title has no video file in the library.');
  P.fileId = file.id;
  loadTrick(file.id);
  if (detail.kind === 'episode') loadSegments(file.id);
  const choice = chooseTracks(file);
  if (choice.audio && choice.audio.index !== file.audio[0]?.index) P.audioIndex = choice.audio.index;
  let start = 0;
  if (opts.start != null) start = Number(opts.start) || 0;
  else if (!opts.restart && detail.progress && !detail.progress.completed) start = detail.progress.position || 0;
  await load(start);
  if (choice.subtitle) selectSubtitle(choice.subtitle.key);
  else if (choice.download) autoDownload(file, choice.download);
}

// Tracks from the user's Settings: preferred audio language, subtitle mode and subtitle language.
function chooseTracks(file) {
  const prefs = state.prefs || {};
  const audio = file.audio || [], subs = file.subtitles || [];
  const auto = prefs.auto_tracks !== '0';
  const chosenAudio = (auto && prefs.audio_lang && audio.find(a => sameLang(a.lang, prefs.audio_lang))) || audio[0] || null;
  const mode = auto ? prefs.subtitle_mode || 'forced' : 'forced';
  const subLang = prefs.subtitle_lang || chosenAudio?.lang || '';
  const forced = subs.find(s => s.forced && sameLang(s.lang, chosenAudio?.lang || subLang)) || null;
  let full = mode === 'always' || (mode === 'foreign' && subLang && chosenAudio && !sameLang(chosenAudio.lang, subLang));
  if (mode === 'off') return { audio: chosenAudio, subtitle: null };
  if (!full) return { audio: chosenAudio, subtitle: forced };
  const inLang = subs.filter(s => !s.forced && sameLang(s.lang, subLang));
  const best = inLang.find(s => s.text && !s.hearing_impaired) || inLang.find(s => s.text) || inLang[0];
  if (best) return { audio: chosenAudio, subtitle: best };
  const canFetch = auto && prefs.subtitle_autodownload === '1' && state.features?.subtitles_online && subLang;
  return { audio: chosenAudio, subtitle: forced, download: canFetch ? subLang : null };
}

async function autoDownload(file, language) {
  const itemId = P.itemId;
  try {
    const r = await api.post('/api/subtitles/auto', { item_id: itemId, file_id: file.id, language });
    if (!P || P.itemId !== itemId || !r.subtitle) {
      if (r.status === 'error' && r.message) toast(`Subtitles: ${r.message}`, 'error');
      return;
    }
    file.subtitles.push(r.subtitle);
    selectSubtitle(r.subtitle.key);
    toast(`${langName(language)} subtitles downloaded${r.hash_match ? ' (exact match for this file)' : ''}`);
  } catch { /* playback goes on without them */ }
}

// ---- mini player (like Plex): the video goes on in the bar at the bottom while you browse ----------------------------
export const playerMini = () => !!P?.mini;
export const playerItem = () => P?.itemId ?? null;

export function minimizePlayer({ navigate = true } = {}) {
  if (!P || P.mini) return;
  closePanel();
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  P.mini = true;
  const el = P.root.querySelector('.player');
  el.classList.add('mini');
  el.classList.remove('idle');
  clearTimeout(P.idleTimer);
  document.body.classList.add('mini-player');
  document.documentElement.classList.remove('player-open');   // browsing again: the page scrolls
  dockRefresh();
  window.dispatchEvent(new CustomEvent('lenta:player-mini'));  // pages may play trailers again (muted)
  if (navigate) {                              // back to the page you came from (or Home)
    if (P.returnTo === 'back') history.back();
    else location.hash = P.returnTo || '#/';
  }
}

/** The title in the player: open its page (a show's page at this episode) and keep watching in the mini player. */
function openDetails() {
  if (!P) return;
  const id = P.itemId;
  minimizePlayer({ navigate: false });
  location.hash = `#/item/${id}`;          // an episode's page forwards to its show, opened at that episode
}

export function expandPlayer({ navigate = true } = {}) {
  if (!P || !P.mini) return;
  P.mini = false;
  P.root.querySelector('.player').classList.remove('mini');
  document.body.classList.remove('mini-player');
  document.documentElement.classList.add('player-open');
  dockRefresh();
  window.dispatchEvent(new CustomEvent('lenta:playback'));     // full screen again: everything else is quiet
  P.returnTo = 'back';
  if (navigate && location.hash !== `#/play/${P.itemId}`) location.hash = `#/play/${P.itemId}`;   // the router sees it is open
  poke();
}

// The minimised video in the bar at the bottom (dock.js), the same bar as music and audiobooks.
const VIDEO = {
  id: 'video',
  visible: () => !!P?.mini,
  info: () => ({ title: P.info?.title || P.detail?.title || '', sub: P.info?.subtitle || '', href: `#/item/${P.itemId}`,
    video: true, artLabel: 'Back to full screen' }),
  titleClick: (full) => { if (full) openDetails(); else location.hash = `#/item/${P.itemId}`; },
  state() {
    const v = P.video, pos = position();
    let buf = pos;
    try {
      const b = v.buffered;
      for (let i = 0; i < b.length; i++) if (b.start(i) <= v.currentTime + 1 && b.end(i) > v.currentTime) buf = b.end(i) + (P.mode === 'direct' ? 0 : P.offset);
    } catch { /* nothing buffered */ }
    return { pos, dur: duration(), buf, paused: v.paused, volume: v.volume, muted: v.muted };
  },
  caps: () => ({ back: 10, fwd: 30, prev: true, prevLabel: 'Back to the start', next: !!P.info?.next_id, nextLabel: 'Next episode',
    settings: true, settingsLabel: 'Playback settings', list: P.detail?.kind === 'episode', listLabel: 'Play queue',
    settingsOn: P.panel === 'settings', listOn: P.panel === 'queue' }),
  list(anchor, full) { if (!full) expandPlayer(); if (P.panel !== 'queue') openPanel('queue'); else closePanel(); },
  toggle: () => (P.video.paused ? P.video.play().catch(() => {}) : P.video.pause()),
  seek: (t) => seekTo(t),
  skip: (s) => seekTo(position() + s),
  prev: () => seekTo(0),
  next: () => playNext(),
  stop: (full) => (full ? closePlayer() : closePlayer({ navigate: false })),
  artClick: () => expandPlayer(),
  more(anchor, full) {
    const big = () => { if (!full) expandPlayer(); };
    popMenu(anchor, [{ heading: P.info?.title || '' },
      full ? { label: 'Minimize player', action: () => minimizePlayer() } : { label: 'Full screen player', action: () => expandPlayer() },
      { label: "Go to the title's page", action: () => openDetails() },
      { label: 'Playback settings…', action: () => { big(); openPanel('settings'); } },
      ...(P.detail?.kind === 'episode' ? [{ label: 'Play queue…', action: () => { big(); openPanel('queue'); } }] : []),
      ...(full ? [{ label: P.stats ? 'Hide playback info' : 'Playback info', action: () => act('stats') }] : []),
      { sep: true }, { label: 'Stop', action: () => (full ? closePlayer() : closePlayer({ navigate: false })) }]);
  },
  settings(anchor, full) { if (!full) expandPlayer(); if (P.panel !== 'settings') openPanel('settings'); else closePanel(); },
  thumb: (t, box) => showTrick(t, box),
  setVolume(v) {
    P.video.volume = v; P.video.muted = v === 0;
    store.set('volume', v); store.set('muted', P.video.muted ? '1' : '0');
  },
  setMuted(m) { P.video.muted = m; store.set('muted', m ? '1' : '0'); },
};
dockRegister(VIDEO, { priority: 2 });

// music or an audiobook starts: a minimised video makes way (one thing plays at a time)
window.addEventListener('lenta:music-start', () => { if (P?.mini) closePlayer({ navigate: false }); });

export function closePlayer({ navigate = true } = {}) {
  if (!P) return;
  const returnTo = P.returnTo;
  teardown('stopped');
  if (navigate) {
    if (returnTo === 'back') history.back();
    else location.hash = returnTo || '#/';
  }
}

// ---- Skip intro (intros found by the server: intros.py) ----------------------------------------------
async function loadSegments(fileId, tries = 0) {
  if (!P) return;
  P.intro = null;
  clearTimeout(P.segTimer);
  if ((state.prefs?.intro_skip || 'button') === 'off') return;
  let r = null;
  try { r = await api.get(`/api/files/${fileId}/segments`); } catch { return; }
  if (!P || P.fileId !== fileId) return;
  if (r.intro) P.intro = { ...r.intro, skipped: false, shown: false };
  else if (r.pending && tries < 10) P.segTimer = setTimeout(() => loadSegments(fileId, tries + 1), 60000);  // being analysed now
}

function checkIntro() {
  const b = $('#pskip');
  const it = P?.intro;
  if (!b) return;
  if (!it || P.video.paused && !P.video.currentTime) { b.hidden = true; return; }
  const t = position();
  const inside = t >= it.start - 0.5 && t < it.end - 1.5;
  const mode = state.prefs?.intro_skip || 'button';
  if (inside && mode === 'auto' && !it.skipped && !P.dragging) {
    it.skipped = true;                                   // once per intro: "Watch intro" brings it back
    it.backTo = t;
    seekTo(it.end);
    showIntroButton(b, 'back');
    clearTimeout(it.hideTimer);
    it.hideTimer = setTimeout(() => { if (b.dataset.mode === 'back') b.hidden = true; }, 6000);
    return;
  }
  if (b.dataset.mode === 'back' && !b.hidden) return;
  if (inside && mode !== 'off') showIntroButton(b, 'skip');
  else b.hidden = true;
}

function showIntroButton(b, mode) {
  if (b.dataset.mode !== mode || b.hidden) {
    b.dataset.mode = mode;
    b.textContent = mode === 'back' ? 'Watch intro' : 'Skip intro';
    b.hidden = false;
  }
  if (!b.onclick) b.onclick = (e) => {
    e.stopPropagation();
    const it = P?.intro;
    if (!it) return;
    if (b.dataset.mode === 'back') { b.hidden = true; b.dataset.mode = ''; seekTo(Math.max(0, it.backTo ?? it.start)); }
    else { it.skipped = true; b.hidden = true; seekTo(it.end); }
  };
}

// ---- seek-bar preview pictures (made by the server: trickplay.py) ----------------------------------
async function loadTrick(fileId, tries = 0) {
  if (!P) return;
  P.trick = null;
  clearTimeout(P.trickTimer);
  clearTimeout(P.segTimer);
  let t = null;
  try { t = await api.get(`/api/files/${fileId}/trickplay`); } catch { return; }
  if (!P || P.fileId !== fileId) return;
  if (t.ready) { P.trick = { ...t, fileId, loaded: new Set() }; return; }
  // not made yet: the server has just moved this video to the front of its queue; look again shortly
  if (tries < 20) P.trickTimer = setTimeout(() => loadTrick(fileId, tries + 1), 30000);
}

function showTrick(sec, box = $('#pthumb')) {
  const t = P?.trick;
  if (!box) return;
  if (!t) { box.hidden = true; return; }
  const per = t.cols * t.rows;
  const i = Math.max(0, Math.min(t.count - 1, Math.floor(sec / t.interval)));
  const sheet = Math.floor(i / per), n = i % per;
  const w = box.classList.contains('dk-thumb') ? (box.closest('.dock-full') ? 240 : 192) : Math.min(240, Math.round(window.innerWidth * 0.36)), k = w / t.width, h = Math.round(t.height * k);
  const url = `/api/files/${t.fileId}/trickplay/${sheet}.jpg`;
  if (!t.loaded.has(sheet)) {                      // fetch the sheet (and the next one) once
    t.loaded.add(sheet);
    new Image().src = url;
    if (sheet + 1 < t.sheets && !t.loaded.has(sheet + 1)) { t.loaded.add(sheet + 1); new Image().src = `/api/files/${t.fileId}/trickplay/${sheet + 1}.jpg`; }
  }
  box.hidden = false;
  box.style.width = `${w}px`;
  box.style.height = `${h}px`;
  box.style.backgroundImage = `url("${url}")`;
  box.style.backgroundSize = `${t.cols * w}px ${t.rows * h}px`;
  box.style.backgroundPosition = `-${(n % t.cols) * w}px -${Math.floor(n / t.cols) * h}px`;
}

// Android app: keep the screen on while a video plays
function keepAwake(on) { try { window.LentaApp?.keepScreenOn(on); } catch { /* not in the app */ } }

function teardown(reason, { quiet = false } = {}) {
  if (!P) return;
  dockUnmount(P.dock);
  keepAwake(false);
  report(reason);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  cancelAnimationFrame(P.raf);
  clearTimeout(P.idleTimer);
  clearInterval(P.statsTimer);
  clearTimeout(P.trickTimer);
  if (P.hls) P.hls.destroy();
  if (P.sessionId) fetch(`/api/hls/${P.sessionId}`, { method: 'DELETE', credentials: 'same-origin', keepalive: true }).catch(() => {});
  P.video.removeAttribute('src');
  P.video.load();
  document.removeEventListener('keydown', onKey, true);
  document.removeEventListener('fullscreenchange', onFs);
  P.root.innerHTML = '';
  document.body.classList.remove('mini-player');
  document.documentElement.classList.remove('player-open');
  P = null;
  dockRefresh();
  if (!quiet) window.dispatchEvent(new CustomEvent('lenta:playback-ended'));
}

// ---------------------------------------------------------------- loading streams
async function load(position, { keepPaused = false } = {}) {
  const seq = ++P.loadSeq;
  showSpinner(true);
  let info;
  try {
    info = await api.post('/api/playback', {
      item_id: P.itemId, file_id: P.fileId, quality: P.quality, start: position,
      audio_index: P.audioIndex, subtitle_key: P.burnedKey, caps: capabilities(P.forceHls),
      replace_session: P.sessionId,
    });
  } catch (err) {
    return showMessage("Playback didn't start", err.message, true);
  }
  if (!P || seq !== P.loadSeq) return;
  P.info = info;
  P.sessionId = info.session_id;
  P.offset = info.offset || 0;
  P.mode = info.mode;
  dockUpdate();
  const video = P.video;
  if (P.hls) { P.hls.destroy(); P.hls = null; }
  if (info.mode === 'direct') {
    video.src = info.url;
    video.addEventListener('loadedmetadata', () => { if (position > 0) video.currentTime = position; }, { once: true });
  } else if (window.Hls && window.Hls.isSupported()) {
    // The stream begins on the keyframe before the wanted position (info.offset); start playing
    // info.lead seconds into it so playback, the seek bar and subtitles all match the film.
    const hls = new window.Hls({
      startPosition: info.lead || 0,
      maxBufferLength: 40, maxMaxBufferLength: 120, backBufferLength: 90,
      manifestLoadPolicy: policy(60000, 3), playlistLoadPolicy: policy(30000, 4), fragLoadPolicy: policy(60000, 8),
      xhrSetup: (xhr) => { xhr.withCredentials = true; },
    });
    P.hls = hls;
    // The stream carries the film's own timestamps: read where it really starts and correct the clock
    // used by the seek bar and subtitles (the server's figure is the keyframe it expects FFmpeg to land on).
    hls.on(window.Hls.Events.INIT_PTS_FOUND, (_, data) => {
      if (!P || P.hls !== hls || data.id !== 'main' || !data.timescale) return;
      const actual = data.initPTS / data.timescale - (info.origin || 0);
      if (!Number.isFinite(actual) || Math.abs(actual - P.offset) > 60) return;   // implausible: keep the estimate
      const wanted = P.offset + (info.lead || 0);
      if (Math.abs(actual - P.offset) > 0.04) {
        P.offset = actual;
        lastSubText = null;
        // Still at the start of this stream: move to the exact moment that was asked for.
        if (video.currentTime < (info.lead || 0) + 2) video.currentTime = Math.max(0, wanted - actual);
      }
    });
    hls.on(window.Hls.Events.ERROR, (_, data) => {
      if (!data.fatal || !P) return;
      if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && data.details !== 'bufferAddCodecError' && (P.recoveries = (P.recoveries || 0) + 1) <= 2) {
        hls.recoverMediaError();
        return;
      }
      hls.stopLoad();
      if (data.details === 'bufferAddCodecError' || data.details === 'manifestIncompatibleCodecsError') {
        showMessage("This browser can't decode the stream", 'LENTA streams H.264 video with AAC audio. Use a current Chrome, Edge, Firefox or Safari.');
        return;
      }
      showMessage('The stream stopped', 'The server stopped sending video. This usually means FFmpeg could not convert this file — the server log has the details.', true);
    });
    hls.loadSource(info.url);
    hls.attachMedia(video);
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = info.url;  // Safari plays HLS natively
    if (info.lead > 0) video.addEventListener('loadedmetadata', () => { video.currentTime = info.lead; }, { once: true });
  } else {
    return showMessage('This browser cannot play streams', 'Use a current version of Chrome, Edge, Firefox or Safari.');
  }
  if (P.speed) { video.defaultPlaybackRate = P.speed; video.playbackRate = P.speed; }
  if (!keepPaused) video.play().catch(() => { /* autoplay blocked: user presses play */ });
  if (P.stats) renderStats();
}

function policy(ttfb, retries) {
  return { default: { maxTimeToFirstByteMs: ttfb, maxLoadTimeMs: ttfb * 2,
    timeoutRetry: { maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0 },
    errorRetry: { maxNumRetry: retries, retryDelayMs: 1000, maxRetryDelayMs: 8000 } } };
}

const position = () => (P.mode === 'direct' ? 0 : P.offset) + (P.video.currentTime || 0);
const duration = () => P.info?.duration || P.video.duration || 0;

function seekTo(t) {
  if (!P?.info) return;
  t = Math.max(0, Math.min(t, duration() - 1));
  if (P.mode === 'direct') { P.video.currentTime = t; return; }
  const rel = t - P.offset;
  const s = P.video.seekable;
  const end = s.length ? s.end(s.length - 1) : 0;
  if (rel >= 0 && rel <= end - 2) P.video.currentTime = rel;
  else load(t, { keepPaused: P.video.paused });   // outside what the server has made: start a new stream there
}

// ---------------------------------------------------------------- UI state
function showSpinner(on) {
  const c = $('#pc');
  if (!c) return;
  if (on) mount(c, html`<div class="spinner"></div>`);
  else if (c.querySelector('.spinner')) c.innerHTML = '';
}

function showMessage(title, text, retry = false) {
  if (!P) return;
  P.root.querySelector('.player')?.classList.remove('idle');
  mount($('#pc'), html`<div class="message"><h3>${title}</h3><p>${text}</p>
    <div class="actions" style="justify-content:center">${retry ? html`<button class="btn primary" data-a="retry">Try again</button>` : ''}
    <button class="btn" data-a="close">Back</button></div></div>`);
}

function updateTime() {
  if (!P?.info) return;
  const pos = position(), dur = duration();
  renderSubs(pos);
  upNext(pos, dur);
  if (Date.now() - P.lastReport > 10000 && !P.video.paused) report('playing');
}

function loop() {
  if (!P) return;
  updateTime();
  checkIntro();
  P.raf = requestAnimationFrame(loop);
}

function report(stateName) {
  if (!P?.info) return;
  P.lastReport = Date.now();
  const body = JSON.stringify({ item_id: P.itemId, position: position(), duration: duration(), state: stateName,
    play_id: P.playId, mode: P.mode, quality: P.quality });
  fetch('/api/progress', { method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: { 'Content-Type': 'application/json' }, body }).catch(() => {});
}

function poke() {
  const el = P?.root.querySelector('.player');
  if (!el || P.mini) return;
  el.classList.remove('idle');
  clearTimeout(P.idleTimer);
  P.idleTimer = setTimeout(() => {
    if (P && !P.video.paused && !P.panel && !P.dragging && !el.querySelector('.top:hover, .bottom:hover') && !document.querySelector('.menu')) el.classList.add('idle');
  }, 3000);
}

// ---------------------------------------------------------------- subtitles
function parseVtt(text) {
  const cues = [];
  const toSec = (s) => {
    const p = s.trim().replace(',', '.').split(':').map(Number);
    return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1];
  };
  for (const block of text.replace(/\r/g, '').split(/\n\n+/)) {
    const lines = block.split('\n');
    const i = lines.findIndex(l => l.includes('-->'));
    if (i < 0) continue;
    const [a, b] = lines[i].split('-->');
    const body = lines.slice(i + 1).join('\n').replace(/<[^>]+>/g, '').replace(/\{\\[^}]*\}/g, '').trim();
    const start = toSec(a), end = toSec(b.trim().split(/\s+/)[0]);
    if (body && Number.isFinite(start) && Number.isFinite(end)) cues.push({ start, end, text: body });
  }
  return cues.sort((x, y) => x.start - y.start);
}

let lastSubText = '';
function renderSubs(t) {
  const layer = $('#psub');
  if (!layer) return;
  let text = '';
  t -= P.subDelay;   // positive delay shows subtitles later
  if (P.cues.length) text = P.cues.filter(c => c.start <= t && c.end > t).map(c => c.text).join('\n');
  if (text === lastSubText) return;
  lastSubText = text;
  layer.innerHTML = text ? `<span>${text.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</span>` : '';
}

async function selectSubtitle(key) {
  const file = currentFile();
  const sub = file?.subtitles.find(s => s.key === key);
  P.cues = [];
  P.subtitleKey = null;
  lastSubText = null;
  if (!sub) {
    if (P.burnedKey) { P.burnedKey = null; await load(position()); }
    return;
  }
  if (sub.image) {   // picture subtitles need the server to draw them into the video
    P.burnedKey = key;
    P.subtitleKey = key;
    await load(position());
    return;
  }
  if (P.burnedKey) { P.burnedKey = null; await load(position()); }
  P.subtitleKey = key;
  P.subDelay = Number(store.get(delayKey(file.id, key), '0')) || 0;
  try {
    const res = await fetch(`/api/subs/${file.id}/${encodeURIComponent(key)}.vtt`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error((await res.json()).detail);
    if (P && P.subtitleKey === key) P.cues = parseVtt(await res.text());
  } catch (err) { toast(`Subtitles unavailable: ${err.message}`, 'error'); }
}

// Manual subtitle timing, remembered per file and subtitle track (for subtitles made for another cut
// or frame rate). Positive values show subtitles later, negative earlier.
const delayKey = (fileId, key) => `subDelay.${fileId}.${key}`;
function setSubDelay(value) {
  if (!P?.subtitleKey || P.burnedKey) return;
  P.subDelay = Math.round(Math.max(-600, Math.min(600, value)) * 100) / 100;
  store.set(delayKey(P.fileId, P.subtitleKey), String(P.subDelay));
  lastSubText = null;
  const label = $('#psubdelay');
  if (label) label.textContent = fmtDelay(P.subDelay);
  toast(P.subDelay ? `Subtitles ${P.subDelay > 0 ? 'later' : 'earlier'} by ${Math.abs(P.subDelay).toFixed(2)} s` : 'Subtitle timing reset');
}
const fmtDelay = (d) => `${d > 0 ? '+' : d < 0 ? '−' : ''}${Math.abs(d).toFixed(2)} s`;

const currentFile = () => (P.detail?.files || []).find(f => f.id === P.fileId);

// ---------------------------------------------------------------- panels
function closePanel() {
  P.panel = null;
  $('#ppanel-root').innerHTML = '';
  dockUpdate();
}

// ---- Playback settings (Plex style): one panel, a label and a value per line; values open a menu
const SPEEDS = [[0.5, '0.5×'], [0.75, '0.75×'], [1, 'Normal'], [1.25, '1.25×'], [1.5, '1.5×'], [1.75, '1.75×'], [2, '2×']];
const SUB_SIZES = [['small', 'Small', 0.75], ['normal', 'Normal', 1], ['large', 'Large', 1.3], ['huge', 'Huge', 1.65]];
const SUB_COLORS = [['white', 'White', '#ffffff'], ['yellow', 'Yellow', '#ffe14d'], ['cyan', 'Cyan', '#7ee8ff'], ['green', 'Green', '#9dff8a']];
const SUB_POS = [['bottom', 'Bottom'], ['top', 'Top']];
const autoplayOn = () => store.get('autoplay', '1') === '1';

function applySubStyle() {
  const layer = $('#psub');
  if (!layer) return;
  const size = SUB_SIZES.find(x => x[0] === store.get('subSize', 'normal')) || SUB_SIZES[1];
  const color = SUB_COLORS.find(x => x[0] === store.get('subColor', 'white')) || SUB_COLORS[0];
  layer.style.setProperty('--sub-scale', size[2]);
  layer.style.setProperty('--sub-color', color[2]);
  layer.classList.toggle('top', store.get('subPos', 'bottom') === 'top');
}

function qualityLabel(v) {
  const f = currentFile();
  if (v === 'original') {
    const bits = [f?.bitrate ? `${(f.bitrate / 1e6).toFixed(1)} Mbps` : '', f?.resolution ? `${f.resolution}${/^(720|1080|1440)p|4K$/.test(f.resolution) ? ' HD' : ''}` : ''].filter(Boolean);
    return `Original${bits.length ? ` (${bits.join(', ')})` : ''}`;
  }
  return (QUALITIES.find(q => q[0] === v) || [v, v])[1];
}
const audioLabel = (a) => (a ? `${langName(a.lang || a.language)} (${(a.codec || '').toUpperCase()} ${channels(a.channels)})${a.title ? ` · ${a.title}` : ''}` : 'None');
const subLabel = (s) => (s ? `${langName(s.lang || s.language)} (${s.image ? 'picture' : (s.codec || (s.external ? 'file' : 'text')).toUpperCase()})${s.forced ? ' forced' : ''}${s.downloaded ? ' · online' : ''}` : 'None');

function settingsPanel(root) {
  const file = currentFile();
  const audio = file?.audio || [];
  const curAudio = audio.find(a => a.index === (P.info?.audio_index ?? audio[0]?.index)) || audio[0];
  const sub = (file?.subtitles || []).find(s => s.key === P.subtitleKey);
  const textSub = sub && !P.burnedKey;
  const dd = (key, label, disabled = false) => html`<button type="button" class="pset-dd" data-ps="${key}" ${disabled ? 'disabled' : ''}>${label}<span class="caret">▾</span></button>`;
  const speed = (SPEEDS.find(x => x[0] === (P.video.playbackRate || 1)) || [1, `${P.video.playbackRate}×`])[1];
  const rows = [
    ['Quality', dd('quality', qualityLabel(P.quality))],
    ['Playback speed', dd('speed', speed)],
    ['Audio stream', audio.length > 1 ? dd('audio', audioLabel(curAudio)) : html`<span class="pset-static">${audioLabel(curAudio)}</span>`],
    ['Subtitles', dd('subs', subLabel(sub))],
    ['Subtitle color', dd('subColor', (SUB_COLORS.find(x => x[0] === store.get('subColor', 'white')) || SUB_COLORS[0])[1], !textSub), !textSub],
    ['Subtitle position', dd('subPos', (SUB_POS.find(x => x[0] === store.get('subPos', 'bottom')) || SUB_POS[0])[1], !textSub), !textSub],
    ['Subtitle size', dd('subSize', (SUB_SIZES.find(x => x[0] === store.get('subSize', 'normal')) || SUB_SIZES[1])[1], !textSub), !textSub],
    ['Subtitles offset', html`<span class="pset-offset"><button type="button" data-delay="-0.25" ${textSub ? '' : 'disabled'} title="Earlier (G)">−</button>
      <b id="psubdelay">${fmtDelay(P.subDelay)}</b><button type="button" data-delay="0.25" ${textSub ? '' : 'disabled'} title="Later (H)">+</button>
      <button type="button" data-delay="reset" ${textSub ? '' : 'disabled'} title="Reset">⟲</button></span>`, !textSub],
    ['Auto play', html`<input type="checkbox" class="pset-check" id="pautoplay" ${autoplayOn() ? 'checked' : ''} aria-label="Play the next episode by itself">`],
  ];
  mount(root, html`<div class="ppanel pset" role="dialog" aria-label="Playback settings">
    <h3>Playback settings</h3>
    <div class="pset-grid">${rows.map(([l, v, off]) => html`<div class="pset-l ${off ? 'off' : ''}">${l}</div><div class="pset-v">${v}</div>`)}</div>
  </div>`);
  root.querySelector('#pautoplay').addEventListener('change', (e) => store.set('autoplay', e.target.checked ? '1' : '0'));
}

function settingsMenu(kind, anchor) {
  const file = currentFile();
  const redraw = () => { if (P?.panel === 'settings') settingsPanel($('#ppanel-root')); };
  const tick = (on, label) => `${label}${on ? '  ✓' : ''}`;
  let items = [];
  if (kind === 'quality') {
    items = QUALITIES.map(([v]) => ({ label: tick(v === P.quality, qualityLabel(v)), action: () => {
      P.quality = v; store.set('quality', v); load(position(), { keepPaused: P.video.paused }).then(redraw);
    } }));
  } else if (kind === 'speed') {
    items = SPEEDS.map(([v, l]) => ({ label: tick(v === (P.video.playbackRate || 1), l), action: () => {
      P.speed = v; P.video.playbackRate = v; P.video.defaultPlaybackRate = v; redraw();
    } }));
  } else if (kind === 'audio') {
    const cur = P.info?.audio_index ?? file?.audio[0]?.index;
    items = (file?.audio || []).map(a => ({ label: tick(a.index === cur, audioLabel(a)), action: () => {
      P.audioIndex = a.index === file.audio[0]?.index ? null : a.index;
      load(position(), { keepPaused: P.video.paused }).then(redraw);
    } }));
  } else if (kind === 'subs') {
    items = [{ label: tick(!P.subtitleKey, 'None'), action: () => selectSubtitle(null).then(redraw) },
      ...(file?.subtitles || []).map(s => ({ label: tick(s.key === P.subtitleKey, `${subLabel(s)}${s.title && !s.downloaded ? ` · ${s.title.slice(0, 40)}` : ''}`),
        action: () => selectSubtitle(s.key).then(redraw) })),
      ...(state.features?.subtitles_online ? [{ sep: true }, { label: 'Search online…', action: () => { P.panel = null; openPanel('subsearch'); } }] : [])];
  } else {
    const list = { subColor: SUB_COLORS, subPos: SUB_POS, subSize: SUB_SIZES }[kind];
    const cur = store.get(kind, list[0 + (kind === 'subSize')][0]);
    items = list.map(([v, l]) => ({ label: tick(v === cur, l), action: () => { store.set(kind, v); applySubStyle(); redraw(); } }));
  }
  popMenu(anchor, items);
}

// ---- Play queue: this episode and the ones after it (Plex style)
async function queuePanel(root) {
  mount(root, html`<div class="ppanel pqueue"><div class="pq-head"><div><h3>Play Queue</h3><div class="pq-n">…</div></div></div><div class="pq-list"></div></div>`);
  const d = P.detail;
  let items = [d];
  if (d?.kind === 'episode' && d.show_id) {
    try {
      P.queue = P.queue && P.queue.show === d.show_id ? P.queue : { show: d.show_id, data: await api.get(`/api/items/${d.show_id}`) };
      const seasons = P.queue.data.seasons || [];
      const all = seasons.filter(s => (d.season === 0 ? s.season === 0 : s.season !== 0)).flatMap(s => s.episodes.map(e => ({ ...e, season: s.season })));
      const at = all.findIndex(e => e.id === d.id);
      items = at >= 0 ? all.slice(at, at + 100) : [d];
    } catch { /* just this one */ }
  }
  if (!P || P.panel !== 'queue') return;
  const show = d?.show_title || P.queue?.data?.title || d?.title || '';
  root.querySelector('.pq-n').textContent = `${items.length} item${items.length === 1 ? '' : 's'}`;
  mount(root.querySelector('.pq-list'), items.map((e, i) => {
    const pic = e.thumb || e.backdrop || e.poster;
    const mins = e.runtime ? `${e.runtime}min` : '';
    return html`<button type="button" class="pq-item ${i === 0 ? 'now' : ''}" data-queue="${e.id}">
      <span class="pq-mark">${i === 0 ? '•••' : ''}</span>
      <span class="pq-pic">${pic ? html`<img loading="lazy" src="${img(pic, 300)}" alt="">` : ''}</span>
      <span class="pq-text"><b>${e.kind === 'episode' ? show : e.title}</b><span>${e.kind === 'episode' ? `S${e.season} · E${e.index ?? '?'} — ${e.title}` : (e.year || '')}</span></span>
      <span class="pq-dur">${mins}</span></button>`;
  }));
}

function openPanel(kind) {
  if (P.panel === kind) return closePanel();
  if (kind === 'tracks' || kind === 'quality') kind = 'settings';
  if (P.panel === kind) return closePanel();
  P.panel = kind;
  const file = currentFile();
  const root = $('#ppanel-root');
  dockUpdate();
  if (kind === 'settings') { settingsPanel(root); return; }
  if (kind === 'queue') { queuePanel(root); return; }
  if (kind === 'subsearch') {
    mount(root, html`<div class="ppanel wide"><div><h4>Find subtitles online</h4><div id="psubsearch"></div>
      <button class="opt" data-a="tracks" style="margin-top:8px">${icon('back')} Back to tracks</button></div></div>`);
    subtitleSearch($('#psubsearch'), { itemId: P.itemId, fileId: P.fileId, onDone: (sub) => {
      file.subtitles.push(sub);
      closePanel();
      selectSubtitle(sub.key);
    } });
    return;
  }
  if (kind === 'tracks') {
    const curAudio = P.info?.audio_index ?? file?.audio[0]?.index;
    mount(root, html`<div class="ppanel">
      <div><h4>Audio</h4>${(file?.audio || []).map(a => html`<button class="opt ${a.index === curAudio ? 'sel' : ''}" data-audio="${a.index}">
        ${langName(a.lang || a.language)}${a.title ? ` · ${a.title}` : ''} <span style="color:var(--faint)">${channels(a.channels)} ${(a.codec || '').toUpperCase()}</span></button>`)}
        ${file?.audio.length ? '' : html`<p class="note">No audio track</p>`}</div>
      <div><h4>Subtitles</h4><button class="opt ${!P.subtitleKey ? 'sel' : ''}" data-sub="">Off</button>
        ${(file?.subtitles || []).map(s => html`<button class="opt ${s.key === P.subtitleKey ? 'sel' : ''}" data-sub="${s.key}">
          ${langName(s.lang || s.language)}${s.title && !s.downloaded ? ` · ${s.title.slice(0, 40)}` : ''}${s.forced ? ' (forced)' : ''}${s.downloaded ? ' · online' : s.external ? ' · file' : ''}</button>`)}
        ${P.subtitleKey && !P.burnedKey ? html`<div class="subdelay"><span>Timing</span>
          <button class="pbtn sm" data-delay="-0.25" title="Show subtitles earlier (G)">−</button>
          <b id="psubdelay">${fmtDelay(P.subDelay)}</b>
          <button class="pbtn sm" data-delay="0.25" title="Show subtitles later (H)">+</button>
          <button class="pbtn sm reset" data-delay="reset" title="Reset">0</button></div>` : ''}
        ${state.features?.subtitles_online ? html`<button class="opt search-online" data-a="subsearch">${icon('search')} Search online…</button>` : ''}
        ${(file?.subtitles || []).some(s => s.image) ? html`<p class="note">Picture-based subtitles are drawn into the video by the server.</p>` : ''}</div>
    </div>`);
  } else {
    mount(root, html`<div class="ppanel single"><div><h4>Quality</h4>
      ${QUALITIES.map(([v, l]) => html`<button class="opt ${v === P.quality ? 'sel' : ''}" data-quality="${v}">${l}</button>`)}
      <p class="note" style="margin-top:8px">${P.info ? `${modeLabel(P.mode)}: ${P.info.reason}` : ''}</p></div></div>`);
  }
}

const modeLabel = (m) => ({ direct: 'Direct play', remux: 'Direct stream', transcode: 'Converting' }[m] || m);

function renderStats() {
  const root = $('#pstats-root');
  if (!P.stats || !P.info) { root.innerHTML = ''; return; }
  const f = P.info.file, a = f.audio.find(x => x.index === P.info.audio_index) || f.audio[0];
  const v = P.video;
  const ahead = (() => { const b = v.buffered; for (let i = 0; i < b.length; i++) if (b.start(i) <= v.currentTime && b.end(i) >= v.currentTime) return b.end(i) - v.currentTime; return 0; })();
  mount(root, html`<div class="stats">
    <div><b>${modeLabel(P.mode)}</b> — ${P.info.reason}</div>
    <div>Source: ${f.resolution} ${(f.video_codec || '').toUpperCase()}${f.hdr ? ' HDR' : ''} · ${(f.container || '').toUpperCase()} · ${f.bitrate ? (f.bitrate / 1e6).toFixed(1) + ' Mbps' : ''}</div>
    <div>Audio: ${a ? `${langName(a.lang || a.language)} ${channels(a.channels)} ${(a.codec || '').toUpperCase()}` : 'none'}</div>
    <div>Playing: ${v.videoWidth}×${v.videoHeight} · quality ${P.quality}${P.info.encoder ? ` · encoder ${P.info.encoder}` : ''}</div>
    <div>Buffered ahead: ${ahead.toFixed(0)} s${P.mode !== 'direct' ? ` · stream starts at ${fmtTime(P.offset)}` : ''}</div>
  </div>`);
}

// ---------------------------------------------------------------- up next
function upNext(pos, dur) {
  const root = $('#pupnext-root');
  if (!autoplayOn()) { if (root.innerHTML) root.innerHTML = ''; return; }
  const nextId = P.info?.next_id;
  const remaining = dur - pos;
  if (!nextId || P.upNextDismissed || dur < 120 || remaining > 25) {
    if (root.innerHTML) root.innerHTML = '';
    return;
  }
  if (!root.innerHTML) {
    mount(root, html`<div class="up-next"><div class="lbl">Next episode</div><div class="ttl" id="un-title">Up next</div>
      <div class="ring"><i id="un-ring" style="width:0"></i></div>
      <div class="actions"><button class="btn primary small" data-a="next">${icon('play')}Play now</button>
      <button class="btn small" data-a="dismiss">Keep watching</button></div></div>`);
    api.get(`/api/items/${nextId}`).then(n => {
      const el = $('#un-title');
      if (el) el.textContent = `S${n.season}:E${n.index} · ${n.title}`;
    }).catch(() => {});
  }
  const ring = $('#un-ring');
  if (ring) ring.style.width = `${Math.min(100, (25 - remaining) / 25 * 100)}%`;
}

function playNext() {
  const nextId = P?.info?.next_id;
  if (!nextId) return;
  report('stopped');
  if (P.mini) { openPlayer(nextId, { returnTo: 'back', mini: true }); return; }     // stays small
  location.replace(`#/play/${nextId}`);
}

// ---------------------------------------------------------------- events
function act(a, el) {
  const v = P.video;
  switch (a) {
    case 'close': return P.mini ? closePlayer({ navigate: false }) : closePlayer();
    case 'minimize': return minimizePlayer();
    case 'details': return openDetails();
    case 'expand': return expandPlayer();
    case 'toggle': return v.paused ? v.play() : v.pause();
    case 'back10': return seekTo(position() - 10);
    case 'fwd10': return seekTo(position() + 10);
    case 'mute': v.muted = !v.muted; store.set('muted', v.muted ? '1' : '0'); return;
    case 'next': return playNext();
    case 'dismiss': P.upNextDismissed = true; $('#pupnext-root').innerHTML = ''; return;
    case 'tracks': case 'quality': return openPanel(a);
    case 'subsearch': P.panel = null; return openPanel('subsearch');
    case 'stats': P.stats = !P.stats; return renderStats();
    case 'retry': P.forceHls = P.forceHls || P.mode === 'direct'; return load(position());
    case 'fullscreen':
      if (document.fullscreenElement) { screen.orientation?.unlock?.(); document.exitFullscreen(); }
      else if (P.root.querySelector('.player').requestFullscreen) {
        P.root.querySelector('.player').requestFullscreen({ navigationUI: 'hide' })
          .then(() => { if (isTouch() && P.video.videoWidth > P.video.videoHeight) screen.orientation?.lock?.('landscape').catch(() => {}); })
          .catch(() => {});
      } else if (P.video.webkitEnterFullscreen) P.video.webkitEnterFullscreen();     // iPhone: the system player
  }
}

function wire() {
  const el = P.root.querySelector('.player');
  const v = P.video;
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-a]');
    if (b) { if (b.tagName === 'A') e.preventDefault(); act(b.dataset.a, b); poke(); return; }
    const audio = e.target.closest('[data-audio]');
    if (audio) {
      const idx = Number(audio.dataset.audio);
      const a = currentFile()?.audio.find(x => x.index === idx);
      P.audioIndex = idx === currentFile()?.audio[0]?.index ? null : idx;
      closePanel();
      return load(position(), { keepPaused: v.paused });
    }
    const ps = e.target.closest('[data-ps]');
    if (ps) { settingsMenu(ps.dataset.ps, ps); return; }
    const qi = e.target.closest('[data-queue]');
    if (qi) {
      const id = Number(qi.dataset.queue);
      closePanel();
      if (id !== P.itemId) { report('stopped'); openPlayer(id, { returnTo: P.returnTo }); }
      return;
    }
    const delay = e.target.closest('[data-delay]');
    if (delay) { setSubDelay(delay.dataset.delay === 'reset' ? 0 : P.subDelay + Number(delay.dataset.delay)); return; }
    const sub = e.target.closest('[data-sub]');
    if (sub) { closePanel(); return selectSubtitle(sub.dataset.sub || null); }
    const q = e.target.closest('[data-quality]');
    if (q) { P.quality = q.dataset.quality; store.set('quality', P.quality); closePanel(); return load(position(), { keepPaused: v.paused }); }
    if (P.mini) { expandPlayer(); return; }                  // mini player: a click on the picture opens it again
    if (P.panel && !e.target.closest('.ppanel')) { closePanel(); return; }
    if (el.classList.contains('touch')) return;          // touch: taps are handled below
    if (e.target === v || e.target.closest('.center')) { v.paused ? v.play() : v.pause(); }
  });
  el.addEventListener('dblclick', (e) => { if (!el.classList.contains('touch') && (e.target === v || e.target.closest('.center'))) act('fullscreen'); });
  el.addEventListener('mousemove', (e) => { if (!el.classList.contains('touch')) poke(); });

  // Touch: one tap shows or hides the controls, a double tap on the left or right side jumps 10 s
  // (each further tap adds 10 s), like the YouTube and Netflix apps.
  let lastTap = 0, tapTimer = 0, streak = 0, streakSide = '';
  el.addEventListener('pointerup', (e) => {
    if (e.pointerType === 'mouse' || P?.panel || P?.mini) return;
    const onPicture = e.target === v || e.target.classList.contains('chrome') || e.target.classList.contains('center')
      || e.target.classList.contains('transport') || e.target.classList.contains('subtitle-layer');
    if (!onPicture) { poke(); return; }
    const now = Date.now();
    const side = e.clientX < innerWidth * 0.38 ? 'left' : e.clientX > innerWidth * 0.62 ? 'right' : '';
    if (now - lastTap < 320 && side) {
      clearTimeout(tapTimer);
      streak = streakSide === side ? streak + 1 : 1;
      streakSide = side;
      seekTo(position() + (side === 'left' ? -10 : 10));
      flash(side, streak * 10);
      lastTap = now;
      return;
    }
    lastTap = now;
    streak = 0; streakSide = '';
    clearTimeout(tapTimer);
    tapTimer = setTimeout(() => {
      if (el.classList.contains('idle') || v.paused) poke();
      else { clearTimeout(P.idleTimer); el.classList.add('idle'); }
      if (el.classList.contains('idle') && v.paused) el.classList.remove('idle');
    }, 260);
  });
  el.addEventListener('touchstart', (e) => { if (!e.target.closest('.chrome button, .seek, .ppanel')) return; poke(); }, { passive: true });

  const playIcons = (name) => ['#pplay2'].forEach(id => { const b = $(id); if (b) mount(b, icon(name)); });
  v.addEventListener('play', () => { playIcons('pause'); poke(); keepAwake(true); });
  v.addEventListener('pause', () => { playIcons('play'); el.classList.remove('idle'); report('paused'); keepAwake(false); });
  v.addEventListener('waiting', () => showSpinner(true));
  v.addEventListener('seeking', () => showSpinner(true));
  v.addEventListener('playing', () => showSpinner(false));
  v.addEventListener('seeked', () => showSpinner(false));
  v.addEventListener('canplay', () => showSpinner(false));
  v.addEventListener('ended', () => {
    if (P.mode !== 'direct' && P.offset + v.duration < duration() - 5) return;  // stream gap, not the real end
    report('stopped');
    if (P.info?.next_id && !P.upNextDismissed && autoplayOn()) playNext();
    else closePlayer();
  });
  v.addEventListener('error', () => {
    if (!P || P.hls) return;
    if (P.mode === 'direct' && !P.forceHls) {   // the browser refused the file: let the server repackage it
      P.forceHls = true;
      load(position());
      return;
    }
    showMessage("This video can't play", 'The browser could not decode the stream.', true);
  });
  document.addEventListener('keydown', onKey, true);
  document.addEventListener('fullscreenchange', onFs);
  P.raf = requestAnimationFrame(loop);
  P.statsTimer = setInterval(() => { if (P?.stats) renderStats(); }, 1000);
  poke();
}

function flash(side, secs) {
  const f = $(side === 'left' ? '#pflash-l' : '#pflash-r');
  if (!f) return;
  f.textContent = `${side === 'left' ? '−' : '+'}${secs} s`;
  f.classList.remove('on'); void f.offsetWidth; f.classList.add('on');
}

function onFs() {
  const b = $('#pfs');
  if (b) mount(b, icon(document.fullscreenElement ? 'exitfs' : 'fullscreen'));
}

function onKey(e) {
  if (!P || P.mini || e.target.closest?.('input, select, textarea, .modal')) return;
  const v = P.video;
  const k = e.key;
  const handled = {
    ' ': () => act('toggle'), k: () => act('toggle'),
    ArrowLeft: () => seekTo(position() - 10), ArrowRight: () => seekTo(position() + 10),
    j: () => seekTo(position() - 10), l: () => seekTo(position() + 10),
    ArrowUp: () => { v.volume = Math.min(1, v.volume + 0.1); v.muted = false; },
    ArrowDown: () => { v.volume = Math.max(0, v.volume - 0.1); },
    f: () => act('fullscreen'), m: () => act('mute'), n: () => playNext(),
    g: () => setSubDelay(P.subDelay - 0.05), h: () => setSubDelay(P.subDelay + 0.05),
    G: () => setSubDelay(P.subDelay - 0.5), H: () => setSubDelay(P.subDelay + 0.5),
    c: () => {
      const subs = currentFile()?.subtitles.filter(s => s.text) || [];
      if (P.subtitleKey) selectSubtitle(null);
      else if (subs.length) selectSubtitle((subs.find(s => sameLang(s.lang, state.prefs?.subtitle_lang)) || subs[0]).key);
    },
    Escape: () => { if (P.panel) closePanel(); else if (!document.fullscreenElement) closePlayer(); },
  }[k];
  if (handled) { e.preventDefault(); e.stopPropagation(); handled(); poke(); }
}

window.addEventListener('pagehide', () => { if (P) report('stopped'); });
