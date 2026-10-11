// One Settings page for everything (the gear in the top bar): a sidebar with groups, items and sub-items
// on the left, the chosen page on the right. Everyone has "My settings"; administrators also get the server,
// library, media and advanced pages (what used to be Server admin).
import { renderRail, state } from './app.js';
import { renderSettings } from './settings.js';
import { renderAdminPage } from './admin.js';
import { $, html, icon, mount } from './ui.js';

export const NAV = [
  { title: 'My settings', items: [
    { id: 'appearance', label: 'Appearance', icon: 'palette' },
    { id: 'home', label: 'Home screen', icon: 'home' },
    { label: 'Playback', icon: 'play', key: 'my-playback', children: [
      { id: 'audio', label: 'Audio and subtitles' },
      { id: 'intros', label: 'TV show intros' },
      { id: 'media-pages', label: 'Media pages' },
    ] },
    { id: 'app', label: 'LENTA app', icon: 'phone' },
    { id: 'account', label: 'Account', icon: 'user' },
  ] },
  { title: 'Server', admin: true, items: [
    { id: 'dashboard', label: 'Dashboard', icon: 'grid' },
    { id: 'general', label: 'General', icon: 'sliders' },
    { id: 'users', label: 'Users', icon: 'users' },
    { id: 'network', label: 'Network', icon: 'globe' },
  ] },
  { title: 'Libraries', admin: true, items: [
    { label: 'Libraries', icon: 'folder', key: 'libs', children: [
      { id: 'libraries', label: 'Manage libraries' },
      { id: 'scanning', label: 'Scanning' },
    ] },
    { label: 'Metadata', icon: 'info', key: 'meta', children: [
      { id: 'fix-matches', label: 'Fix matches' },
      { id: 'metadata-sources', label: 'TMDB and language' },
      { id: 'artwork', label: 'Artwork sources' },
      { id: 'about-panel', label: 'About panel extras' },
    ] },
  ] },
  { title: 'Media', admin: true, items: [
    { id: 'transcoding', label: 'Transcoding', icon: 'cpu' },
    { id: 'trailers', label: 'Trailers and theme music', icon: 'film' },
    { id: 'subtitles', label: 'Subtitles', icon: 'subs' },
    { id: 'previews', label: 'Previews and intros', icon: 'image' },
  ] },
  { title: 'Advanced', admin: true, items: [
    { id: 'logs', label: 'Logs', icon: 'doc' },
  ] },
];

const USER_PAGES = new Set(['appearance', 'home', 'audio', 'intros', 'media-pages', 'app', 'account']);

/** Every page: id -> { label, group, parent }. */
function pages() {
  const out = {};
  for (const g of NAV) {
    if (g.admin && !state.user?.is_admin) continue;
    for (const it of g.items) {
      if (it.children) it.children.forEach(c => { out[c.id] = { label: c.label, group: g.title, parent: it }; });
      else out[it.id] = { label: it.label, group: g.title, parent: null };
    }
  }
  return out;
}

// Old Server admin addresses (bookmarks, links in the docs) open the matching page.
export const ADMIN_TABS = { dashboard: 'dashboard', libraries: 'libraries', users: 'users', metadata: 'fix-matches',
  settings: 'general', logs: 'logs' };

const opened = new Set();
let navTop = 0;                         // the sidebar's scroll position, kept between pages
let reopenRail = false;                 // the main side menu was open when Settings opened: open it again on leaving

// Settings has its own menu, so the main side menu folds to icons while you're here (not with Redline's
// menu bar on top); it opens again when you leave, as it was.
function foldMainMenu() {
  if (document.documentElement.dataset.theme === 'redline' || document.body.classList.contains('rail-collapsed')) return;
  document.body.classList.add('rail-collapsed');
  renderRail();
  reopenRail = true;
  const back = () => {
    if (location.hash.startsWith('#/settings')) return;
    window.removeEventListener('hashchange', back);
    if (reopenRail) { reopenRail = false; document.body.classList.remove('rail-collapsed'); renderRail(); }
  };
  window.addEventListener('hashchange', back);
}               // sub-menus opened by hand during this visit

function remember(page) { try { localStorage.setItem('lenta.settingsPage', page); } catch { /* private mode */ } }
function last() { try { return localStorage.getItem('lenta.settingsPage'); } catch { return null; } }

export async function renderSettingsPage(page, q = new URLSearchParams()) {
  const all = pages();
  if (!page || !all[page]) {
    const p = last();
    location.replace(`#/settings/${p && all[p] ? p : 'appearance'}`);
    return;
  }
  remember(page);
  foldMainMenu();
  const info = all[page];
  if (info.parent) opened.add(info.parent.key);
  const view = $('#view');
  const item = (it) => html`<a class="sset-item ${it.id === page ? 'active' : ''}" href="#/settings/${it.id}" ${it.id === page ? 'aria-current="page"' : ''}>
    ${it.icon ? icon(it.icon) : ''}<span>${it.label}</span></a>`;
  const parent = (it) => {
    const open = opened.has(it.key);
    const has = it.children.some(c => c.id === page);
    return html`<button type="button" class="sset-item sset-parent ${has ? 'has-active' : ''}" data-key="${it.key}" aria-expanded="${open}">
        ${icon(it.icon)}<span>${it.label}</span>${icon('chevron-down')}</button>
      <div class="sset-children" ${open ? '' : 'hidden'}>${it.children.map(c => item(c))}</div>`;
  };
  // the sidebar keeps its scroll position when you pick another page (it is drawn again with the page)
  const navScroll = view.querySelector('.sset-list')?.parentElement?.scrollTop ?? navTop;
  mount(view, html`<div class="sset">
    <aside class="sset-nav" aria-label="Settings">
      <button type="button" class="sset-menu-btn" aria-expanded="false">${icon('menu')}<span>${info.group} › ${info.label}</span>${icon('chevron-down')}</button>
      <div class="sset-list">
        ${NAV.filter(g => !g.admin || state.user.is_admin).map(g => html`<div class="sset-group">${g.title}</div>
          ${g.items.map(it => (it.children ? parent(it) : item(it)))}`)}
      </div>
    </aside>
    <main class="sset-main">
      <div class="sset-head"><div class="sset-eyebrow">${info.group}${info.parent ? ` › ${info.parent.label}` : ''}</div>
        <h1 class="page-title">${info.label}</h1></div>
      <div id="sset-body"></div>
    </main></div>`);
  const nav = view.querySelector('.sset-nav');
  nav.scrollTop = navScroll;
  nav.addEventListener('scroll', () => { navTop = nav.scrollTop; }, { passive: true });
  view.querySelectorAll('.sset-parent').forEach(b => b.addEventListener('click', () => {
    const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', open);
    b.nextElementSibling.hidden = !open;
    if (open) opened.add(b.dataset.key); else opened.delete(b.dataset.key);
  }));
  const menuBtn = view.querySelector('.sset-menu-btn');
  menuBtn.addEventListener('click', () => {
    const open = menuBtn.getAttribute('aria-expanded') !== 'true';
    menuBtn.setAttribute('aria-expanded', open);
    view.querySelector('.sset').classList.toggle('menu-open', open);
  });
  const body = $('#sset-body');
  if (USER_PAGES.has(page)) await renderSettings(body, page);
  else await renderAdminPage(page, body, q);
}
