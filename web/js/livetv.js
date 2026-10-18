// Settings › Live TV: the tuners LENTA found by itself, the playlists you added, and the channels
// they carry. LENTA looks for tuners on its own and scans a new one once, so this page is mostly
// somewhere to watch that happen — and to add an m3u or Xtream playlist.
import { api } from './api.js';
import { $, $$, confirmDialog, html, icon, mount, modal, popMenu, raw, toast } from './ui.js';

let timer = 0;

const FAMILY = { aerial: 'Aerial (DVB-T/T2)', cable: 'Cable (DVB-C)', satellite: 'Satellite (DVB-S/S2)' };
const describe = (t) => (t.families || []).map(f => FAMILY[f] || f).join(' · ') || 'standard unknown';

// When a result is from this morning rather than from the scan you just ran, saying so saves an hour.
function ago(when) {
  const secs = Math.max(0, Math.round(Date.now() / 1000 - when));
  if (secs < 90) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  return new Date(when * 1000).toLocaleString();
}

function sourceLine(s) {
  const scan = s.scan && s.scan.running ? s.scan : null;
  const pct = scan && scan.total ? Math.min(100, Math.round(scan.done / scan.total * 100)) : 0;
  const cfg = s.config || {};
  const sat = (cfg.families || []).length === 1 && cfg.families[0] === 'satellite';
  return html`<div class="tv-source ${s.enabled ? '' : 'off'}" data-src="${s.id}">
    <div class="tv-ic">${icon(s.kind === 'tuner' ? 'live' : 'globe')}</div>
    <div class="tv-body">
      <div class="tv-name">${s.name}${s.provider ? html`<span class="tv-prov">${s.provider}</span>` : ''}</div>
      <div class="tv-sub">${s.kind === 'tuner'
        ? html`${describe(cfg)}${(cfg.inputs || []).length > 1 ? ` · ${cfg.inputs.length} inputs` : ''}${cfg.usb_id ? ` · ${cfg.usb_id}` : ''}${cfg.frontend && cfg.frontend !== cfg.product ? ` · ${cfg.frontend}` : ''}`
        : html`${s.kind === 'xtream' ? 'Xtream login' : 'Playlist'} · ${cfg.url || ''}`}</div>
      ${scan ? html`<div class="tv-prog"><i style="width:${pct}%"></i></div>
          <div class="tv-sub">Looking at ${scan.label || '…'} — ${scan.found} channel${scan.found === 1 ? '' : 's'} so far${scan.network ? ` on ${scan.network}` : ''}</div>`
        : html`<div class="tv-sub">${s.channels} channel${s.channels === 1 ? '' : 's'}${s.status ? ` · ${s.status}` : ''}${s.last_scan ? ` · ${ago(s.last_scan)}` : ''}</div>`}
      ${sat && !s.last_scan ? html`<div class="tv-note">A satellite tuner needs the dish settings (LNB and DiSEqC) before it can scan. That page is coming next.</div>` : ''}
      ${s.kind === 'tuner' && s.last_scan && !s.channels && !scan ? html`<div class="tv-note">
        Nothing locked on any frequency. Usually that is the aerial — nothing plugged into the stick, an indoor
        aerial out of range, or a cable feed on unusual frequencies.
        <button class="btn small" data-trace="${s.id}">Show what the scan saw</button>
        <pre class="tv-trace" id="tv-trace-${s.id}" hidden></pre>
        If that is empty, no scan has run since the server was last restarted — press <b>Scan again</b>.
        To compare against the tuner itself:<br><code>sudo /opt/lenta/deploy/tv-check.py</code></div>` : ''}
    </div>
    <div class="tv-actions">
      ${scan ? html`<button class="btn small" data-stop="${s.id}">Stop</button>`
        : html`<button class="btn small" data-scan="${s.id}">${s.last_scan ? 'Scan again' : 'Scan'}</button>`}
      <button class="btn small icon" data-menu="${s.id}" aria-label="More">${icon('dots')}</button>
    </div>
  </div>`;
}

// What is on, and how much of it LENTA has heard so far. The guide is not fetched from anywhere: it
// rides along with watching, so the honest thing to report is how much has arrived and from where.
function guideLine(g) {
  if (!g) return '';
  const busy = g.running ? html` <b>Listening to ${g.where} now.</b>`
    : g.collecting ? html` <b>Listening on ${g.collecting} tuner${g.collecting === 1 ? '' : 's'} now.</b>` : '';
  const swept = ' LENTA also tunes the multiplexes nobody is watching, whenever an input is spare.';
  if (!g.programmes) {
    return html`<p class="lead" style="margin:0 0 14px">No programme guide yet. It is not fetched from
      anywhere — it arrives with the picture, so watch any channel for a minute or two and the guide for
      every channel on that multiplex fills itself in.${swept}${busy}</p>`;
  }
  const until = g.last ? new Date(g.last * 1000) : null;
  const ahead = g.hours > 48 ? `${Math.round(g.hours / 24)} days` : `${Math.max(0, Math.round(g.hours))} hours`;
  return html`<p class="lead" style="margin:0 0 14px">Guide: <b>${g.programmes.toLocaleString()}</b>
    programmes on <b>${g.channels}</b> channel${g.channels === 1 ? '' : 's'}, ${ahead} ahead${
      until ? ` (to ${until.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })})` : ''}.
    Watching a channel collects the guide for every channel on its multiplex, and LENTA tunes the rest
    when an input is spare.${busy}</p>`;
}

// Where the logos come from is the person's choice: collections move, and a wrong address should be
// something they can correct rather than something baked in.
function askLogoSource(current, country) {
  return new Promise((resolve) => {
    // modal() hands back { box, close } — look inside its own box rather than the whole page, so two
    // dialogs can never pick up each other's fields.
    const m = modal(html`<h2>Find channel logos</h2>
      <form id="lgform">
        <p class="lead">A broadcast carries no artwork, so logos are matched by name against a collection.
          LENTA asks the collection what it holds and matches your channels against that — it never guesses
          at addresses, so a collection organised differently simply matches fewer channels.</p>
        <div class="field"><label for="lg-country">Your country</label>
          <input class="input" id="lg-country" placeholder="bulgaria" value="${country}"></div>
        <p class="lead">Many channels exist in several countries. Naming yours picks the local version —
          your BNT and bTV rather than someone else's.</p>
        <details><summary class="lead" style="cursor:pointer">Use a different collection</summary>
          <div class="field" style="margin-top:10px"><label for="lg-url">Where to ask what logos exist</label>
            <input class="input" id="lg-url" type="url" value="${current}"></div></details>
        <div class="field"><label class="check"><input type="checkbox" id="lg-replace">
          <span>Replace logos channels already have</span></label></div>
        <div class="foot"><button type="button" class="btn" data-close>Cancel</button>
          <button class="btn primary">Find logos</button></div>
      </form>`, { onClose: () => resolve(null) });
    m.box.querySelector('#lgform').addEventListener('submit', (e) => {
      e.preventDefault();
      const out = { index: m.box.querySelector('#lg-url').value.trim(),
                    country: m.box.querySelector('#lg-country').value.trim(),
                    replace: m.box.querySelector('#lg-replace').checked };
      // Closing fires onClose, which answers null. A promise keeps its first answer, so give the real
      // one before closing rather than after.
      resolve(out);
      m.close();
    });
  });
}

function channelRow(c) {
  return html`<tr data-ch="${c.id}" class="${c.hidden ? 'tv-hidden' : ''}">
    <td class="tv-no">${c.number ?? ''}</td>
    <td><img class="tv-logo" src="/api/livetv/channels/${c.id}/logo" alt="" loading="lazy"><span>${c.name}</span>
      ${c.scrambled ? html`<span class="tv-lock" title="Scrambled — needs a card or subscription">${icon('lock')}</span>` : ''}</td>
    <td class="mono">${c.kind === 'radio' ? 'Radio' : 'TV'}</td>
    <td>${c.provider || ''}</td>
    <td>${c.source_name}</td>
    <td class="right"><button class="btn small" data-hide="${c.id}">${c.hidden ? 'Show' : 'Hide'}</button></td>
  </tr>`;
}

export async function renderLiveTv(body) {
  clearInterval(timer);
  const draw = async () => {
    let o;
    try {
      o = await api.get('/api/admin/livetv');
    } catch (ex) {                       // say so rather than leaving an empty page behind
      clearInterval(timer);
      if (!body.isConnected) return;
      mount(body, html`<section class="panel"><h3>Live TV</h3>
        <p class="lead">The server could not answer: ${ex.message}</p>
        <p class="lead">The server log (Settings › Logs) will say why. <button class="btn small" id="tv-retry">Try again</button></p>
      </section>`);
      $('#tv-retry')?.addEventListener('click', draw);
      return;
    }
    if (!body.isConnected) { clearInterval(timer); return; }
    let now = { feeds: [] }, failed = { failures: [] };
    try { now = await api.get('/api/admin/livetv/live'); } catch { /* nothing playing, or the route is older */ }
    try { failed = await api.get('/api/admin/livetv/live-failures'); } catch { /* an older server */ }
    const tuners = o.sources.filter(s => s.kind === 'tuner');
    const lists = o.sources.filter(s => s.kind !== 'tuner');
    const busy = o.sources.some(s => s.scan && s.scan.running);
    mount(body, html`
      ${now.feeds.length ? html`<section class="panel"><h3>Playing now</h3>
        <p class="lead">Each of these holds one tuner input. A channel you have left is kept ready for
          ${Math.round((now.feeds[0]?.keeps_for || 1800) / 60)} minutes so going back to it is instant — it
          gives up its input at once if another channel needs one. If a channel stutters, <i>Show what the
          stream is doing</i> has FFmpeg's own account of it.</p>
        ${now.feeds.map(f => html`<div class="tv-source">
          <div class="tv-ic">${icon('live')}</div>
          <div class="tv-body">
            <div class="tv-name">${f.name}${f.number != null ? html`<span class="tv-prov">${f.number}</span>` : ''}${
              f.warm ? html`<span class="tv-prov">kept ready · ${ago(Date.now() / 1000 - f.idle)}</span>` : ''}</div>
            <div class="tv-sub">input ${f.input} · ${f.frequency ? `${Math.round(f.frequency / 1e6)} MHz · ` : ''}${f.video}/${f.audio} · ${
              f.mode === 'copy' ? 'passed through' : f.mode === 'remux' ? 'sound re-encoded' : 're-encoded'} · ${
              f.warm ? 'nobody watching' : `${f.viewers} watching`} · ${f.segments} segments in ${f.seconds}s</div>
            ${f.error ? html`<div class="tv-note">${f.error}</div>` : ''}
            <button class="btn small" data-flog="${f.id}">Show what the stream is doing</button>
            <pre class="tv-trace" id="tv-flog-${f.id}" hidden></pre>
          </div>
          <div class="tv-actions"><button class="btn small" data-fstop="${f.id}">Stop</button></div>
        </div>`)}
      </section>` : ''}
      ${failed.failures.length ? html`<section class="panel"><h3>Would not start</h3>
        <p class="lead">The last few channels that failed to come on. A channel is tuned first and converted
          second, so these show the tuning attempts — and FFmpeg's own output only when it got that far.</p>
        ${failed.failures.slice().reverse().map((f, i) => html`<div class="tv-source">
          <div class="tv-ic">${icon('live')}</div>
          <div class="tv-body">
            <div class="tv-name">${f.name}</div>
            <div class="tv-sub">input ${f.input} · ${f.video}/${f.audio} · ${f.mode} · ${ago(f.when)}</div>
            ${f.error ? html`<div class="tv-note">${f.error}</div>` : ''}
            <button class="btn small" data-xlog="${i}">Show what happened</button>
            <pre class="tv-trace" id="tv-xlog-${i}" hidden>${(f.lines || []).join('\n')}</pre>
          </div>
        </div>`)}
      </section>` : ''}
      <section class="panel" data-panel="tuners"><h3>TV tuners</h3>
        <p class="lead">LENTA looks for USB tuners by itself, every minute, and scans a new one once in the
          background. Nothing here needs setting up.</p>
        ${tuners.length ? tuners.map(sourceLine)
          : html`<div class="tv-empty">
              ${o.dvb_present
                ? html`<b>No tuner found.</b> The system has a DVB folder but no tuner in it. Plug the tuner in, give it a moment, then press <i>Look again</i>.`
                : html`<b>No tuner found.</b> This machine has no <code>/dev/dvb</code>, so either no tuner is plugged in or its driver isn't loaded. Most USB tuners work with the drivers already in Ubuntu; some need a firmware file in <code>/lib/firmware</code>.`}
              <div style="margin-top:12px"><button class="btn small" id="tv-detect">Look again</button></div></div>`}
        ${tuners.length ? html`<div style="margin-top:12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap">
          <button class="btn small" id="tv-detect">Look again</button>
          <label class="check"><input type="checkbox" id="tv-auto" ${o.auto_scan ? raw('checked') : ''}>
            <span>Scan a newly found tuner by itself</span></label></div>
          <p class="hint">Switch this off while you are testing the tuner from the command line — a scan holds
            the tuner, and nothing else can use it meanwhile.</p>` : ''}
      </section>

      <section class="panel" data-panel="playlists"><h3>Playlists</h3>
        <p class="lead">An m3u address or an Xtream Codes login. Its channels sit in the same list as the tuner's.</p>
        ${lists.map(sourceLine)}
        <div style="margin-top:12px"><button class="btn small primary" id="tv-add">${icon('plus')}Add a playlist</button></div>
      </section>

      <section class="panel" data-panel="channels"><h3>Channels</h3>
        ${o.channels.length ? html`
          <p class="lead">${o.channels.length} found, in the order the provider asks for. Hidden channels stay
            out of the Live TV library.</p>
          <div style="margin:10px 0 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
            <button class="btn small" id="tv-logos">Find channel logos</button>
            <span class="lead" style="margin:0">A broadcast carries no artwork, so logos are matched by name
              against a collection you point LENTA at. Channels without one get a monogram.</span>
          </div>
          ${guideLine(o.guide)}
          <div style="margin:-6px 0 14px;display:flex;gap:18px;flex-wrap:wrap">
            <label class="check"><input type="checkbox" id="g-watching" ${o.guide?.watching_on ? raw('checked') : ''}>
              <span>Collect while watching</span></label>
            <label class="check"><input type="checkbox" id="g-sweep" ${o.guide?.sweep_on ? raw('checked') : ''}>
              <span>Tune spare inputs to collect the rest</span></label>
          </div>
          <div class="table-wrap"><table class="table"><thead><tr>
            <th>No.</th><th>Channel</th><th>Type</th><th>Provider</th><th>Source</th><th class="right">Shown</th>
          </tr></thead><tbody>${o.channels.map(channelRow)}</tbody></table></div>`
          : html`<p class="lead">Nothing found yet. ${tuners.length ? 'A scan takes a few minutes — it tries every frequency a broadcaster may use, then follows the network it finds.' : 'Plug in a tuner or add a playlist.'}</p>`}
      </section>

      <section class="panel" data-panel="watch"><h3>Watching</h3>
        <p class="lead">Add a <b>Live TV</b> library in <a href="#/settings/libraries">Settings › Libraries</a> and
          these channels appear in the menu. Playing them comes in the next step.</p>
      </section>`);

    $('#tv-detect')?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try { await api.post('/api/admin/livetv/detect'); toast('Looked again'); } catch (ex) { toast(ex.message, 'error'); }
      draw();
    });
    $('#tv-add')?.addEventListener('click', addPlaylist);
    $('#tv-auto')?.addEventListener('change', async (e) => {
      try { await api.patch('/api/admin/livetv/settings', { auto_scan: e.currentTarget.checked }); }
      catch (ex) { toast(ex.message, 'error'); }
    });
    $$('[data-stop]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      try { await api.post(`/api/admin/livetv/sources/${b.dataset.stop}/stop`); toast('Stopping the scan…'); }
      catch (ex) { toast(ex.message, 'error'); }
      setTimeout(draw, 800);
    }));
    $$('[data-scan]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      try { await api.post(`/api/admin/livetv/sources/${b.dataset.scan}/scan`); } catch (ex) { toast(ex.message, 'error'); }
      draw();
    }));
    const guideSwitch = async () => {
      try {
        await api.post('/api/admin/livetv/guide', {
          watching: $('#g-watching').checked, sweep: $('#g-sweep').checked });
        toast('Saved. It takes effect on the next channel you start.');
      } catch (ex) { toast(ex.message, 'error'); }
    };
    $('#g-watching')?.addEventListener('change', guideSwitch);
    $('#g-sweep')?.addEventListener('change', guideSwitch);
    $('#tv-logos')?.addEventListener('click', async () => {
      const want = await askLogoSource(o.logo_index || '', o.logo_country || '');
      if (!want) return;
      const btn = $('#tv-logos');
      btn.disabled = true;
      btn.textContent = 'Looking…';
      try {
        const r = await api.post('/api/admin/livetv/logos', want);
        toast(r.found
          ? `${r.found} logo${r.found === 1 ? '' : 's'} found. ${r.skipped ? `${r.skipped} already had one. ` : ''}Anything unmatched keeps its monogram.`
          : `Nothing matched. That collection offers ${r.offered} names, none of them yours — try a different country, or another collection.`);
      } catch (ex) { toast(ex.message, 'error'); }
      draw();
    });
    $$('[data-xlog]').forEach(b => b.addEventListener('click', () => {
      const pre = $(`#tv-xlog-${b.dataset.xlog}`);
      if (!pre) return;
      pre.hidden = !pre.hidden;
      b.textContent = pre.hidden ? 'Show what happened' : 'Hide';
    }));
    $$('[data-flog]').forEach(b => b.addEventListener('click', async () => {
      const pre = $(`#tv-flog-${b.dataset.flog}`);
      if (!pre) return;
      if (!pre.hidden) { pre.hidden = true; b.textContent = 'Show what the stream is doing'; return; }
      b.disabled = true;
      try {
        const r = await api.get(`/api/livetv/live/${b.dataset.flog}/log`);
        pre.textContent = r.lines.length ? r.lines.join('\n') : 'Nothing to report — the stream has said nothing.';
      } catch (ex) { pre.textContent = `Could not read it: ${ex.message}`; }
      pre.hidden = false; b.disabled = false; b.textContent = 'Hide';
    }));
    $$('[data-fstop]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      try { await api.del(`/api/admin/livetv/live/${b.dataset.fstop}`); } catch (ex) { toast(ex.message, 'error'); }
      draw();
    }));
    $$('[data-trace]').forEach(b => b.addEventListener('click', async () => {
      const pre = $(`#tv-trace-${b.dataset.trace}`);
      if (!pre) return;
      if (!pre.hidden) { pre.hidden = true; b.textContent = 'Show what the scan saw'; return; }
      b.disabled = true;
      try {
        const r = await api.get(`/api/admin/livetv/sources/${b.dataset.trace}/trace`);
        pre.textContent = r.lines.length ? r.lines.join('\n')
          : 'No scan has run since the server was last restarted.';
      } catch (ex) { pre.textContent = `Could not read it: ${ex.message}`; }
      pre.hidden = false;
      b.disabled = false;
      b.textContent = 'Hide';
    }));
    $$('[data-menu]').forEach(b => b.addEventListener('click', () => {
      const s = o.sources.find(x => x.id === Number(b.dataset.menu));
      popMenu(b, [
        { label: s.enabled ? 'Switch off' : 'Switch on', action: async () => {
          await api.patch(`/api/admin/livetv/sources/${s.id}`, { enabled: !s.enabled }); draw();
        } },
        { label: 'Rename…', action: async () => {
          const name = await askName(s.name);
          if (name) { await api.patch(`/api/admin/livetv/sources/${s.id}`, { name }); draw(); }
        } },
        ...(s.kind === 'tuner' ? [{ label: 'Full scan (every frequency)', hint: 'slower', action: async () => {
          await api.post(`/api/admin/livetv/sources/${s.id}/scan?deep=true`); draw();
        } }] : [{ sep: true }, { label: 'Remove', action: async () => {
          if (await confirmDialog('Remove this playlist?', `${s.name} and its channels are removed from LENTA.`, 'Remove', true)) {
            await api.del(`/api/admin/livetv/sources/${s.id}`); draw();
          }
        } }]),
      ]);
    }));
    $$('[data-hide]').forEach(b => b.addEventListener('click', async () => {
      const row = b.closest('tr');
      const hidden = !row.classList.contains('tv-hidden');
      try { await api.patch(`/api/admin/livetv/channels/${b.dataset.hide}`, { hidden }); draw(); }
      catch (ex) { toast(ex.message, 'error'); }
    }));
    clearInterval(timer);
    if (busy) timer = setInterval(draw, 1500);        // follow a scan while it runs
  };
  await draw();
  window.addEventListener('hashchange', () => clearInterval(timer), { once: true });
}

function askName(current) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal(html`<h2>Rename</h2>
      <form id="nform"><input class="input" id="nval" value="${current}" autofocus style="width:100%">
      <div class="foot"><button type="button" class="btn" data-close>Cancel</button>
        <button class="btn primary">Save</button></div></form>`, { onClose: () => { if (!done) resolve(null); } });
    m.box.querySelector('#nform').addEventListener('submit', (e) => {
      e.preventDefault();
      done = true;
      const v = m.box.querySelector('#nval').value.trim();
      m.close();
      resolve(v || null);
    });
  });
}

function addPlaylist() {
  const m = modal(html`<h2>Add a playlist</h2>
    <form id="pform">
      <div class="field"><label for="p-kind">Type</label>
        <select class="input" id="p-kind">
          <option value="m3u">m3u address</option>
          <option value="xtream">Xtream Codes login</option>
        </select></div>
      <div class="field"><label for="p-name">Name</label><input class="input" id="p-name" placeholder="My provider"></div>
      <div class="field"><label for="p-url">Address</label><input class="input" id="p-url" placeholder="http://example.com/playlist.m3u" required></div>
      <div id="p-auth" hidden>
        <div class="field"><label for="p-user">User name</label><input class="input" id="p-user"></div>
        <div class="field"><label for="p-pass">Password</label><input class="input" id="p-pass" type="password"></div>
      </div>
      <div class="foot"><button type="button" class="btn" data-close>Cancel</button>
        <button class="btn primary">Add and read it</button></div>
    </form>`);
  const kind = m.box.querySelector('#p-kind');
  kind.addEventListener('change', () => {
    m.box.querySelector('#p-auth').hidden = kind.value !== 'xtream';
    m.box.querySelector('#p-url').placeholder = kind.value === 'xtream'
      ? 'http://example.com:8080' : 'http://example.com/playlist.m3u';
  });
  m.box.querySelector('#pform').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = { kind: kind.value, name: m.box.querySelector('#p-name').value,
      url: m.box.querySelector('#p-url').value, username: m.box.querySelector('#p-user').value,
      password: m.box.querySelector('#p-pass').value };
    try {
      await api.post('/api/admin/livetv/playlists', body);
      m.close();
      toast('Reading the playlist…');
      renderLiveTv($('#sset-body') || $('#view'));
    } catch (ex) { toast(ex.message, 'error'); }
  });
}
