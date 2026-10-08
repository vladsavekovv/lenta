// Rendering helpers: escaped templates, icons, formatting, cards, rows, modals, toasts.

export class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(s);

export function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function renderVal(v) {
  if (v === null || v === undefined || v === false) return '';
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(renderVal).join('');
  return esc(v);
}
export function html(strings, ...vals) {
  let out = '';
  strings.forEach((s, i) => { out += s; if (i < vals.length) out += renderVal(vals[i]); });
  return new Raw(out);
}
export function mount(el, content) { el.innerHTML = renderVal(content); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---- icons (24px stroke) ----
const P = {
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  movies: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  shows: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M8 2l4 4 4-4"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  photos: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
  list: '<path d="M5 3h14v18l-7-5-7 5z"/>',
  admin: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  play: '<path d="M7 4l13 8-13 8z" fill="currentColor"/>',
  pause: '<rect x="6" y="4" width="4" height="16" fill="currentColor"/><rect x="14" y="4" width="4" height="16" fill="currentColor"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  award: '<circle cx="12" cy="9" r="5"/><path d="M8.5 13 7 21l5-3 5 3-1.5-8"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  next: '<path d="M9 5l7 7-7 7"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  rew: '<path d="M11 6L4 12l7 6zM20 6l-7 6 7 6z" fill="currentColor"/>',
  replay10: '<path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 4v4h4"/><text x="12" y="15.5" font-size="7" text-anchor="middle" fill="currentColor" stroke="none" font-family="sans-serif" font-weight="700">10</text>',
  fwd10: '<path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 4v4h-4"/><text x="12" y="15.5" font-size="7" text-anchor="middle" fill="currentColor" stroke="none" font-family="sans-serif" font-weight="700">10</text>',
  skipnext: '<path d="M5 5l10 7-10 7z" fill="currentColor"/><path d="M19 5v14"/>',
  skipprev: '<path d="M19 5L9 12l10 7z" fill="currentColor"/><path d="M5 5v14"/>',
  volume: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  mute: '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9l5 6M22 9l-5 6"/>',
  fullscreen: '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
  exitfs: '<path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/>',
  subs: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 13h4M13 13h4M7 16h10"/>',
  gear: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  stats: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  shuffle: '<path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>',
  folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  restart: '<path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 4v4h4"/>',
  film: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.5v5l4.5-2.5z" fill="currentColor"/>',
  note: '<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M5.5 8h1M5.5 11h1"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  unlock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
  dots: '<circle cx="12" cy="5" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1.6" fill="currentColor" stroke="none"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  text: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  'chevron-down': '<path d="M6 9l6 6 6-6"/>',
  'chevron-up': '<path d="M6 15l6-6 6 6"/>',
  grip: '<circle cx="9" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.4" fill="currentColor" stroke="none"/>',
  expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  grid: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
  rows: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  cloud: '<path d="M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 8.5a4 4 0 0 1-.5 9.5z"/><path d="M12 11v6M9.5 14.5 12 17l2.5-2.5"/>',
};
export const icon = (name) => raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ''}</svg>`);
export const KIND_ICON = { movies: 'movies', shows: 'shows', music: 'music', photos: 'photos' };

// ---- formatting ----
export function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}
export function fmtRuntime(min) {
  if (!min) return '';
  const h = Math.floor(min / 60), m = min % 60;
  return h ? `${h}h ${m}m` : `${m}m`;
}
export function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i > 2 ? 1 : 0)} ${u[i]}`;
}
export function fmtAgo(ts) {
  if (!ts) return 'never';
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}
export const LANGS = { eng: 'English', en: 'English', bul: 'Bulgarian', bg: 'Bulgarian', rus: 'Russian', ru: 'Russian', ger: 'German', deu: 'German', de: 'German',
  fre: 'French', fra: 'French', fr: 'French', spa: 'Spanish', es: 'Spanish', ita: 'Italian', it: 'Italian', jpn: 'Japanese', ja: 'Japanese',
  kor: 'Korean', ko: 'Korean', chi: 'Chinese', zho: 'Chinese', zh: 'Chinese', por: 'Portuguese', pt: 'Portuguese', pol: 'Polish', pl: 'Polish',
  gre: 'Greek', ell: 'Greek', el: 'Greek', tur: 'Turkish', tr: 'Turkish', rum: 'Romanian', ron: 'Romanian', ro: 'Romanian', srp: 'Serbian', sr: 'Serbian',
  ukr: 'Ukrainian', uk: 'Ukrainian', dut: 'Dutch', nld: 'Dutch', nl: 'Dutch', swe: 'Swedish', sv: 'Swedish', hin: 'Hindi', ara: 'Arabic', heb: 'Hebrew', und: 'Unknown' };
export const lang = (code) => LANGS[(code || 'und').toLowerCase()] || (code || 'Unknown').toUpperCase();
export function channels(n) { return n === 1 ? 'Mono' : n === 2 ? 'Stereo' : n === 6 ? '5.1' : n === 8 ? '7.1' : n ? `${n} ch` : ''; }

export function img(url, w) {
  if (!url) return null;
  if (!w) return url;
  return url + (url.includes('?') ? '&' : '?') + `w=${w}`;
}

// Generated posters for titles without artwork: the hue comes from the active theme (CSS
// --poster-hue), shifted a little per title so neighbouring cards differ.
function hueShift(s) {
  let h = 0;
  for (const c of s || '') h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return (h % 7) * 9 - 27;
}
export function genPoster(item) {
  return html`<div class="genposter" style="--h:${hueShift(item.title)}"><div class="bar"></div><div class="gt">${item.title}</div>${item.year ? html`<div class="gy">${item.year}</div>` : ''}</div>`;
}

// ---- cards ----
// Admins get Plex-style tools on every card: a pencil (edit metadata) and a ⋮ menu. editor.js
// turns this on at sign-in and handles the clicks.
let cardTools = false;
export function setCardTools(on) { cardTools = on; }
const TOOL_KINDS = new Set(['movie', 'show', 'season', 'episode', 'album', 'photoalbum']);
const MENU_KINDS = new Set(['movie', 'show', 'season', 'episode']);
function tools(item) {
  if (!cardTools || !TOOL_KINDS.has(item.kind)) return '';
  return html`<span class="card-tools">
    <span class="ct-btn ct-edit" role="button" tabindex="0" data-card-edit="${item.id}" title="Edit metadata" aria-label="Edit metadata">${icon('edit')}</span>
    ${MENU_KINDS.has(item.kind) ? html`<span class="ct-btn ct-more" role="button" tabindex="0" data-card-menu="${item.id}" data-kind="${item.kind}" title="More" aria-label="More">${icon('dots')}</span>` : ''}
  </span>`;
}
export function itemHref(item) {
  if (item.kind === 'episode') return `#/item/${item.show_id || item.id}?season=${item.season ?? ''}&episode=${item.id}`;
  return `#/item/${item.id}`;
}
function progressBar(item) {
  const p = item.progress;
  if (!p || p.completed || !p.duration || !p.position) return '';
  return html`<div class="progress"><i style="width:${Math.min(100, p.position / p.duration * 100).toFixed(1)}%"></i></div>`;
}
// "Recently added" (some themes show it at the bottom of the picture): added in the last two weeks
function newBadge(item) {
  const t = item.added_at;
  if (!t || !(item.kind === 'movie' || item.kind === 'show')) return '';
  const ms = typeof t === 'number' ? t * (t < 1e12 ? 1000 : 1) : Date.parse(t);
  return ms && Date.now() - ms < 14 * 86400000 ? html`<span class="new-badge">Recently added</span>` : '';
}

function badge(item) {
  if (item.kind === 'show' && item.unwatched > 0) return html`<span class="badge" title="${item.unwatched} unwatched">${item.unwatched}</span>`;
  if ((item.kind === 'movie' && item.progress?.completed) || (item.kind === 'show' && item.unwatched === 0 && item.child_count))
    return html`<span class="badge done" title="Watched">${icon('check')}</span>`;
  return '';
}

// ---- picture style: wide (Netflix style, the default) or the original posters (Settings › Look)
let artStyle = 'wide';
export function setArtStyle(style) { artStyle = style === 'poster' ? 'poster' : 'wide'; }
export const wideArt = () => artStyle === 'wide';
// Episodes in rows (Continue watching, Next up, new episodes): the show's artwork with its logo, or the episode's still
let episodeArt = 'show';
export function setEpisodeArt(v) { episodeArt = v === 'still' ? 'still' : 'show'; }
const WIDE_KINDS = new Set(['movie', 'show']);

// A movie or show as a wide picture with its name below. Uses the backdrop (or a frame from the
// video); a title with only a poster shows it whole over a blurred copy of itself.
export function wideCard(item, opts = {}) {
  const art = item.backdrop || item.thumb;
  const href = itemHref(item);
  const picture = art ? html`<img loading="lazy" src="${img(art, 500)}" alt="">`
    : item.poster ? html`<span class="wide-fill" style="background-image:url('${img(item.poster, 185)}')"></span><img class="wide-poster" loading="lazy" src="${img(item.poster, 342)}" alt="">`
    : genPoster(item);
  // the title's logo on the picture, like a streaming service, when the picture has no title written on it
  const withLogo = !!(art && item.backdrop && item.backdrop_clean && item.logo);
  const logo = withLogo ? html`<img class="card-logo" loading="lazy" src="${img(item.logo, 300)}" alt="">`
    : art && item.backdrop_clean ? html`<span class="card-name">${item.title}</span>` : '';   // a clean picture without a logo: the name on it
  return html`<div class="card landscape wide${withLogo ? ' has-logo' : ''}${art ? ' has-art' : ''}" data-id="${item.id}" data-kind="${item.kind}">
    <a class="frame-link" href="${href}" title="${item.title}${item.year ? ` (${item.year})` : ''}">
      <div class="frame">${picture}${logo}${badge(item)}${newBadge(item)}${progressBar(item)}${tools(item)}</div></a>
    <div class="caption"><a class="t" href="${href}">${item.title}</a><div class="s">${opts.sub ?? item.year ?? ''}</div></div>
  </div>`;
}

export function posterCard(item, opts = {}) {
  if (wideArt() && WIDE_KINDS.has(item.kind)) return wideCard(item, opts);
  const src = item.poster ? img(item.poster, 342) : null;
  const href = itemHref(item);
  return html`<div class="card poster${opts.captioned ? ' captioned' : ''}" data-id="${item.id}" data-kind="${item.kind}">
    <a class="frame-link" href="${href}" title="${item.title}${item.year ? ` (${item.year})` : ''}">
      <div class="frame">${src ? html`<img loading="lazy" src="${src}" alt="">` : genPoster(item)}${badge(item)}${progressBar(item)}${tools(item)}</div></a>
    <div class="caption"><a class="t" href="${href}">${item.title}</a><div class="s">${opts.sub ?? item.year ?? ''}</div></div>
  </div>`;
}

export function landscapeCard(item, opts = {}) {
  let art = item.thumb || item.backdrop || item.show_backdrop;
  if (item.kind === 'movie' || item.kind === 'show') art = item.backdrop || item.thumb;
  const showArt = item.kind === 'episode' && episodeArt === 'show' && item.show_backdrop;
  if (showArt) art = item.show_backdrop;
  const src = art ? img(art, 500) : null;
  const ownLogo = (item.kind === 'movie' || item.kind === 'show') && item.backdrop && art === item.backdrop && item.backdrop_clean && item.logo;
  const logoSrc = showArt && item.show_logo ? item.show_logo : ownLogo ? item.logo : null;
  const logo = logoSrc ? html`<img class="card-logo" loading="lazy" src="${img(logoSrc, 300)}" alt="">` : '';
  let title = item.title, sub = opts.sub;
  if (item.kind === 'episode') {
    title = item.show_title || item.title;
    sub = sub ?? `S${item.season}:E${item.index ?? '?'} · ${item.title}`;
  }
  if (sub === undefined) sub = item.year || (item.child_count ? `${item.child_count} photos` : '');
  const details = itemHref(item);
  const href = opts.play ? `#/play/${item.id}` : details;
  // The picture plays (Continue watching) or opens; the name always opens the details page.
  return html`<div class="card landscape${logoSrc ? ' has-logo' : ''}${src && (item.kind === 'movie' || item.kind === 'show' || showArt) ? ' has-art' : ''}" data-id="${item.id}" data-kind="${item.kind}">
    <a class="frame-link" href="${href}" title="${opts.play ? `Play ${title}` : title}">
      <div class="frame">${src ? html`<img loading="lazy" src="${src}" alt="">` : genPoster({ title, year: '' })}${logo}${progressBar(item)}
        ${opts.play ? html`<div class="play-hint">${icon('play')}</div>` : tools(item)}</div></a>
    <div class="caption"><a class="t" href="${details}">${opts.label ? html`<span style="color:var(--accent)">${opts.label} · </span>` : ''}${title}</a><div class="s">${sub}</div></div>
  </div>`;
}

export function squareCard(item) {
  const src = item.poster ? img(item.poster, 342) : null;
  return html`<div class="card square" data-id="${item.id}" data-kind="${item.kind}">
    <a class="frame-link" href="#/item/${item.id}" title="${item.title}">
      <div class="frame">${src ? html`<img loading="lazy" src="${src}" alt="">` : genPoster(item)}${tools(item)}</div></a>
    <div class="caption"><a class="t" href="#/item/${item.id}">${item.title}</a><div class="s">${item.artist || ''}${item.year ? ` · ${item.year}` : ''}</div></div>
  </div>`;
}

// Top 10 rows (Highest rated): a big hollow number with the poster tucked against it, Netflix style.
// The digits are outlines of Golos Text (SIL OFL) at weight 780, with a "1" without a foot like Netflix's,
// drawn as shapes so they look the same everywhere (old TVs too). Units: 728 = the poster's height.
const RANK_DIGITS = {"0":[579.8,"M289.4 727.9Q229.6 727.9 177.3 708.8Q124.9 689.7 85 647.2Q45 604.6 22.5 534.8Q0 464.9 0 364V344Q0 250 22.5 184.3Q45 118.6 85 78Q124.9 37.5 177.3 18.8Q229.6 0.1 289.4 0.1Q349.2 0.1 402.1 18.8Q454.9 37.5 494.6 78Q534.2 118.6 557 184.3Q579.8 250 579.8 344V364Q579.8 464.9 557 534.8Q534.2 604.6 494.6 647.2Q454.9 689.7 402.1 708.8Q349.2 727.9 289.4 727.9ZM289.4 554.3Q305.8 554.3 319.8 545.9Q333.8 537.6 344 516.3Q354.2 495 360 458.1Q365.8 421.3 365.8 364V344Q365.8 278 356 240.9Q346.2 203.7 329 188.7Q311.7 173.7 289.4 173.7Q267.7 173.7 250.8 188.7Q233.8 203.7 223.4 240.9Q213 278 213 344V364Q213 421.3 219 458.1Q225 495 235.5 516.3Q246 537.6 259.8 545.9Q273.5 554.3 289.4 554.3Z"],"1":[359.2,"M145.6 714 L145.6 224.4 L0.0 328.4 L0.0 125 L155.4 14 L359.2 14 L359.2 714Z"],"2":[565.8,"M0 714V577.1L222.5 394Q272.3 353.6 300.1 325.7Q327.9 297.8 338.8 276.7Q349.8 255.5 349.8 234Q349.8 205.2 334.5 190Q319.3 174.7 290.4 174.7Q253.3 174.7 234.2 198.9Q215 223.1 215 277H10Q10 186.5 40.7 125.3Q71.4 64 133.7 32Q195.9 0.1 290.4 0.1Q381.9 0.1 440.4 28.6Q498.9 57.1 527.4 107.6Q555.8 158.2 555.8 224Q555.8 271.7 534.6 314Q513.4 356.3 474.6 395.4Q435.8 434.6 382.2 475L296.1 540.2H565.8V714Z"],"3":[563.8,"M281.4 727.9Q184.9 727.9 122.2 693.4Q59.5 659 29.8 598.5Q0 538 0 461H197Q197 508.1 216.9 534.3Q236.7 560.5 285 560.5Q320.3 560.5 339 543Q357.8 525.5 357.8 494.3Q357.8 461 339.1 444.4Q320.5 427.8 279.4 427.8H228.3V284.2H279.4Q310.8 284.2 327.3 269.1Q343.8 254 343.8 224.4Q343.8 197.4 327.9 182.5Q312.1 167.5 283 167.5Q245.1 167.5 226.1 189.7Q207.1 211.8 207.1 261H9.7Q9.7 176.6 40.2 118.6Q70.6 60.6 131.5 30.4Q192.3 0.1 281.4 0.1Q369.9 0.1 430.4 24.5Q490.8 48.9 522.3 93.7Q553.8 138.6 553.8 199.2Q553.8 254.6 523 294.3Q492.1 334 439.2 351Q503.1 367.1 533.5 407.7Q563.8 448.2 563.8 510.3Q563.8 576.8 529.5 626.1Q495.1 675.3 432 701.6Q368.9 727.9 281.4 727.9Z"],"4":[586.0,"M300.7 714V592.9H0V446.4L303.6 14H514.1V419.1H586V592.9H514.1V714ZM173.2 419.1H300.7V234.6Z"],"5":[553.8,"M275.8 727.9Q197.9 727.9 145.5 709.2Q93 690.5 61 657.2Q28.9 623.9 14.5 581.4Q0.2 539 0 491H201.4Q203.4 527.4 221.5 547.3Q239.6 567.3 270.2 567.3Q306.9 567.3 322.7 543.6Q338.6 520 338.6 473.5Q338.6 441.5 329.8 422.6Q321 403.7 305.4 396.1Q289.7 388.4 268.6 388.4Q246.7 388.4 229.7 397.3Q212.7 406.2 202 424H15V14H514.6V187.2H208V270.2Q227.7 254.9 257.4 245.6Q287.1 236.2 328.4 236.2Q394.8 236.2 445.3 260.9Q495.7 285.6 524.8 337.3Q553.8 389 553.8 470.4Q553.8 548.9 520.6 606.9Q487.4 665 425.4 696.5Q363.4 727.9 275.8 727.9Z"],"6":[573.8,"M297.2 727.9Q152.4 727.9 76.2 648Q0 568 0 387.3V367.3Q0 235.7 37.5 154.9Q75 74.1 144.1 37.1Q213.1 0.1 306.4 0.1Q380.8 0.1 434.9 19.2Q488.9 38.4 520.9 82.7Q552.8 127 558.8 203H367.2Q360.9 182.7 345.7 171.7Q330.5 160.7 301.8 160.7Q273.5 160.7 252.3 174.1Q231.1 187.5 219.2 219.6Q207.3 251.7 206.2 311Q227.9 280.7 267.9 266.9Q307.8 253.1 359.7 253.1Q430 253.1 477.5 281.2Q524.9 309.4 549.4 359Q573.8 408.7 573.8 471.8V482.8Q573.8 557.1 540.5 612.6Q507.2 668.1 445.2 698Q383.1 727.9 297.2 727.9ZM286.8 567.3Q322.1 567.3 340.9 544.9Q359.8 522.6 359.8 486.1V476.1Q359.8 440 340.9 418.1Q322.1 396.1 286.8 396.1Q251.3 396.1 232.3 418.1Q213.2 440 213.2 476.1V486.1Q213.2 522.7 232.3 545Q251.3 567.3 286.8 567.3Z"],"7":[508.0,"M82 714 291 187.2H0V14H508V163.1L307.8 714Z"],"8":[583.8,"M291.4 727.9Q204.9 727.9 139.3 702.9Q73.6 677.9 36.8 630.5Q0 583 0 515.3Q0 458.5 33.7 413Q67.5 367.5 134.1 350.1Q82.8 332.9 51.4 293.2Q20 253.5 20 197.6Q20 137 52.3 92.8Q84.5 48.7 145.6 24.4Q206.7 0.1 291.4 0.1Q377.5 0.1 438.2 24.4Q498.8 48.7 531.2 92.8Q563.6 137 563.6 197.6Q563.6 253 532.2 292.6Q500.7 332.2 448.6 350.1Q516 368.9 549.9 412.3Q583.8 455.7 583.8 515.3Q583.8 583 546.9 630.5Q510 677.9 444.4 702.9Q378.7 727.9 291.4 727.9ZM291.4 567.7Q321.4 567.7 342 559.6Q362.6 551.5 373.9 535.7Q385.2 520 385.2 497.8Q385.2 461.9 357.9 443Q330.6 424 291.4 419.2Q253.4 424 225.8 443.3Q198.2 462.5 198.2 497.8Q198.2 520 210 535.7Q221.8 551.5 242.6 559.6Q263.4 567.7 291.4 567.7ZM291.4 288.8Q322.7 284.6 343.8 268.6Q365 252.7 365 221.4Q365 191.2 345.1 175.8Q325.2 160.3 291.4 160.3Q258.1 160.3 238.2 175.5Q218.2 190.7 218.2 221.4Q218.2 252.7 239.8 268.3Q261.4 284 291.4 288.8Z"],"9":[573.8,"M271.4 727.9Q196.3 727.9 141 708.4Q85.6 688.8 53.8 644.4Q22 600 15 525H206.2Q212.5 544.7 228.2 556Q243.8 567.3 276.6 567.3Q303.6 567.3 323.5 553.9Q343.4 540.5 354.7 508.2Q366.1 475.9 367.2 417Q345.4 447.3 305.4 461.1Q265.4 474.9 214.1 474.9Q144.1 474.9 96.3 446.6Q48.5 418.3 24.2 368.6Q0 318.8 0 255.6V245.2Q0 170.3 33.3 115.1Q66.6 59.9 128.8 30Q191 0.1 275.6 0.1Q373.3 0.1 439.6 34.2Q505.9 68.3 539.9 143.6Q573.8 218.8 573.8 340.7V360.9Q573.8 492.5 536.5 573.2Q499.2 653.9 431.5 690.9Q363.8 727.9 271.4 727.9ZM286.6 331.9Q322.5 331.9 341.3 310.1Q360.2 288.4 360.2 251.3V241.9Q360.2 206.2 340.6 183.5Q321 160.7 286.6 160.7Q251.1 160.7 232.1 182.9Q213 205.1 213 241.3V251.3Q213 288 232.1 309.9Q251.1 331.9 286.6 331.9Z"]};
const RANK_H = 728, RANK_GAP = 18;
export function rankCard(item, n) {
  const src = item.poster ? img(item.poster, 342) : null;
  const href = itemHref(item);
  let x = 0;
  const paths = String(n).split('').map((ch) => {
    const [w, path] = RANK_DIGITS[ch];
    const p = `<path transform="translate(${x} 0)" d="${path}"/>`;
    x += w + RANK_GAP;
    return p;
  });
  const w = x - RANK_GAP;
  // the poster covers the last quarter of the number; sizes are in poster heights (--rh)
  const numW = w / RANK_H, cover = numW * 0.25;
  return html`<div class="card rank" data-id="${item.id}" data-kind="${item.kind}" style="--num-w:${numW.toFixed(3)};--tile-w:${(numW - cover + 2 / 3).toFixed(3)}">
    <a class="frame-link" href="${href}" title="${n}. ${item.title}${item.year ? ` (${item.year})` : ''}">
      ${raw(`<svg class="rank-num" viewBox="0 0 ${Math.ceil(w)} ${RANK_H}" preserveAspectRatio="xMinYMax meet" aria-hidden="true">${paths.join('')}</svg>`)}
      <div class="frame">${src ? html`<img loading="lazy" src="${src}" alt="">` : genPoster(item)}${badge(item)}${newBadge(item)}${progressBar(item)}${tools(item)}</div></a>
    <div class="caption"><a class="t" href="${href}">${item.title}</a><div class="s">${item.year ?? ''}</div></div>
  </div>`;
}

export function cardFor(item, style, index = 0) {
  if (style === 'top10') return rankCard(item, index + 1);
  if (style === 'continue') return landscapeCard(item, { play: true, label: item.continue === 'next' ? 'Next' : null });
  if (item.kind === 'album') return squareCard(item);
  if (style === 'landscape' && WIDE_KINDS.has(item.kind)) return wideCard(item);
  if (item.kind === 'photoalbum' || item.kind === 'episode' || style === 'landscape') return landscapeCard(item);
  return posterCard(item);
}

export function rowStyle(row) {
  if (row.style === 'continue') return 'landscape';
  if (row.style) return row.style;
  const k = row.items[0]?.kind;
  if (k === 'album') return 'square';
  if (k === 'photoalbum' || k === 'episode') return 'landscape';
  return wideArt() ? 'landscape' : 'poster';
}

export function row(rowData) {
  const style = rowStyle(rowData);
  const title = rowData.library_id ? html`<a href="#/library/${rowData.library_id}">${rowData.title}</a>` : rowData.title;
  return html`<section class="row" aria-label="${rowData.title}"><h2>${title}</h2>
    <div class="scroller">
      <button class="scroll-btn prev" data-scroll="-1" aria-label="Scroll left">${icon('back')}</button>
      <div class="track ${style}">${rowData.items.map((i, n) => cardFor(i, rowData.style, n))}</div>
      <button class="scroll-btn next" data-scroll="1" aria-label="Scroll right">${icon('next')}</button>
    </div></section>`;
}

// Carousel arrows only where there is more to see: ← once scrolled, → until the end.
function updateScroller(sc) {
  const t = sc.querySelector('.track');
  if (!t) return;
  sc.classList.toggle('can-left', t.scrollLeft > 4);
  sc.classList.toggle('can-right', t.scrollLeft + t.clientWidth < t.scrollWidth - 4);
  // the arrows are exactly as tall as the pictures (themes use --frame-top / --frame-h)
  const f = t.querySelector('.card .frame');
  if (f) {
    const top = f.getBoundingClientRect().top - sc.getBoundingClientRect().top;
    sc.style.setProperty('--frame-top', `${Math.round(top)}px`);
    sc.style.setProperty('--frame-h', `${Math.round(f.offsetHeight)}px`);
  }
}
let scrollerFrame = 0;
const updateAllScrollers = () => {
  cancelAnimationFrame(scrollerFrame);
  scrollerFrame = requestAnimationFrame(() => document.querySelectorAll('.scroller').forEach(updateScroller));
};
document.addEventListener('scroll', (e) => {
  if (e.target.classList?.contains('track')) updateScroller(e.target.parentElement);
}, { capture: true, passive: true });
window.addEventListener('resize', updateAllScrollers);
new MutationObserver(updateAllScrollers).observe(document.documentElement, { childList: true, subtree: true });
document.addEventListener('load', (e) => { if (e.target.tagName === 'IMG') updateAllScrollers(); }, true);

document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-scroll]');
  if (!b) return;
  const track = b.parentElement.querySelector('.track');
  track.scrollBy({ left: Number(b.dataset.scroll) * track.clientWidth * 0.85, behavior: 'smooth' });
});

// ---- toasts ----
export function toast(message, kind = 'info') {
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = message;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3500);
}

// ---- modals ----
export function modal(content, { wide = false, onClose } = {}) {
  const root = $('#modal-root');
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal${wide ? ' wide' : ''}" role="dialog" aria-modal="true"></div>`;
  const box = back.firstElementChild;
  mount(box, content);
  const prevFocus = document.activeElement;
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', close);
    onClose?.();
    prevFocus?.focus?.();
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  box.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close);     // dialogs never outlive the page that opened them
  root.append(back);
  // On phones, focusing a field would throw the keyboard over the dialog before it is even read.
  if (isPhone() || isTouch()) { box.tabIndex = -1; box.focus({ preventScroll: true }); }
  else (box.querySelector('[autofocus]') || box.querySelector('input, select, button'))?.focus();
  return { box, close };
}

// ---- phones ----
export const isPhone = () => window.matchMedia('(max-width: 720px), (max-height: 500px) and (hover: none)').matches;
export const isTouch = () => window.matchMedia('(hover: none), (pointer: coarse)').matches;

/** Bottom sheet (phones): slides up from the bottom; closes on the backdrop, Escape, a swipe down or navigation. */
export function sheet(content, { title = '', onClose, cls = '' } = {}) {
  document.querySelectorAll('.sheet-back').forEach(b => b._close?.());
  const back = document.createElement('div');
  back.className = 'sheet-back';
  back.innerHTML = `<div class="sheet ${cls}" role="dialog" aria-modal="true"><div class="sheet-grip" aria-hidden="true"></div></div>`;
  const box = back.firstElementChild;
  if (title) box.insertAdjacentHTML('beforeend', String(html`<div class="sheet-title">${title}</div>`));
  const body = document.createElement('div');
  body.className = 'sheet-body';
  mount(body, content);
  box.append(body);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    back.classList.remove('open');
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', close);
    setTimeout(() => back.remove(), 220);
    onClose?.();
  };
  back._close = close;
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  back.addEventListener('click', (e) => { if (e.target === back || e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close);
  // swipe the sheet down to close it
  let y0 = null, dy = 0;
  box.addEventListener('touchstart', (e) => { if (body.scrollTop <= 0) { y0 = e.touches[0].clientY; dy = 0; } }, { passive: true });
  box.addEventListener('touchmove', (e) => {
    if (y0 == null) return;
    dy = Math.max(0, e.touches[0].clientY - y0);
    box.style.transform = dy ? `translateY(${dy}px)` : '';
  }, { passive: true });
  box.addEventListener('touchend', () => {
    if (y0 == null) return;
    y0 = null; box.style.transform = '';
    if (dy > 90) close();
  });
  document.body.append(back);
  requestAnimationFrame(() => back.classList.add('open'));
  return { box, body, close };
}

export function confirmDialog(title, text, okLabel = 'Confirm', danger = false) {
  return new Promise((resolve) => {
    let ok = false;
    const m = modal(html`<h2>${title}</h2><p style="color:var(--muted)">${text}</p>
      <div class="foot"><button class="btn" data-close>Cancel</button><button class="btn ${danger ? 'danger' : 'primary'}" data-ok>${okLabel}</button></div>`,
      { onClose: () => resolve(ok) });
    m.box.querySelector('[data-ok]').addEventListener('click', () => { ok = true; m.close(); });
  });
}

export function popMenu(anchor, items) {
  document.querySelectorAll('.menu').forEach(m => m.remove());
  if (isPhone()) return actionSheet(items);
  const menu = document.createElement('div');
  menu.className = 'menu';
  mount(menu, items.map((it, i) => it.sep ? html`<hr>` : it.heading ? html`<div class="menu-head">${it.heading}</div>`
    : html`<button data-i="${i}" ${it.disabled ? raw('disabled') : ''}>${it.label}${it.hint ? html`<span class="menu-hint">${it.hint}</span>` : ''}</button>`));
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  const top = Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 10);
  menu.style.top = `${Math.max(10, r.top > window.innerHeight / 2 ? r.top - menu.offsetHeight - 6 : top)}px`;
  menu.style.left = `${Math.min(r.left, window.innerWidth - menu.offsetWidth - 10)}px`;
  const off = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('mousedown', off); } };
  setTimeout(() => document.addEventListener('mousedown', off));
  menu.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-i]');
    if (!b) return;
    menu.remove();
    items[Number(b.dataset.i)].action();
  });
}

/** popMenu on phones: the same items as a list in a bottom sheet, with big touch targets. */
function actionSheet(items) {
  const head = items.find(it => it.heading)?.heading || '';
  const sh = sheet(html`<div class="action-list">${items.map((it, i) => it.heading ? '' : it.sep ? html`<hr>`
    : html`<button data-i="${i}" ${it.disabled ? raw('disabled') : ''}><span>${it.label}</span>${it.hint ? html`<span class="menu-hint">${it.hint}</span>` : ''}</button>`)}</div>`, { title: head });
  sh.body.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-i]');
    if (!b) return;
    sh.close();
    items[Number(b.dataset.i)].action();
  });
}
