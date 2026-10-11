// The player bar (Plex style), one design for everything that plays:
//   * at the bottom of the screen while you browse: a minimised video, music and audiobooks;
//   * inside the full-screen video player, as its controls (shown when you move the mouse).
//
//   ────────────────────────●───────────────────────────   time bar across the whole width (preview on hover)
//   [picture]  Title            ⏮  ↺  ⏯  ↻  ⏭  ⏹            ⋮  repeat  shuffle  settings  list  volume
//              Subtitle
//              12:34 / 45:00
//
// Each source (player.js for video, music.js for music and books) registers an adapter; the bottom bar shows
// the first one that is visible (a minimised video before music). The full-screen player mounts its own copy.
import { $, fmtTime, html, icon, mount, setDockScroll } from './ui.js';

const adapters = [];          // in priority order
let active = null;            // what the bottom bar shows
const views = new Set();      // drawn bars: { el, adapter(), full, drag, lastPaused }
let raf = 0;

/** adapter: { id, visible(), info(), state(), caps(), ...actions } — see player.js and music.js */
export function dockRegister(adapter, { priority = 0 } = {}) {
  adapter.priority = priority;
  adapters.push(adapter);
  adapters.sort((a, b) => b.priority - a.priority);
}

/** Something plays in the bottom bar right now (a minimised video, music or a book; not paused). Pages keep
 *  their trailers going meanwhile, but muted (extras.js, hovercard.js). */
export const dockPlaying = () => !!active && !active.state().paused;
const announce = () => window.dispatchEvent(new CustomEvent('lenta:dock'));

const barView = { el: null, adapter: () => active, full: false, drag: null, lastPaused: null };

/** Something changed (started, stopped, minimised, new title): pick the source and draw the bottom bar again. */
export function dockRefresh() {
  active = adapters.find(a => a.visible()) || null;
  const el = $('#musicbar');
  if (!el) return;
  barView.el = el;
  if (!active) {
    el.hidden = true;
    el.innerHTML = '';
    views.delete(barView);
    document.body.classList.remove('has-music', 'dock-video');
    setDockScroll(false);
    announce();
    return;
  }
  views.add(barView);
  render(barView);
  setDockScroll(true);
  announce();
}

/** New details (title, chapter, a toggle): redraw every bar. */
export function dockUpdate() { views.forEach(v => render(v)); }

/** The full-screen player's controls: the same bar inside the player. Returns a handle for dockUnmount. */
export function dockMount(el, adapter) {
  const v = { el, adapter: () => adapter, full: true, drag: null, lastPaused: null };
  views.add(v);
  render(v);
  return v;
}
export function dockUnmount(v) { if (v) views.delete(v); }

function btn(name, ic, label, { on = false, disabled = false, cls = '' } = {}) {
  return html`<button type="button" class="dk-btn ${cls}${on ? ' on' : ''}" data-dk="${name}" aria-label="${label}" title="${label}" ${disabled ? 'disabled' : ''}>${icon(ic)}</button>`;
}

function render(v) {
  const a = v.adapter(), el = v.el;
  if (!a || !el) return;
  const info = a.info(v.full), caps = a.caps(v.full), st = a.state();
  if (!v.full) {
    el.hidden = false;
    el.className = `musicbar dock dock-${a.id}`;
    document.body.classList.add('has-music');
    document.body.classList.toggle('dock-video', a.id === 'video');
  } else {
    el.className = `bottom dock dock-full dock-${a.id}`;
  }
  const back = caps.back || 10, fwd = caps.fwd || 30;
  mount(el, html`
    <div class="dk-seek" role="slider" tabindex="0" aria-label="Time" aria-valuemin="0" aria-valuemax="${Math.round(st.dur || 0)}">
      <div class="dk-rail"><div class="dk-buf"></div><div class="dk-done"></div></div>
      <span class="dk-knob"></span>
      <div class="dk-tip"><div class="dk-thumb" hidden></div><span class="dk-tipt"></span></div>
    </div>
    <div class="dk-left">
      ${v.full ? '' : html`<button type="button" class="dk-art ${info.video ? 'is-video' : ''}" data-dk="art" aria-label="${info.artLabel || 'Open'}" title="${info.artLabel || 'Open'}">
        ${info.video ? '' : info.art ? html`<img class="dk-art-bg" src="${info.art}" alt=""><img class="dk-art-img" src="${info.art}" alt="">` : html`<span class="dk-art-ph">${icon(info.placeholder || 'music')}</span>`}
        <span class="dk-art-hover">${icon(info.video ? 'expand' : 'chevron')}</span>
      </button>`}
      <div class="dk-meta">
        <a class="dk-t" data-dk="title" href="${info.href || '#'}" title="${info.title}">${info.title}</a>
        <div class="dk-s" title="${info.sub || ''}">${info.sub || ''}</div>
        <div class="dk-time">${fmtTime(st.pos)} / ${fmtTime(st.dur)}</div>
      </div>
    </div>
    <div class="dk-center">
      ${btn('prev', 'skipprev', caps.prevLabel || 'Previous', { disabled: !caps.prev, cls: 'dk-hide-sm' })}
      ${btn('back', back === 15 ? 'replay15' : 'replay10', `Back ${back} seconds`, { cls: 'dk-hide-xs' })}
      <button type="button" class="dk-btn dk-play" data-dk="toggle" aria-label="${st.paused ? 'Play' : 'Pause'}" title="${st.paused ? 'Play' : 'Pause'}">${icon(st.paused ? 'play' : 'pause')}</button>
      ${btn('fwd', fwd === 30 ? 'fwd30' : 'fwd10', `Forward ${fwd} seconds`, { cls: 'dk-hide-xs' })}
      ${btn('next', 'skipnext', caps.nextLabel || 'Next', { disabled: !caps.next, cls: 'dk-hide-sm' })}
      ${btn('stop', 'stop', 'Stop', { cls: 'dk-stop' })}
    </div>
    <div class="dk-right">
      ${btn('more', 'dots', 'More')}
      ${btn('repeat', caps.repeat === 'one' ? 'repeat1' : 'repeat', caps.repeat === 'one' ? 'Repeat this one' : caps.repeat === 'all' ? 'Repeat all' : 'Repeat', { on: !!caps.repeat && caps.repeat !== 'off', disabled: caps.repeat === undefined })}
      ${btn('shuffle', 'shuffle', 'Shuffle', { on: !!caps.shuffle, disabled: caps.shuffle === undefined })}
      ${caps.speed ? html`<button type="button" class="dk-btn dk-speed" data-dk="settings" aria-label="${caps.settingsLabel}" title="${caps.settingsLabel}">${caps.speed}</button>`
        : btn('settings', 'sliders', caps.settingsLabel || 'Settings', { disabled: !caps.settings, on: !!caps.settingsOn })}
      ${btn('list', 'queue', caps.listLabel || 'Play queue', { disabled: !caps.list, on: !!caps.listOn })}
      ${btn('mute', st.muted || st.volume === 0 ? 'mute' : 'volume', st.muted ? 'Sound on' : 'Mute')}
      <span class="dk-vbar" role="slider" tabindex="0" aria-label="Volume" aria-valuemin="0" aria-valuemax="100" title="Volume">
        <span class="dk-vfill" style="height:${Math.round((st.muted ? 0 : st.volume) * 100)}%"></span></span>
    </div>`);
  el.dockView = v;
  v.lastPaused = st.paused;
  wire(v);
  cancelAnimationFrame(raf);
  tick();
}

function tick() {
  for (const v of views) {
    const a = v.adapter();
    if (!a || !v.el?.isConnected) { if (v !== barView) views.delete(v); continue; }
    const st = a.state(), q = (s) => v.el.querySelector(s);
    const pos = v.drag != null ? v.drag * st.dur : st.pos;
    const pct = st.dur ? Math.min(100, pos / st.dur * 100) : 0;
    const done = q('.dk-done'), knob = q('.dk-knob'), buf = q('.dk-buf'), time = q('.dk-time');
    if (done) done.style.width = `${pct}%`;
    if (knob) knob.style.left = `${pct}%`;
    if (buf) buf.style.width = `${st.dur && st.buf ? Math.min(100, st.buf / st.dur * 100) : 0}%`;
    if (time) time.textContent = `${fmtTime(pos)} / ${fmtTime(st.dur)}`;
    if (st.paused !== v.lastPaused) {
      if (v === barView) announce();
      v.lastPaused = st.paused;
      const p = q('.dk-play');
      if (p) { mount(p, icon(st.paused ? 'play' : 'pause')); p.setAttribute('aria-label', st.paused ? 'Play' : 'Pause'); p.title = st.paused ? 'Play' : 'Pause'; }
    }
    const vf = q('.dk-vfill'), vol = st.muted ? 0 : st.volume;
    if (vf && vf.dataset.v !== String(vol)) {
      vf.dataset.v = String(vol); vf.style.height = `${Math.round(vol * 100)}%`;
      q('.dk-vbar')?.setAttribute('aria-valuenow', String(Math.round(vol * 100)));
      const m = q('[data-dk="mute"]');
      if (m) mount(m, icon(vol === 0 ? 'mute' : 'volume'));
    }
    const sub = a.liveSub?.();
    const s = q('.dk-s');
    if (sub != null && s && s.textContent !== sub) { s.textContent = sub; s.title = sub; }
  }
  raf = views.size ? requestAnimationFrame(tick) : 0;
}

function wire(v) {
  const el = v.el;
  const seek = el.querySelector('.dk-seek');
  const frac = (e) => { const r = seek.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); };
  const tip = (f) => {
    const t = el.querySelector('.dk-tip'), r = seek.getBoundingClientRect();
    const dur = v.adapter().state().dur || 0;
    el.querySelector('.dk-tipt').textContent = fmtTime(f * dur);
    v.adapter().thumb?.(f * dur, el.querySelector('.dk-thumb'));
    const half = (t.offsetWidth || 40) / 2;
    t.style.left = `${Math.max(half, Math.min(r.width - half, f * r.width))}px`;
  };
  seek.addEventListener('pointermove', (e) => { tip(frac(e)); if (v.drag != null) v.drag = frac(e); });
  seek.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    v.drag = frac(e); seek.classList.add('dragging'); seek.setPointerCapture(e.pointerId); tip(v.drag);
  });
  const end = (e, go) => {
    if (v.drag == null) return;
    const f = frac(e);
    v.drag = null; seek.classList.remove('dragging');
    if (go) v.adapter().seek(f * (v.adapter().state().dur || 0));
  };
  seek.addEventListener('pointerup', (e) => end(e, true));
  seek.addEventListener('pointercancel', (e) => end(e, false));
  seek.addEventListener('click', (e) => e.stopPropagation());
  seek.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { v.adapter().skip(10); e.preventDefault(); e.stopPropagation(); }
    if (e.key === 'ArrowLeft') { v.adapter().skip(-10); e.preventDefault(); e.stopPropagation(); }
  });
  // volume: the thin upright bar at the right end (like Plex): click or drag up and down, or use the wheel
  const vb = el.querySelector('.dk-vbar');
  const vfrac = (e) => { const r = vb.getBoundingClientRect(); return Math.max(0, Math.min(1, (r.bottom - e.clientY) / r.height)); };
  const setVol = (x) => { v.adapter().setVolume(Math.round(x * 20) / 20); };
  let vdrag = false;
  vb.addEventListener('pointerdown', (e) => { vdrag = true; vb.setPointerCapture(e.pointerId); setVol(vfrac(e)); e.stopPropagation(); });
  vb.addEventListener('pointermove', (e) => { if (vdrag) setVol(vfrac(e)); });
  vb.addEventListener('pointerup', () => { vdrag = false; });
  vb.addEventListener('pointercancel', () => { vdrag = false; });
  vb.addEventListener('click', (e) => e.stopPropagation());
  vb.addEventListener('wheel', (e) => { e.preventDefault(); const st = v.adapter().state(); setVol(Math.max(0, Math.min(1, (st.muted ? 0 : st.volume) - Math.sign(e.deltaY) * 0.05))); }, { passive: false });
  vb.addEventListener('keydown', (e) => {
    const st = v.adapter().state(), cur = st.muted ? 0 : st.volume;
    if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { setVol(Math.min(1, cur + 0.05)); e.preventDefault(); e.stopPropagation(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { setVol(Math.max(0, cur - 0.05)); e.preventDefault(); e.stopPropagation(); }
  });
}

document.addEventListener('click', (e) => {
  const b = e.target.closest('.dock [data-dk]');
  const v = b?.closest('.dock')?.dockView;
  const a = v?.adapter();
  if (!b || !a || b.disabled) return;
  const name = b.dataset.dk;
  e.stopPropagation();
  if (name === 'title' && a.titleClick) { e.preventDefault(); a.titleClick(v.full); return; }
  if (name === 'title') return;                       // a normal link
  e.preventDefault();
  const st = a.state();
  ({
    art: () => a.artClick(),
    toggle: () => a.toggle(),
    back: () => a.skip(-(a.caps(v.full).back || 10)),
    fwd: () => a.skip(a.caps(v.full).fwd || 30),
    prev: () => a.prev(),
    next: () => a.next(),
    stop: () => a.stop(v.full),
    more: () => a.more(b, v.full),
    repeat: () => { a.repeat(); dockUpdate(); },
    shuffle: () => { a.shuffle(); dockUpdate(); },
    settings: () => a.settings(b, v.full),
    list: () => a.list(b, v.full),
    mute: () => { a.setMuted(!st.muted); dockUpdate(); },
  }[name] || (() => {}))();
}, true);
