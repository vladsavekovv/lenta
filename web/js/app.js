// LENTA Web Client — boot, session, navigation rail and hash router.
import { api } from './api.js';
import './hovercard.js';
import { initCardTools } from './editor.js';
import { $, html, icon, isTouch, KIND_ICON, mount, nfx, onPageScroll, pageScroller, pageScrollTo, pageY, popMenu, raw, setArtStyle, setEpisodeArt, setViewer, sheet, syncNfx, toast } from './ui.js';
import { inAndroidApp, installAvailable, promptInstall } from './pwa.js';
import { renderAuth, changePasswordDialog } from './auth.js';
import { views } from './views.js';
import { libraryMenu } from './admin.js';
import { openPlayer, openLiveChannel, closePlayer, expandPlayer, minimizePlayer, playerIsLive, playerItem, playerMini, playerOpen } from './player.js';
import { ADMIN_TABS, renderSettingsPage } from './settings-shell.js';
import { applyKeyWatch, initTv, inTvApp, changeServer } from './tv.js';
import { runCleanups } from './extras.js';

export const state = { user: null, server: null, libraries: [], prefs: {}, features: {}, languages: [] };

// ---- themes ----
export const THEMES = [
  { id: 'projector', name: 'Projector', desc: 'Plum night and an amber lamp' },
  { id: 'noir', name: 'Noir', desc: 'Graphite and silver, no colour' },
  { id: 'abyss', name: 'Abyss', desc: 'Deep water, a thread of cyan' },
  { id: 'velvet', name: 'Velvet', desc: 'Oxblood curtains, crimson seats' },
  { id: 'aurora', name: 'Aurora', desc: 'Midnight indigo, violet glow' },
  { id: 'netflix', name: 'Netflix', desc: 'Streaming black and signal red, menu on top' },
  { id: 'hbo', name: 'HBO Max', desc: 'Black canvas, blue beam, menu on top' },
];
export function applyTheme(id) {
  if (id === 'redline') id = 'netflix';            // the theme was called Redline before 1.3.1
  const theme = THEMES.some(t => t.id === id) ? id : 'projector';
  document.documentElement.dataset.theme = theme;
  // themes whose menu is a bar along the top rather than a column on the left
  document.documentElement.classList.toggle('topnav', theme === 'netflix' || theme === 'hbo');
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', bg || '#17121F');
  setTimeout(fitNav, 50);
  appBarColors(bg || '#17121F');
  if (syncNfx() && state.user && lastViewKey) { renderRail(); refreshView(); }      // the phone look came or went: draw the page again
}
window.addEventListener('resize', () => { if (syncNfx() && state.user && lastViewKey) { renderRail(); refreshView(); } });

// The phone header (Netflix): see-through at the top of the page, black once you scroll; the library chips slide
// away while you scroll down and come back when you scroll up, like the app's.
let lastY = 0;
onPageScroll(() => {
  if (!nfx()) return;
  const y = pageY(), root = document.documentElement;
  root.classList.toggle('mh-solid', y > 8);
  if (Math.abs(y - lastY) > 6) { root.classList.toggle('mh-tuck', y > lastY && y > 90); lastY = y; }
});

// The Android app: its status and navigation bars take the theme's background colour.
function appBarColors(css) {
  const app = window.LentaApp;
  if (!app || typeof app.setColors !== 'function') return;          // the browser, or an older app
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(css.trim());
  let hex = m ? m[1] : null;
  if (!hex) {
    const rgb = /rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/.exec(getComputedStyle(document.body).backgroundColor || '');
    if (!rgb) return;
    hex = [rgb[1], rgb[2], rgb[3]].map(n => Number(n).toString(16).padStart(2, '0')).join('');
  }
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  try { app.setColors((0xff000000 | parseInt(hex, 16)) | 0); } catch { /* nothing to do */ }
}

const routes = [
  [/^\/$/, (m, q) => views.home()],
  [/^\/library\/(\d+)$/, (m, q) => views.library(Number(m[1]), q)],
  [/^\/item\/(\d+)$/, (m, q) => views.item(Number(m[1]), q)],
  [/^\/search$/, (m, q) => views.search(q)],
  [/^\/person$/, (m, q) => views.person(q.get('name'))],
  [/^\/mylist$/, () => views.mylist()],
  [/^\/guide$/, (m, q) => views.guide(q)],
  // Server admin is part of Settings now; old addresses open the matching page
  [/^\/admin(?:\/(\w+))?$/, (m, q) => { location.replace(`#/settings/${ADMIN_TABS[m[1] || 'dashboard'] || 'dashboard'}${q.toString() ? `?${q}` : ''}`); }],
  [/^\/settings(?:\/([\w-]+))?$/, (m, q) => renderSettingsPage(m[1], q)],
];

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, query = ''] = raw.split('?');
  return { path, query: new URLSearchParams(query) };
}

let lastViewKey = null;

async function route() {
  if (!state.user) return;
  const { path, query } = parseHash();
  // A live channel keeps the /play/ prefix so the Android app still knows to go full screen.
  const live = path.match(/^\/play\/live\/(\d+)$/);
  if (live) {
    // Already on this channel (coming back from the mini player, or from Settings): bring it up again
    // rather than starting it afresh — a restart drops the tuner and reopens it for no reason.
    if (playerIsLive() && playerItem() === Number(live[1])) {
      expandPlayer({ navigate: false });
      return;
    }
    const hadView = !!lastViewKey;
    if (!hadView) await renderView('/', new URLSearchParams());
    openLiveChannel(Number(live[1]), { returnTo: hadView ? 'back' : '#/' });
    return;
  }
  const play = path.match(/^\/play\/(\d+)$/);
  if (play) {
    // the same title is already playing (e.g. back from the mini player): carry on, don't start it again
    if (playerOpen() && playerItem() === Number(play[1]) && !query.has('restart') && !query.has('t') && !query.has('file')) {
      expandPlayer({ navigate: false });
      return;
    }
    const hadView = !!lastViewKey;
    if (!hadView) await renderView('/', new URLSearchParams());
    openPlayer(Number(play[1]), { restart: query.get('restart') === '1', start: query.get('t'), file: query.get('file'),
      returnTo: hadView ? 'back' : '#/' });
    return;
  }
  // Browsing away from a film can close it — you can always resume. A live channel cannot be resumed,
  // and closing it hands the tuner back, so it carries on in the mini player instead. Going to
  // Settings › Live TV to read the log is the obvious case, and it should not end what you are reading about.
  if (playerOpen() && !playerMini()) {
    if (playerIsLive()) minimizePlayer({ navigate: false });
    else closePlayer({ navigate: false });
  }
  await renderView(path, query);
}

async function renderView(path, query) {
  const view = $('#view');
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) {
      const key = path + '?' + query.toString();
      const samePage = lastViewKey && lastViewKey.split('?')[0] === path;
      lastViewKey = key;
      runCleanups();          // stop theme music, trailers and scroll effects of the previous page
      renderRail();
      try {
        await fn(m, query);
      } catch (err) {
        mount(view, html`<div class="page"><div class="empty"><h3>This page didn't load</h3><p>${err.message}</p>
          <button class="btn" onclick="location.reload()">Reload</button></div></div>`);
      }
      if (!samePage && !keepScroll) { pageScrollTo(0); view.focus({ preventScroll: true }); }
      return;
    }
  }
  location.hash = '#/';
}

// A menu bar along the top (Netflix theme) with more libraries than fit: show one "Libraries" button instead.
function fitNav() {
  const rail = $('#rail');
  if (!rail) return;
  document.body.classList.remove('nav-compact');
  const cs = getComputedStyle(rail);
  if (cs.position !== 'fixed' || cs.flexDirection !== 'row' || rail.getBoundingClientRect().top > 0) return;
  const kids = [...rail.children].filter(k => k.getBoundingClientRect().width > 0);
  const right = kids.find(k => k.matches('a[href="#/search"]'));
  const leftEnd = Math.max(0, ...kids.filter(k => !k.matches('a[href="#/search"], a[href="#/settings"], a[href="#/admin"], #account-btn, .spacer'))
    .map(k => k.getBoundingClientRect().right));
  if (rail.scrollWidth > rail.clientWidth + 1 || (right && leftEnd > right.getBoundingClientRect().left - 24)) document.body.classList.add('nav-compact');
}
let fitTimer = 0;
window.addEventListener('resize', () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitNav, 120); });

let keepScroll = false;

// Draw the page again (after editing details, refreshing metadata...) without losing your place: the page keeps
// its scroll position, rows keep theirs, and a library loads as many pages as it had shown.
export async function refreshView() {
  const y = pageY();
  const rows = [...document.querySelectorAll('.track')].map(t => t.scrollLeft);
  lastViewKey = null; keepScroll = true;
  try { await route(); } finally { keepScroll = false; }
  document.querySelectorAll('.track').forEach((t, i) => { if (rows[i]) t.scrollLeft = rows[i]; });
  if (!y) return;
  const root = () => pageScroller() || document.scrollingElement || document.documentElement;
  for (let tries = 0; tries < 40; tries++) {          // more pictures load as the page is scrolled down
    pageScrollTo(y);
    if (root().scrollHeight >= y + root().clientHeight || !document.querySelector('#sentinel')) break;
    await new Promise(r => setTimeout(r, 150));
  }
  pageScrollTo(y);
}

export function renderRail() {
  const rail = $('#rail');
  const { path } = parseHash();
  const active = (p) => (p === '/' ? path === '/' : path.startsWith(p)) ? 'active' : '';
  const u = state.user;
  // title= gives the collapsed (icons only) menu its names on hover.
  const link = (href, cls, ic, label) => html`<a href="${href}" class="${cls}" title="${label}">${icon(ic)}<span class="label">${label}</span></a>`;
  const collapsed = document.body.classList.contains('rail-collapsed');
  setViewer(u.username);
  // the phone header's page name (Netflix); pages without one (a title's page, Settings) have no header
  const lib = path.startsWith('/library/') ? state.libraries.find(l => `/library/${l.id}` === path) : null;
  const headTitle = path === '/' ? 'Home' : lib ? lib.name : path === '/search' ? 'Search' : path === '/mylist' ? 'My List' : '';
  mount(rail, html`
    <div class="brand-row">
      <a class="brand-mark-link" href="#/" title="Home" aria-label="${state.server?.name || 'LENTA'} — Home"><img class="brand-mark" src="img/lenta-mark.png" alt=""></a>
      <button class="rail-toggle" id="rail-toggle" aria-expanded="${collapsed ? 'false' : 'true'}"
        aria-label="${collapsed ? 'Expand menu' : 'Collapse menu'}" title="${collapsed ? 'Expand menu' : 'Collapse menu'} ( \\ )">${icon('menu')}</button>
      <a class="brand" href="#/" title="Home" aria-label="${state.server?.name || 'LENTA'} — Home">
        <span class="brand-text"><img class="brand-word" src="img/lenta-wordmark.png" alt="LENTA">
          <small>${state.server?.name && state.server.name.toUpperCase() !== 'LENTA' ? state.server.name : 'Media server'}</small></span></a></div>
    ${link('#/', active('/'), 'home', 'Home')}
    ${link('#/search', active('/search'), 'search', 'Search')}
    ${link('#/mylist', `${active('/mylist')} mobile-hide`, 'list', 'My list')}
    ${state.libraries.length ? html`<div class="group">Libraries</div>` : ''}
    <div class="lib-list" id="lib-list">${state.libraries.map(l => html`<div class="lib-item" data-lib="${l.id}" ${u.is_admin && !isTouch() ? raw('draggable="true"') : ''}>
      ${link(`#/library/${l.id}`, path === `/library/${l.id}` ? 'active' : '', KIND_ICON[l.kind], l.name)}
      ${u.is_admin ? html`<button class="lib-more" data-lib-menu="${l.id}" aria-label="${l.name} options" title="Options · drag to reorder">${icon('dots')}</button>` : ''}</div>`)}</div>
    <div class="spacer"></div>
    ${link('#/settings', active('/settings'), 'admin', 'Settings')}
    <button class="navlike" id="account-btn" aria-label="Account" title="${u.username}">
      <span class="avatar" style="background:${u.color || '#F2B33D'}">${u.username[0].toUpperCase()}</span><span class="label">${u.username}</span></button>
    <header class="mhead" aria-label="${headTitle || 'LENTA'}">
      <div class="mh-top">
        <a class="mh-mark" href="#/" aria-label="Home"><img src="img/lenta-mark.png" alt=""></a>
        <h1 class="mh-title">${headTitle}</h1>
      </div>
      ${state.libraries.length > 0 ? html`<nav class="mh-chips" aria-label="Libraries">${state.libraries.map(l =>
        html`<a class="mh-chip ${path === `/library/${l.id}` ? 'on' : ''}" href="#/library/${l.id}">${l.name}</a>`)}</nav>` : ''}
    </header>
    <div class="tabbar" role="tablist" aria-label="Main">
      <a href="#/" class="tab-home ${active('/')}">${icon('home')}<span>Home</span></a>
      <a href="#/search" class="tab-search ${active('/search')}">${icon('search')}<span>Search</span></a>
      <button type="button" id="tab-libs" class="tab-libs ${path.startsWith('/library') ? 'active' : ''}">${icon('grid')}<span>Libraries</span></button>
      <a href="#/mylist" class="tab-list ${active('/mylist')}">${icon('list')}<span>My list</span></a>
      <button type="button" id="tab-me" class="tab-me ${path.startsWith('/settings') || path.startsWith('/admin') ? 'active' : ''}">
        <span class="avatar" style="background:${u.color || '#F2B33D'}">${u.username[0].toUpperCase()}</span><span>${nfx() ? 'My LENTA' : u.username}</span></button>
    </div>`);
  document.body.classList.toggle('mh-on', !!headTitle);
  $('#tab-libs').addEventListener('click', librariesSheet);
  $('#tab-me').addEventListener('click', meSheet);
  $('#rail-toggle').addEventListener('click', () => setRailCollapsed(!document.body.classList.contains('rail-collapsed')));
  rail.querySelectorAll('[data-lib-menu]').forEach(b => b.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    const lib = state.libraries.find(l => l.id === Number(b.dataset.libMenu));
    if (lib) libraryMenu(b, lib, { move: (dir) => moveLibrary(lib.id, dir) });
  }));
  if (u.is_admin) { wireLibraryDrag($('#lib-list')); wireLibraryLongPress($('#lib-list')); }
  fitNav();
  $('#account-btn').addEventListener('click', (e) => popMenu(e.currentTarget, [
    { label: 'Settings', action: () => { location.hash = '#/settings'; } },
    { label: 'Change password', action: changePasswordDialog },
    { label: 'Sign out', action: signOut },
  ]));
}

// ---- phones: bottom tab bar sheets ---------------------------------------------------------
function librariesSheet() {
  const u = state.user;
  const { path } = parseHash();
  const sh = sheet(html`<div class="sheet-libs">
    <img class="sheet-logo" src="img/lenta-logo.png" alt="LENTA">
    ${state.libraries.length ? state.libraries.map(l => html`<div class="sheet-lib ${path === `/library/${l.id}` ? 'active' : ''}">
      <a href="#/library/${l.id}">${icon(KIND_ICON[l.kind])}<span>${l.name}</span></a>
      ${u.is_admin ? html`<button class="lib-more" data-lib-menu="${l.id}" aria-label="${l.name} options">${icon('dots')}</button>` : ''}</div>`)
      : html`<p class="sheet-empty">No libraries yet.${u.is_admin ? ' Add one in Settings › Libraries.' : ''}</p>`}</div>`, { title: 'Libraries' });
  sh.body.querySelectorAll('[data-lib-menu]').forEach(b => b.addEventListener('click', () => {
    const lib = state.libraries.find(l => l.id === Number(b.dataset.libMenu));
    sh.close();
    if (lib) libraryMenu(b, lib, { move: (dir) => moveLibrary(lib.id, dir) });
  }));
}

function meSheet() {
  const u = state.user;
  const items = [
    ...(nfx() ? [{ label: 'My List', action: () => { location.hash = '#/mylist'; } }] : []),     // the phone tab bar has no My list tab
    { label: 'Settings', action: () => { location.hash = '#/settings'; } },
    ...(installAvailable() ? [{ label: 'Install the LENTA app', action: promptInstall }] : []),
    ...(inAndroidApp() ? [{ label: 'Change server…', hint: location.host, action: () => window.LentaApp.changeServer() }] : []),
    ...(inTvApp() ? [{ label: 'Change server…', hint: location.host, action: changeServer }] : []),
    { label: 'Change password', action: changePasswordDialog },
    { sep: true },
    { label: 'Sign out', action: signOut },
  ];
  popMenu($('#tab-me'), [{ heading: `Signed in as ${u.username}` }, ...items]);
}

// ---- library order: drag in the menu (administrators), saved for everyone ---------------
async function saveLibraryOrder(ids) {
  const byId = new Map(state.libraries.map(l => [l.id, l]));
  state.libraries = ids.map(id => byId.get(id)).filter(Boolean);
  renderRail();
  try { await api.put('/api/admin/libraries/order', { ids }); }
  catch (ex) { toast(ex.message, 'error'); await loadLibraries(); }
  if (parseHash().path === '/') refreshView();          // Home rows follow the library order
}

export function moveLibrary(id, dir) {
  const ids = state.libraries.map(l => l.id);
  const i = ids.indexOf(id), j = i + dir;
  if (i < 0 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  saveLibraryOrder(ids);
}

// Icons-only menu (tablets, collapsed menu) has no room for ⋯: a long press or right-click opens it.
function wireLibraryLongPress(list) {
  if (!list) return;
  const open = (item) => {
    const lib = state.libraries.find(l => l.id === Number(item.dataset.lib));
    if (lib) libraryMenu(item, lib, { move: (dir) => moveLibrary(lib.id, dir) });
  };
  list.addEventListener('contextmenu', (e) => {
    const item = e.target.closest('.lib-item');
    if (!item) return;
    e.preventDefault();
    open(item);
  });
  let timer = 0, fired = false;
  list.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse') return;
    const item = e.target.closest('.lib-item');
    if (!item) return;
    fired = false;
    timer = setTimeout(() => { fired = true; navigator.vibrate?.(10); open(item); }, 550);
  });
  const cancel = () => clearTimeout(timer);
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(t => list.addEventListener(t, cancel));
  list.addEventListener('click', (e) => { if (fired) { e.preventDefault(); fired = false; } }, true);
}

function wireLibraryDrag(list) {
  if (!list) return;
  let dragged = null;
  list.addEventListener('dragstart', (e) => {
    dragged = e.target.closest('.lib-item');
    if (!dragged) return;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragged.dataset.lib);
    requestAnimationFrame(() => dragged?.classList.add('dragging'));
  });
  list.addEventListener('dragover', (e) => {
    if (!dragged) return;
    e.preventDefault();
    const over = e.target.closest('.lib-item');
    if (!over || over === dragged) return;
    const r = over.getBoundingClientRect();
    list.insertBefore(dragged, e.clientY > r.top + r.height / 2 ? over.nextSibling : over);
  });
  list.addEventListener('drop', (e) => e.preventDefault());
  list.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    dragged = null;
    const ids = [...list.querySelectorAll('.lib-item')].map(x => Number(x.dataset.lib));
    if (ids.join() !== state.libraries.map(l => l.id).join()) saveLibraryOrder(ids);
  });
}

// ---- collapsing the menu to icons (remembered on this device) ---------------------------
function setRailCollapsed(collapsed) {
  document.body.classList.toggle('rail-collapsed', collapsed);
  renderRail();
  $('#rail-toggle')?.focus({ preventScroll: true });
}
// Collapsed or open when LENTA opens: Settings › Appearance › Side menu (☰ or the \ key switches it for this visit).
document.body.classList.add('rail-collapsed');
export function applyMenuDefault(value) { document.body.classList.toggle('rail-collapsed', value !== 'open'); }
document.addEventListener('keydown', (e) => {
  if (e.key !== '\\' || e.ctrlKey || e.metaKey || e.altKey || e.target.closest?.('input, textarea, select, [contenteditable]')) return;
  if (document.querySelector('#player-root .player')) return;
  setRailCollapsed(!document.body.classList.contains('rail-collapsed'));
});

export async function loadLibraries() {
  state.libraries = await api.get('/api/libraries');
  renderRail();
}

async function signOut() {
  await api.post('/api/auth/logout').catch(() => {});
  location.hash = '#/';
  location.reload();
}

export async function startSession() {
  state.user = await api.get('/api/auth/me');
  state.prefs = state.user.prefs;
  state.features = state.user.features;
  applyTheme(state.prefs.theme);
  initCardTools();
  setArtStyle(state.prefs.card_style);
  setEpisodeArt(state.prefs.episode_art);
  applyMenuDefault(state.prefs.menu_default);
  // Switched on from any browser, it appears on the television — which is the point: the set whose
  // remote is misbehaving is the worst place to go hunting through settings for the switch.
  applyKeyWatch(state.prefs.tv_key_watch === '1');
  api.get('/api/languages').then(l => { state.languages = l; }).catch(() => {});
  state.server = { ...(state.server || {}), name: state.user.server_name };
  document.title = state.user.server_name || 'LENTA';
  $('#app').classList.remove('bare');
  await loadLibraries();
  lastViewKey = null;
  await route();
}

async function boot() {
  try {
    state.server = await api.get('/api/server');
  } catch (err) {
    mount($('#view'), html`<div class="auth"><div class="auth-inner"><img class="auth-logo" src="img/lenta-logo.png" alt="LENTA"><h1>${err.message}</h1>
      <button class="btn primary" onclick="location.reload()">Try again</button></div></div>`);
    $('#app').classList.add('bare');
    return;
  }
  document.title = state.server.name || 'LENTA';
  applyTheme(state.server.theme);
  if (state.server.setup_required || !state.server.signed_in) {
    $('#app').classList.add('bare');
    renderAuth(state.server, startSession);
    return;
  }
  try {
    await startSession();
  } catch {
    $('#app').classList.add('bare');
    renderAuth(state.server, startSession);
  }
}

window.addEventListener('hashchange', route);
window.addEventListener('lenta:signedout', () => {
  if (!state.user) return;
  state.user = null;
  runCleanups();
  closePlayer({ navigate: false });
  toast('Your session ended. Sign in again.', 'error');
  $('#app').classList.add('bare');
  api.get('/api/server').then(s => { applyTheme(s.theme); renderAuth(s, startSession); });   // sign-in uses the server's default theme
});
initTv();
// the menu bar of some themes turns solid once the page is scrolled
let scrolledNow = false;
onPageScroll(() => {
  const on = pageY() > 8;
  if (on !== scrolledNow) { scrolledNow = on; document.documentElement.classList.toggle('scrolled', on); }
});
boot();
