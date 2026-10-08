// Netflix-style preview: rest the pointer on a movie, show or episode and a larger card pops up
// with Play, My list and Watched; the ⌄ button opens the rest (More info, and for admins Edit
// metadata, Refresh Metadata, Fix match). Mouse and trackpad only: touch screens tap through as before.
import { api } from './api.js';
import { adminItemMenu } from './admin.js';
import { refreshView, state } from './app.js';
import { handTrailer, miniPlaying, previewTrailerPlaying, trailerPlayer, whenSoundAllowed } from './extras.js';
import { fmtRuntime, html, icon, img, itemHref, mount, toast } from './ui.js';

const KINDS = new Set(['movie', 'show', 'episode']);
const OPEN_DELAY = 550, CLOSE_DELAY = 140;
const canHover = () => window.matchMedia('(hover: hover) and (pointer: fine)').matches;
const cache = new Map();          // id -> { at, data }

let pop = null, card = null, openTimer = 0, closeTimer = 0, token = 0;

function preview(id) {
  const hit = cache.get(id);
  if (hit && Date.now() - hit.at < 30000) return Promise.resolve(hit.data);
  return api.get(`/api/items/${id}/preview`).then(data => { cache.set(id, { at: Date.now(), data }); return data; });
}

// Two ways to close: hide() takes the preview away when the pointer leaves it, and then looks at what
// the pointer is resting on now (often the next title, which the preview was partly covering) so its
// preview follows without having to move away and back. close() cancels everything (Esc, scroll,
// leaving the page).
let pointerX = -1, pointerY = -1, pending = null;
document.addEventListener('pointermove', (e) => { pointerX = e.clientX; pointerY = e.clientY; }, { passive: true, capture: true });

function cardUnderPointer() {
  if (pointerX < 0) return null;
  for (const el of document.elementsFromPoint(pointerX, pointerY)) {
    if (pop && pop.contains(el)) continue;
    const c = el.closest?.('.card[data-id]');
    if (c) return KINDS.has(c.dataset.kind) && !c.closest('.no-preview') ? c : null;
    if (el.closest?.('.hovercard')) continue;
    return null;
  }
  return null;
}

function schedule(c) {
  if (pending === c) return;
  clearTimeout(openTimer);
  pending = c;
  preview(Number(c.dataset.id)).catch(() => {});      // fetch while the pointer settles, so it opens ready
  openTimer = setTimeout(() => { pending = null; open(c); }, OPEN_DELAY);
}

function unschedule() { clearTimeout(openTimer); pending = null; }

// ---- the trailer inside the preview (Settings › Look › Trailer in hover previews)
const TRAILER_DELAY = 0;          // the trailer is indexed on the server: load it straight away
const trailers = new Map();        // id -> trailer {key, name} or null, so each title is looked up once
let tr = null;                     // { timer, frame, stopListen, fallback, muted }

function stopTrailer() {
  if (!tr) return;
  clearTimeout(tr.timer); clearTimeout(tr.fallback);
  tr.soundWait?.();
  tr.player?.destroy();
  tr.layer?.remove();
  tr = null;
  previewTrailerPlaying(false);
}

async function lookupTrailer(id) {
  if (trailers.has(id)) return trailers.get(id);
  let t = null;
  try { t = (await api.get(`/api/items/${id}/extras?only=trailer`)).trailer || null; } catch { /* no trailer */ }
  trailers.set(id, t);
  return t;
}

function startTrailer(d, thisPop) {
  const mode = state.prefs?.hover_trailer || 'muted';
  if (mode === 'off' || miniPlaying()) return;
  const mine = { muted: true, d, time: 0, timeAt: 0, playing: false };
  tr = mine;
  mine.timer = setTimeout(async () => {
    const t = d.trailer ? { key: d.trailer } : await lookupTrailer(d.id);
    if (tr !== mine || pop !== thisPop || !t) return;
    const art = thisPop.querySelector('.hc-art');
    const layer = document.createElement('div');
    layer.className = 'hc-trailer';
    mine.player = trailerPlayer(t.key, { muted: true,
      onState: (st) => mine.onState?.(st), onTime: (s) => { mine.time = s; mine.timeAt = performance.now(); } });
    layer.append(mine.player.el);
    art.append(layer);
    mine.layer = layer;
    mine.key = t.key;
    mine.frame = mine.player.el;
    const reveal = () => {
      if (tr !== mine || art.classList.contains('playing')) return;
      clearTimeout(mine.fallback);
      mine.revealAt = performance.now();
      art.classList.add('playing');
      // the sound button sits on the picture; it must not follow the picture's link
      const btn = document.createElement('button');
      btn.className = 'hc-sound';
      btn.type = 'button';
      const paint = () => { mount(btn, icon(mine.muted ? 'mute' : 'volume')); btn.title = mine.muted ? 'Turn sound on' : 'Mute'; };
      const setMuted = (m) => {
        mine.muted = m;
        mine.player.setMuted(m);
        previewTrailerPlaying(true, { sound: !m });
        paint();
      };
      btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); mine.soundWait?.(); setMuted(!mine.muted); });
      paint();
      art.append(btn);
      previewTrailerPlaying(true, { sound: false });     // this one plays now; the banner waits
      // with sound: on now if the browser allows it, otherwise at your first click or key press
      if (mode === 'sound') mine.soundWait = whenSoundAllowed(() => { if (tr === mine && mine.muted) setMuted(false); });
    };
    mine.onState = (st) => {
      mine.playing = st === 1;
      if (st === 1) reveal();
      if (st === 0) clearTimeout(mine.fallback);
      if (st === 0 && tr === mine) { art.classList.remove('playing'); art.querySelector('.hc-sound')?.remove(); previewTrailerPlaying(false); }
    };
    mine.fallback = setTimeout(reveal, 4000);
  }, TRAILER_DELAY);
}

function hide() {
  clearTimeout(closeTimer);
  stopTrailer();
  const was = card;
  if (pop) {
    const p = pop;
    p.classList.remove('in');
    setTimeout(() => p.remove(), 160);
  }
  pop = null; card = null;
  const next = cardUnderPointer();
  if (next && next !== was) schedule(next);
}

function close() {
  unschedule(); clearTimeout(closeTimer);
  token++;
  hide();
  unschedule();
}

// Placement rule: the preview always covers the whole title you point at (picture and caption), and
// reaches past it by at most 35% of a neighbouring title's size (plus the gap) on any side. The
// picture is sized to fit: cropped shorter when space is tight, taller when the card is tall.
const SPILL = 0.35;
function spacing(card) {
  const cs = getComputedStyle(card.parentElement);
  const num = (v) => (Number.isFinite(parseFloat(v)) ? parseFloat(v) : 14);
  return { x: num(cs.columnGap), y: num(cs.rowGap) };
}
function place(el, card) {
  const r = card.getBoundingClientRect();
  const gap = spacing(card);
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const minX = (document.querySelector('#view')?.getBoundingClientRect().left || 0) + 8;   // not over the menu
  const extX = gap.x + SPILL * r.width;
  // horizontal: as wide as allowed on each side, never past the window edge, never narrower than the card
  const left = Math.min(r.left, Math.max(minX, r.left - extX));
  const right = Math.max(r.right, Math.min(vw - 8, r.right + extX));
  const w = right - left;
  el.style.width = `${w}px`;
  // vertical: the picture is a full 16:9 frame (room for the trailer, Netflix style); the pop-up may
  // reach over the rows above and below, only the window limits it
  const art = el.querySelector('.hc-art'), body = el.querySelector('.hc-body');
  const bodyH = body.offsetHeight;
  let artH = w * 9 / 16;
  artH = Math.min(artH, vh - 16 - bodyH);              // fits in the window
  artH = Math.max(artH, r.height - bodyH, 72);         // and always hides the whole card
  art.style.height = `${artH}px`;
  const h = artH + bodyH;
  // centred on the card, nudged to stay on screen, always covering it
  let top = r.top + r.height / 2 - h / 2;
  top = Math.max(top, 8);
  top = Math.min(top, vh - h - 8);
  top = Math.min(top, r.top);                                // covers the top edge
  top = Math.max(top, r.bottom - h);                         // covers the bottom edge
  el.style.left = `${left + window.scrollX}px`;
  el.style.top = `${top + window.scrollY}px`;
  el.style.transformOrigin = `${r.left + r.width / 2 - left}px ${r.top + r.height / 2 - top}px`;
}

function facts(d) {
  const kind = { movie: 'Movie', show: 'TV show', episode: 'Episode' }[d.kind];
  const genres = (d.genres || []).slice(0, 2);
  const bits = [d.year, d.kind === 'show' ? (d.child_count ? `${d.child_count} season${d.child_count === 1 ? '' : 's'}` : '') : (d.runtime ? fmtRuntime(d.runtime) : '')]
    .filter(Boolean);
  return html`<div class="hc-kind">${kind}${genres.map(g => html`<span class="sep">|</span>${g}`)}</div>
    <div class="hc-facts">${bits.map((b, i) => html`${i ? html`<span class="dot">•</span>` : ''}<span>${b}</span>`)}
      ${d.certification ? html`<span class="dot">•</span><span class="box">${d.certification}</span>` : ''}
      ${d.quality ? html`<span class="dot">•</span><span class="box">${d.quality}${d.hdr ? ' HDR' : ''}</span>` : ''}
      ${d.rating ? html`<span class="dot">•</span><span class="score">★ ${d.rating.toFixed(1)}</span>` : ''}</div>`;
}

function options(d) {
  const opts = [{ label: 'More info', icon: 'info', action: () => { location.hash = itemHref(d); } }];
  opts.push({ label: d.watched ? 'Mark as unwatched' : (d.kind === 'show' ? 'Mark every episode watched' : 'Mark as watched'),
    icon: 'eye', action: () => setWatched(d, !d.watched) });
  if (state.user?.is_admin) {
    const icons = { 'Edit metadata…': 'edit', 'Refresh Metadata': 'refresh', 'Fix match…': 'search' };
    for (const o of adminItemMenu(d)) {
      if (o.label === 'Fix match…' && d.kind === 'episode') continue;
      opts.push({ ...o, icon: icons[o.label] || 'gear' });
    }
  }
  return opts;
}

async function setWatched(d, watched) {
  try {
    await api.post(`/api/items/${d.id}/watched`, { watched });
    cache.delete(d.id);
    toast(watched ? `Marked ${d.title} watched` : `Marked ${d.title} unwatched`);
    close();
    refreshView();
  } catch (ex) { toast(ex.message, 'error'); }
}

function render(d, fallbackImg) {
  const art = d.backdrop || d.thumb || d.show_backdrop;
  const src = art ? img(art, 780) : fallbackImg;
  const href = itemHref(d);
  mount(pop, html`
    <a class="hc-art" href="${href}" aria-label="${d.title} — more info">
      ${src ? html`<img src="${src}" alt="">` : html`<div class="hc-noart"></div>`}
      ${d.logo ? html`<img class="hc-logo" src="${img(d.logo, 500)}" alt="">` : html`<div class="hc-title">${d.kind === 'episode' ? d.show_title || d.title : d.title}</div>`}
      ${d.progress && !d.progress.completed && d.progress.duration ? html`<div class="progress"><i style="width:${Math.min(100, d.progress.position / d.progress.duration * 100).toFixed(1)}%"></i></div>` : ''}
    </a>
    <div class="hc-body">
      <div class="hc-buttons">
        ${d.play ? html`<a class="hc-btn play" href="#/play/${d.play.item_id}" title="${d.play.action === 'resume' ? 'Resume' : 'Play'}" aria-label="Play">${icon('play')}</a>` : ''}
        ${d.kind !== 'episode' ? html`<button class="hc-btn ${d.in_watchlist ? 'on' : ''}" data-hc="list" title="${d.in_watchlist ? 'Remove from My list' : 'Add to My list'}">${icon(d.in_watchlist ? 'check' : 'plus')}</button>` : ''}
        <button class="hc-btn ${d.watched ? 'on' : ''}" data-hc="watched" title="${d.watched ? 'Watched — mark as unwatched' : 'Mark as watched'}">${icon('eye')}</button>
        <button class="hc-btn more" data-hc="more" title="More options" aria-expanded="false">${icon('chevron')}</button>
      </div>
      <a class="hc-name" href="${href}">${d.kind === 'episode' ? html`${d.show_title || ''} <span>S${d.season}:E${d.index ?? '?'} · ${d.title}</span>` : d.title}</a>
      ${facts(d)}
      <div class="hc-options" hidden>${options(d).map((o, i) => html`<button data-opt="${i}">${icon(o.icon)}${o.label}</button>`)}</div>
    </div>`);
  const opts = options(d);
  pop.querySelector('[data-hc=more]').addEventListener('click', (e) => {
    const box = pop.querySelector('.hc-options');
    box.hidden = !box.hidden;
    e.currentTarget.setAttribute('aria-expanded', String(!box.hidden));
    e.currentTarget.classList.toggle('open', !box.hidden);
    if (card) place(pop, card);                 // re-fit: the picture gives up room to the options
  });
  pop.querySelectorAll('[data-opt]').forEach(b => b.addEventListener('click', () => {
    const o = opts[Number(b.dataset.opt)];
    close();
    o.action();
  }));
  pop.querySelector('[data-hc=list]')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget, on = !d.in_watchlist;
    try {
      await api.post(`/api/items/${d.id}/watchlist`, { on });
      d.in_watchlist = on; cache.delete(d.id);
      btn.classList.toggle('on', on); mount(btn, icon(on ? 'check' : 'plus'));
      btn.title = on ? 'Remove from My list' : 'Add to My list';
      toast(on ? `Added ${d.title} to My list` : `Removed ${d.title} from My list`);
    } catch (ex) { toast(ex.message, 'error'); }
  });
  pop.querySelector('[data-hc=watched]').addEventListener('click', () => setWatched(d, !d.watched));
}

async function open(c) {
  const my = ++token;
  const id = Number(c.dataset.id);
  const anchor = c.querySelector('.frame-link') || c;
  const fallbackImg = c.querySelector('.frame img')?.src;
  let d;
  try { d = await preview(id); } catch { return; }
  if (my !== token || !c.isConnected || cardUnderPointer() !== c || document.querySelector('.modal-back, .menu, #player-root .player')) return;
  pop?.remove();
  pop = document.createElement('div');
  pop.className = 'hovercard';
  pop.addEventListener('pointerenter', () => clearTimeout(closeTimer));
  pop.addEventListener('pointerleave', () => { clearTimeout(closeTimer); closeTimer = setTimeout(hide, CLOSE_DELAY); });
  card = c;
  render(d, fallbackImg);
  stopTrailer();
  startTrailer(d, pop);
  document.body.append(pop);
  pop.style.visibility = 'hidden';            // measure first, then show
  place(pop, c);
  pop.style.visibility = '';
  requestAnimationFrame(() => pop?.classList.add('in'));
}

document.addEventListener('pointerover', (e) => {
  if (!canHover() || pop?.contains(e.target)) return;
  const c = e.target.closest?.('.card[data-id]');
  if (!c || !KINDS.has(c.dataset.kind) || c.closest('.no-preview')) return;     // e.g. My list: static pictures
  if (c === card) { clearTimeout(closeTimer); return; }
  schedule(c);
});
document.addEventListener('pointerout', (e) => {
  const c = e.target.closest?.('.card[data-id]');
  if (!c || c.contains(e.relatedTarget) || pop?.contains(e.relatedTarget)) return;
  if (c === pending) unschedule();
  if (c === card) { clearTimeout(closeTimer); closeTimer = setTimeout(hide, CLOSE_DELAY); }
});
// Clicking into the title while its preview trailer plays: the title's page continues it (extras.js).
window.addEventListener('hashchange', () => {
  const t = tr;
  if (t?.frame && t.playing && t.key && !location.hash.startsWith('#/play/')) {
    // the player reports its position now and then; in between (or if it never does) count from then
    const now = t.timeAt ? t.time + (performance.now() - t.timeAt) / 1000
      : t.revealAt ? (performance.now() - t.revealAt) / 1000 : 0;
    handTrailer({ ids: [t.d.id, t.d.show_id, t.d.parent_id].filter(Boolean), key: t.player?.key?.() || t.key, time: now, playing: true, muted: t.muted });
  }
  close();
});
let scrollY0 = 0;
window.addEventListener('scroll', () => { if (pop && Math.abs(window.scrollY - scrollY0) > 60) close(); }, { passive: true });
document.addEventListener('pointerover', () => { if (!pop) scrollY0 = window.scrollY; }, true);
window.addEventListener('blur', close);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && pop) close(); });
window.addEventListener('lenta:playback', close);
window.addEventListener('lenta:metadata-changed', (e) => { cache.delete(e.detail?.id); trailers.delete(e.detail?.id); });
