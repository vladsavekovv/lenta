// Per-user settings: look (theme), playback languages and subtitles, trailers and theme music.
import { api } from './api.js';
import { applyMenuDefault, applyTheme, renderRail, state, THEMES } from './app.js';
import { changePasswordDialog } from './auth.js';
import { ensureLanguages, languageOptions } from './subtitles.js';
import { $, html, icon, modal, mount, raw, setArtStyle, setEpisodeArt, toast } from './ui.js';
import { inAndroidApp, installAvailable, installHelp, isAndroid, onInstallChange, promptInstall } from './pwa.js';
import { keyWatching, setKeyWatch, tvMode } from './tv.js';
import { blockLayout, layoutBar, layoutButtons, pageLayout } from './layout.js';

// Colours for the theme previews (kept here so a card can show a theme that isn't active).
const PREVIEW = {
  projector: { bg: '#17121F', card: '#2B2237', text: '#F1ECF6', accent: '#F2B33D', glow: 'rgb(242 179 61 / .28)', r: '4px' },
  noir: { bg: '#121214', card: '#26262A', text: '#EEEEEF', accent: '#F2F2F2', glow: 'rgb(255 255 255 / .12)', r: '1px' },
  abyss: { bg: '#08151A', card: '#152B33', text: '#E3F2F4', accent: '#5CD6C8', glow: 'rgb(92 214 200 / .25)', r: '8px' },
  velvet: { bg: '#170C0F', card: '#2D191F', text: '#F5EAEC', accent: '#EC5A5F', glow: 'rgb(236 90 95 / .3)', r: '3px' },
  netflix: { bg: '#141414', card: '#2F2F2F', text: '#FFFFFF', accent: '#E50914', glow: 'rgb(229 9 20 / .3)', r: '4px' },
  hbo: { bg: '#000000', card: '#141020', text: '#FFFFFF', accent: '#71B8F2', glow: 'rgb(0 43 231 / .45)', r: '8px' },
  aurora: { bg: '#0B0D1C', card: '#1A1E40', text: '#ECEEFF', accent: 'linear-gradient(100deg,#A895FF,#63D8F2)', glow: 'rgb(168 149 255 / .35)', r: '10px' },
};

// Settings › My settings pages and the blocks each one shows
const PAGE_PANELS = { appearance: ['look'], home: ['home'], audio: ['audio'], intros: ['intros'], 'media-pages': ['media'],
  app: ['app'], account: ['account'] };
let pending = null;      // unsaved changes, kept while you move between the My settings pages

export async function renderSettings(target = $('#view'), page = 'appearance') {
  await ensureLanguages();
  const saved = { ...state.prefs };
  const draft = pending || { ...state.prefs };
  pending = draft;
  const f = state.features || {};
  const view = target;
  const body = view;
  const videoLibs = (state.libraries || []).filter(l => l.kind === 'movies' || l.kind === 'shows');
  const bannerLibs = (draft.home_banner_libraries || '').split(',').filter(Boolean);
  // Home's shape is the server's, not each person's: only an administrator is offered the sections, the
  // banner's libraries, how often it changes and the 30 seconds limit. Everyone gets the sound switch.
  const owns = !!state.user.is_admin;
  mount(view, html`<div class="lay-head">${layoutButtons()}</div>
    ${layoutBar()}
    <div class="settings"><div class="settings-cols">
      <section class="panel" data-panel="look"><h3>Look</h3><p class="lead">All seven themes are dark. The change shows at once; save to keep it.</p>
        <div class="theme-grid">${THEMES.map(t => {
          const c = PREVIEW[t.id];
          return html`<button type="button" class="theme-card ${draft.theme === t.id ? 'on' : ''}" data-theme-id="${t.id}" aria-pressed="${draft.theme === t.id}">
            <span class="mini" style="--m-bg:${c.bg};--m-card:${c.card};--m-text:${c.text};--m-accent:${c.accent};--m-glow:${c.glow};--m-r:${c.r}">
              <span class="glow"></span><span class="ttl"></span><span class="ln"></span><span class="btnm"></span>
              <span class="cards"><i></i><i></i><i></i><i></i></span></span>
            <span class="tn">${t.name}${draft.theme === t.id ? html`<span style="color:var(--accent);display:inline-flex">${icon('check')}</span>` : ''}</span>
            <span class="td">${t.desc}</span></button>`;
        })}</div>
        ${state.user.is_admin ? html`<div class="default-theme" id="p-default-theme"></div>` : ''}
        <div class="row-field" style="margin-top:22px"><label for="p-menu">Side menu when LENTA opens</label>
          <select class="input" id="p-menu">${[['collapsed', 'Collapsed — icons only'], ['open', 'Open — icons and names']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.menu_default ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:0">☰ at the top of the menu (or the \ key) opens or collapses it for the moment.</p>
        <div class="row-field" style="margin-top:18px"><label for="p-cards">Pictures of movies and shows</label>
          <select class="input" id="p-cards">${[['wide', 'Wide pictures with the name below (Netflix style)'], ['poster', 'Posters (the original style)']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.card_style ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:0">Used everywhere: Home, libraries, search and My list. In a library, the slider next to the sort menu sets how many fit in a row.</p>
        <div class="row-field" style="margin-top:18px"><label for="p-epart">Pictures of TV episodes</label>
          <select class="input" id="p-epart">${[['show', "The show's picture with its title (like movies)"], ['still', "A picture from the episode"]].map(([v, l]) =>
            html`<option value="${v}" ${v === (draft.episode_art || 'show') ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:0">In Continue watching, Next up and the rows of new episodes. The episode's season, number and name stay under the picture.</p>
        <div class="row-field" style="margin-top:18px"><label for="p-hover-trailer">Trailer in hover previews</label>
          <select class="input" id="p-hover-trailer">${[['off', 'Off — show the picture only'], ['muted', 'Play muted'], ['sound', 'Play with sound']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.hover_trailer ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:0">When you rest the pointer on a title, its preview card plays the trailer after a moment.
          With sound, the Home banner and theme music pause while it plays.</p>
      </section>

      <section class="panel" data-panel="audio"><h3>Audio and subtitles</h3>
        <label class="check" style="margin-bottom:6px"><input type="checkbox" id="p-auto" ${draft.auto_tracks === '1' ? raw('checked') : ''}>Automatically select audio and subtitle tracks</label>
        <p style="color:var(--muted);font-size:14px;margin:0 0 20px 28px">Pick tracks in your preferred languages when a title has them.</p>
        <div class="row-field"><label for="p-audio">Prefer audio tracks in</label>
          <select class="input" id="p-audio">${languageOptions(draft.audio_lang, { any: 'Original language' })}</select></div>
        <div class="row-field"><label for="p-mode">Subtitle mode</label>
          <select class="input" id="p-mode">${[['off', 'Off'], ['forced', 'Only forced subtitles'], ['always', 'Always enabled'],
            ['foreign', 'When the audio isn’t in my language']].map(([v, l]) => html`<option value="${v}" ${v === draft.subtitle_mode ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <div class="row-field"><label for="p-sublang">Prefer subtitles in</label>
          <select class="input" id="p-sublang">${languageOptions(draft.subtitle_lang, { any: 'Same as audio' })}</select></div>
        <label class="check" style="margin-top:6px"><input type="checkbox" id="p-dl" ${draft.subtitle_autodownload === '1' ? raw('checked') : ''} ${f.subtitles_online ? '' : raw('disabled')}>
          Download missing subtitles automatically when I press Play</label>
        <p style="color:var(--muted);font-size:14px;margin:6px 0 0 28px">${f.subtitles_online
          ? 'Searches OpenSubtitles in your subtitle language, preferring an exact match for your file.'
          : 'Needs online subtitles, which an administrator turns on in Settings › Subtitles.'}</p>
      </section>

      <section class="panel" data-panel="intros"><h3>TV show intros</h3>
        <div class="row-field"><label for="p-intro">When an episode's intro starts</label>
          <select class="input" id="p-intro">${[['button', 'Show a Skip intro button'], ['auto', 'Skip it automatically'], ['off', 'Off — play the intro']].map(([v, l]) =>
            html`<option value="${v}" ${v === (draft.intro_skip || 'button') ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:0">LENTA finds each season's intro by comparing the episodes' sound${f.intro_detection === false ? '. An administrator has switched intro detection off in Settings › Previews and intros.' : '.'}
          With <i>Skip it automatically</i> you can still press <b>Watch intro</b> to go back.</p>
      </section>

      <section class="panel" data-panel="home"><h3>Home screen</h3>
        <div class="row-field"><label for="p-home-trailer">Trailer in the big banner</label>
          <select class="input" id="p-home-trailer">${[['off', 'Off — show the picture only'], ['muted', 'Play in the background, muted'], ['sound', 'Play in the background, with sound']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.home_trailer ? raw('selected') : ''}>${l}</option>`)}</select></div>
        ${draft.home_trailer === 'off' ? html`<p style="color:var(--red);font-size:14px;margin:-6px 0 12px">While this is Off, no trailer plays in the banner, not even for titles ticked in Edit.</p>` : ''}
        ${owns ? html`
        <label class="check" style="margin:-4px 0 16px"><input type="checkbox" id="p-home-30" ${draft.home_trailer_30 === '1' ? raw('checked') : ''}>30 seconds limit</label>
        <p style="color:var(--muted);font-size:14px;margin:-8px 0 16px">The banner only plays trailers of titles marked <i>Show the trailer in the Home banner</i> (Edit › General); the others show their picture.</p>
        <div class="row-field"><label for="p-rotate">Change to another title every</label>
          <select class="input" id="p-rotate">${[['0', 'Never — keep one title per visit'], ['30', '30 seconds'], ['60', '1 minute'], ['120', '2 minutes'], ['300', '5 minutes'], ['600', '10 minutes']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.home_banner_interval ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <div class="row-field" style="align-items:flex-start"><label>Pick titles from</label>
          <div class="lib-checks">${videoLibs.length ? videoLibs.map(l => html`<label class="check"><input type="checkbox" data-banner-lib="${l.id}"
            ${!bannerLibs.length || bannerLibs.includes(String(l.id)) ? raw('checked') : ''}>${l.name}</label>`)
            : html`<span style="color:var(--muted)">No movie or TV libraries yet</span>`}</div></div>
        <p style="color:var(--muted);font-size:14px;margin:0">The featured title at the top of Home plays its trailer after a few seconds,
          and moves on to another random title from these libraries with its own trailer. It waits while you're scrolled down or in another tab.
          This is separate from the trailer setting for media pages below.</p>
        <h4 class="set-sub">Sections</h4>
        <p style="color:var(--muted);font-size:14px;margin:0 0 10px">Every row on Home. Drag one by its handle (or use the arrows) to move it,
          press <b>✎</b> to rename it, choose its libraries or delete it, and add your own with <b>New section</b>.
          Sections that appear later, such as a new library or genre, show up here too.</p>
        <ol class="sec-order" id="p-sections"><li class="sec-empty">Loading…</li></ol>
        <div class="sec-actions"><button type="button" class="btn" id="p-sections-new">${icon('plus')}New section</button>
          <button type="button" class="btn ghost" id="p-sections-reset">Default order</button></div>
        <div id="p-sections-hidden"></div>`
        : html`<p style="color:var(--muted);font-size:14px;margin:0">The rows on Home, their order and which titles the big banner
          draws from are the ones set for this server. The sound of the banner's trailer is yours: turn it off above if you'd rather
          it played quietly.</p>`}
      </section>

      <section class="panel" data-panel="media"><h3>Media pages</h3>
        <div class="row-field"><label for="p-trailer">Trailers</label>
          <select class="input" id="p-trailer">${[['off', 'Off'], ['button', 'Show a Trailer button'], ['background', 'Play in the background, muted'], ['background_sound', 'Play in the background, with sound']].map(([v, l]) =>
            html`<option value="${v}" ${v === draft.trailer_mode ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <div class="row-field"><label for="p-tskip">Start trailers after</label>
          <select class="input" id="p-tskip">${[['0', 'The beginning'], ['3', '3 seconds'], ['5', '5 seconds'], ['8', '8 seconds']].map(([v, l]) =>
            html`<option value="${v}" ${v === (draft.trailer_skip || '0') ? raw('selected') : ''}>${l}</option>`)}</select></div>
        <p style="color:var(--muted);font-size:14px;margin:-6px 0 14px">Skips the green "approved for appropriate audiences" card many older trailers start with (5–8 seconds is usually enough).
          Applies to trailers on media pages, in previews, on Home and the Trailer button.</p>
        <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-music" ${draft.theme_music === '1' ? raw('checked') : ''}>Play theme music when I open a movie or show</label>
        <div class="row-field" style="margin-left:28px"><label for="p-vol">Theme music volume</label>
          <input class="range" type="range" id="p-vol" min="0.05" max="1" step="0.05" value="${draft.theme_music_volume}"></div>
        <p style="color:var(--muted);font-size:14px;margin:0">Uses a theme file stored with the movie (theme.mp3) when there is one${f.theme_music_online ? ', otherwise a soundtrack preview from Apple Music' : ''}.
          ${f.trailers ? '' : ' Trailers need a TMDB key in Settings › Metadata › TMDB and language.'}</p>
      </section>

      <section class="panel" data-panel="app"><h3>LENTA app</h3>
        ${inAndroidApp() ? html`<p class="lead">You're using the LENTA Android app ${window.LentaApp.version()}, connected to <b>${location.host}</b>.</p>
          <button class="btn" id="p-change-server">Change server…</button>`
        : html`<p class="lead" id="p-install-text">${installHelp()}</p>
          <button class="btn primary" id="p-install" ${installAvailable() ? '' : raw('hidden')}>${icon('download')}Install the LENTA app</button>
          ${isAndroid() ? html`<p class="lead" style="margin-top:16px">Or get the Android app: it opens LENTA full screen and needs no browser settings.
            In the app, type this server's address: <b>${location.host}</b></p>
            <a class="btn" href="/download/lenta.apk" download>${icon('download')}Download the Android app (APK)</a>` : ''}`}
        <div id="p-keywatch-box" style="margin-top:18px">
          <label class="check"><input type="checkbox" id="p-keywatch"><span>Show what the remote sends (televisions)</span></label>
          <p class="lead">Puts a panel in the corner <b>on your televisions</b> showing the number each remote
            key sends and what LENTA makes of it — for when a key does nothing and nobody can say why, since
            the sets disagree about the numbers. Switch it on here, then open LENTA on the TV and press the
            key. Remember to switch it off afterwards.</p>
        </div>
        <p class="lead" style="margin-top:16px"><b>Samsung TV</b> (2017 or newer): open <b>${location.origin}</b> in the TV's Internet browser,
          or install the LENTA TV app from this server: switch the TV to Developer Mode, then on the server run
          <code>sudo /opt/lenta/deploy/install-tv.sh &lt;TV's IP address&gt;</code>. Step-by-step guide: docs/SAMSUNG-TV.md.</p></section>

      <section class="panel" data-panel="account"><h3>Account</h3>
        <button class="btn" id="p-pass">Change password</button></section>
      </div>
      <div id="p-hidden" hidden></div>
    </div>`);
  // only this page's blocks are shown; the others stay (hidden) so every control keeps working
  const keep = PAGE_PANELS[page] || ['look'];
  [...view.querySelectorAll('.settings-cols > .panel')].forEach(p => { if (!keep.includes(p.dataset.panel)) view.querySelector('#p-hidden').append(p); });

  // The key watcher: on at once, off at once, no Save — it is a torch, not a setting.
  const kw = view.querySelector('#p-keywatch');
  if (kw) {
    kw.checked = state.prefs?.tv_key_watch === '1';
    kw.addEventListener('change', async () => {
      const on = kw.checked;
      setKeyWatch(on);                                  // here and now, if this is a television
      try {
        await api.put('/api/me/prefs', { tv_key_watch: on ? '1' : '0' });
        state.prefs.tv_key_watch = on ? '1' : '0';      // and on the television, next time it opens
        toast(on ? 'The key panel will show on your televisions' : 'Key panel off');
      } catch (ex) {
        toast(ex.message || 'Not saved');
      }
    });
  }

  let lay = null;
  const isDirty = () => Object.keys(draft).some(k => String(draft[k]) !== String(saved[k]));
  const dirty = () => lay?.changed();       // the page's Save / Discard buttons follow
  view.querySelectorAll('[data-theme-id]').forEach(b => b.addEventListener('click', () => {
    draft.theme = b.dataset.themeId;
    applyTheme(draft.theme);
    view.querySelectorAll('[data-theme-id]').forEach(x => {
      const on = x === b;
      x.classList.toggle('on', on);
      x.setAttribute('aria-pressed', on);
      const name = THEMES.find(t => t.id === x.dataset.themeId).name;
      mount(x.querySelector('.tn'), html`${name}${on ? html`<span style="color:var(--accent);display:inline-flex">${icon('check')}</span>` : ''}`);
    });
    dirty();
    showDefaultTheme();
  }));
  // Administrators: make the chosen theme the server's default (the sign-in screen and new users)
  const showDefaultTheme = () => {
    const box = $('#p-default-theme');
    if (!box) return;
    const name = THEMES.find(t => t.id === draft.theme)?.name || draft.theme;
    const cur = THEMES.find(t => t.id === state.server?.theme)?.name || state.server?.theme || 'Projector';
    if (draft.theme === state.server?.theme) {
      mount(box, html`<span class="note">${icon('check')} ${name} is the server's default theme: the sign-in screen and new users get it.</span>`);
      return;
    }
    mount(box, html`<button type="button" class="btn" id="p-make-default">Use ${name} on the sign-in screen</button>
      <span class="note">Server default now: ${cur}. It's used on the sign-in screen and for new users (also in Settings › General).</span>`);
    $('#p-make-default').addEventListener('click', async () => {
      try {
        await api.put('/api/admin/settings', { default_theme: draft.theme });
        state.server = { ...(state.server || {}), theme: draft.theme };
        toast(`${name} is now the server's default theme`);
        showDefaultTheme();
      } catch (ex) { toast(ex.message, 'error'); }
    });
  };
  showDefaultTheme();
  // Some of these are only on an administrator's page (the Home screen's shape is the server's): a control
  // that is not there is not an error.
  const bind = (id, key, get) => $(id)?.addEventListener(id === '#p-vol' ? 'input' : 'change', (e) => { draft[key] = get(e.target); dirty(); });
  bind('#p-auto', 'auto_tracks', el => (el.checked ? '1' : '0'));
  bind('#p-audio', 'audio_lang', el => el.value);
  bind('#p-mode', 'subtitle_mode', el => el.value);
  bind('#p-sublang', 'subtitle_lang', el => el.value);
  bind('#p-dl', 'subtitle_autodownload', el => (el.checked ? '1' : '0'));
  bind('#p-trailer', 'trailer_mode', el => el.value);
  bind('#p-home-trailer', 'home_trailer', el => el.value);
  bind('#p-intro', 'intro_skip', el => el.value);
  bind('#p-tskip', 'trailer_skip', el => el.value);
  bind('#p-cards', 'card_style', el => el.value);
  bind('#p-epart', 'episode_art', el => el.value);
  bind('#p-menu', 'menu_default', el => el.value);
  bind('#p-hover-trailer', 'hover_trailer', el => el.value);
  bind('#p-rotate', 'home_banner_interval', el => el.value);
  const libTicks = (attr, key, what) => body.querySelectorAll(`[${attr}]`).forEach(cb => cb.addEventListener('change', () => {
    const boxes = [...body.querySelectorAll(`[${attr}]`)];
    if (!boxes.some(b => b.checked)) { cb.checked = true; toast(`Keep at least one library for ${what}`); return; }
    // every library ticked is stored as "all", so libraries added later are included automatically
    draft[key] = boxes.every(b => b.checked) ? '' : boxes.filter(b => b.checked).map(b => b.getAttribute(attr)).join(',');
    dirty();
  }));
  libTicks('data-banner-lib', 'home_banner_libraries', 'the banner');
  sectionOrder(body, draft, dirty);
  const pl = pageLayout(state.prefs.settings_layout, page);       // one saved arrangement per page
  lay = blockLayout({ root: view, canvas: view.querySelector('.settings-cols'), value: pl.value,
    form: { dirty: isDirty, save: () => saveSettings(), discard: () => { pending = null; applyTheme(state.prefs.theme); renderSettings(target, page); } },
    names: { look: 'Look', audio: 'Audio and subtitles', intros: 'TV show intros', home: 'Home screen', media: 'Media pages', app: 'LENTA app', account: 'Account' },
    save: async (value) => {
      const all = pageLayout(state.prefs.settings_layout, page).merge(value);
      await api.put('/api/me/prefs', { settings_layout: all });
      state.prefs.settings_layout = all; draft.settings_layout = all; saved.settings_layout = all;   // not an unsaved setting
    } });
  bind('#p-music', 'theme_music', el => (el.checked ? '1' : '0'));
  bind('#p-home-30', 'home_trailer_30', el => (el.checked ? '1' : '0'));
  bind('#p-vol', 'theme_music_volume', el => el.value);
  $('#p-pass').addEventListener('click', changePasswordDialog);
  const installUi = () => {
    if (!$('#p-install')) return;
    $('#p-install').hidden = !installAvailable();
    $('#p-install-text').textContent = installAvailable() ? 'Install LENTA on this device: it opens in its own window (full screen on phones) with its icon on your home screen.' : installHelp();
  };
  installUi();
  const offInstall = onInstallChange(installUi);
  window.addEventListener('hashchange', offInstall, { once: true });
  $('#p-install')?.addEventListener('click', async () => { if (await promptInstall()) toast('LENTA installed'); installUi(); });
  $('#p-change-server')?.addEventListener('click', () => window.LentaApp.changeServer());
  const saveSettings = async () => {
    try {
      const r = await api.put('/api/me/prefs', draft);
      state.prefs = r.prefs;
      state.features = r.features;
      Object.assign(saved, r.prefs);
      Object.assign(draft, r.prefs);
      applyTheme(state.prefs.theme);
      setArtStyle(state.prefs.card_style);
      setEpisodeArt(state.prefs.episode_art);
      applyMenuDefault(state.prefs.menu_default);
      renderRail();
      dirty();
      toast('Settings saved');
    } catch (ex) { toast(ex.message, 'error'); }
  };
  // Leaving Settings without saving: put the saved theme back and forget the changes.
  const restore = () => {
    if (!location.hash.startsWith('#/settings')) { pending = null; applyTheme(state.prefs.theme); }
    window.removeEventListener('hashchange', restore);
  };
  dirty();                 // changes made on another page and not saved yet
  window.addEventListener('hashchange', restore);
}


// ---- Settings › Home screen › Sections -----------------------------------------------------------------
// Every Home row: drag by the handle (mouse or touch) or move with the arrows (TV remote); edit to rename,
// pick libraries or delete; New section adds your own. Kept in draft.home_sections / draft.home_row_order.
const RULE_NAMES = { recent: 'Recently added', top: 'Highest rated (Top 10)', shuffle: 'Shuffled, a new mix every day',
  genre: 'One genre', channels: 'Channels' };

// The channels to choose from, fetched once the first picker opens — a tuner can carry a couple of
// hundred, which is not worth loading for someone who never opens Settings › Home screen.
let channelList = null;
async function allChannels() {
  if (!channelList) channelList = (await api.get('/api/livetv/channels')).channels || [];
  return channelList;
}

// The second dialog: every channel with a tick beside it. Answers with the chosen ids, or null if the
// person backed out, so that backing out leaves the section's existing choice alone.
function pickChannels(chosen) {
  return new Promise(async (resolve) => {
    let list;
    try { list = await allChannels(); } catch { toast('Could not load the channel list', 'error'); resolve(null); return; }
    if (!list.length) { toast('No channels yet - scan a tuner or add a playlist first.'); resolve(null); return; }
    const set = new Set(chosen);
    let answered = false;
    const m = modal(html`<h2>Channels in this section</h2>
      <p class="lead">Ticked channels appear in the section, in the order your provider numbers them.</p>
      <form id="chan-form">
        <div class="chan-head">
          <input class="input" id="chan-q" type="search" placeholder="Search channels" autocomplete="off">
          <button type="button" class="btn small" data-all>All</button>
          <button type="button" class="btn small" data-none>None</button>
        </div>
        <div class="chan-list" id="chan-list">${list.map(c => html`<label class="chan-row" data-name="${(c.name || '').toLowerCase()}">
          <input type="checkbox" data-chan="${c.id}" ${set.has(c.id) ? raw('checked') : ''}>
          <img src="/api/livetv/channels/${c.id}/logo" alt="" loading="lazy">
          <span class="chan-no">${c.number ?? ''}</span>
          <span class="chan-name">${c.name}</span></label>`)}</div>
        <div class="foot"><span class="chan-count" id="chan-count"></span>
          <button type="button" class="btn" data-close>Cancel</button>
          <button class="btn primary">Done</button></div>
      </form>`, { wide: true, onClose: () => { if (!answered) resolve(null); } });
    const box = m.box;
    const ticks = () => [...box.querySelectorAll('[data-chan]')];
    const shown = () => ticks().filter(t => !t.closest('.chan-row').hidden);
    const count = () => { box.querySelector('#chan-count').textContent = `${ticks().filter(t => t.checked).length} chosen`; };
    count();
    box.addEventListener('change', count);
    box.querySelector('#chan-q').addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      box.querySelectorAll('.chan-row').forEach(r => { r.hidden = !!q && !r.dataset.name.includes(q); });
    });
    // All and None act on what the search is showing, so they stay useful once it is narrowed down.
    box.querySelector('[data-all]').addEventListener('click', () => { shown().forEach(t => { t.checked = true; }); count(); });
    box.querySelector('[data-none]').addEventListener('click', () => { shown().forEach(t => { t.checked = false; }); count(); });
    box.querySelector('#chan-form').addEventListener('submit', (e) => {
      e.preventDefault();
      answered = true;                       // answer before closing: onClose would otherwise answer null
      resolve(ticks().filter(t => t.checked).map(t => Number(t.dataset.chan)));
      m.close();
    });
  });
}

async function sectionOrder(body, draft, dirty) {
  const list = body.querySelector('#p-sections');
  if (!list) return;
  let data;
  try { data = await api.get('/api/home/sections'); } catch { mount(list, html`<li class="sec-empty">Couldn't load the sections</li>`); return; }
  if (!list.isConnected) return;
  const libs = data.libraries || [];
  const genres = data.genres || [];
  let sections = data.sections || [];
  let cfg;
  try { cfg = JSON.parse(draft.home_sections || '{}') || {}; } catch { cfg = {}; }
  cfg = { custom: cfg.custom || [], titles: cfg.titles || {}, libs: cfg.libs || {}, hidden: cfg.hidden || [] };

  const libNames = (ids) => {
    const set = new Set(ids);
    if (libs.length && libs.every(l => set.has(l.id))) return 'All libraries';
    const names = libs.filter(l => set.has(l.id)).map(l => l.name);
    return names.length ? names.join(', ') : 'No libraries';
  };
  const describe = (x) => {
    if (x.custom && x.rule === 'channels') {
      const n = (x.channels || []).length;
      return `Channels · ${n} chosen`;
    }
    return [x.custom ? RULE_NAMES[x.rule] + (x.rule === 'genre' && x.genre ? `: ${x.genre}` : '') : '', libNames(x.libs)].filter(Boolean).join(' · ');
  };
  const store = () => {
    draft.home_sections = JSON.stringify(cfg);
    draft.home_row_order = JSON.stringify([...list.querySelectorAll('li[data-sec]')].map(li => li.dataset.sec));
    dirty();
  };
  const render = () => {
    const shown = sections.filter(x => !x.hidden);
    mount(list, shown.length ? html`${shown.map(x => html`<li class="sec-item" data-sec="${x.id}">
      <span class="sec-grip" aria-hidden="true" title="Drag to move">${icon('grip')}</span>
      <span class="sec-name"><b>${x.title}</b>${x.custom ? html`<span class="sec-tag">Own</span>` : ''}<small>${describe(x)}</small></span>
      <button type="button" class="sec-btn" data-edit aria-label="Edit ${x.title}" title="Rename, libraries, delete">${icon('edit')}</button>
      <button type="button" class="sec-btn" data-move="-1" aria-label="Move ${x.title} up">${icon('chevron-up')}</button>
      <button type="button" class="sec-btn" data-move="1" aria-label="Move ${x.title} down">${icon('chevron-down')}</button></li>`)}`
      : html`<li class="sec-empty">No sections. Add one with New section.</li>`);
    const hidden = sections.filter(x => x.hidden);
    mount(body.querySelector('#p-sections-hidden'), hidden.length ? html`<div class="sec-hidden"><div class="sec-hidden-title">Deleted sections</div>
      ${hidden.map(x => html`<div class="sec-hidden-item"><span>${x.title}</span><button type="button" class="btn small" data-restore="${x.id}">Restore</button></div>`)}</div>` : '');
  };
  render();

  // ---- the edit dialog (also for a new section)
  const edit = (x, isNew = false) => {
    const custom = !!x.custom;
    const m = modal(html`<h2>${isNew ? 'New section' : `Edit ${x.title}`}</h2>
      <form id="sec-form">
        <div class="field"><label for="sec-name">Name</label><input class="input" id="sec-name" maxlength="80" value="${x.title}" required autofocus>
          ${!custom && x.title !== x.default_title ? html`<span class="hint">Original name: ${x.default_title}</span>` : ''}</div>
        ${custom ? html`<div class="field"><label for="sec-rule">Shows</label><select class="input" id="sec-rule">
            ${Object.entries(RULE_NAMES).map(([k, v]) => html`<option value="${k}" ${k === x.rule ? raw('selected') : ''}>${v}</option>`)}</select></div>
          <div class="field" id="sec-genre-f" ${x.rule === 'genre' ? '' : raw('hidden')}><label for="sec-genre">Genre</label><select class="input" id="sec-genre">
            ${genres.map(g => html`<option ${g === x.genre ? raw('selected') : ''}>${g}</option>`)}</select></div>
          <div class="field" id="sec-chan-f" ${x.rule === 'channels' ? '' : raw('hidden')}><label>Channels</label>
            <div class="chan-pickrow"><button type="button" class="btn" id="sec-chan">Choose channels</button>
              <span class="hint" id="sec-chan-n"></span></div></div>`
          : x.type === 'genre' ? html`<p class="hint" style="margin:-4px 0 14px;color:var(--muted);font-size:14px">Shows ${x.genre} titles from these libraries.</p>` : ''}
        <div class="field"><label>Fill it from</label><div class="lib-checks">${libs.map(l => html`<label class="check"><input type="checkbox" data-sec-lib="${l.id}" ${x.libs.includes(l.id) ? raw('checked') : ''}>${l.name}</label>`)}</div></div>
        <p class="error-text" id="sec-err"></p>
        <div class="foot">
          ${isNew ? '' : html`<button type="button" class="btn danger" id="sec-del" style="margin-right:auto">${icon('trash')}Delete</button>`}
          ${!custom && !isNew && (x.renamed || x.libs_changed || x.title !== x.default_title) ? html`<button type="button" class="btn ghost" id="sec-reset">Original settings</button>` : ''}
          <button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${isNew ? 'Add section' : 'Apply'}</button></div>
      </form>`);
    const box = m.box;
    x.channels = [...(x.channels || [])];
    const liveLibs = new Set(libs.filter(l => l.kind === 'livetv').map(l => l.id));
    const rule = box.querySelector('#sec-rule');
    const chanCount = () => {
      const n = x.channels.length;
      box.querySelector('#sec-chan-n').textContent = n ? `${n} channel${n === 1 ? '' : 's'} chosen` : 'None chosen yet';
    };
    // Showing a live channel needs a live TV library in the section, so the option appears with one and
    // goes away without one - rather than offering a choice that would quietly produce an empty row.
    const syncRule = () => {
      const live = [...box.querySelectorAll('[data-sec-lib]')].some(c => c.checked && liveLibs.has(Number(c.dataset.secLib)));
      const opt = rule?.querySelector('option[value="channels"]');
      if (opt) {
        opt.hidden = !live;
        opt.disabled = !live;
        if (!live && rule.value === 'channels') rule.value = 'recent';
      }
      if (!rule) return;
      box.querySelector('#sec-genre-f').hidden = rule.value !== 'genre';
      box.querySelector('#sec-chan-f').hidden = rule.value !== 'channels';
      chanCount();
    };
    rule?.addEventListener('change', syncRule);
    box.querySelectorAll('[data-sec-lib]').forEach(c => c.addEventListener('change', syncRule));
    box.querySelector('#sec-chan')?.addEventListener('click', async () => {
      const got = await pickChannels(x.channels);
      if (got) { x.channels = got; chanCount(); }
    });
    if (custom) syncRule();
    box.querySelector('#sec-reset')?.addEventListener('click', () => {
      delete cfg.titles[x.id]; delete cfg.libs[x.id];
      Object.assign(x, { title: x.default_title, libs: [...(x.default_libs || x.libs)], renamed: false, libs_changed: false });
      m.close(); render(); store();
    });
    box.querySelector('#sec-del')?.addEventListener('click', () => {
      if (custom) {
        cfg.custom = cfg.custom.filter(c => c.id !== x.id);
        sections = sections.filter(s2 => s2 !== x);
      } else {
        if (!cfg.hidden.includes(x.id)) cfg.hidden.push(x.id);
        x.hidden = true;
      }
      m.close(); render(); store();
      toast(custom ? `Deleted ${x.title}` : `Deleted ${x.title}. You can restore it under Deleted sections.`);
    });
    box.querySelector('#sec-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const name = box.querySelector('#sec-name').value.trim();
      const picked = [...box.querySelectorAll('[data-sec-lib]')].filter(c => c.checked).map(c => Number(c.dataset.secLib));
      const err = box.querySelector('#sec-err');
      if (!name) { err.textContent = 'Give the section a name.'; return; }
      if (!picked.length) { err.textContent = 'Tick at least one library.'; return; }
      x.title = name; x.libs = picked;
      if (custom) {
        x.rule = box.querySelector('#sec-rule').value;
        x.genre = x.rule === 'genre' ? box.querySelector('#sec-genre').value : '';
        if (x.rule === 'genre' && !x.genre) { err.textContent = 'There are no genres yet.'; return; }
        if (x.rule !== 'channels') x.channels = [];
        if (x.rule === 'channels' && !x.channels.length) {
          err.textContent = 'Choose at least one channel for this section.'; return;
        }
        const c = { id: x.id, title: x.title, libs: x.libs, rule: x.rule, genre: x.genre, channels: x.channels };
        const at = cfg.custom.findIndex(c2 => c2.id === x.id);
        if (at >= 0) cfg.custom[at] = c; else cfg.custom.push(c);
        if (isNew) sections.unshift(x);         // new sections start at the top; move them where you like
      } else {
        if (name !== x.default_title) cfg.titles[x.id] = name; else delete cfg.titles[x.id];
        const same = (x.default_libs || []).length === picked.length && picked.every(i => (x.default_libs || []).includes(i));
        if (same) delete cfg.libs[x.id]; else cfg.libs[x.id] = picked;
      }
      m.close(); render(); store();
    });
  };

  body.querySelector('#p-sections-new').addEventListener('click', () => {
    const video = libs.filter(l => l.kind === 'movies' || l.kind === 'shows').map(l => l.id);
    edit({ id: `c-${Date.now().toString(36)}`, title: '', custom: true, rule: 'recent', genre: '', channels: [],
      libs: video.length ? video : libs.map(l => l.id) }, true);
  });
  body.querySelector('#p-sections-hidden').addEventListener('click', (e) => {
    const b = e.target.closest('[data-restore]');
    if (!b) return;
    const x = sections.find(s2 => s2.id === b.dataset.restore);
    cfg.hidden = cfg.hidden.filter(i => i !== x.id);
    x.hidden = false;
    render(); store();
  });
  body.querySelector('#p-sections-reset').addEventListener('click', async () => {
    let def;
    try { def = (await api.get('/api/home/sections?default=1')).sections || []; } catch { return; }
    // the default order of the built-in sections, own sections after them
    const byId = new Map(sections.map(x => [x.id, x]));
    const ordered = def.map(d => byId.get(d.id)).filter(Boolean);
    sections = [...ordered, ...sections.filter(x => !ordered.includes(x))];
    render();
    draft.home_row_order = '';
    draft.home_sections = JSON.stringify(cfg);
    dirty();
  });
  list.addEventListener('click', (e) => {
    if (e.target.closest('[data-edit]')) {
      edit(sections.find(x => x.id === e.target.closest('li').dataset.sec));
      return;
    }
    const b = e.target.closest('[data-move]');
    if (!b) return;
    const li = b.closest('li');
    if (b.dataset.move === '-1' && li.previousElementSibling) li.parentNode.insertBefore(li, li.previousElementSibling);
    else if (b.dataset.move === '1' && li.nextElementSibling) li.parentNode.insertBefore(li.nextElementSibling, li);
    else return;
    b.focus();
    syncOrder();
  });
  // keep `sections` in the order of the list (so a later render doesn't undo a move)
  const syncOrder = () => {
    const order = [...list.querySelectorAll('li[data-sec]')].map(li => li.dataset.sec);
    sections.sort((p, q) => (order.indexOf(p.id) + 1 || 1e6) - (order.indexOf(q.id) + 1 || 1e6));
    store();
  };
  // dragging: the item follows the pointer, the others make room
  const down = window.PointerEvent ? 'pointerdown' : 'mousedown';
  const move = window.PointerEvent ? 'pointermove' : 'mousemove';
  const up = window.PointerEvent ? ['pointerup', 'pointercancel'] : ['mouseup'];
  list.addEventListener(down, (e) => {
    const grip = e.target.closest('.sec-grip');
    if (!grip || (e.button !== undefined && e.button !== 0)) return;
    e.preventDefault();
    const li = grip.closest('li');
    const grab = e.clientY - li.getBoundingClientRect().top;      // where on the item it was picked up
    li.classList.add('dragging');
    list.classList.add('sorting');
    const onMove = (ev) => {
      let before = null;
      for (const o of list.children) {
        if (o === li) continue;
        const r = o.getBoundingClientRect();
        if (ev.clientY < r.top + r.height / 2) { before = o; break; }
      }
      if (before !== li.nextElementSibling) list.insertBefore(li, before);
      li.style.transform = '';
      li.style.transform = `translateY(${ev.clientY - grab - li.getBoundingClientRect().top}px)`;
    };
    const onUp = () => {
      li.style.transform = '';
      li.classList.remove('dragging');
      list.classList.remove('sorting');
      window.removeEventListener(move, onMove);
      up.forEach(u => window.removeEventListener(u, onUp));
      syncOrder();
    };
    window.addEventListener(move, onMove);
    up.forEach(u => window.addEventListener(u, onUp));
  });
}
