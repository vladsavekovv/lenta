// Settings › About — what this LENTA is: version and licence, the machine it runs on, what the
// library holds, which client this device is running, and who the data comes from. Read-only: there
// is nothing to save here, so the page carries no Save bar and no draft.
//
// Not the About panel of a film or a show — that is views.js with the server's about.py. This page
// is about the installation, and it is the page to photograph when something has gone wrong: it
// names the version, the engine and the client build in one picture.
import { api } from './api.js';
import { $, fmtBytes, html, icon, mount } from './ui.js';
import { inAndroidApp, isStandalone } from './pwa.js';
import { tvMode, tvPlatform } from './tv.js';
import { state } from './app.js';

const num = (n) => String(n ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');

/** Uptime as a person reads it: "6 days, 4 hours", not 148:12:09. */
export function upFor(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  const part = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`;
  if (d) return `${part(d, 'day')}, ${part(h, 'hour')}`;
  if (h) return `${part(h, 'hour')}, ${part(m, 'minute')}`;
  return part(m, 'minute');
}

/** "1 240 films · 48 shows (1 102 episodes) · 188 channels" — only what this account can see. */
export function libraryLine(counts = {}) {
  const c = (k) => counts[k] || 0;
  const parts = [];
  if (c('movie')) parts.push(`${num(c('movie'))} film${c('movie') === 1 ? '' : 's'}`);
  if (c('show')) parts.push(`${num(c('show'))} TV show${c('show') === 1 ? '' : 's'}${c('episode') ? ` (${num(c('episode'))} episodes)` : ''}`);
  if (c('album')) parts.push(`${num(c('album'))} album${c('album') === 1 ? '' : 's'}${c('track') ? ` (${num(c('track'))} tracks)` : ''}`);
  if (c('book')) parts.push(`${num(c('book'))} audiobook${c('book') === 1 ? '' : 's'}`);
  if (c('photoalbum')) parts.push(`${num(c('photoalbum'))} photo album${c('photoalbum') === 1 ? '' : 's'}${c('photo') ? ` (${num(c('photo'))} photos)` : ''}`);
  if (c('channel')) parts.push(`${num(c('channel'))} channel${c('channel') === 1 ? '' : 's'}`);
  return parts.join(' · ') || 'nothing yet';
}

const HW = { nvenc: 'NVIDIA NVENC', qsv: 'Intel Quick Sync', vaapi: 'VA-API', none: 'Off — software (CPU)' };

// Which of the three builds this device was handed. Worth naming precisely: it is the first thing to
// know about a television fault, and the one thing nobody can work out from a photograph of the screen.
const BUILDS = {
  new: 'Standard build',
  mid: 'Compatibility build — for televisions from 2019 to 2022',
  old: 'Compatibility build — for televisions from 2017 to 2018',
};

/** What this engine will admit to playing, for the photograph.
 *
 *  Worth having written down because it is the one number nobody can work out from a desk: a Samsung
 *  set decodes far more than its browser will own up to, and how much more decides whether a film is
 *  played as it is or converted first. canPlayType's three answers are kept as they come — "probably"
 *  and "maybe" mean different things and flattening them to a tick would throw away the distinction.
 */
function codecFacts() {
  const v = document.createElement('video');
  const a = document.createElement('audio');
  const say = (el, mime) => { try { return el.canPlayType(mime) || 'no'; } catch { return 'threw'; } };
  const list = (pairs, el) => pairs.map(([name, mime]) => `${name} ${say(el, mime)}`).join(' · ');
  return [
    ['Video', list([['H.264', 'video/mp4; codecs="avc1.640028"'], ['HEVC', 'video/mp4; codecs="hvc1.2.4.L153.B0"'],
                    ['VP9', 'video/webm; codecs="vp9"'], ['AV1', 'video/mp4; codecs="av01.0.08M.08"']], v)],
    ['Containers', list([['MKV', 'video/x-matroska'], ['TS', 'video/mp2t'], ['AVI', 'video/x-msvideo'],
                         ['HLS', 'application/vnd.apple.mpegurl']], v)],
    ['Audio', list([['AAC', 'audio/mp4; codecs="mp4a.40.2"'], ['AC-3', 'audio/mp4; codecs="ac-3"'],
                    ['E-AC-3', 'audio/mp4; codecs="ec-3"'], ['FLAC', 'audio/flac']], a)],
  ];
}

/** What the app's own page could read off the television, when LENTA is running inside it. Empty in a
 *  TV's web browser, and empty in the app until 1.17 — before that the client ran on the server's
 *  origin, where Tizen grants no device API at all. */
function tvFacts() {
  const p = tvPlatform();
  if (!p) return [];
  const panel = { fhd: 'FHD', uhd: '4K', '8k': '8K' }[p.panel] || 'unknown';
  return [
    ['Television', [p.realModel || p.model, p.modelCode, p.firmware].filter(Boolean).join(' · ') || 'not readable'],
    ['Panel', `${panel}${p.hdr ? ' · HDR' : ''}${p.tizenVersion ? ` · Tizen ${p.tizenVersion}` : ''}`],
    ['Remote keys', p.registered?.length ? `${p.registered.length} registered (${p.registered.join(', ')})`
      : 'none registered — only the arrows, OK and Back arrive'],
    ['Set plays the film', !p.avplay ? 'not offered by this app'
      : state.prefs?.tv_player === '1' ? 'on — the television decodes'
      : 'available, switched off (Settings › Playback › Media pages)'],
  ];
}

/** This device rather than the server: which of the three clients it was given, and the engine behind it. */
function deviceFacts() {
  const root = document.documentElement;
  const missing = ['no-vars', 'no-grid', 'no-sticky', 'no-flexgap'].filter(c => root.classList.contains(c));
  const where = inAndroidApp() ? 'Android app' : isStandalone() ? 'installed app window' : 'browser';
  return [
    ['Client', BUILDS[window.LENTA_TIER] || (window.LENTA_LEGACY ? BUILDS.old : BUILDS.new)],
    ['Running in', `${where}${tvMode() ? ' · television mode' : ''}`],
    ['Screen', `${window.screen?.width || 0} × ${window.screen?.height || 0}`],
    missing.length ? ['Engine lacks', missing.map(c => ({ 'no-vars': 'CSS variables', 'no-grid': 'CSS grid', 'no-sticky': 'sticky position', 'no-flexgap': 'flex gap' })[c]).join(', ')] : null,
    ...(tvMode() ? codecFacts() : []),
    ...tvFacts(),
  ].filter(Boolean);
}

const facts = (rows) => html`<dl class="facts">${rows.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>`;

export async function renderAbout(target = $('#view')) {
  mount(target, html`<div class="settings"><p class="lead">Reading…</p></div>`);
  let s;
  try {
    s = await api.get('/api/system');
  } catch (ex) {
    mount(target, html`<div class="settings"><p class="lead">${ex.message || "Couldn't read the server's details."}</p></div>`);
    return;
  }
  const a = s.admin;
  const server = [
    ['Version', s.version],
    s.server_name ? ['Server name', s.server_name] : null,
    ['Running for', upFor(s.uptime)],
    ['Library', `${libraryLine(s.counts)} — ${fmtBytes(s.media_size)} in ${num(s.libraries)} ${s.libraries === 1 ? 'library' : 'libraries'}`],
    ['Transcoding', `${HW[s.hardware.accel] || s.hardware.accel}${s.hardware.encoder ? ` · ${s.hardware.encoder}` : ''}`],
    ['Machine', [s.os, s.kernel, s.arch].filter(Boolean).join(' · ')],
  ].filter(Boolean);
  const built = [
    ['Python', s.python],
    s.fastapi ? ['FastAPI', s.fastapi] : null,
    s.uvicorn ? ['Uvicorn', s.uvicorn] : null,
    ['SQLite', s.sqlite],
    ['FFmpeg', s.ffmpeg],
  ].filter(Boolean);
  const admin = a ? [
    ['Listening on', html`<span class="mono">${a.listening}</span>`],
    ['Service account', a.service_user || 'unknown'],
    ['Data folder', html`<span class="mono">${a.data_dir}</span>${a.data_free == null ? '' : ` — ${fmtBytes(a.data_free)} free`}`],
    ['Transcoding folder', html`<span class="mono">${a.transcode_dir}</span>`],
    a.db_size == null ? null : ['Database', fmtBytes(a.db_size)],
    ['Accounts', num(a.users)],
  ].filter(Boolean) : [];

  mount(target, html`<div class="settings about-lenta">
    <div class="about-hero">
      <img class="about-logo" src="img/lenta-logo.png" alt="">
      <div><h2>${s.app}</h2>
        <p class="about-ver">Version ${s.version} · ${s.licence}</p></div>
    </div>

    <div class="settings-cols">
      <section class="panel"><h3>This server</h3>${facts(server)}</section>

      <section class="panel"><h3>This device</h3>${facts(deviceFacts())}
        <p class="about-ua mono">${navigator.userAgent}</p></section>

      <section class="panel"><h3>Built with</h3>${facts(built)}
        <p class="about-note">The web client is plain JavaScript — no framework. Playback uses
          <b>hls.js</b> where the browser cannot play a stream itself.</p></section>

      ${a ? html`<section class="panel"><h3>This installation</h3>${facts(admin)}</section>` : ''}

      <section class="panel"><h3>Where the information comes from</h3>
        <!-- TMDB require their logo alongside the notice below, and require it to be less prominent
             than LENTA's own (API Terms of Use, paragraph 3). The file is their official one, dropped
             in by hand from themoviedb.org/about/logos-attribution — it is their trademark, not ours
             to draw. If it is not there, the image takes itself off the page rather than showing a
             broken picture, and tests/test_tmdb_terms.py says so. -->
        <img class="about-tmdb" src="img/tmdb.svg" alt="The Movie Database"
             onerror="this.style.display='none'">
        ${facts([
          ['Films and shows', 'The Movie Database (TMDB)'],
          ['Streaming availability', 'JustWatch, through TMDB'],
          ['Trailers', 'KinoCheck and YouTube'],
          ['Subtitles', 'OpenSubtitles'],
          ['Background and ratings', 'Wikipedia, IMDb and OMDb'],
          ['Live TV guide', 'The broadcast itself (DVB event tables)'],
        ])}
        <p class="about-note">This product uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
          Streaming availability data is provided by JustWatch. Nothing from a streaming service plays
          inside LENTA — a title opens on the service itself.</p></section>

      <section class="panel"><h3>Licence and source</h3>
        <p class="about-note">LENTA is released into the <b>public domain</b> under the Unlicense.
          No copyright is claimed: copy it, change it, sell it, there is nothing to ask. The third-party
          libraries it is built on keep their own licences.</p>
        <p><a class="btn" href="${s.project}" target="_blank" rel="noopener">${icon('external')}Source on GitHub</a></p></section>
    </div></div>`);
}
