// Online subtitle search (OpenSubtitles through the server) and the subtitle dialog on media pages.
import { api } from './api.js';
import { state } from './app.js';
import { confirmDialog, html, icon, lang as langFallback, modal, mount, raw, toast } from './ui.js';

export async function ensureLanguages() {
  if (!state.languages?.length) {
    try { state.languages = await api.get('/api/languages'); } catch { state.languages = []; }
  }
  return state.languages;
}

export function langName(code) {
  const c = (code || '').toLowerCase();
  const hit = (state.languages || []).find(l => l.code === c) || (state.languages || []).find(l => l.code === c.split('-')[0]);
  return hit ? hit.native : langFallback(code);
}

export function languageOptions(selected, { any = null } = {}) {
  return html`${any ? html`<option value="">${any}</option>` : ''}${(state.languages || []).map(l =>
    html`<option value="${l.code}" ${l.code === selected ? raw('selected') : ''}>${l.native}${l.native !== l.name ? ` — ${l.name}` : ''}</option>`)}`;
}

// Renders a language picker + results list into `container`. onDone(subtitle) after a download.
export async function subtitleSearch(container, { itemId, fileId, lang, onDone }) {
  await ensureLanguages();
  const start = lang || state.prefs?.subtitle_lang || 'en';
  mount(container, html`<div class="sub-search">
    <div class="bar"><select class="input" aria-label="Subtitle language">${languageOptions(start)}</select>
      <button type="button" class="btn" data-go>${icon('search')}Search</button></div>
    <div class="sub-list" aria-live="polite"></div></div>`);
  const select = container.querySelector('select');
  const list = container.querySelector('.sub-list');
  const run = async () => {
    mount(list, html`<p style="color:var(--muted);margin:6px 2px">Searching OpenSubtitles…</p>`);
    let r;
    try {
      r = await api.get(`/api/subtitles/search?item_id=${itemId}${fileId ? `&file_id=${fileId}` : ''}&language=${encodeURIComponent(select.value)}`);
    } catch (ex) {
      mount(list, html`<p class="error-text" style="margin:6px 2px">${ex.message}</p>`);
      return;
    }
    if (!r.results.length) {
      mount(list, html`<p style="color:var(--muted);margin:6px 2px">No ${langName(select.value)} subtitles found for this title.</p>`);
      return;
    }
    mount(list, r.results.map((s, i) => html`<button type="button" class="sub-item" data-i="${i}" ${s.have ? raw('disabled') : ''}>
      <span class="rel">${s.release || 'Subtitle'}</span>
      <span class="meta2">
        ${s.hash_match ? html`<span class="tag-match">Matches your file</span>` : ''}
        <span>${s.downloads.toLocaleString()} downloads</span>
        ${s.hearing_impaired ? html`<span>For the hard of hearing</span>` : ''}
        ${s.machine_translated ? html`<span>Machine translated</span>` : ''}
        ${s.uploaded ? html`<span>${s.uploaded}</span>` : ''}
      </span>
      <span class="get">${s.have ? 'Downloaded' : 'Download'}</span></button>`));
    list.querySelectorAll('.sub-item:not([disabled])').forEach(b => b.addEventListener('click', async () => {
      const s = r.results[Number(b.dataset.i)];
      b.disabled = true;
      b.querySelector('.get').textContent = 'Downloading…';
      try {
        const res = await api.post('/api/subtitles/download', { item_id: itemId, file_id: r.file_id, provider_id: s.provider_id,
          language: r.language, release: s.release, hearing_impaired: s.hearing_impaired });
        b.querySelector('.get').textContent = 'Downloaded';
        toast(`${langName(r.language)} subtitles added`);
        onDone?.(res.subtitle);
      } catch (ex) {
        b.disabled = false;
        b.querySelector('.get').textContent = 'Download';
        toast(ex.message, 'error');
      }
    }));
  };
  container.querySelector('[data-go]').addEventListener('click', run);
  select.addEventListener('change', run);
  run();
}

function subLabel(s) {
  const kind = s.downloaded ? 'Downloaded' : s.external ? 'File next to the video' : s.image ? 'Embedded · picture' : 'Embedded';
  return html`<span>${langName(s.lang || s.language)}${s.title ? html` <small>· ${s.title}</small>` : ''}
    ${s.forced ? html` <small>· forced</small>` : ''}${s.hearing_impaired ? html` <small>· SDH</small>` : ''}<br><small>${kind}</small></span>`;
}

export async function openSubtitleDialog(item, file) {
  await ensureLanguages();
  const online = state.features?.subtitles_online;
  const m = modal(html`<h2>Subtitles</h2>
    <div class="sub-have" id="sd-have"></div>
    <h3 style="font:500 18px/1.2 var(--display);margin:0 0 12px">Find online</h3>
    <div id="sd-search">${online ? '' : html`<p style="color:var(--muted)">Online subtitles are off. An administrator can add an
      OpenSubtitles API key in Server admin › Settings.</p>`}</div>
    <div class="foot"><button class="btn" data-close>Done</button></div>`, { wide: true });
  const renderHave = () => {
    const subs = file.subtitles || [];
    mount(m.box.querySelector('#sd-have'), subs.length
      ? subs.map(s => html`<div>${subLabel(s)}${s.downloaded ? html`<button class="btn small" data-rm="${s.dl_id}">Remove</button>` : ''}</div>`)
      : html`<p style="color:var(--muted);margin:0">This file has no subtitles yet.</p>`);
    m.box.querySelectorAll('[data-rm]').forEach(b => b.addEventListener('click', async () => {
      if (!await confirmDialog('Remove these subtitles?', 'They can be downloaded again later.', 'Remove', true)) return;
      try {
        await api.del(`/api/subtitles/${b.dataset.rm}`);
        file.subtitles = file.subtitles.filter(s => String(s.dl_id) !== b.dataset.rm);
        renderHave();
      } catch (ex) { toast(ex.message, 'error'); }
    }));
  };
  renderHave();
  if (online) {
    subtitleSearch(m.box.querySelector('#sd-search'), { itemId: item.id, fileId: file.id, onDone: (sub) => {
      file.subtitles = [...(file.subtitles || []), sub];
      renderHave();
    } });
  }
}
