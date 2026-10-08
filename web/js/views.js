// Browsing views: home, library, details, search, people, my list.
import { api } from './api.js';
import { state, refreshView } from './app.js';
import { playTracks, currentTrackId } from './music.js';
import { adminItemMenu } from './admin.js';
import { onCleanup, openTrailer, stageHtml, startExtras, startHomeTrailer, stopHomeTrailer, wireStage } from './extras.js';
import { openSubtitleDialog } from './subtitles.js';
import {
  $, $$, cardFor, channels, esc, fmtBytes, fmtRuntime, fmtTime, genPoster, html, icon, img, itemHref, lang, mount,
  popMenu, posterCard, raw, row, squareCard, landscapeCard, toast, modal, wideArt,
} from './ui.js';

const view = () => $('#view');

function playLabel(play) {
  if (!play) return 'Play';
  if (play.action === 'resume') return play.season != null ? `Resume S${play.season}:E${play.episode}` : 'Resume';
  if (play.action === 'next' || (play.action === 'start' && play.season != null))
    return `Play S${play.season}:E${play.episode}`;
  if (play.action === 'rewatch') return 'Watch again';
  return 'Play';
}

function metaLine(d, extras = []) {
  const parts = [];
  if (d.year) parts.push(html`<span>${d.year}</span>`);
  if (d.certification) parts.push(html`<span class="cert">${d.certification}</span>`);
  if (d.kind === 'show' && d.seasons) {
    const n = d.seasons.filter(s => s.season !== 0).length;
    parts.push(html`<span>${n} season${n === 1 ? '' : 's'}</span>`);
  } else if (d.kind === 'show' && d.child_count) {
    parts.push(html`<span>${d.child_count} season${d.child_count === 1 ? '' : 's'}</span>`);
  } else if (d.runtime) parts.push(html`<span>${fmtRuntime(d.runtime)}</span>`);
  if (d.rating) parts.push(html`<span class="score" title="TMDB rating">${d.rating.toFixed(1)}</span>`);
  return html`<div class="meta">${parts}${extras}</div>`;
}

// ---------------------------------------------------------------- home
// The billboard's second information line (some themes): Movie · Action · 2025 · 1h 23m · 16+
function heroMeta(h) {
  const parts = [h.kind === 'show' ? 'Series' : 'Movie'];
  const genres = Array.isArray(h.genres) ? h.genres : [];
  if (genres[0]) parts.push(genres[0]);
  if (h.year) parts.push(String(h.year));
  if (h.kind === 'show' && h.child_count) parts.push(`${h.child_count} season${h.child_count === 1 ? '' : 's'}`);
  else if (h.runtime) parts.push(fmtRuntime(h.runtime));
  return html`<div class="hero-meta">${parts.map(p => html`<span>${p}</span>`)}${h.certification ? html`<span class="cert">${h.certification}</span>` : ''}</div>`;
}

// Little labels in the billboard's corner (some themes): new in the library, rating
function heroBadges(h) {
  const added = h.added_at ? (typeof h.added_at === 'number' ? h.added_at * (h.added_at < 1e12 ? 1000 : 1) : Date.parse(h.added_at)) : 0;
  const recent = added && Date.now() - added < 30 * 86400000;
  const badges = [];
  if (recent) badges.push(html`<span class="hb new"><img class="hb-mark" src="img/lenta-mark.png" alt="">Recently added</span>`);
  if (h.rating) badges.push(html`<span class="hb score"><b>★</b>${h.rating.toFixed(1)}</span>`);
  return badges.length ? html`<div class="hero-badges">${badges}</div>` : '';
}

// The page takes on the colour of the billboard picture (some themes use it as their background)
export function tintFromPicture(url) {
  if (!url) return;
  const im = new Image();
  im.onload = () => {
    try {
      const c = document.createElement('canvas'); c.width = 24; c.height = 14;
      const x = c.getContext('2d'); x.drawImage(im, 0, 0, 24, 14);
      const d = x.getImageData(0, 0, 24, 14).data;
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
      r /= n; g /= n; b /= n;
      const k = 70 / Math.max(r, g, b, 1);                 // keep the hue, make it dark
      const f = (v) => Math.round(Math.min(255, v * k * .75 + 14));
      document.documentElement.style.setProperty('--hero-tint', `rgb(${f(r)}, ${f(g)}, ${f(b)})`);
    } catch { /* picture from another site: keep the plain background */ }
  };
  im.src = url;
}

function hero(h) {
  if (!h) return '';
  const art = h.backdrop || h.thumb;
  return html`<section class="hero" data-art="${img(art, 1920)}">
    <div class="art" style="background-image:url('${img(art, 1920)}')"></div>
    ${heroBadges(h)}
    <div class="hero-body">
      ${h.logo ? html`<img class="logo" src="${img(h.logo, 780)}" alt="${h.title}">` : html`<h1 class="marquee">${h.title}</h1>`}
      ${metaLine(h)}${heroMeta(h)}
      ${h.overview ? html`<p class="overview clamp-3">${h.overview}</p>` : ''}
      <div class="actions">
        ${h.play ? html`<a class="btn primary" href="#/play/${h.play.item_id}">${icon('play')}${playLabel(h.play)}</a>` : ''}
        <a class="btn ghost" href="#/item/${h.id}">${icon('info')}More info</a>
      </div>
    </div></section>`;
}

async function home() {
  if (!state.libraries.length) {
    mount(view(), html`<div class="page"><div class="empty"><h3>No libraries yet</h3>
      ${state.user.is_admin
        ? html`<p>Point LENTA at the folders that hold your movies, shows, music or photos and it will organise them here.</p>
               <a class="btn primary" href="#/admin/libraries">Add a library</a>`
        : html`<p>An administrator hasn't shared any libraries with you yet.</p>`}</div></div>`);
    return;
  }
  const data = await api.get('/api/home');
  if (!data.rows.length) {
    mount(view(), html`<div class="page"><div class="empty"><h3>Your libraries are empty for now</h3>
      <p>If you just added a library, LENTA is still scanning it. Titles appear here as soon as they are read.</p>
      ${state.user.is_admin ? html`<a class="btn" href="#/admin">See scan progress</a>` : ''}</div></div>`);
    return;
  }
  mount(view(), html`${hero(data.hero)}<div class="rows" ${data.hero ? '' : raw('style="padding-top:40px"')}>${data.rows.map(row)}</div>`);
  document.body.classList.add('home-page');
  onCleanup(() => document.body.classList.remove('home-page'));
  if (data.hero) tintFromPicture(document.querySelector('.hero')?.dataset.art);
  startHomeTrailer(data.hero);
  if (data.hero) rotateBanner(data.hero);
}

// The banner moves to another random title (from the libraries chosen in Settings › Home screen)
// on a timer, with that title's trailer. It waits while you're scrolled away from it, in another
// tab, in a dialog or watching something.
function rotateBanner(first) {
  const every = Number(state.prefs?.home_banner_interval ?? 60);
  if (!every) return;
  const shown = [first.id];
  let elapsed = 0, busy = false, inView = true;
  const el = () => document.querySelector('.hero');
  const io = new IntersectionObserver(([e]) => { inView = e.intersectionRatio > 0.35; }, { threshold: [0, 0.35] });
  io.observe(el());
  const tick = setInterval(async () => {
    if (busy || !inView || document.hidden || document.querySelector('.modal-back, #player-root .player')) return;
    if (++elapsed < every) return;
    elapsed = 0; busy = true;
    try {
      const { hero: next } = await api.get(`/api/home/banner?exclude=${shown.slice(-50).join(',')}`);
      const cur = el();
      if (!next || !cur || next.id === shown[shown.length - 1]) return;
      shown.push(next.id);
      stopHomeTrailer(550);                             // its sound fades out with the picture
      cur.classList.add('swapping');                    // fade out, swap, fade in
      await new Promise(r => setTimeout(r, 600));
      if (!cur.isConnected) return;
      const tmp = document.createElement('div');
      mount(tmp, hero(next));
      cur.replaceChildren(...tmp.firstElementChild.childNodes);
      cur.dataset.art = tmp.firstElementChild.dataset.art;
      cur.classList.remove('trailer-on');
      tintFromPicture(cur.dataset.art);
      requestAnimationFrame(() => cur.classList.remove('swapping'));
      startHomeTrailer(next);
    } catch { /* try again next time */ } finally { busy = false; }
  }, 1000);
  onCleanup(() => { clearInterval(tick); io.disconnect(); });
}

// ---------------------------------------------------------------- library
const SORTS = {
  video: [['added', 'Recently added'], ['title', 'Title'], ['year', 'Year'], ['rating', 'Rating'], ['release', 'Release date'], ['random', 'Shuffle']],
  music: [['added', 'Recently added'], ['title', 'Album'], ['year', 'Year']],
  photos: [['release', 'Newest first'], ['title', 'Name'], ['added', 'Recently added']],
};

// Library sizes for the slider, 1 (small, many per row) to 7 (large, few per row): minimum card width.
const ZOOM = {
  poster: [96, 116, 138, 160, 188, 222, 262],
  landscape: [168, 204, 240, 282, 332, 392, 462],
  square: [108, 128, 150, 174, 200, 232, 272],
};

// On phones the slider picks whole columns instead (smallest setting = most per row).
const PHONE_COLS = { poster: [4, 3, 3, 3, 2, 2, 1], landscape: [3, 2, 2, 2, 1, 1, 1], square: [4, 3, 3, 2, 2, 2, 1] };
const gridVars = (style, z) => `--cw:${ZOOM[style][z - 1]}px;--cols:${PHONE_COLS[style][z - 1]}`;

async function savePref(values) {
  try {
    const r = await api.put('/api/me/prefs', values);
    state.prefs = r.prefs;
  } catch (ex) { toast(ex.message, 'error'); }
}

function factsLine(i) {
  return [i.year, i.kind === 'show' ? (i.child_count ? `${i.child_count} season${i.child_count === 1 ? '' : 's'}` : '')
    : (i.runtime ? fmtRuntime(i.runtime) : ''), i.certification, i.rating ? `★ ${i.rating.toFixed(1)}` : '']
    .filter(Boolean).join(' · ');
}

// Detail view: a picture, the facts and the start of the description for each title.
function detailRow(i, group) {
  const href = itemHref(i);
  const art = group === 'music' ? i.poster : (wideArt() ? (i.backdrop || i.thumb || i.poster) : (i.poster || i.thumb));
  const watched = i.progress?.completed || (i.kind === 'show' && i.unwatched === 0 && i.child_count);
  return html`<article class="drow ${wideArt() && group !== 'music' ? 'wide' : ''}${group === 'music' ? ' square' : ''}">
    <a class="dthumb" href="${href}">${art ? html`<img loading="lazy" src="${img(art, 342)}" alt="">` : genPoster(i)}
      ${i.progress && !i.progress.completed && i.progress.duration ? html`<span class="progress"><i style="width:${Math.min(100, i.progress.position / i.progress.duration * 100).toFixed(1)}%"></i></span>` : ''}</a>
    <div class="dinfo">
      <a class="dt" href="${href}">${i.title}</a>
      <div class="dmeta">${group === 'music' ? [i.artist, i.year].filter(Boolean).join(' · ') : factsLine(i)}${watched ? html` · <span class="good">Watched</span>` : ''}</div>
      ${(i.genres || []).length ? html`<div class="dgen">${i.genres.slice(0, 4).join(', ')}</div>` : ''}
      ${i.overview ? html`<p class="dover">${i.overview}</p>` : ''}
    </div>
    ${i.kind === 'movie' ? html`<a class="hc-btn play dplay" href="#/play/${i.id}" title="Play" aria-label="Play ${i.title}">${icon('play')}</a>` : ''}
  </article>`;
}

// Table view: one line per title; click a column heading to sort by it.
function tableHead(group, sort) {
  const cols = group === 'music'
    ? [['title', 'Album'], [null, 'Artist'], ['year', 'Year'], [null, 'Genre'], ['added', 'Added']]
    : [['title', 'Title'], ['year', 'Year'], [null, 'Length'], [null, 'Rated'], ['rating', 'Rating'], [null, 'Genres'], ['added', 'Added']];
  return cols.map(([key, label]) => html`<th ${key ? raw(`data-sort-by="${key}" class="sortable${key === sort ? ' on' : ''}"`) : ''}>${label}${key === sort ? ' ▾' : ''}</th>`);
}
function tableRow(i, group) {
  const href = itemHref(i);
  const thumb = group === 'music' ? i.poster : (i.poster || i.thumb || i.backdrop);
  const added = i.added_at ? new Date(i.added_at * 1000).toLocaleDateString() : '';
  const name = html`<td><a class="tt" href="${href}">${thumb ? html`<img loading="lazy" src="${img(thumb, 92)}" alt="">` : html`<span class="noimg"></span>`}<span>${i.title}</span></a></td>`;
  if (group === 'music') {
    return html`<tr>${name}<td>${i.artist || ''}</td><td>${i.year || ''}</td><td>${(i.genres || []).join(', ')}</td><td>${added}</td></tr>`;
  }
  const length = i.kind === 'show' ? (i.child_count ? `${i.child_count} season${i.child_count === 1 ? '' : 's'}` : '') : (i.runtime ? fmtRuntime(i.runtime) : '');
  return html`<tr>${name}<td>${i.year || ''}</td><td>${length}</td><td>${i.certification || ''}</td>
    <td>${i.rating ? i.rating.toFixed(1) : ''}</td><td class="tg">${(i.genres || []).slice(0, 3).join(', ')}</td><td>${added}</td></tr>`;
}

async function library(id, q) {
  const lib = state.libraries.find(l => l.id === id);
  if (!lib) throw new Error('That library does not exist or is not shared with you.');
  const group = lib.kind === 'music' ? 'music' : lib.kind === 'photos' ? 'photos' : 'video';
  const sort = q.get('sort') || (group === 'photos' ? 'release' : 'added');
  const filter = q.get('filter') || '';
  const genre = q.get('genre') || '';
  const style = lib.kind === 'music' ? 'square' : lib.kind === 'photos' ? 'landscape' : (wideArt() ? 'landscape' : 'poster');
  const mode = group === 'photos' ? 'grid' : (state.prefs?.library_view || 'grid');
  const zoom = Math.min(7, Math.max(1, Number(state.prefs?.library_zoom || 4)));
  const render = (items) => items.map(i => mode === 'detail' ? detailRow(i, group) : mode === 'table' ? tableRow(i, group)
    : style === 'poster' ? posterCard(i, { captioned: true }) : cardFor(i, style));
  const params = new URLSearchParams({ sort, limit: 60 });
  if (filter) params.set('filter', filter);
  if (genre) params.set('genre', genre);
  const [first, genres] = await Promise.all([
    api.get(`/api/libraries/${id}/items?${params}&offset=0`),
    group === 'photos' ? Promise.resolve([]) : api.get(`/api/libraries/${id}/genres`),
  ]);
  const noun = { movies: 'movie', shows: 'show', music: 'album', photos: 'album' }[lib.kind];
  mount(view(), html`<div class="page">
    <div class="page-head">
      <div><h1 class="page-title">${lib.name}</h1>
        <div class="page-sub">${first.total} ${noun}${first.total === 1 ? '' : 's'}${genre ? ` in ${genre}` : ''}</div></div>
      <div class="toolbar">
        ${genres.length ? html`<select class="input" id="genre" aria-label="Genre"><option value="">All genres</option>
          ${genres.map(g => html`<option value="${g.name}" ${g.name === genre ? raw('selected') : ''}>${g.name}</option>`)}</select>` : ''}
        ${group === 'video' ? html`<select class="input" id="filter" aria-label="Watch status">
          ${[['', 'Everything'], ['unwatched', 'Unwatched'], ['in_progress', 'In progress'], ['watched', 'Watched']].map(([v, l]) =>
            html`<option value="${v}" ${v === filter ? raw('selected') : ''}>${l}</option>`)}</select>` : ''}
        <select class="input" id="sort" aria-label="Sort">${SORTS[group].map(([v, l]) =>
          html`<option value="${v}" ${v === sort ? raw('selected') : ''}>${l}</option>`)}</select>
        <div class="lib-view">
          ${mode === 'grid' ? html`<input type="range" class="zoom" id="zoom" min="1" max="7" step="1" value="${zoom}"
            aria-label="Size of the pictures" title="Bigger or smaller pictures (fewer or more per row)">` : ''}
          ${group !== 'photos' ? html`<button class="view-btn" id="view-btn" aria-label="Change the view" title="Grid, detail or table view">
            ${icon(mode === 'table' ? 'rows' : mode === 'detail' ? 'text' : 'grid')}${icon('chevron')}</button>` : ''}
        </div>
      </div>
    </div>
    ${first.total ? '' : html`<div class="empty"><h3>Nothing here${filter || genre ? ' with these filters' : ' yet'}</h3>
      <p>${filter || genre ? 'Try a different filter.' : 'Files appear as soon as the library scan reads them.'}</p></div>`}
    ${mode === 'table' ? html`<div class="table-wrap"><table class="table ltable"><thead><tr>${tableHead(group, sort)}</tr></thead>
        <tbody id="grid">${render(first.items)}</tbody></table></div>`
      : mode === 'detail' ? html`<div class="dlist" id="grid">${render(first.items)}</div>`
      : html`<div class="grid ${style} zoomable" id="grid" style="${gridVars(style, zoom)}">${render(first.items)}</div>`}
    <div class="sentinel" id="sentinel"></div>
  </div>`);
  const update = (key, value) => {
    const p = new URLSearchParams(q);
    value ? p.set(key, value) : p.delete(key);
    location.hash = `#/library/${id}${p.toString() ? '?' + p : ''}`;
  };
  $('#sort').addEventListener('change', e => update('sort', e.target.value));
  $('#filter')?.addEventListener('change', e => update('filter', e.target.value));
  $('#genre')?.addEventListener('change', e => update('genre', e.target.value));
  // Size slider (Plex style): changes at once, remembered for you.
  let saveZoom = 0;
  $('#zoom')?.addEventListener('input', (e) => {
    const z = Number(e.target.value);
    $('#grid').setAttribute('style', gridVars(style, z));
    clearTimeout(saveZoom);
    saveZoom = setTimeout(() => savePref({ library_zoom: String(z) }), 400);
  });
  $('#view-btn')?.addEventListener('click', (e) => popMenu(e.currentTarget, [['grid', 'Grid view'], ['detail', 'Detail view'], ['table', 'Table view']]
    .map(([v, l]) => ({ label: `${l}${v === mode ? '  ✓' : ''}`, action: async () => { if (v !== mode) { await savePref({ library_view: v }); refreshView(); } } }))));
  $$('[data-sort-by]').forEach(th => th.addEventListener('click', () => update('sort', th.dataset.sortBy)));

  let offset = first.items.length, loading = false;
  const grid = $('#grid');
  const io = new IntersectionObserver(async (entries) => {
    if (!entries[0].isIntersecting || loading || offset >= first.total) return;
    loading = true;
    const more = await api.get(`/api/libraries/${id}/items?${params}&offset=${offset}`);
    offset += more.items.length;
    grid.insertAdjacentHTML('beforeend', render(more.items).map(x => x.s).join(''));
    loading = false;
    if (!more.items.length) io.disconnect();
  }, { rootMargin: '800px' });
  io.observe($('#sentinel'));
}

// ---------------------------------------------------------------- details
async function item(id, q) {
  const d = await api.get(`/api/items/${id}`);
  if (d.kind === 'episode' && d.show_id) { location.replace(`#/item/${d.show_id}?season=${d.season}&episode=${d.id}`); return; }
  if (d.kind === 'track' && d.parent) { location.replace(`#/item/${d.parent.id}`); return; }
  if (d.kind === 'photo' && d.parent) { location.replace(`#/item/${d.parent.id}?photo=${d.id}`); return; }
  if (d.kind === 'album') return albumDetail(d);
  if (d.kind === 'photoalbum') return photoAlbumDetail(d, q);
  return videoDetail(d, q);
}

function techLine(f) {
  if (!f) return '';
  const a = f.audio[0];
  const subs = [...new Set(f.subtitles.map(s => lang(s.language)))];
  const bits = [
    [f.resolution, (f.video_codec || '').toUpperCase() + (f.hdr ? ' · HDR' : '')],
    a ? ['Audio', `${f.audio.map(x => lang(x.language)).filter((v, i, s) => s.indexOf(v) === i).join(', ')} · ${channels(a.channels)} ${(a.codec || '').toUpperCase()}`] : null,
    subs.length ? ['Subtitles', subs.join(', ')] : null,
    ['File', `${(f.container || '').toUpperCase()} · ${fmtBytes(f.size)}`],
  ].filter(Boolean);
  return html`<div class="tech">${bits.map(([k, v]) => html`<span><b>${k}</b> ${v}</span>`)}</div>`;
}

function detailChips(d, file) {
  const chips = [];
  if (d.year) chips.push(html`<span class="chip-g">${d.year}</span>`);
  if (d.certification) chips.push(html`<span class="chip-g">${d.certification}</span>`);
  if (d.extra?.edition) chips.push(html`<span class="chip-g score">${d.extra.edition}</span>`);
  if (d.kind === 'show' && d.seasons) {
    const n = d.seasons.filter(s => s.season !== 0).length;
    chips.push(html`<span class="chip-g">${n} season${n === 1 ? '' : 's'}</span>`);
  } else if (d.runtime) chips.push(html`<span class="chip-g">${fmtRuntime(d.runtime)}</span>`);
  if (d.rating) chips.push(html`<span class="chip-g score" title="TMDB rating">★ ${d.rating.toFixed(1)}</span>`);
  if (file?.resolution) chips.push(html`<span class="chip-g">${file.resolution}${file.hdr ? ' · HDR' : ''}</span>`);
  if (d.progress?.completed) chips.push(html`<span class="chip-g good">Watched</span>`);
  if (d.kind === 'show' && d.unwatched) chips.push(html`<span class="chip-g score">${d.unwatched} unwatched</span>`);
  for (const g of (d.genres || []).slice(0, 4))
    chips.push(html`<a class="chip-g" href="#/library/${d.library_id}?genre=${encodeURIComponent(g)}">${g}</a>`);
  return html`<div class="chips">${chips}</div>`;
}

// ---- About panel: TMDB facts at once, then Wikipedia / OMDb / collection from /about
const money = (n) => n ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(n) : '';
const longDate = (s) => {
  if (!s) return '';
  const dt = new Date(`${s}T00:00:00`);
  return isNaN(dt) ? s : dt.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
};
const personLinks = (names) => (names || []).length
  ? html`${names.map((n, i) => html`${i ? ', ' : ''}<a href="#/person?name=${encodeURIComponent(n)}">${n}</a>`)}` : '';

const regionName = (c) => {
  if (!/^[A-Z]{2}$/.test(c)) return c;
  try { return new Intl.DisplayNames(['en'], { type: 'region' }).of(c) || c; } catch { return c; }
};

function aboutFacts(d, ex, people, isShow) {
  const langs = (ex.languages || []).filter(l => l !== ex.original_language);
  const counts = isShow && ex.seasons_count
    ? `${ex.seasons_count} season${ex.seasons_count === 1 ? '' : 's'}${ex.episodes_count ? ` · ${ex.episodes_count} episodes` : ''}` : '';
  return [
    [isShow ? 'Created by' : 'Directed by', personLinks(people)],
    ['Written by', personLinks(ex.writers)],
    ['Producers', personLinks(ex.producers)],
    ['Music', personLinks(ex.composer)],
    ['Cinematography', personLinks(ex.cinematography)],
    ['Editing', personLinks(ex.editors)],
    [isShow ? 'Network' : 'Studio', (ex.studios || []).join(', ')],
    ['Production', isShow ? (ex.companies || []).join(', ') : ''],
    ['Country', (ex.countries || []).map(regionName).join(', ')],
    ['Language', ex.original_language ? `${ex.original_language}${langs.length ? ` (also ${langs.join(', ')})` : ''}` : ''],
    ['Status', isShow ? [ex.status, ex.show_type && ex.show_type !== 'Scripted' ? ex.show_type : ''].filter(Boolean).join(' · ') : ''],
    ['Episodes', counts],
    ['Original title', d.original_title && d.original_title !== d.title ? d.original_title : ''],
    [isShow ? 'First aired' : 'Released', longDate(d.release_date)],
    ['Last aired', isShow && ex.status !== 'Returning Series' ? longDate(ex.last_air) : ''],
    ['Next episode', isShow ? longDate(ex.next_air) : ''],
    ['Budget', money(ex.budget)],
    ['Box office', money(ex.revenue)],
  ].filter(([, v]) => v);
}

const aboutBody = (facts) => facts.length ? html`<dl class="facts">${facts.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>` : '';
const keywordChips = (ex) => ex.keywords?.length ? html`<div class="keywords">${ex.keywords.map(k => html`<span>${k}</span>`)}</div>` : '';

function ratingBadge(source, value, cls, title) {
  return html`<span class="rating ${cls}" title="${title || source}"><b>${source}</b>${value}</span>`;
}

function externalLinks(d, ex, wiki) {
  const links = [];
  if (ex.imdb_id) links.push(['IMDb', `https://www.imdb.com/title/${ex.imdb_id}/`]);
  if (d.tmdb_id) links.push(['TMDB', `https://www.themoviedb.org/${d.tmdb_kind === 'tv' ? 'tv' : 'movie'}/${d.tmdb_id}`]);
  if (wiki?.url) links.push(['Wikipedia', wiki.url]);
  if (ex.homepage) links.push(['Official site', ex.homepage]);
  return links.map(([n, u]) => html`<a href="${u}" target="_blank" rel="noopener noreferrer">${n} ${icon('external')}</a>`);
}

async function loadAbout(d, ex) {
  let a;
  try { a = await api.get(`/api/items/${d.id}/about`); } catch { return; }
  const box = $('#about-ratings');
  if (!box || !box.isConnected) return;
  if (a.details) {                       // fetched just now for a title the background refresh hadn't reached
    Object.assign(ex, a.details);
    const isShow = d.kind === 'show';
    mount($('#about-facts'), aboutBody(aboutFacts(d, ex, isShow ? ex.creators : ex.directors, isShow)));
    mount($('#about-keywords'), keywordChips(ex));
    mount($('#about-links'), externalLinks(d, ex, null));
  }
  const o = a.omdb;
  if (o) {
    const badges = [];
    if (o.imdb_rating && !ex.imdb_rating) badges.push(ratingBadge('IMDb', o.imdb_rating, 'imdb', o.imdb_votes ? `IMDb · ${o.imdb_votes.toLocaleString()} votes` : 'IMDb'));
    if (o.rotten_tomatoes) badges.push(ratingBadge('Rotten Tomatoes', o.rotten_tomatoes, `rt ${parseInt(o.rotten_tomatoes, 10) >= 60 ? 'fresh' : 'rotten'}`, 'Rotten Tomatoes (critics)'));
    if (o.metacritic) badges.push(ratingBadge('Metacritic', o.metacritic.replace('/100', ''), 'mc', 'Metacritic'));
    box.insertAdjacentHTML('beforeend', badges.map(String).join(''));
    if (o.awards) mount($('#about-awards'), html`<p class="awards">${icon('award')}${o.awards}</p>`);
    if (o.box_office && !ex.revenue && d.kind === 'movie') {
      const dl = view().querySelector('.d-info .facts');
      dl?.insertAdjacentHTML('beforeend', String(html`<dt>Box office</dt><dd>${o.box_office} <span style="opacity:.6">(US)</span></dd>`));
    }
  }
  if (a.wiki) {
    mount($('#about-wiki'), html`<blockquote class="wiki">
      <p class="clamp-5" title="Show all">${a.wiki.extract}</p>
      <footer>From Wikipedia${a.wiki.lang !== 'en' ? ` (${a.wiki.lang})` : ''} · <a href="${a.wiki.url}" target="_blank" rel="noopener noreferrer">Read the article</a> · CC BY-SA</footer></blockquote>`);
    const p = $('#about-wiki p');
    p.addEventListener('click', () => p.classList.toggle('clamp-5'));
    mount($('#about-links'), externalLinks(d, ex, a.wiki));
  }
  if (a.collection?.items?.length) {
    mount($('#about-collection'), html`<div style="margin-top:34px">${row({ title: `Part of ${a.collection.name}`, items: a.collection.items })}</div>`);
  } else if (a.collection) {
    mount($('#about-collection'), html`<p class="coll-note">Part of ${a.collection.name}</p>`);
  }
}

function videoDetail(d, q) {
  const art = d.backdrop || d.thumb;
  const file = d.files?.[0];
  const play = d.play;
  const isShow = d.kind === 'show';
  const ex = d.extra || {};
  const cast = ex.cast || [];
  const people = isShow ? ex.creators : ex.directors;
  const facts = aboutFacts(d, ex, people, isShow);
  const seasons = d.seasons || [];
  const wanted = Number(q.get('season') ?? NaN);
  let season = seasons.find(s => s.season === wanted) || seasons.find(s => s.id === d.selected_season)
    || seasons.find(s => play && s.season === play.season) || seasons.find(s => s.season !== 0) || seasons[0];
  const p = d.progress;
  const resumePct = p && !p.completed && p.duration ? Math.min(100, p.position / p.duration * 100) : 0;

  mount(view(), html`${stageHtml(art)}
    <article class="detail">
      <section class="d-hero">
        <div class="d-head">
          ${d.logo ? html`<img class="d-logo" src="${img(d.logo, 780)}" alt="${d.title}">` : html`<h1 class="d-title">${d.title}</h1>`}
          ${d.tagline ? html`<p class="d-tagline">${d.tagline}</p>` : ''}
          ${detailChips(d, file)}
          ${resumePct ? html`<div class="d-progress"><span class="bar"><i style="width:${resumePct.toFixed(1)}%"></i></span>
            ${fmtTime(p.duration - p.position)} left</div>` : ''}
          ${d.overview ? html`<p class="d-overview clamp" title="Show the full description">${d.overview}</p>`
            : (d.matched ? '' : html`<p class="d-overview" style="opacity:.6">No description yet.${state.user.is_admin ? ' Add a TMDB key in Server admin › Settings or fix the match.' : ''}</p>`)}
          <div class="actions d-actions">
            ${play ? html`<a class="btn primary" id="play-btn" href="#/play/${play.item_id}">${icon('play')}${playLabel(play)}</a>` : ''}
            ${play?.action === 'resume' ? html`<a class="btn glass-btn" href="#/play/${play.item_id}?restart=1" title="From the start">${icon('restart')}<span class="lbl">From the start</span></a>` : ''}
            <button class="btn glass-btn" id="trailer-btn" title="Trailer" hidden>${icon('film')}<span class="lbl">Trailer</span></button>
            <button class="btn glass-btn ${d.in_watchlist ? 'on' : ''}" id="list-btn" title="My list">${icon(d.in_watchlist ? 'check' : 'plus')}<span class="lbl">My list</span></button>
            ${file ? html`<button class="btn glass-btn icon" id="subs-btn" aria-label="Subtitles" title="Subtitles">${icon('subs')}</button>` : ''}
            <button class="btn glass-btn icon" id="more-btn" aria-label="More actions">${raw('<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>')}</button>
          </div>
        </div>
        <div class="d-aside" id="d-aside"></div>
      </section>

      ${isShow && season ? html`<section class="glass d-panel" id="episodes-section">
        <div class="d-panel-head">
          ${seasons.length > 1 && seasons.length <= 12
            ? html`<div class="seg" role="tablist">${seasons.map(s => html`<button type="button" role="tab" data-season="${s.season}" class="${s === season ? 'on' : ''}">${s.title}</button>`)}</div>`
            : seasons.length > 1 ? html`<select class="input" id="season" aria-label="Season" style="width:auto">${seasons.map(s =>
                html`<option value="${s.season}" ${s === season ? raw('selected') : ''}>${s.title} (${s.child_count})</option>`)}</select>`
            : html`<h2>${season.title}</h2>`}
          <button class="btn small glass-btn" id="season-watched"></button>
        </div>
        <div class="episodes" id="episodes"></div></section>` : ''}

      ${cast.length ? html`<section class="glass d-panel"><h2>Cast</h2><div class="cast-row">${cast.map(c => html`
        <a class="face" href="#/person?name=${encodeURIComponent(c.name)}">
          ${c.profile ? html`<img loading="lazy" src="${img(c.profile, 185)}" alt="">` : html`<span class="ph">${c.name[0]}</span>`}
          <span class="n">${c.name}</span><span class="c">${c.character || ''}</span></a>`)}</div></section>` : ''}

      <section class="glass d-panel d-info">
        <div class="card poster"><div class="frame">${d.poster ? html`<img loading="lazy" src="${img(d.poster, 342)}" alt="">` : genPoster(d)}</div></div>
        <div>
          <div class="about-head"><h2>About</h2><div class="ratings" id="about-ratings">${d.rating ? ratingBadge('TMDB', d.rating.toFixed(1), 'tmdb') : ''}${ex.imdb_rating
            ? ratingBadge('IMDb', Number(ex.imdb_rating).toFixed(1), 'imdb', ex.imdb_votes ? `IMDb · ${Number(ex.imdb_votes).toLocaleString()} votes` : 'IMDb') : ''}</div></div>
          <div id="about-facts">${aboutBody(facts, ex)}</div>
          <div id="about-awards"></div>
          <div id="about-keywords">${keywordChips(ex)}</div>
          <div id="about-wiki"></div>
          <div class="ext-links" id="about-links">${externalLinks(d, ex, null)}</div>
          ${d.files?.length > 1 ? html`<div class="field" style="max-width:460px;margin:18px 0 0"><label for="version">Version</label>
            <select class="input" id="version">${d.files.map(f => html`<option value="${f.id}">${f.resolution} ${(f.video_codec || '').toUpperCase()} · ${fmtBytes(f.size)} · ${f.filename}</option>`)}</select></div>` : ''}
          ${techLine(file)}
        </div>
      </section>
      <div id="about-collection"></div>
      ${d.similar?.length ? html`<div style="margin-top:34px">${row({ title: 'More like this', items: d.similar })}</div>` : ''}
    </article>`);
  wireStage();
  if (d.kind === 'movie' || isShow) loadAbout(d, ex);

  const ov = view().querySelector('.d-overview.clamp');
  ov?.addEventListener('click', () => ov.classList.toggle('clamp'));
  const playBtn = $('#play-btn');
  let chosenFile = file;
  $('#version')?.addEventListener('change', (e) => {
    chosenFile = d.files.find(f => String(f.id) === e.target.value) || file;
    if (playBtn) playBtn.href = `#/play/${play.item_id}?file=${e.target.value}`;
  });
  $('#list-btn').addEventListener('click', async (e) => {
    const btn = e.currentTarget, on = !btn.classList.contains('on');
    await api.post(`/api/items/${d.id}/watchlist`, { on });
    btn.classList.toggle('on', on);
    mount(btn, html`${icon(on ? 'check' : 'plus')}<span class="lbl">My list</span>`);
    toast(on ? `Added ${d.title} to My list` : `Removed ${d.title} from My list`);
  });
  $('#subs-btn')?.addEventListener('click', () => openSubtitleDialog(d, chosenFile));
  $('#more-btn').addEventListener('click', (e) => {
    const watched = isShow ? d.unwatched === 0 : d.progress?.completed;
    const items = [{
      label: watched ? 'Mark as unwatched' : (isShow ? 'Mark every episode watched' : 'Mark as watched'),
      action: async () => { await api.post(`/api/items/${d.id}/watched`, { watched: !watched }); refreshView(); },
    }];
    if (file) items.push({ label: 'Download original file', action: () => { location.href = `/api/stream/file/${chosenFile.id}?download=1`; } });
    if (state.user.is_admin) items.push(...adminItemMenu(d));
    popMenu(e.currentTarget, items);
  });

  if (isShow && season) {
    const renderEpisodes = () => {
      mount($('#episodes'), season.episodes.map(ep => {
        const pr = ep.progress;
        const pct = pr && !pr.completed && pr.duration ? Math.min(100, pr.position / pr.duration * 100) : 0;
        return html`<div class="episode" tabindex="0" role="link" data-play="${ep.id}" id="ep-${ep.id}">
          <div class="num">${ep.index ?? '·'}</div>
          <div class="frame">${ep.thumb ? html`<img loading="lazy" src="${img(ep.thumb, 500)}" alt="">` : genPoster({ title: ep.title })}
            ${pct ? html`<div class="progress"><i style="width:${pct.toFixed(1)}%"></i></div>` : ''}</div>
          <div><h4>${ep.title}${pr?.completed ? html`<span class="watched" title="Watched">${icon('check')}</span>` : ''}</h4>
            ${ep.overview ? html`<p class="clamp-3">${ep.overview}</p>` : ''}</div>
          <div class="rt">${ep.runtime ? `${ep.runtime}m` : ''}</div></div>`;
      }));
      $('#season-watched').textContent = season.unwatched ? 'Mark season watched' : 'Mark season unwatched';
    };
    const pickSeason = (num) => {
      season = seasons.find(s => String(s.season) === String(num)) || season;
      history.replaceState(null, '', `#/item/${d.id}?season=${season.season}`);
      view().querySelectorAll('[data-season]').forEach(b => b.classList.toggle('on', b.dataset.season === String(season.season)));
      renderEpisodes();
    };
    renderEpisodes();
    view().querySelectorAll('[data-season]').forEach(b => b.addEventListener('click', () => pickSeason(b.dataset.season)));
    $('#season')?.addEventListener('change', (e) => pickSeason(e.target.value));
    $('#season-watched').addEventListener('click', async () => {
      await api.post(`/api/items/${season.id}/watched`, { watched: season.unwatched > 0 });
      refreshView();
    });
    $('#episodes').addEventListener('click', (e) => {
      const ep = e.target.closest('[data-play]');
      if (ep) location.hash = `#/play/${ep.dataset.play}`;
    });
    $('#episodes').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.dataset.play) location.hash = `#/play/${e.target.dataset.play}`;
    });
    const target = q.get('episode') && $(`#ep-${q.get('episode')}`);
    if (target) setTimeout(() => target.scrollIntoView({ block: 'center' }), 50);
  }

  startExtras(d, {
    onTrailer: (t) => {
      const b = $('#trailer-btn');
      if (!b) return;
      b.hidden = false;
      b.onclick = () => openTrailer(t.key);
    },
  });
}

function albumDetail(d) {
  const total = d.tracks.reduce((s, t) => s + (t.duration || 0), 0);
  const multiDisc = new Set(d.tracks.map(t => t.disc)).size > 1;
  const queue = d.tracks.map(t => ({ id: t.id, title: t.title, artist: t.artist || d.artist, album: d.title, cover: d.poster, duration: t.duration, albumId: d.id }));
  mount(view(), html`${stageHtml(d.poster, { soft: true })}
    <article class="detail">
      <section class="d-hero" style="min-height:auto;padding-top:clamp(60px,12vh,140px);grid-template-columns:auto minmax(0,1fr);align-items:end">
        <div class="card square" style="width:clamp(170px,20vw,280px)"><div class="frame" style="box-shadow:0 24px 60px rgb(var(--deep-rgb) / .55)">
          ${d.poster ? html`<img src="${img(d.poster, 500)}" alt="">` : genPoster(d)}</div></div>
        <div class="d-head">
          <h1 class="d-title" style="font-size:clamp(40px,6vw,84px)">${d.title}</h1>
          <div class="chips"><span class="chip-g score">${d.artist}</span>${d.year ? html`<span class="chip-g">${d.year}</span>` : ''}
            <span class="chip-g">${d.tracks.length} track${d.tracks.length === 1 ? '' : 's'} · ${fmtTime(total)}</span>
            ${(d.genres || []).map(g => html`<span class="chip-g">${g}</span>`)}</div>
          <div class="actions" style="margin-top:8px">
            <button class="btn primary" id="play-album">${icon('play')}Play</button>
            <button class="btn glass-btn" id="shuffle-album">${icon('shuffle')}Shuffle</button>
          </div>
        </div>
      </section>
      <section class="glass d-panel"><table class="tracks"><tbody>${d.tracks.map((t, i) => html`
        ${multiDisc && (i === 0 || d.tracks[i - 1].disc !== t.disc) ? html`<tr><td colspan="3" style="color:var(--muted);font-weight:600;cursor:default">Disc ${t.disc}</td></tr>` : ''}
        <tr data-i="${i}" class="${currentTrackId() === t.id ? 'playing' : ''}" tabindex="0">
          <td class="no">${t.index ?? i + 1}</td>
          <td>${t.title}${t.artist && t.artist !== d.artist ? html`<div style="font-size:13px;color:var(--muted)">${t.artist}</div>` : ''}</td>
          <td class="dur">${fmtTime(t.duration)}</td></tr>`)}</tbody></table></section>
      ${d.more_by_artist?.length ? html`<div style="margin-top:34px">${row({ title: `More by ${d.artist}`, items: d.more_by_artist })}</div>` : ''}
    </article>`);
  wireStage();
  $('#play-album').addEventListener('click', () => playTracks(queue, 0));
  $('#shuffle-album').addEventListener('click', () => playTracks([...queue].sort(() => Math.random() - .5), 0));
  view().querySelector('.tracks').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-i]');
    if (tr) playTracks(queue, Number(tr.dataset.i));
  });
}

function photoAlbumDetail(d, q) {
  const cover = d.photos[0]?.full;
  mount(view(), html`${cover ? html`<div class="stage soft" id="stage" aria-hidden="true">
      <div class="stage-art" style="background-image:url('${cover}')"></div><div class="stage-shade"></div><div class="stage-dim"></div></div>` : ''}
    <article class="detail"><div class="page">
      <div class="page-head"><div><h1 class="page-title">${d.title}</h1>
        <div class="page-sub">${d.photos.length} photo${d.photos.length === 1 ? '' : 's'}${d.year ? ` · ${d.year}` : ''}</div></div></div>
      <div class="photo-grid">${d.photos.map((p, i) => html`<a href="#" data-i="${i}" aria-label="${p.title}">
        <img loading="lazy" src="${p.thumb}?w=500" alt="" ${p.width && p.height ? raw(`width="${p.width}" height="${p.height}"`) : ''}></a>`)}</div></div></article>`);
  wireStage();
  view().querySelector('.photo-grid').addEventListener('click', (e) => {
    const a = e.target.closest('a[data-i]');
    if (!a) return;
    e.preventDefault();
    lightbox(d.photos, Number(a.dataset.i));
  });
  if (q.get('photo')) {
    const open = d.photos.findIndex(p => String(p.id) === q.get('photo'));
    if (open >= 0) lightbox(d.photos, open);
  }
}

function lightbox(photos, index) {
  const el = document.createElement('div');
  el.className = 'lightbox';
  el.setAttribute('role', 'dialog');
  document.body.append(el);
  const show = () => {
    const p = photos[index];
    mount(el, html`<div class="lb-top"><span>${index + 1} / ${photos.length}</span>
        <button class="pbtn" data-close aria-label="Close">${icon('close')}</button></div>
      <div class="lb-stage"><img src="${p.full}" alt="${p.title}">
        ${index > 0 ? html`<button class="pbtn big nav prev" data-step="-1" aria-label="Previous">${icon('back')}</button>` : ''}
        ${index < photos.length - 1 ? html`<button class="pbtn big nav next" data-step="1" aria-label="Next">${icon('next')}</button>` : ''}</div>
      <div class="lb-bottom">${p.title}${p.taken ? ` · ${p.taken.slice(0, 16)}` : ''}${p.camera ? ` · ${p.camera}` : ''}</div>`);
    if (photos[index + 1]) new Image().src = photos[index + 1].full;
  };
  const close = () => { el.remove(); document.removeEventListener('keydown', onKey); window.removeEventListener('hashchange', close); };
  window.addEventListener('hashchange', close);
  const step = (n) => { const next = index + n; if (next >= 0 && next < photos.length) { index = next; show(); } };
  const onKey = (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowRight') step(1);
    if (e.key === 'ArrowLeft') step(-1);
  };
  el.addEventListener('click', (e) => {
    if (e.target.closest('[data-close]')) close();
    const s = e.target.closest('[data-step]');
    if (s) step(Number(s.dataset.step));
  });
  document.addEventListener('keydown', onKey);
  // touch: swipe sideways for the next or previous photo, swipe down to close
  let x0 = null, y0 = 0;
  el.addEventListener('touchstart', (e) => { if (e.touches.length === 1) { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; } }, { passive: true });
  el.addEventListener('touchend', (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.4) step(dx < 0 ? 1 : -1);
    else if (dy > 90 && dy > Math.abs(dx) * 1.4) close();
  });
  show();
}

// ---------------------------------------------------------------- search
async function search(q) {
  const query = q.get('q') || '';
  mount(view(), html`<div class="page">
    <input class="search-input" id="sq" placeholder="Titles, people, songs" value="${query}" autocomplete="off" aria-label="Search">
    <div id="results" style="margin-top:34px"></div></div>`);
  const input = $('#sq');
  input.focus();
  input.setSelectionRange(query.length, query.length);
  let timer, seq = 0;
  const run = async () => {
    const term = input.value.trim();
    history.replaceState(null, '', term ? `#/search?q=${encodeURIComponent(term)}` : '#/search');
    const out = $('#results');
    if (term.length < 2) { mount(out, ''); return; }
    const mine = ++seq;
    const r = await api.get(`/api/search?q=${encodeURIComponent(term)}`);
    if (mine !== seq) return;
    const video = r.titles.filter(t => t.kind === 'movie' || t.kind === 'show');
    const albums = r.titles.filter(t => t.kind === 'album');
    const photoAlbums = r.titles.filter(t => t.kind === 'photoalbum');
    const nothing = !r.titles.length && !r.episodes.length && !r.tracks.length && !r.people.length;
    mount(out, html`
      ${nothing ? html`<div class="empty"><h3>Nothing matches "${term}"</h3><p>Check the spelling, or search for an actor, an episode or a song.</p></div>` : ''}
      ${video.length ? html`<h2 class="section-title" style="margin:0 0 14px">Movies and shows</h2><div class="grid ${wideArt() ? 'landscape' : 'poster'}" style="margin-bottom:40px">${video.map(i => posterCard(i, { captioned: true }))}</div>` : ''}
      ${r.people.length ? html`<h2 class="section-title" style="margin:0 0 14px">People</h2><div class="people-list" style="margin-bottom:40px">${r.people.map(p => html`
        <a class="person" href="#/person?name=${encodeURIComponent(p.name)}">${p.profile ? html`<img src="${img(p.profile, 185)}" alt="">` : html`<span class="ph">${p.name[0]}</span>`}
        <span><div class="n">${p.name}</div><div class="c">${p.count} title${p.count === 1 ? '' : 's'}</div></span></a>`)}</div>` : ''}
      ${r.episodes.length ? html`<h2 class="section-title" style="margin:0 0 14px">Episodes</h2><div class="grid landscape" style="margin-bottom:40px">${r.episodes.map(e => landscapeCard(e))}</div>` : ''}
      ${albums.length ? html`<h2 class="section-title" style="margin:0 0 14px">Albums</h2><div class="grid square" style="margin-bottom:40px">${albums.map(squareCard)}</div>` : ''}
      ${r.tracks.length ? html`<h2 class="section-title" style="margin:0 0 14px">Songs</h2><table class="tracks" style="margin-bottom:40px"><tbody>${r.tracks.map((t, i) => html`
        <tr data-t="${i}"><td class="no">${icon('play')}</td><td>${t.title}<div style="font-size:13px;color:var(--muted)">${t.artist} · ${t.album_title}</div></td><td class="dur"></td></tr>`)}</tbody></table>` : ''}
      ${photoAlbums.length ? html`<h2 class="section-title" style="margin:0 0 14px">Photo albums</h2><div class="grid landscape">${photoAlbums.map(a => landscapeCard(a))}</div>` : ''}`);
    out.querySelector('.tracks')?.addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-t]');
      if (!tr) return;
      const queue = r.tracks.map(t => ({ id: t.id, title: t.title, artist: t.artist, album: t.album_title, cover: null, albumId: t.parent_id }));
      playTracks(queue, Number(tr.dataset.t));
    });
  };
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 250); });
  if (query) run();
}

async function person(name) {
  const d = await api.get(`/api/people?name=${encodeURIComponent(name || '')}`);
  mount(view(), html`<div class="page">
    <div class="page-head"><div style="display:flex;gap:22px;align-items:center">
      ${d.profile ? html`<img src="${img(d.profile, 185)}" alt="" style="width:96px;height:96px;border-radius:50%;object-fit:cover">` : ''}
      <div><h1 class="page-title">${d.name}</h1><div class="page-sub">${d.items.length} title${d.items.length === 1 ? '' : 's'} on this server</div></div></div></div>
    <div class="grid ${wideArt() ? 'landscape' : 'poster'}">${d.items.map(i => posterCard(i, { captioned: true, sub: [i.character ? `as ${i.character}` : '', ...(i.roles || [])].filter(Boolean).join(' · ') || i.year }))}</div></div>`);
}

// My list: the same pictures as on Home, but static (no previews or trailers on hover) and editable:
// a round select button shows on a picture as you point at it; pick one or several, then remove them.
async function mylist() {
  const items = await api.get('/api/watchlist');
  const count = (n) => `${n} saved`;
  mount(view(), html`<div class="page mylist-page">
    <div class="page-head"><div><h1 class="page-title">My list</h1><div class="page-sub" id="ml-count">${count(items.length)}</div></div></div>
    ${items.length ? html`<div class="grid ${wideArt() ? 'landscape' : 'poster'} no-preview" id="ml-grid">${items.map(i => i.kind === 'album' ? squareCard(i) : posterCard(i, { captioned: true }))}</div>`
      : html`<div class="empty"><h3>Your list is empty</h3><p>Use the My list button on any movie or show to keep it here for later.</p></div>`}
    <div class="ml-bar" id="ml-bar" hidden>
      <span class="ml-n" id="ml-n"></span>
      <button type="button" class="btn ghost small" id="ml-all">Select all</button>
      <button type="button" class="btn ghost small" id="ml-clear">Clear</button>
      <button type="button" class="btn primary small" id="ml-remove">${icon('trash')}Remove from My list</button>
    </div></div>`);
  const grid = $('#ml-grid');
  if (!grid) return;
  const page = grid.closest('.mylist-page');
  const titleOf = (c) => items.find(i => String(i.id) === c.dataset.id)?.title || '';
  grid.querySelectorAll('.card[data-id]').forEach((c) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ml-check';
    b.setAttribute('aria-pressed', 'false');
    b.setAttribute('aria-label', `Select ${titleOf(c)}`);
    b.title = 'Select';
    mount(b, icon('check'));
    c.append(b);
  });
  const selected = () => [...grid.querySelectorAll('.card.selected')];
  const update = () => {
    const n = selected().length;
    page.classList.toggle('selecting', n > 0);
    $('#ml-bar').hidden = n === 0;
    $('#ml-n').textContent = `${n} selected`;
  };
  const toggle = (c, on = !c.classList.contains('selected')) => {
    c.classList.toggle('selected', on);
    const b = c.querySelector('.ml-check');
    b.setAttribute('aria-pressed', String(on));
    b.title = on ? 'Selected' : 'Select';
    update();
  };
  // the select button always selects; while something is selected, a click on a picture selects it too
  grid.addEventListener('click', (e) => {
    const c = e.target.closest('.card[data-id]');
    if (!c) return;
    if (e.target.closest('.ml-check') || (page.classList.contains('selecting') && !e.target.closest('.card-tools'))) {
      e.preventDefault(); e.stopPropagation();
      toggle(c);
    }
  }, true);
  $('#ml-all').addEventListener('click', () => grid.querySelectorAll('.card[data-id]').forEach(c => toggle(c, true)));
  $('#ml-clear').addEventListener('click', () => selected().forEach(c => toggle(c, false)));
  const onKey = (e) => { if (e.key === 'Escape' && selected().length) selected().forEach(c => toggle(c, false)); };
  document.addEventListener('keydown', onKey);
  onCleanup(() => document.removeEventListener('keydown', onKey));
  $('#ml-remove').addEventListener('click', async () => {
    const cards = selected();
    if (!cards.length) return;
    const btn = $('#ml-remove');
    btn.disabled = true;
    const done = [];
    await Promise.all(cards.map(c => api.post(`/api/items/${c.dataset.id}/watchlist`, { on: false })
      .then(() => done.push(c)).catch(() => {})));
    btn.disabled = false;
    done.forEach(c => { c.classList.add('ml-gone'); setTimeout(() => c.remove(), 250); });
    setTimeout(() => {
      const left = grid.querySelectorAll('.card[data-id]').length;
      $('#ml-count').textContent = count(left);
      update();
      if (!left) refreshView();
    }, 260);
    if (done.length) toast(done.length === 1 ? `Removed ${titleOf(done[0])} from My list` : `Removed ${done.length} titles from My list`);
    if (done.length < cards.length) toast(`${cards.length - done.length} couldn't be removed. Try again.`, 'error');
  });
}

export const views = { home, library, item, search, person, mylist };
