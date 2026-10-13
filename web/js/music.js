// Music and audiobooks: what plays in the bar at the bottom of the screen (drawn by dock.js).
// Songs: a queue of tracks. An audiobook: its parts (files) played as one long recording, with chapters,
// speed, back 15 / forward 30 seconds, and your place kept on the server (Continue watching, other devices).
import { api } from './api.js';
import { dockRefresh, dockRegister } from './dock.js';
import { fmtTime, icon, img, popMenu, toast } from './ui.js';

const audio = new Audio();
audio.preload = 'auto';
let queue = [];
let index = -1;
let transcodeIds = new Set();
let lastReport = 0;
const playId = Math.random().toString(36).slice(2);

// ---- audiobook state
let book = null;            // { id, title, author, cover, parts: [{file_id, start, duration}], chapters, duration }
let part = -1;              // the part (file) that is loaded
let base = 0;               // when converting, the stream starts here (seconds into the part)
let pendingSeek = null;     // seconds into the part, applied once the part can play
const SPEEDS = [0.75, 1, 1.1, 1.25, 1.5, 1.75, 2];
function savedSpeed() { try { return Number(localStorage.getItem('lenta.bookSpeed')) || 1; } catch { return 1; } }

export const currentTrackId = () => (book ? null : queue[index]?.id ?? null);
export const currentBookId = () => book?.id ?? null;

export function playTracks(tracks, start = 0) {
  leaveBook();
  shuffled = false;
  window.dispatchEvent(new CustomEvent('lenta:music-start'));     // a minimised video makes way
  queue = tracks;
  load(start);
}

export function stopMusic() {
  audio.pause();
  report('stopped');
  queue = []; index = -1; book = null; part = -1;
  render();
  document.dispatchEvent(new CustomEvent('lenta:book'));
}

function leaveBook() {
  if (!book) return;
  report('stopped');
  book = null; part = -1;
  audio.playbackRate = 1;
  document.dispatchEvent(new CustomEvent('lenta:book'));
}

function load(i) {
  if (i < 0 || i >= queue.length) return;
  index = i;
  const t = queue[index];
  audio.src = `/api/stream/track/${t.id}${transcodeIds.has(t.id) ? '?transcode_audio=1' : ''}`;
  audio.play().catch(() => {});
  render();
  highlight();
  mediaSession(t.title, t.artist || '', t.album || '', t.cover);
}

function mediaSession(title, artist, album, cover) {
  if (!('mediaSession' in navigator)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title, artist, album, artwork: cover ? [{ src: img(cover, 500), sizes: '500x500' }] : [],
    });
    navigator.mediaSession.setActionHandler('previoustrack', prev);
    navigator.mediaSession.setActionHandler('nexttrack', next);
    navigator.mediaSession.setActionHandler('seekbackward', () => skip(-15));
    navigator.mediaSession.setActionHandler('seekforward', () => skip(30));
  } catch { /* older browsers */ }
}

// ---- audiobooks

/** Start (or carry on with) a book. at: seconds into the whole book. */
export function playBook(d, at = 0) {
  if (!d.parts?.length) { toast(`${d.title}: its files are missing. Scan the library again.`, 'error'); return; }
  if (book && book.id === d.id) { seekBook(at); audio.play().catch(() => {}); return; }
  if (book) report('stopped');
  window.dispatchEvent(new CustomEvent('lenta:music-start'));
  queue = []; index = -1;
  book = { id: d.id, title: d.title, author: d.artist || '', cover: d.poster, parts: d.parts,
    chapters: d.chapters || [], duration: d.duration || d.parts.reduce((s, p) => s + (p.duration || 0), 0) };
  part = -1;
  mediaSession(book.title, book.author, '', book.cover);
  seekBook(at);
  audio.play().catch(() => {});
  render();
  document.dispatchEvent(new CustomEvent('lenta:book'));
}

const bookPos = () => (book && part >= 0 ? book.parts[part].start + base + ((pendingSeek ?? audio.currentTime) || 0) : 0);
export const bookPosition = () => bookPos();

function partAt(t) {
  let i = 0;
  while (i < book.parts.length - 1 && t >= book.parts[i + 1].start) i++;
  return i;
}

function loadPart(i, offset = 0) {
  part = i;
  const p = book.parts[i];
  const conv = transcodeIds.has(`f${p.file_id}`);
  base = conv ? offset : 0;
  pendingSeek = conv ? null : offset;
  audio.src = `/api/stream/track/${book.id}?file=${p.file_id}${conv ? `&transcode_audio=1&start=${offset.toFixed(2)}` : ''}`;
  audio.playbackRate = savedSpeed();
}

function seekBook(t) {
  if (!book) return;
  t = Math.max(0, Math.min(book.duration - 1, t || 0));
  const i = partAt(t);
  const offset = t - book.parts[i].start;
  const conv = transcodeIds.has(`f${book.parts[i].file_id}`);
  const wasPlaying = !audio.paused;
  if (i !== part || conv) {
    loadPart(i, offset);
    if (wasPlaying) audio.play().catch(() => {});
  } else {
    audio.currentTime = offset;
  }
  tick();
}

function skip(sec) {
  if (book) seekBook(bookPos() + sec);
  else if (isFinite(audio.duration)) audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + sec));
}

function chapterIndex(t = bookPos()) {
  const ch = book?.chapters || [];
  let i = -1;
  ch.forEach((c, n) => { if (t >= c.start - 0.5) i = n; });
  return i;
}

export function jumpToChapter(n) {
  const c = book?.chapters?.[n];
  if (c) { seekBook(c.start); audio.play().catch(() => {}); }
}

function setSpeed(v) {
  try { localStorage.setItem('lenta.bookSpeed', String(v)); } catch { /* private mode */ }
  audio.playbackRate = v;
  render();
}

// ---- shared

function next() {
  if (book) {
    const ch = book.chapters, i = chapterIndex();
    if (ch[i + 1]) jumpToChapter(i + 1); else if (part < book.parts.length - 1) { loadPart(part + 1); audio.play().catch(() => {}); }
    return;
  }
  if (index < queue.length - 1) load(index + 1);
  else if (repeatMode === 'all' && queue.length) load(0);
  else { audio.pause(); render(); }
}
function prev() {
  if (book) {
    const ch = book.chapters, i = chapterIndex();
    if (i < 0) return seekBook(0);
    seekBook(bookPos() - ch[i].start > 4 || i === 0 ? ch[i].start : ch[i - 1].start);
    return;
  }
  if (audio.currentTime > 4) audio.currentTime = 0; else if (index > 0) load(index - 1);
}

function highlight() {
  document.querySelectorAll('.tracks tr[data-i]').forEach(tr => tr.classList.remove('playing'));
  const id = currentTrackId();
  if (!id) return;
  document.querySelectorAll('.tracks tr[data-i]').forEach(tr => {
    if (queue[Number(tr.dataset.i)]?.id === id) tr.classList.add('playing');
  });
}

// ---- the bar at the bottom (dock.js draws it; this tells it what is playing) ----
let repeatMode = 'off';      // off | all | one (songs)
let shuffled = false;

function render() {
  // Play buttons on book pictures show Pause for the book that is playing
  document.querySelectorAll('[data-book-play]').forEach((b) => {
    const on = book && Number(b.dataset.bookPlay) === book.id && !audio.paused;
    if (b.classList.contains('on') !== !!on) { b.classList.toggle('on', !!on); b.innerHTML = String(icon(on ? 'pause' : 'play')); }
  });
  dockRefresh();
}

function bookSub(t = bookPos()) {
  const ci = chapterIndex(t);
  return [book.author, ci >= 0 ? book.chapters[ci].title : ''].filter(Boolean).join(' · ');
}

let lastChapter = -2;
function tick() {
  if (!book) return;
  const ci = chapterIndex();
  if (ci !== lastChapter) {
    lastChapter = ci;
    document.dispatchEvent(new CustomEvent('lenta:chapter', { detail: { book: book.id, chapter: ci } }));
  }
}

function speedMenu(anchor) {
  popMenu(anchor, [{ heading: 'Speed' }, ...SPEEDS.map(v => ({ label: `${v}×${v === (audio.playbackRate || 1) ? '  ✓' : ''}`, action: () => setSpeed(v) }))]);
}

dockRegister({
  id: 'music',
  visible: () => !!book || index >= 0,
  info() {
    if (book) return { title: book.title, sub: bookSub(), href: `#/item/${book.id}`, art: book.cover ? img(book.cover, 342) : null,
      placeholder: 'book', artLabel: 'Open the book' };
    const t = queue[index];
    return { title: t.title, sub: [t.artist, t.album].filter(Boolean).join(' · '), href: t.albumId ? `#/item/${t.albumId}` : '#',
      art: t.cover ? img(t.cover, 342) : null, placeholder: 'music', artLabel: 'Open the album' };
  },
  liveSub: () => (book ? bookSub() : null),
  state() {
    const vol = { volume: audio.volume, muted: audio.muted, paused: audio.paused };
    if (book) return { ...vol, pos: bookPos(), dur: book.duration, buf: 0 };
    const d = audio.duration && isFinite(audio.duration) ? audio.duration : queue[index]?.duration || 0;
    let buf = 0;
    try { const b = audio.buffered; if (b.length) buf = b.end(b.length - 1); } catch { /* none yet */ }
    return { ...vol, pos: audio.currentTime || 0, dur: d, buf };
  },
  caps() {
    if (book) {
      return { back: 15, fwd: 30, prev: true, next: true, prevLabel: 'Previous chapter', nextLabel: 'Next chapter',
        speed: `${audio.playbackRate || 1}×`, settingsLabel: 'Playback speed',
        list: book.chapters.length > 0, listLabel: 'Chapters' };
    }
    return { back: 10, fwd: 30, prev: true, next: index < queue.length - 1 || repeatMode === 'all',
      repeat: repeatMode, shuffle: shuffled, list: queue.length > 0, listLabel: 'Play queue' };
  },
  toggle: () => (audio.paused ? audio.play().catch(() => {}) : audio.pause()),
  seek(t) {
    if (book) { seekBook(t); return; }
    if (isFinite(audio.duration)) audio.currentTime = Math.max(0, Math.min(audio.duration, t));
  },
  skip: (s) => skip(s),
  prev: () => prev(),
  next: () => next(),
  stop: () => stopMusic(),
  artClick() { location.hash = this.info().href; },
  more(anchor) {
    if (book) {
      popMenu(anchor, [{ heading: book.title },
        { label: 'Open the book', action: () => { location.hash = `#/item/${book.id}`; } },
        { label: `Speed (${audio.playbackRate || 1}×)…`, action: () => speedMenu(anchor) },
        ...(book.chapters.length ? [{ label: 'Chapters…', action: () => this.list(anchor) }] : []),
        { sep: true }, { label: 'Stop', action: stopMusic }]);
      return;
    }
    const t = queue[index];
    popMenu(anchor, [{ heading: t.title },
      ...(t.albumId ? [{ label: 'Go to the album', action: () => { location.hash = `#/item/${t.albumId}`; } }] : []),
      { label: 'Play queue…', action: () => this.list(anchor) },
      { sep: true }, { label: 'Stop', action: stopMusic }]);
  },
  repeat() { repeatMode = { off: 'all', all: 'one', one: 'off' }[repeatMode]; },
  shuffle() {
    shuffled = !shuffled;
    if (!book && queue.length > 1) {                 // the songs after this one, in a new order (or back in order)
      const cur = queue[index];
      const rest = queue.filter((_, i) => i !== index);
      if (shuffled) {
        queue.original = queue.original || [...queue];
        for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
        const orig = queue.original;
        queue = [cur, ...rest]; queue.original = orig; index = 0;
      } else if (queue.original) {
        const orig = queue.original;
        queue = orig; index = Math.max(0, orig.indexOf(cur));
      }
      highlight();
    }
  },
  settings(anchor) { if (book) speedMenu(anchor); },
  list(anchor) {
    if (book) {
      const ci = chapterIndex();
      popMenu(anchor, [{ heading: 'Chapters' }, ...book.chapters.map((c, n) => ({
        label: `${n === ci ? '▶ ' : ''}${c.title}`, hint: fmtTime(c.start), action: () => jumpToChapter(n) }))]);
      return;
    }
    popMenu(anchor, [{ heading: 'Play queue' }, ...queue.map((t, n) => ({
      label: `${n === index ? '▶ ' : ''}${t.title}`, hint: t.duration ? fmtTime(t.duration) : '', action: () => load(n) }))]);
  },
  setVolume(v) { audio.volume = v; audio.muted = v === 0; },
  setMuted(m) { audio.muted = m; },
}, { priority: 1 });

function report(stateName) {
  if (book) {
    api.post('/api/progress', { item_id: book.id, position: bookPos(), duration: book.duration, state: stateName,
      play_id: playId, mode: 'direct' }).catch(() => {});
    return;
  }
  const t = queue[index];
  if (!t) return;
  api.post('/api/progress', { item_id: t.id, position: audio.currentTime || 0, duration: audio.duration || t.duration || 0,
    state: stateName, play_id: playId, mode: transcodeIds.has(t.id) ? 'transcode' : 'direct' }).catch(() => {});
}

audio.addEventListener('loadedmetadata', () => {
  if (book && pendingSeek != null) {
    const s = pendingSeek; pendingSeek = null;
    if (s > 0) audio.currentTime = s;
  }
  if (book) audio.playbackRate = savedSpeed();
});
audio.addEventListener('timeupdate', () => {
  tick();
  if (Date.now() - lastReport > (book ? 10000 : 15000)) { lastReport = Date.now(); report(audio.paused ? 'paused' : 'playing'); }
});
// The Android app keeps playing with the screen off while music or a book plays (a wake lock).
const appAudio = (on) => { try { window.LentaApp?.audioPlaying?.(on); } catch { /* the browser */ } };
audio.addEventListener('play', () => { render(); report('playing'); appAudio(true); });
audio.addEventListener('pause', () => { render(); report('paused'); appAudio(false); });
audio.addEventListener('ended', () => {
  if (book) {
    if (part < book.parts.length - 1) { loadPart(part + 1); audio.play().catch(() => {}); return; }
    report('stopped');       // the end of the book: marked as finished
    render();
    return;
  }
  if (repeatMode === 'one') { audio.currentTime = 0; audio.play().catch(() => {}); return; }
  next();
});
audio.addEventListener('error', () => {
  if (book) {
    const p = book.parts[part];
    if (!p) return;
    const key = `f${p.file_id}`;
    if (!transcodeIds.has(key)) {           // the browser can't decode this file: the server converts it
      const at = (pendingSeek ?? audio.currentTime) || 0;
      transcodeIds.add(key);
      loadPart(part, base + at);
      audio.play().catch(() => {});
    } else toast(`Can't play ${book.title}`, 'error');
    return;
  }
  const t = queue[index];
  if (!t) return;
  if (!transcodeIds.has(t.id)) {           // browser can't decode it: ask the server to convert
    transcodeIds.add(t.id);
    load(index);
  } else {
    toast(`Can't play ${t.title}`, 'error');
    next();
  }
});

// before the page closes: keep the place in the book
window.addEventListener('pagehide', () => {
  if (!book) return;
  try {
    navigator.sendBeacon?.('/api/progress', new Blob([JSON.stringify({ item_id: book.id, position: bookPos(),
      duration: book.duration, state: 'paused', play_id: playId, mode: 'direct' })], { type: 'application/json' }));
  } catch { /* nothing to do */ }
});

export function pauseMusic() { if (!audio.paused) audio.pause(); }
export const musicPlaying = () => !audio.paused && (queue.length > 0 || !!book);
export const musicPaused = () => audio.paused;
export function toggleMusic() { audio.paused ? audio.play().catch(() => {}) : audio.pause(); }
