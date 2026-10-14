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
import { tvMode } from './tv.js';

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

/** This device rather than the server: which of the two clients it was given, and the engine behind it. */
function deviceFacts() {
  const legacy = !!window.LENTA_LEGACY;
  const root = document.documentElement;
  const missing = ['no-vars', 'no-grid', 'no-sticky', 'no-flexgap'].filter(c => root.classList.contains(c));
  const where = inAndroidApp() ? 'Android app' : isStandalone() ? 'installed app window' : 'browser';
  return [
    ['Client', legacy ? 'Compatibility build — for televisions from 2017 to 2022' : 'Standard build'],
    ['Running in', `${where}${tvMode() ? ' · television mode' : ''}`],
    ['Screen', `${window.screen?.width || 0} × ${window.screen?.height || 0}`],
    missing.length ? ['Engine lacks', missing.map(c => ({ 'no-vars': 'CSS variables', 'no-grid': 'CSS grid', 'no-sticky': 'sticky position', 'no-flexgap': 'flex gap' })[c]).join(', ')] : null,
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
        ${facts([
          ['Films and shows', 'The Movie Database (TMDB)'],
          ['Streaming availability', 'JustWatch, through TMDB'],
          ['Trailers', 'KinoCheck and YouTube'],
          ['Subtitles', 'OpenSubtitles'],
          ['Background and ratings', 'Wikipedia, IMDb and OMDb'],
          ['Live TV guide', 'The broadcast itself (DVB event tables)'],
        ])}
        <p class="about-note">This product uses the TMDB API but is not endorsed or certified by TMDB.
          Streaming availability data is provided by JustWatch. Nothing from a streaming service plays
          inside LENTA — a title opens on the service itself.</p></section>

      <section class="panel"><h3>Licence and source</h3>
        <p class="about-note">LENTA is released into the <b>public domain</b> under the Unlicense.
          No copyright is claimed: copy it, change it, sell it, there is nothing to ask. The third-party
          libraries it is built on keep their own licences.</p>
        <p><a class="btn" href="${s.project}" target="_blank" rel="noopener">${icon('external')}Source on GitHub</a></p></section>
    </div></div>`);
}
