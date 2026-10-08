// Administrative dashboard: overview, libraries, users, metadata fixes, settings, logs.
import { api } from './api.js';
import { openEditor, refreshMetadata } from './editor.js';
import { state, loadLibraries, refreshView, renderRail, THEMES } from './app.js';
import {
  $, confirmDialog, fmtAgo, fmtBytes, fmtTime, html, icon, img, KIND_ICON, modal, mount, popMenu, raw, toast,
} from './ui.js';
import { blockLayout, layoutBar, layoutButtons } from './layout.js';

// The Dashboard and Settings tabs: blocks you can fold, move and resize (layout.js), a layout per page and user
const layoutPref = (key) => ({
  value: state.prefs?.[key] || '',
  save: async (value) => { await api.put('/api/me/prefs', { [key]: value }); state.prefs[key] = value; },
});

const TABS = [['dashboard', 'Dashboard'], ['libraries', 'Libraries'], ['users', 'Users'], ['metadata', 'Metadata'], ['settings', 'Settings'], ['logs', 'Logs']];
const KIND_NAMES = { movies: 'Movies', shows: 'TV shows', music: 'Music', photos: 'Photos' };
let pollTimer = null;

export async function renderAdmin(tab, q = new URLSearchParams()) {
  clearInterval(pollTimer);
  const view = $('#view');
  if (!state.user?.is_admin) { location.hash = '#/'; return; }
  if (!TABS.some(([t]) => t === tab)) tab = 'dashboard';
  mount(view, html`<div class="page">
    <div class="page-head"><div><h1 class="page-title">Server admin</h1><div class="page-sub">${state.user.server_name}</div></div></div>
    <nav class="tabs">${TABS.map(([t, l]) => html`<a href="#/admin/${t}" class="${t === tab ? 'active' : ''}">${l}</a>`)}</nav>
    <div id="admin-body"></div></div>`);
  const body = $('#admin-body');
  await { dashboard, libraries, users, metadata, settings, logs }[tab](body, q);
  // stop polling once the user leaves the page
  const stop = () => { if (!location.hash.startsWith(`#/admin/${tab}`) && !(tab === 'dashboard' && location.hash === '#/admin')) { clearInterval(pollTimer); window.removeEventListener('hashchange', stop); } };
  window.addEventListener('hashchange', stop);
}

// ---------------------------------------------------------------- dashboard
async function dashboard(body) {
  let lay = null;
  const draw = async () => {
    let o;
    try { o = await api.get('/api/admin/overview'); } catch { return; }
    if (!body.isConnected) { clearInterval(pollTimer); return; }
    const c = o.counts;
    const scan = o.scan;
    const hw = o.hardware;
    const hwName = { nvenc: 'NVIDIA NVENC', qsv: 'Intel Quick Sync', vaapi: 'VA-API', none: 'Software (x264)' }[hw.accel] || hw.accel;
    if (lay?.editing()) return;               // while the blocks are being arranged they stay as they are
    const markup = html`<div class="panels" id="dash-panels">
      <div data-panel="now" class="panel wide"><h3>Playing now</h3>
        ${o.now_playing.length ? o.now_playing.map(n => {
          const pct = n.duration ? n.position / n.duration * 100 : 0;
          const session = o.sessions.find(s => s.item_id === n.item_id && s.user === n.user && s.running);
          return html`<div class="np"><span class="pill ${n.mode === 'transcode' ? 'accent' : 'green'}" style="text-align:center">${{ direct: 'Direct', remux: 'Stream', transcode: 'Convert' }[n.mode] || n.mode || 'Play'}</span>
            <div><div style="font-weight:600">${n.title}</div>
              <div class="who">${n.user} · ${n.state === 'paused' ? 'paused' : 'playing'} · ${fmtTime(n.position)} of ${fmtTime(n.duration)}${n.quality && n.quality !== 'original' ? ` · ${n.quality}` : ''}${session?.speed ? ` · FFmpeg ${session.speed}` : ''}</div>
              <div class="meter"><i style="width:${pct.toFixed(1)}%"></i></div></div></div>`;
        }) : html`<p style="color:var(--muted);margin:0">Nobody is watching right now.</p>`}</div>

      <div data-panel="library" class="panel"><h3>Library</h3><div class="stat-line">
        <div><strong>${c.movie || 0}</strong><span>Movies</span></div>
        <div><strong>${c.show || 0}</strong><span>Shows</span></div>
        <div><strong>${c.episode || 0}</strong><span>Episodes</span></div>
        <div><strong>${c.album || 0}</strong><span>Albums</span></div>
        <div><strong>${c.track || 0}</strong><span>Tracks</span></div>
        <div><strong>${c.photo || 0}</strong><span>Photos</span></div></div>
        <p style="color:var(--muted);font-size:14px;margin:16px 0 0">${fmtBytes(o.media_size)} of media across ${o.libraries} librar${o.libraries === 1 ? 'y' : 'ies'} · ${o.users} user${o.users === 1 ? '' : 's'}</p></div>

      <div data-panel="scan" class="panel"><h3>Library scan</h3>
        ${scan.running ? html`${(scan.scans?.length ? scan.scans : [scan]).map(sc => html`<div class="scan-one">
            <div>${sc.library} — ${sc.phase}</div>
            <div class="meter"><i style="width:${sc.total ? (sc.done / sc.total * 100).toFixed(1) : 2}%"></i></div>
            <div style="font-size:13px;color:var(--muted)">${sc.done} of ${sc.total}${scan.queued.includes(sc.library_id) ? ' · another scan of it waiting' : ''}</div></div>`)}
            ${(scan.scans?.length || 0) > 1 ? html`<div style="font-size:13px;color:var(--muted)">${scan.scans.length} libraries scanning side by side</div>` : ''}`
          : html`<div style="color:var(--muted)">Idle${scan.finished ? ` · last finished ${fmtAgo(scan.finished)}` : ''}</div>
            ${scan.message ? html`<div style="font-size:14px;margin-top:6px">${scan.message}</div>` : ''}`}
        ${o.trailers?.titles ? html`<div style="font-size:13px;color:var(--muted);margin-top:10px">Trailers indexed: ${o.trailers.with_trailer} of ${o.trailers.titles} titles have one${o.trailers.files ? ` · ${o.trailers.files} your files` : ''}${o.trailers.apple_enabled ? ` · ${o.trailers.apple} from Apple` : ''}${o.trailers.kinocheck_enabled ? ` · ${o.trailers.kinocheck} from KinoCheck (${o.trailers.kinocheck_usage.today} of ${o.trailers.kinocheck_usage.limit} lookups today)` : ''}${o.trailers.running ? ` · looking up ${o.trailers.done} of ${o.trailers.total}` : ''}${o.trailers.apple_error ? html`<br><span style="color:var(--muted)">Apple trailers: ${o.trailers.apple_error}</span>` : ''}${o.trailers.kinocheck_error ? html`<br><span style="color:var(--red)">KinoCheck: ${o.trailers.kinocheck_error}</span>` : ''}</div>` : ''}
        ${o.watcher?.enabled ? html`<div style="font-size:13px;color:var(--muted);margin-top:4px">Watching ${o.watcher.folders} folders for new media${o.watcher.waiting ? ` · ${o.watcher.waiting} copy in progress, scanned when it finishes` : ''}</div>` : ''}
        ${o.intros?.enabled && o.intros.episodes ? html`<div style="font-size:13px;color:var(--muted);margin-top:4px">Intros: found in ${o.intros.found} of ${o.intros.checked} episodes checked (${o.intros.episodes} in all)${o.intros.running && o.intros.total ? (o.intros.paused ? ' · paused while someone is watching' : ` · looking at season ${o.intros.done + 1} of ${o.intros.total}`) : ''}</div>` : ''}
        ${o.trickplay?.enabled && o.trickplay.videos ? html`<div style="font-size:13px;color:var(--muted);margin-top:4px">Seek-bar previews: ${o.trickplay.ready} of ${o.trickplay.videos} videos${o.trickplay.running && o.trickplay.total ? (o.trickplay.paused ? ' · paused while someone is watching' : ` · making ${o.trickplay.done + 1} of ${o.trickplay.total}`) : ''}</div>` : ''}
        ${o.about?.titles ? html`<div style="font-size:13px;color:var(--muted);margin-top:4px">About panels: ${o.about.wiki} with a Wikipedia summary${o.about.omdb_enabled ? ` · ${o.about.omdb} with IMDb / Rotten Tomatoes ratings (${o.about.omdb_used_today} OMDb lookups today)` : ''}${o.about.running && o.about.phase ? ` · updating ${{ details: 'TMDB details', wikipedia: 'Wikipedia', omdb: 'ratings' }[o.about.phase]} ${o.about.done} of ${o.about.total}` : ''}</div>` : ''}
        <div class="actions" style="margin-top:16px"><button class="btn small" id="scan-all">${icon('refresh')}Scan all libraries</button></div></div>

      <div data-panel="transcoding" class="panel"><h3>Transcoding</h3>
        <div style="font-size:15px"><b>${hwName}</b> <span class="pill ${hw.accel === 'none' ? '' : 'green'}">${hw.encoder}</span></div>
        <div style="font-size:14px;color:var(--muted);margin-top:8px">Working hardware: ${hw.available.length ? hw.available.join(', ') : 'none found'}${hw.accel === 'nvenc' ? ` · full GPU pipeline: ${hw.scale_cuda ? 'yes' : 'no'}` : ''} · HDR tone mapping: ${hw.accel === 'nvenc' && hw.tonemap_cuda ? 'GPU' : hw.tonemap ? 'CPU' : 'not available'}</div>
        <div style="font-size:13px;color:var(--faint);margin-top:6px">FFmpeg ${o.ffmpeg}</div>
        ${o.sessions.length ? html`<div class="table-wrap" style="margin-top:14px"><table class="table"><thead><tr><th>Stream</th><th>Mode</th><th>Speed</th><th></th></tr></thead><tbody>
          ${o.sessions.map(s => html`<tr><td>${s.title}<div class="sub">${s.user} · ${s.quality}${s.paused ? ' · paused (ahead)' : ''}${s.error ? ' · failed' : ''}</div></td>
            <td>${s.mode}<div class="sub">${s.pipeline || s.encoder}</div></td><td>${s.speed || '—'}</td>
            <td class="right"><button class="btn small danger" data-kill="${s.id}">Stop</button></td></tr>`)}</tbody></table></div>`
          : html`<p style="font-size:14px;color:var(--muted);margin:14px 0 0">No conversions running.</p>`}</div>

      <div data-panel="storage" class="panel"><h3>Storage</h3>${o.storage.length ? o.storage.map(s => s.missing
          ? html`<div style="margin-bottom:14px"><div>${s.path}</div><div class="pill red">Not reachable — is the drive or share mounted?</div></div>`
          : html`<div style="margin-bottom:14px"><div style="font-size:14px;word-break:break-all">${s.path}</div>
            <div class="meter"><i style="width:${(s.used / s.total * 100).toFixed(1)}%"></i></div>
            <div style="font-size:13px;color:var(--muted)">${fmtBytes(s.free)} free of ${fmtBytes(s.total)}</div></div>`)
        : html`<p style="color:var(--muted);margin:0">Add a library to see disk usage.</p>`}</div>

      <div data-panel="server" class="panel"><h3>Server</h3><dl class="facts">
        <dt>Version</dt><dd>${o.version}</dd><dt>Running for</dt><dd>${fmtTime(o.uptime)}</dd>
        <dt>Data folder</dt><dd style="word-break:break-all">${o.data_dir}
          ${o.data_free != null ? html`<span style="color:var(--muted)"> · ${fmtBytes(o.data_free)} free</span>` : ''}
          <div style="margin-top:6px;font-size:12px;color:var(--faint)">Change it on the server with
            <code style="user-select:all">sudo /opt/lenta/deploy/set-data-dir.sh /new/path</code></div></dd>
        ${o.transcode_dir && !o.transcode_dir.startsWith(o.data_dir + '/') ? html`<dt>Transcoding</dt><dd style="word-break:break-all">${o.transcode_dir}</dd>` : ''}
        <dt>API</dt><dd><a href="/api/docs" target="_blank" style="color:var(--accent)">/api/docs</a></dd></dl></div>
    </div>`;
    const live = body.querySelector('#dash-panels');
    if (!live) {
      mount(body, html`<div class="lay-head">${layoutButtons()}</div>${layoutBar()}${markup}`);
      lay = blockLayout({ root: $('#view'), canvas: body.querySelector('#dash-panels'), ...layoutPref('admin_dashboard_layout') });
    } else {
      // every few seconds: new contents into the same blocks, so their place, size and folding stay
      const fresh = document.createElement('div');
      mount(fresh, markup);
      fresh.querySelectorAll('.panel[data-panel]').forEach((np) => {
        live.querySelector(`.panel[data-panel="${np.dataset.panel}"]`)?.replaceChildren(...np.childNodes);
      });
      lay.refresh();
    }
    $('#scan-all')?.addEventListener('click', async () => { await api.post('/api/admin/scan'); toast('Scanning all libraries'); draw(); });
    body.querySelectorAll('[data-kill]').forEach(b => b.addEventListener('click', async () => {
      await api.del(`/api/admin/sessions/${b.dataset.kill}`); toast('Stream stopped'); draw();
    }));
  };
  await draw();
  pollTimer = setInterval(draw, 3000);
}

// ---------------------------------------------------------------- libraries
async function libraries(body) {
  const draw = async () => {
    const libs = await api.get('/api/admin/libraries');
    clearTimeout(libraries.timer);   // refresh while something is scanning or waiting
    if (libs.some(l => l.status.state !== 'idle')) libraries.timer = setTimeout(() => body.isConnected && draw(), 3000);
    mount(body, html`
      <div class="actions" style="margin-bottom:22px"><button class="btn primary" id="add-lib">${icon('plus')}Add library</button></div>
      ${libs.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Library</th><th>Folders</th><th>Contents</th><th>Last scan</th><th></th></tr></thead><tbody>
        ${libs.map(l => html`<tr>
          <td><div style="display:flex;gap:10px;align-items:center;font-weight:600"><span style="width:20px;color:var(--accent)">${icon(KIND_ICON[l.kind])}</span>${l.name}</div><div class="sub">${KIND_NAMES[l.kind]}</div></td>
          <td>${l.paths.map(p => html`<div class="mono" style="word-break:break-all">${p}</div>`)}</td>
          <td>${l.count} ${{ movies: 'movies', shows: 'shows', music: 'albums', photos: 'albums' }[l.kind]}<div class="sub">${l.files} files · ${fmtBytes(l.size)}</div>
            ${l.unmatched ? html`<a class="pill accent" href="#/admin/metadata?library=${l.id}">${l.unmatched} without metadata</a>` : ''}</td>
          <td>${l.status.state === 'scanning' ? html`<b style="color:var(--accent)">Scanning</b><div class="sub">${l.status.phase}${l.status.total ? ` · ${l.status.done} of ${l.status.total}` : ''}</div>`
            : l.status.state === 'queued' ? html`<b>Waiting</b><div class="sub">after this library's current scan</div>` : fmtAgo(l.last_scan)}
            ${l.report && l.report.ok === false ? html`<div class="scan-problem">Scan failed: ${l.report.error}</div>` : ''}
            ${l.report && l.status.state === 'idle' && l.report.ok && !l.report.found ? html`<div class="scan-problem">No video files found in these folders</div>` : ''}
            ${(l.report?.problems || []).slice(0, 3).map(p => html`<div class="scan-problem">${p}</div>`)}</td>
          <td class="right"><button class="btn small" data-scan="${l.id}">Scan</button>
            ${['movies', 'shows'].includes(l.kind) ? html`<button class="btn small" data-refresh="${l.id}" title="Fetch metadata again for every title">Refresh metadata</button>` : ''}
            <button class="btn small icon" data-edit="${l.id}" aria-label="Edit">${icon('edit')}</button>
            <button class="btn small icon danger" data-del="${l.id}" aria-label="Remove">${icon('trash')}</button></td></tr>`)}</tbody></table></div>`
      : html`<div class="empty"><h3>No libraries yet</h3><p>A library is a folder of one kind of media. Make one for movies, one for TV shows, and so on.
          LENTA scans it, matches titles online and keeps it up to date.</p></div>`}`);
    $('#add-lib').addEventListener('click', () => libraryDialog(null, draw));
    body.querySelectorAll('[data-scan]').forEach(b => b.addEventListener('click', async () => {
      await api.post(`/api/admin/libraries/${b.dataset.scan}/scan`); toast('Scan started. Progress is on the Dashboard.');
    }));
    body.querySelectorAll('[data-refresh]').forEach(b => b.addEventListener('click', async () => {
      await api.post(`/api/admin/libraries/${b.dataset.refresh}/scan?refresh=1`); toast('Refreshing metadata for this library');
    }));
    body.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => libraryDialog(libs.find(l => l.id === Number(b.dataset.edit)), draw)));
    body.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
      const l = libs.find(x => x.id === Number(b.dataset.del));
      if (!await confirmDialog(`Remove ${l.name}?`, 'LENTA forgets this library and its watch history. Your files on disk are not touched.', 'Remove library', true)) return;
      await api.del(`/api/admin/libraries/${l.id}`);
      toast(`Removed ${l.name}`);
      await loadLibraries(); draw();
    }));
  };
  await draw();
}

function libraryDialog(lib, done) {
  let paths = lib ? [...lib.paths] : [];
  const m = modal(html`<h2>${lib ? `Edit ${lib.name}` : 'Add library'}</h2>
    <form id="libform">
      <div class="field"><span class="label">Type</span><div class="kinds">${Object.entries(KIND_NAMES).map(([k, n]) => html`
        <label>${icon(KIND_ICON[k])}<input type="radio" name="kind" value="${k}" ${(lib ? lib.kind === k : k === 'movies') ? raw('checked') : ''} ${lib ? raw('disabled') : ''}>${n}</label>`)}</div></div>
      <div class="field"><label for="lname">Name</label><input class="input" id="lname" value="${lib?.name || ''}" placeholder="Movies" required></div>
      <div class="field"><span class="label">Folders</span><div class="chips" id="chips"></div>
        <div style="display:flex;gap:8px"><input class="input mono" id="lpath" placeholder="/mnt/media/movies"><button type="button" class="btn" id="browse">${icon('folder')}Browse</button></div>
        <span class="hint">Folders on the server. A NAS share must be mounted on the server first (for example under /mnt).</span></div>
      <p class="error-text" id="lerr"></p>
      <div class="foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${lib ? 'Save and scan' : 'Add and scan'}</button></div>
    </form>`, { wide: true });
  const box = m.box;
  const chips = () => mount(box.querySelector('#chips'), paths.map((p, i) => html`<span class="chip mono">${p}<button type="button" data-rm="${i}" aria-label="Remove folder">${icon('close')}</button></span>`));
  chips();
  box.querySelector('#chips').addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) { paths.splice(Number(b.dataset.rm), 1); chips(); } });
  box.querySelector('input[name=kind]:checked')?.addEventListener('change', () => {});
  box.querySelectorAll('input[name=kind]').forEach(r => r.addEventListener('change', () => {
    const name = box.querySelector('#lname');
    if (!name.value || Object.values(KIND_NAMES).includes(name.value)) name.value = KIND_NAMES[r.value];
  }));
  if (!lib) box.querySelector('#lname').value = 'Movies';
  const addTyped = () => { const v = box.querySelector('#lpath').value.trim(); if (v && !paths.includes(v)) { paths.push(v); chips(); } box.querySelector('#lpath').value = ''; };
  box.querySelector('#lpath').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addTyped(); } });
  box.querySelector('#browse').addEventListener('click', () => folderPicker(box.querySelector('#lpath').value || '/', (p) => { if (!paths.includes(p)) paths.push(p); chips(); }));
  box.querySelector('#libform').addEventListener('submit', async (e) => {
    e.preventDefault();
    addTyped();
    const kind = box.querySelector('input[name=kind]:checked').value;
    try {
      const payload = { name: box.querySelector('#lname').value, kind, paths };
      if (lib) await api.put(`/api/admin/libraries/${lib.id}`, payload);
      else await api.post('/api/admin/libraries', payload);
      m.close();
      toast(lib ? 'Library saved. Scanning now.' : 'Library added. Scanning now — titles appear as they are read.');
      await loadLibraries();
      done();
    } catch (ex) { box.querySelector('#lerr').textContent = ex.message; }
  });
}

function folderPicker(start, onPick) {
  const m = modal(html`<h2>Choose a folder</h2><div id="fp"></div>
    <div class="foot"><button class="btn" data-close>Cancel</button><button class="btn primary" id="fp-ok">Use this folder</button></div>`, { wide: true });
  let current = start;
  const go = async (path) => {
    let r;
    try { r = await api.get(`/api/admin/browse?path=${encodeURIComponent(path)}`); }
    catch (ex) { if (path !== '/') return go('/'); toast(ex.message, 'error'); return; }
    current = r.path;
    mount(m.box.querySelector('#fp'), html`<div class="mono" style="margin-bottom:10px;color:var(--accent);word-break:break-all">${r.path}</div>
      <div class="folder-list">${r.parent ? html`<button data-p="${r.parent}">${icon('back')}Up one level</button>` : ''}
        ${r.folders.map(f => html`<button data-p="${f.path}">${icon('folder')}${f.name}</button>`)}
        ${r.folders.length ? '' : html`<div style="padding:14px;color:var(--muted)">No folders inside.</div>`}</div>`);
  };
  m.box.querySelector('#fp').addEventListener('click', (e) => { const b = e.target.closest('[data-p]'); if (b) go(b.dataset.p); });
  m.box.querySelector('#fp-ok').addEventListener('click', () => { onPick(current); m.close(); });
  go(start);
}

// ---------------------------------------------------------------- users
async function users(body) {
  const draw = async () => {
    const [list, libs] = await Promise.all([api.get('/api/admin/users'), api.get('/api/admin/libraries')]);
    mount(body, html`
      <div class="actions" style="margin-bottom:22px"><button class="btn primary" id="add-user">${icon('plus')}Add user</button></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>User</th><th>Role</th><th>Libraries</th><th>Last watched</th><th></th></tr></thead><tbody>
      ${list.map(u => html`<tr>
        <td><div style="display:flex;gap:12px;align-items:center"><span class="avatar" style="background:${u.color}">${u.username[0].toUpperCase()}</span><b>${u.username}</b></div></td>
        <td>${u.is_admin ? html`<span class="pill accent">Administrator</span>` : html`<span class="pill">Viewer</span>`}</td>
        <td>${u.is_admin || u.all_libraries ? 'All' : (u.library_ids.map(id => libs.find(l => l.id === id)?.name).filter(Boolean).join(', ') || 'None')}</td>
        <td>${fmtAgo(u.last_watched)}</td>
        <td class="right"><button class="btn small icon" data-edit="${u.id}" aria-label="Edit">${icon('edit')}</button>
          ${u.id !== state.user.id ? html`<button class="btn small icon danger" data-del="${u.id}" aria-label="Remove">${icon('trash')}</button>` : ''}</td></tr>`)}
      </tbody></table></div>`);
    $('#add-user').addEventListener('click', () => userDialog(null, libs, draw));
    body.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => userDialog(list.find(u => u.id === Number(b.dataset.edit)), libs, draw)));
    body.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
      const u = list.find(x => x.id === Number(b.dataset.del));
      if (!await confirmDialog(`Remove ${u.username}?`, 'Their account, watch history and My list are deleted.', 'Remove user', true)) return;
      await api.del(`/api/admin/users/${u.id}`); toast(`Removed ${u.username}`); draw();
    }));
  };
  await draw();
}

function userDialog(u, libs, done) {
  const m = modal(html`<h2>${u ? `Edit ${u.username}` : 'Add user'}</h2>
    <form id="uform">
      <div class="field"><label for="uname">Username</label><input class="input" id="uname" value="${u?.username || ''}" required autofocus></div>
      <div class="field"><label for="upass">${u ? 'New password' : 'Password'}</label><input class="input" id="upass" type="password" autocomplete="new-password" ${u ? '' : raw('required minlength="6"')}>
        ${u ? html`<span class="hint">Leave empty to keep the current password.</span>` : ''}</div>
      <div class="field"><label class="check"><input type="checkbox" id="uadmin" ${u?.is_admin ? raw('checked') : ''}>Administrator — can manage libraries, users and settings</label></div>
      <div class="field" id="ulibs"><span class="label">Library access</span>
        <label class="check"><input type="checkbox" id="uall" ${!u || u.all_libraries ? raw('checked') : ''}>All libraries, including ones added later</label>
        <div id="ulist" style="display:grid;gap:8px;margin:6px 0 0 28px">${libs.map(l => html`<label class="check"><input type="checkbox" value="${l.id}" ${u?.library_ids.includes(l.id) ? raw('checked') : ''}>${l.name}</label>`)}</div></div>
      <p class="error-text" id="uerr"></p>
      <div class="foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${u ? 'Save' : 'Add user'}</button></div>
    </form>`);
  const box = m.box;
  const sync = () => {
    const admin = box.querySelector('#uadmin').checked;
    box.querySelector('#ulibs').hidden = admin;
    box.querySelector('#ulist').hidden = box.querySelector('#uall').checked;
  };
  box.querySelector('#uadmin').addEventListener('change', sync);
  box.querySelector('#uall').addEventListener('change', sync);
  sync();
  box.querySelector('#uform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      username: box.querySelector('#uname').value, password: box.querySelector('#upass').value || null,
      is_admin: box.querySelector('#uadmin').checked, all_libraries: box.querySelector('#uall').checked,
      library_ids: [...box.querySelectorAll('#ulist input:checked')].map(i => Number(i.value)),
    };
    try {
      if (u) await api.put(`/api/admin/users/${u.id}`, payload); else await api.post('/api/admin/users', payload);
      m.close(); toast(u ? 'User saved' : `Added ${payload.username}`); done();
    } catch (ex) { box.querySelector('#uerr').textContent = ex.message; }
  });
}

// ---------------------------------------------------------------- metadata
async function metadata(body, q = new URLSearchParams()) {
  const libId = Number(q.get('library')) || null;
  const lib = libId && state.libraries.find(l => l.id === libId);
  const [items, settings] = await Promise.all([api.get(`/api/admin/unmatched${lib ? `?library_id=${lib.id}` : ''}`), api.get('/api/admin/settings')]);
  mount(body, html`
    ${settings.tmdb_api_key_set ? '' : html`<div class="panel" style="margin-bottom:22px;border-color:var(--accent)"><h3>Online metadata is off</h3>
      <p style="margin:0 0 14px;color:var(--muted)">Posters, descriptions and cast come from The Movie Database. Add a free TMDB API key in Settings to turn it on.</p>
      <a class="btn small primary" href="#/admin/settings">Open settings</a></div>`}
    <h2 class="section-title" style="margin:0 0 6px">Titles without metadata${lib ? ` in ${lib.name}` : ''}</h2>
    ${lib ? html`<p style="margin:0 0 10px"><a href="#/admin/metadata" style="color:var(--accent)">Show every library</a></p>` : ''}
    <p style="color:var(--muted);margin:0 0 14px">LENTA tried every reading of these names it could think of — the file name, its folders,
      with and without the year, numbers written both ways, subtitles and editions dropped — and found no confident match.
      Search for the right title to fix them.</p>
    ${items.length && settings.tmdb_api_key_set ? html`<div class="actions" style="margin-bottom:18px">
      <button class="btn small" id="retry-all">${icon('refresh')}Search again for all</button><span id="retry-state" class="sub" style="color:var(--muted)"></span></div>` : ''}
    ${items.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Title as read from the file</th><th>File</th><th></th></tr></thead><tbody>
      ${items.map(i => html`<tr><td><b>${i.title}</b>${i.year ? ` (${i.year})` : ''}<div class="sub">${i.kind === 'show' ? 'TV show' : 'Movie'}</div>
        ${i.attempts?.length ? html`<details class="attempts"><summary>Tried ${i.attempts.length} searches</summary><ul>${i.attempts.map(a => html`<li>${a}</li>`)}</ul></details>` : ''}</td>
        <td class="mono" style="word-break:break-all;max-width:420px">${i.path || ''}</td>
        <td class="right"><button class="btn small" data-fix="${i.id}">Fix match</button><button class="btn small icon" data-edit="${i.id}" aria-label="Edit">${icon('edit')}</button></td></tr>`)}
      </tbody></table></div>` : html`<div class="empty" style="padding-top:10px"><h3>Everything is matched</h3><p>Every movie and show has metadata.</p></div>`}`);
  $('#retry-all')?.addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      let st = await api.post(`/api/admin/unmatched/retry${lib ? `?library_id=${lib.id}` : ''}`);
      while (st.running && body.isConnected) {
        $('#retry-state').textContent = `${st.done} of ${st.total} · ${st.matched} matched`;
        await new Promise(r => setTimeout(r, 1500));
        st = await api.get('/api/admin/unmatched/retry');
      }
      toast(`${st.matched} of ${st.total} titles matched`);
      if (body.isConnected) metadata(body, q);
    } catch (ex) { toast(ex.message, 'error'); e.target.disabled = false; }
  });
  body.querySelectorAll('[data-fix]').forEach(b => b.addEventListener('click', () => {
    const it = items.find(i => i.id === Number(b.dataset.fix));
    matchDialog(it, () => metadata(body, q));
  }));
  body.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => {
    const it = items.find(i => i.id === Number(b.dataset.edit));
    openEditor(it.id, () => metadata(body, q));
  }));
}

// ⋯ next to a library in the menu
export async function libraryMenu(anchor, lib, { move }) {
  let full;
  try { full = (await api.get('/api/admin/libraries')).find(l => l.id === lib.id); } catch (ex) { toast(ex.message, 'error'); return; }
  if (!full) return;
  const video = ['movies', 'shows'].includes(full.kind);
  const idx = state.libraries.findIndex(l => l.id === lib.id);
  const busy = full.status.state !== 'idle';
  const items = [{ heading: full.name },
    { label: 'Scan library files', hint: busy ? (full.status.state === 'scanning' ? 'scanning…' : 'waiting…') : '', action: async () => {
      await api.post(`/api/admin/libraries/${lib.id}/scan`); toast(`Scanning ${full.name}. Progress is on the Dashboard.`);
    } }];
  if (video) {
    items.push({ label: 'Refresh all metadata', hint: `${full.count} title${full.count === 1 ? '' : 's'}`, action: async () => {
      await api.post(`/api/admin/libraries/${lib.id}/scan?refresh=1`);
      toast(`Refreshing metadata for ${full.name}. Fields you locked stay as they are.`);
    } });
    items.push({ label: 'Fix missing metadata…', hint: full.unmatched ? `${full.unmatched} missing` : 'none missing',
      action: () => { location.hash = `#/admin/metadata?library=${lib.id}`; } });
  }
  items.push({ label: 'Edit library…', action: () => libraryDialog(full, () => { refreshView(); }) });
  items.push({ sep: true },
    { label: 'Move up', disabled: idx <= 0, action: () => move(-1) },
    { label: 'Move down', disabled: idx >= state.libraries.length - 1, action: () => move(1) });
  popMenu(anchor, items);
}

export function adminItemMenu(d) {
  return [
    { label: 'Edit metadata…', action: () => openEditor(d.id) },
    { label: 'Refresh Metadata', action: () => refreshMetadata(d.id) },
    { label: 'Fix match…', action: () => matchDialog(d, refreshView) },
  ];
}

function matchDialog(item, done) {
  const m = modal(html`<h2>Fix match</h2>
    <form id="mform" style="display:flex;gap:8px;margin-bottom:16px">
      <input class="input" id="mq" value="${item.title}" aria-label="Title" style="flex:1">
      <input class="input" id="my" value="${item.year || ''}" aria-label="Year" placeholder="Year" style="width:90px">
      <button class="btn primary">${icon('search')}Search</button></form>
    <div class="match-results" id="mres"><p style="color:var(--muted)">Search TMDB for the right ${item.kind === 'show' ? 'show' : 'movie'}.</p></div>
    <div class="foot"><button class="btn" data-close>Cancel</button></div>`, { wide: true });
  const box = m.box;
  const run = async () => {
    const res = box.querySelector('#mres');
    mount(res, html`<p style="color:var(--muted)">Searching…</p>`);
    try {
      const yr = box.querySelector('#my').value.trim();
      const results = await api.get(`/api/admin/match?item_id=${item.id}&query=${encodeURIComponent(box.querySelector('#mq').value)}${yr ? `&year=${yr}` : ''}`);
      mount(res, results.length ? results.map(r => html`<button class="match-result" data-tmdb="${r.tmdb_id}">
        ${r.poster ? html`<img src="${img(r.poster, 92)}" alt="">` : html`<span class="ph"></span>`}
        <span><b>${r.title}</b>${r.year ? ` (${r.year})` : ''}<p class="clamp-3">${r.overview || ''}</p></span></button>`)
        : html`<p style="color:var(--muted)">No results. Try the original title or remove the year.</p>`);
    } catch (ex) { mount(res, html`<p class="error-text">${ex.message}</p>`); }
  };
  box.querySelector('#mform').addEventListener('submit', (e) => { e.preventDefault(); run(); });
  box.querySelector('#mres').addEventListener('click', async (e) => {
    const b = e.target.closest('[data-tmdb]');
    if (!b) return;
    b.disabled = true;
    try {
      await api.post(`/api/admin/items/${item.id}/match`, { tmdb_id: Number(b.dataset.tmdb) });
      m.close(); toast('Match applied'); done();
    } catch (ex) { toast(ex.message, 'error'); b.disabled = false; }
  });
  run();
}

// ---------------------------------------------------------------- settings
async function settings(body) {
  const s = await api.get('/api/admin/settings');
  const o = await api.get('/api/admin/overview');
  const art = await api.get('/api/admin/artwork-sources');
  const artOrder = { movie: art.movie, show: art.show };
  const hw = o.hardware;
  mount(body, html`<div class="lay-head">${layoutButtons()}</div>${layoutBar()}<form id="sform"><div class="panels" id="aset-panels">
    <div data-panel="general" class="panel"><h3>General</h3>
      <div class="field"><label for="s-name">Server name</label><input class="input" id="s-name" value="${s.server_name}" maxlength="40"></div>
      <div class="field"><label for="s-scan">Scan libraries every</label>
        <select class="input" id="s-scan">${[[0, 'Never — only when I press Scan'], [15, '15 minutes'], [60, 'Hour'], [360, '6 hours'], [1440, 'Day']].map(([v, l]) =>
          html`<option value="${v}" ${String(v) === s.scan_interval_minutes ? raw('selected') : ''}>${l}</option>`)}</select></div>
      <label class="check"><input type="checkbox" id="s-watch" ${s.watch_libraries !== '0' ? raw('checked') : ''}>Watch library folders for new media</label>
      <div class="field" style="margin:8px 0 0 28px"><label for="s-watch-int">Look for new media every</label>
        <select class="input" id="s-watch-int" style="width:180px">${[[30, '30 seconds'], [60, 'minute'], [120, '2 minutes'], [300, '5 minutes']].map(([v, l]) =>
          html`<option value="${v}" ${String(v) === (s.watch_interval || '60') ? raw('selected') : ''}>${l}</option>`)}</select>
        <span class="hint">A film or episode copied into a library folder (also on a NAS) is added with its poster and details shortly after the copy finishes.
          Only folder times are read, so this is light even for big libraries.</span></div>
      <div class="field"><label for="s-theme">Default theme</label>
        <select class="input" id="s-theme">${THEMES.map(t => html`<option value="${t.id}" ${t.id === s.default_theme ? raw('selected') : ''}>${t.name} — ${t.desc}</option>`)}</select>
        <span class="hint">Used on the sign-in screen and for users who haven't picked one in their Settings.</span></div>
      <label class="check"><input type="checkbox" id="s-users" ${s.show_users_on_login === '1' ? raw('checked') : ''}>Show profile pictures on the sign-in screen</label>
    </div>

    <div data-panel="online-subtitles" class="panel"><h3>Online subtitles</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 16px">From OpenSubtitles.com. Create a free account, then an API consumer at opensubtitles.com › API to get a key.
        The login is optional but raises the daily download limit.</p>
      <div class="field"><label for="s-os-key">OpenSubtitles API key</label>
        <input class="input mono" id="s-os-key" autocomplete="off" placeholder="${s.opensubtitles_api_key_set ? `Saved (${s.opensubtitles_api_key_hint}) — paste a new one to replace it` : 'Paste your API key'}"></div>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <div class="field" style="flex:1;min-width:150px"><label for="s-os-user">Username (not email)</label><input class="input" id="s-os-user" autocomplete="off" value="${s.opensubtitles_username}">
          <span class="hint" id="s-os-user-hint"></span></div>
        <div class="field" style="flex:1;min-width:150px"><label for="s-os-pass">Password</label>
          <input class="input" id="s-os-pass" type="password" autocomplete="new-password" placeholder="${s.opensubtitles_password_set ? 'Saved — type to replace' : ''}"></div></div>
      <button type="button" class="btn small" id="s-os-test">Test OpenSubtitles</button>
    </div>

    <div data-panel="theme-music" class="panel"><h3>Theme music</h3>
      <label class="check"><input type="checkbox" id="s-music" ${s.theme_music_online === '1' ? raw('checked') : ''}>Look up soundtrack previews online</label>
      <p style="color:var(--muted);font-size:14px;margin:10px 0 0">A theme file next to the media always wins (theme.mp3, theme.flac … or a theme-music folder).
        Without one, LENTA finds the soundtrack album on Apple Music and plays the 30-second preview of its main theme.
        Each person turns theme music on or off in their own Settings.</p>
    </div>

    <div data-panel="metadata" class="panel"><h3>Metadata</h3>
      <div class="field"><label for="s-tmdb">TMDB API key or read access token</label>
        <input class="input mono" id="s-tmdb" placeholder="${s.tmdb_api_key_set ? `Saved (${s.tmdb_api_key_hint}) — paste a new one to replace it` : 'Paste your key'}" autocomplete="off">
        <span class="hint">Free at themoviedb.org → Settings → API. Used for posters, descriptions, cast and episode titles.</span></div>
      <div style="display:flex;gap:12px">
        <div class="field" style="flex:1"><label for="s-lang">Language</label><input class="input" id="s-lang" value="${s.metadata_language}" placeholder="en-US">
          <span class="hint">For example en-US or bg-BG</span></div>
        <div class="field" style="width:120px"><label for="s-region">Region</label><input class="input" id="s-region" value="${s.metadata_region}" placeholder="US">
          <span class="hint">For age ratings</span></div></div>
      <button type="button" class="btn small" id="s-test">Test key</button>
    </div>

    <div data-panel="artwork-sources" class="panel wide"><h3>Artwork sources</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 16px">Where posters, backgrounds and logos come from. For each picture the
        first switched-on source that has one is used; the editor's Poster, Background and Logo tabs offer the pictures of every
        switched-on source. Pictures you choose or upload yourself are locked and never replaced.</p>
      <div class="art-cols">${['movie', 'show'].map(k => html`<div><div class="art-col-head">${k === 'movie' ? 'Movies' : 'TV shows'}</div>
        <ol class="art-sources" data-kind="${k}"></ol></div>`)}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:18px">
        <div class="field" style="flex:1;min-width:230px;margin:0"><label for="s-fanart">Fanart.tv project API key</label>
          <input class="input mono" id="s-fanart" autocomplete="off" placeholder="${s.fanart_api_key_set ? `Saved (${s.fanart_api_key_hint}) — paste a new one to replace it` : 'Paste the project key'}"></div>
        <div class="field" style="flex:1;min-width:230px;margin:0"><label for="s-fanart-client">Personal API key <span style="color:var(--muted);font-weight:400">(optional)</span></label>
          <input class="input mono" id="s-fanart-client" autocomplete="off" placeholder="${s.fanart_client_key_set ? `Saved (${s.fanart_client_key_hint})` : 'Paste the personal key'}"></div>
        <button type="button" class="btn small" id="s-fanart-test">Test Fanart.tv</button>
        <button type="button" class="btn small" id="s-art-apply">${icon('refresh')}Apply to every title now</button></div>
      <span class="hint" style="display:block;margin-top:8px">Fanart.tv is free: sign up at fanart.tv and open <b>Get an API key</b>. The <b>project key</b> is
        required; the personal key is optional and makes newly added pictures show up sooner.
        <b>Apply to every title now</b> saves this order and picks each title's pictures again in the background; new titles follow it automatically.</span>
      <div class="sub" id="s-art-state" style="margin-top:8px;color:var(--muted)"></div>
    </div>

    <div data-panel="trailers" class="panel"><h3>Trailers</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 14px">Each title's trailer comes from the first of these that has one:</p>
      <ol class="tr-order">
        <li><b>Your trailer files</b> — next to the movie: <code>Movie Name-trailer.mp4</code>, <code>trailer.mp4</code>, or any video in a
          <code>trailers</code> folder (like Plex and Jellyfin). MP4, M4V, WebM or MOV.</li>
        <li><label class="check" style="margin:0"><input type="checkbox" id="s-apple" ${s.apple_trailers !== '0' ? raw('checked') : ''}>
          <span><b>Apple TV trailers</b> — HD video from Apple for movies, no key needed</span></label></li>
        <li><label class="check" style="margin:0"><input type="checkbox" id="s-kc" ${s.kinocheck_trailers !== '0' ? raw('checked') : ''}>
          <span><b>KinoCheck</b> — official trailers chosen by an editorial team, 1080p or better, in English (German too when your metadata language is German). Played from YouTube.</span></label>
          <div class="kc-key">
            <div class="field"><label for="s-kc-key">KinoCheck API key <span style="color:var(--muted);font-weight:400">(optional: without one, 1,000 lookups a day)</span></label>
              <input class="input mono" id="s-kc-key" autocomplete="off" placeholder="${s.kinocheck_api_key_set ? `Saved (${s.kinocheck_api_key_hint}) — paste a new one to replace it` : 'Paste your KinoCheck API key'}"></div>
            <button type="button" class="btn small" id="s-kc-test">Test KinoCheck</button>
          </div>
          <div class="sub" id="s-kc-result" style="margin:6px 0 0 26px;font-size:13px;color:var(--muted)"></div></li>
        <li><b>YouTube</b> — the other trailers listed on TMDB</li>
      </ol>
      <p style="color:var(--muted);font-size:13px;margin:10px 0 0">Your files and Apple's play directly in LENTA: no ads, no YouTube logo, and they start at once. KinoCheck picks the official trailer in the best quality, so you get fewer fan-made or low-quality uploads.
        Pick a different one per title in Edit metadata › Trailers.</p>
      <div class="actions" style="margin-top:12px"><button type="button" class="btn small" id="s-apple-test">Test Apple trailers</button></div>
      <div class="sub" id="s-apple-result" style="margin-top:8px;font-size:13px;color:var(--muted)"></div>
    </div>

    <div data-panel="seek-bar-previews" class="panel"><h3>Seek-bar previews</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 14px">Pictures from the video above the player's time bar while you move along it (like Plex).
        LENTA makes them in the background at low priority: a minute or two per film, a few MB each, stored in the data folder.</p>
      <label class="check"><input type="checkbox" id="s-trick" ${s.trickplay !== '0' ? raw('checked') : ''}>Make seek-bar preview pictures</label>
      <div class="field" style="margin-top:12px"><label for="s-trick-int">One picture every</label>
        <select class="input" id="s-trick-int" style="width:160px">${[5, 10, 15, 20, 30].map(n => html`<option value="${n}" ${String(n) === (s.trickplay_interval || '10') ? raw('selected') : ''}>${n} seconds</option>`)}</select>
        <span class="hint">Changing this makes all pictures again.</span></div>
    </div>

    <div data-panel="intro-detection" class="panel"><h3>Intro detection</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 14px">Finds the intro of every TV episode by comparing the sound of the episodes of a season
        (the same theme tune, 15 seconds to 2½ minutes long), so the player can offer <b>Skip intro</b>. Runs in the background at low priority and reads only
        the first minutes of each episode. Each person chooses in their Settings whether to see a button, skip automatically, or neither.</p>
      <label class="check"><input type="checkbox" id="s-intro" ${s.intro_detection !== '0' ? raw('checked') : ''}>Detect intros in TV shows</label>
    </div>

    <div data-panel="about-panel-extras" class="panel"><h3>About panel extras</h3>
      <p style="color:var(--muted);font-size:14px;margin:-6px 0 16px">Fill each title's About panel with more than TMDB has.</p>
      <label class="check"><input type="checkbox" id="s-imdb" ${s.imdb_ratings !== '0' ? raw('checked') : ''}>Show IMDb ratings</label>
      <p style="color:var(--muted);font-size:13px;margin:4px 0 16px 30px">No key needed: from IMDb's free daily ratings file (for personal, non-commercial use),
        refreshed once a day.${o.imdb?.rated ? ` ${o.imdb.rated} titles rated${o.imdb.updated ? `, updated ${fmtAgo(o.imdb.updated)}` : ''}.` : ''}${o.imdb?.error ? ` Last update failed: ${o.imdb.error}` : ''}</p>
      <label class="check"><input type="checkbox" id="s-wiki" ${s.wikipedia_summaries !== '0' ? raw('checked') : ''}>Show the Wikipedia summary</label>
      <p style="color:var(--muted);font-size:13px;margin:4px 0 16px 30px">No key needed. In your metadata language when that Wikipedia has the article, otherwise in English.</p>
      <div class="field"><label for="s-omdb">OMDb API key <span style="color:var(--muted);font-weight:400">(optional)</span></label>
        <input class="input mono" id="s-omdb" autocomplete="off" placeholder="${s.omdb_api_key_set ? `Saved (${s.omdb_api_key_hint}) — paste a new one to replace it` : 'Paste your key'}">
        <span class="hint">Adds IMDb rating and votes, Rotten Tomatoes, Metacritic, awards and box office. Free key (1,000 lookups a day) at omdbapi.com → API Key; click the activation link they e-mail you.</span></div>
      <button type="button" class="btn small" id="s-omdb-test">Test OMDb</button>
    </div>

    <div data-panel="transcoding" class="panel"><h3>Transcoding</h3>
      <div class="field"><label for="s-hw">Hardware acceleration</label>
        <select class="input" id="s-hw">${[['auto', 'Automatic (best available)'], ['nvenc', 'NVIDIA NVENC'], ['qsv', 'Intel Quick Sync'], ['vaapi', 'VA-API (Intel / AMD)'], ['none', 'Off — CPU only']].map(([v, l]) =>
          html`<option value="${v}" ${v === s.hw_accel ? raw('selected') : ''}>${l}${hw.available.includes(v) ? ' ✓' : ''}</option>`)}</select>
        <span class="hint">Now using ${hw.encoder}. Working on this machine: ${hw.available.join(', ') || 'no hardware encoder'}.</span></div>
      <div class="field"><label for="s-dev">VA-API device</label><input class="input mono" id="s-dev" value="${s.vaapi_device}"></div>
      <div class="field"><label for="s-preset">CPU encoder speed</label><select class="input" id="s-preset">${['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium'].map(p =>
        html`<option ${p === s.x264_preset ? raw('selected') : ''}>${p}</option>`)}</select><span class="hint">Faster uses less CPU and gives slightly larger streams.</span></div>
      <div class="field"><label for="s-max">Simultaneous conversions</label><input class="input" id="s-max" type="number" min="1" max="16" value="${s.max_transcodes}" style="width:120px"></div>
      <button type="button" class="btn small" id="s-detect">${icon('refresh')}Detect hardware again</button>
    </div>
    </div>
    <div class="aset-save"><button class="btn primary">Save settings</button></div>
  </form>`);
  blockLayout({ root: $('#view'), canvas: body.querySelector('#aset-panels'), ...layoutPref('admin_settings_layout') });
  const val = (id) => body.querySelector(id).value;
  const payload = () => ({
    server_name: val('#s-name'), scan_interval_minutes: val('#s-scan'), watch_libraries: body.querySelector('#s-watch').checked ? '1' : '0', watch_interval: val('#s-watch-int'),
    show_users_on_login: body.querySelector('#s-users').checked ? '1' : '0',
    default_theme: val('#s-theme'), theme_music_online: body.querySelector('#s-music').checked ? '1' : '0',
    opensubtitles_api_key: val('#s-os-key').trim() || null, opensubtitles_username: val('#s-os-user').trim(),
    opensubtitles_password: val('#s-os-pass') || null,
    tmdb_api_key: val('#s-tmdb').trim() || null, metadata_language: val('#s-lang'), metadata_region: val('#s-region'),
    wikipedia_summaries: body.querySelector('#s-wiki').checked ? '1' : '0',
    imdb_ratings: body.querySelector('#s-imdb').checked ? '1' : '0',
    apple_trailers: body.querySelector('#s-apple').checked ? '1' : '0',
    kinocheck_trailers: body.querySelector('#s-kc').checked ? '1' : '0',
    trickplay: body.querySelector('#s-trick').checked ? '1' : '0', trickplay_interval: val('#s-trick-int'),
    intro_detection: body.querySelector('#s-intro').checked ? '1' : '0',
    kinocheck_api_key: val('#s-kc-key').trim() || null,
    fanart_api_key: val('#s-fanart').trim() || null, fanart_client_key: val('#s-fanart-client').trim() || null,
    artwork_sources_movie: artOrder.movie.map(x => ({ id: x.id, enabled: x.enabled })),
    artwork_sources_show: artOrder.show.map(x => ({ id: x.id, enabled: x.enabled })), omdb_api_key: val('#s-omdb').trim() || null,
    hw_accel: val('#s-hw'), vaapi_device: val('#s-dev'), x264_preset: val('#s-preset'), max_transcodes: val('#s-max'),
  });
  body.querySelector('#sform').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const p = payload();
      const keyAdded = p.tmdb_api_key && !s.tmdb_api_key_set;
      await api.put('/api/admin/settings', p);
      state.user.server_name = p.server_name;
      state.server = { ...(state.server || {}), theme: p.default_theme };
      renderRail();
      toast('Settings saved');
      api.get('/api/me/prefs').then(r => { state.features = r.features; }).catch(() => {});
      if (keyAdded) {
        await api.post('/api/admin/scan');
        toast('Fetching metadata for your libraries');
      }
      settings(body);
    } catch (ex) { toast(ex.message, 'error'); }
  });
  const osUser = body.querySelector('#s-os-user');
  const osHint = () => {
    const isEmail = osUser.value.includes('@');
    body.querySelector('#s-os-user-hint').textContent = isEmail
      ? 'This looks like an email. OpenSubtitles only accepts the username shown on your profile.' : '';
    body.querySelector('#s-os-user-hint').style.color = isEmail ? 'var(--red)' : '';
  };
  osUser.addEventListener('input', osHint);
  osHint();
  body.querySelector('#s-os-test').addEventListener('click', async () => {
    try {
      await api.put('/api/admin/settings', { opensubtitles_api_key: val('#s-os-key').trim() || null,
        opensubtitles_username: val('#s-os-user').trim(), opensubtitles_password: val('#s-os-pass') || null });
      const r = await api.post('/api/admin/settings/test-opensubtitles');
      toast(r.user ? `OpenSubtitles works — signed in as ${r.user}${r.remaining != null ? `, ${r.remaining} downloads left today` : ''}` : 'OpenSubtitles key works (no login)');
    } catch (ex) { toast(ex.message, 'error'); }
  });
  body.querySelector('#s-test').addEventListener('click', async () => {
    try { await api.post('/api/admin/settings/test-tmdb', { tmdb_api_key: val('#s-tmdb').trim() || null }); toast('TMDB key works'); }
    catch (ex) { toast(ex.message, 'error'); }
  });
  // ---- artwork sources: tick to use, arrows to reorder (saved with "Save settings")
  const drawSources = () => body.querySelectorAll('.art-sources').forEach(ol => {
    const k = ol.dataset.kind, list = artOrder[k];
    mount(ol, list.map((x, i) => html`<li class="${x.enabled ? '' : 'off'}">
      <label class="check"><input type="checkbox" data-src="${i}" ${x.enabled ? raw('checked') : ''}>
        <span><b>${x.name}</b><small>${x.hint}</small></span></label>
      <span class="art-move"><button type="button" class="btn small icon" data-up="${i}" ${i === 0 ? raw('disabled') : ''} aria-label="Move ${x.name} up">${icon('chevron')}</button>
        <button type="button" class="btn small icon down" data-down="${i}" ${i === list.length - 1 ? raw('disabled') : ''} aria-label="Move ${x.name} down">${icon('chevron')}</button></span></li>`));
    ol.querySelectorAll('[data-src]').forEach(cb => cb.addEventListener('change', () => { list[Number(cb.dataset.src)].enabled = cb.checked; drawSources(); }));
    const move = (i, d) => { const j = i + d; [list[i], list[j]] = [list[j], list[i]]; drawSources(); };
    ol.querySelectorAll('[data-up]').forEach(b => b.addEventListener('click', () => move(Number(b.dataset.up), -1)));
    ol.querySelectorAll('[data-down]').forEach(b => b.addEventListener('click', () => move(Number(b.dataset.down), 1)));
  });
  drawSources();
  body.querySelector('#s-kc-test').addEventListener('click', async (e) => {
    const btn = e.currentTarget, out = body.querySelector('#s-kc-result');
    btn.disabled = true; out.textContent = 'Asking KinoCheck…'; out.style.color = '';
    try {
      const r = await api.post('/api/admin/settings/test-kinocheck', { kinocheck_api_key: val('#s-kc-key').trim() || null });
      out.textContent = r.message; out.style.color = r.ok ? '' : 'var(--red)';
      if (r.report?.length) {
        const d = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre');
        sm.textContent = 'What KinoCheck answered'; pre.textContent = r.report.join('\n');
        pre.style.cssText = 'white-space:pre-wrap;word-break:break-all;font-size:12px;margin:6px 0 0;color:var(--muted)';
        d.append(sm, pre); d.style.marginTop = '6px'; out.append(d);
      }
    } catch (ex) { out.textContent = ex.message; out.style.color = 'var(--red)'; }
    finally { btn.disabled = false; }
  });
  body.querySelector('#s-apple-test').addEventListener('click', async (e) => {
    const btn = e.currentTarget, out = body.querySelector('#s-apple-result');
    btn.disabled = true; out.textContent = 'Asking Apple…';
    try {
      const r = await api.post('/api/admin/settings/test-apple', {});
      out.textContent = r.message + ' ';
      out.style.color = r.ok ? '' : 'var(--red)';
      if (r.report?.length) {
        const d = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre');
        sm.textContent = 'What Apple answered'; pre.textContent = r.report.join('\n');
        pre.style.cssText = 'white-space:pre-wrap;font-size:12px;margin:6px 0 0;color:var(--muted)';
        d.append(sm, pre); d.style.marginTop = '6px'; out.append(d);
      }
      if (r.sample) { const a = document.createElement('a'); a.href = r.sample; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'Play the sample'; out.append(a); }
    } catch (ex) { out.textContent = ex.message; out.style.color = 'var(--red)'; }
    finally { btn.disabled = false; }
  });
  body.querySelector('#s-fanart-test').addEventListener('click', async () => {
    try { const r = await api.post('/api/admin/settings/test-fanart', { fanart_api_key: val('#s-fanart').trim() || null, fanart_client_key: val('#s-fanart-client').trim() || null }); toast(`Fanart.tv key works — ${r.sample}`); }
    catch (ex) { toast(ex.message, 'error'); }
  });
  const artState = (st) => {
    const el = body.querySelector('#s-art-state');
    if (!el) return;
    el.textContent = st.running ? `Picking pictures: ${st.done} of ${st.total} titles · ${st.changed} changed so far`
      : st.finished ? `Last run: ${st.changed} of ${st.total} titles got new pictures` : '';
  };
  artState(art.apply);
  body.querySelector('#s-art-apply').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      await api.put('/api/admin/settings', payload());
      let st = await api.post('/api/admin/artwork/apply');
      toast('Saved. Picking pictures for every title in the background.');
      while (st.running && body.isConnected) { artState(st); await new Promise(r => setTimeout(r, 2000)); st = (await api.get('/api/admin/artwork-sources')).apply; }
      artState(st);
    } catch (ex) { toast(ex.message, 'error'); }
    if (body.isConnected) body.querySelector('#s-art-apply').disabled = false;
  });
  body.querySelector('#s-omdb-test').addEventListener('click', async () => {
    try {
      const r = await api.post('/api/admin/settings/test-omdb', { omdb_api_key: val('#s-omdb').trim() || null });
      toast(`OMDb key works — ${r.sample}`);
    } catch (ex) { toast(ex.message, 'error'); }
  });
  body.querySelector('#s-detect').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    const r = await api.post('/api/admin/hardware/detect');
    toast(`Using ${r.encoder}${r.available.length ? ` · found ${r.available.join(', ')}` : ''}`);
    settings(body);
  });
}

// ---------------------------------------------------------------- logs
async function logs(body) {
  const draw = async () => {
    const rows = await api.get('/api/admin/logs?limit=300');
    if (!body.isConnected) { clearInterval(pollTimer); return; }
    mount(body, html`<div class="table-wrap"><table class="table"><thead><tr><th>Time</th><th>Level</th><th>Source</th><th>Message</th></tr></thead><tbody>
      ${rows.map(r => html`<tr class="log-row ${r.level}"><td style="white-space:nowrap">${new Date(r.time * 1000).toLocaleTimeString()}</td><td>${r.level}</td><td>${r.source}</td>
        <td style="word-break:break-word">${r.message}</td></tr>`)}</tbody></table></div>
      ${rows.length ? '' : html`<p style="color:var(--muted)">Nothing logged yet.</p>`}`);
  };
  await draw();
  pollTimer = setInterval(draw, 5000);
}
