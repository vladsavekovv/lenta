// Music: queue + persistent bottom bar player.
import { api } from './api.js';
import { $, fmtTime, html, icon, img, mount, toast } from './ui.js';

const audio = new Audio();
audio.preload = 'auto';
let queue = [];
let index = -1;
let transcodeIds = new Set();
let lastReport = 0;
const playId = Math.random().toString(36).slice(2);

export const currentTrackId = () => queue[index]?.id ?? null;

export function playTracks(tracks, start = 0) {
  queue = tracks;
  load(start);
}

export function stopMusic() {
  audio.pause();
  report('stopped');
  queue = []; index = -1;
  $('#musicbar').hidden = true;
  document.body.classList.remove('has-music');
}

function load(i) {
  if (i < 0 || i >= queue.length) return;
  index = i;
  const t = queue[index];
  audio.src = `/api/stream/track/${t.id}${transcodeIds.has(t.id) ? '?transcode_audio=1' : ''}`;
  audio.play().catch(() => {});
  render();
  highlight();
  if ('mediaSession' in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artist || '', album: t.album || '',
      artwork: t.cover ? [{ src: img(t.cover, 500), sizes: '500x500' }] : [],
    });
    navigator.mediaSession.setActionHandler('previoustrack', prev);
    navigator.mediaSession.setActionHandler('nexttrack', next);
  }
}

function next() { if (index < queue.length - 1) load(index + 1); else { audio.pause(); render(); } }
function prev() { if (audio.currentTime > 4) audio.currentTime = 0; else if (index > 0) load(index - 1); }

function highlight() {
  document.querySelectorAll('.tracks tr[data-i]').forEach(tr => tr.classList.remove('playing'));
  const id = currentTrackId();
  if (!id) return;
  document.querySelectorAll('.tracks tr[data-i]').forEach(tr => {
    if (queue[Number(tr.dataset.i)]?.id === id) tr.classList.add('playing');
  });
}

function render() {
  const bar = $('#musicbar');
  const t = queue[index];
  if (!t) return;
  bar.hidden = false;
  document.body.classList.add('has-music');
  mount(bar, html`
    <div class="now">${t.cover ? html`<img src="${img(t.cover, 185)}" alt="">` : html`<span class="ph"></span>`}
      <div style="min-width:0"><div class="t">${t.title}</div><div class="s">${t.artist}${t.album ? ` · ${t.album}` : ''}</div></div></div>
    <div class="mid">
      <div class="controls">
        <button class="pbtn" data-m="prev" aria-label="Previous">${icon('skipprev')}</button>
        <button class="pbtn main" data-m="toggle" aria-label="${audio.paused ? 'Play' : 'Pause'}">${icon(audio.paused ? 'play' : 'pause')}</button>
        <button class="pbtn" data-m="next" aria-label="Next">${icon('skipnext')}</button>
      </div>
      <div class="line"><span id="m-cur">${fmtTime(audio.currentTime)}</span>
        <div class="seek" id="m-seek"><div class="rail-bar"><div class="done" id="m-done"></div></div><div class="knob" id="m-knob"></div></div>
        <span id="m-dur">${fmtTime(audio.duration || t.duration)}</span></div>
    </div>
    <div class="right">
      <input class="volume" type="range" min="0" max="1" step="0.05" value="${audio.volume}" aria-label="Volume" id="m-vol">
      <button class="pbtn" data-m="close" aria-label="Close player">${icon('close')}</button>
    </div>`);
  tick();
}

function tick() {
  const d = audio.duration || queue[index]?.duration || 0;
  const pct = d ? audio.currentTime / d * 100 : 0;
  const done = $('#m-done'), knob = $('#m-knob');
  if (done) done.style.width = `${pct}%`;
  if (knob) knob.style.left = `${pct}%`;
  const cur = $('#m-cur'), dur = $('#m-dur');
  if (cur) cur.textContent = fmtTime(audio.currentTime);
  if (dur) dur.textContent = fmtTime(d);
}

function report(stateName) {
  const t = queue[index];
  if (!t) return;
  api.post('/api/progress', { item_id: t.id, position: audio.currentTime || 0, duration: audio.duration || t.duration || 0,
    state: stateName, play_id: playId, mode: transcodeIds.has(t.id) ? 'transcode' : 'direct' }).catch(() => {});
}

audio.addEventListener('timeupdate', () => {
  tick();
  if (Date.now() - lastReport > 15000) { lastReport = Date.now(); report(audio.paused ? 'paused' : 'playing'); }
});
audio.addEventListener('play', () => { render(); report('playing'); });
audio.addEventListener('pause', () => { render(); report('paused'); });
audio.addEventListener('ended', next);
audio.addEventListener('error', () => {
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

document.addEventListener('click', (e) => {
  const b = e.target.closest('#musicbar [data-m]');
  if (!b) return;
  const a = b.dataset.m;
  if (a === 'toggle') audio.paused ? audio.play() : audio.pause();
  if (a === 'next') next();
  if (a === 'prev') prev();
  if (a === 'close') stopMusic();
});
document.addEventListener('input', (e) => { if (e.target.id === 'm-vol') audio.volume = Number(e.target.value); });
document.addEventListener('pointerdown', (e) => {
  const seek = e.target.closest('#m-seek');
  if (!seek) return;
  const r = seek.getBoundingClientRect();
  const d = audio.duration;
  if (d && isFinite(d)) audio.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * d;
});

export function pauseMusic() { if (!audio.paused) audio.pause(); }
export const musicPlaying = () => !audio.paused && queue.length > 0;
