// Plex-style metadata editing: the pencil and ⋮ menu on cards, and the Edit dialog.
// Every field has a lock; changing a field locks it so "Refresh Metadata" keeps your version.
import { api } from './api.js';
import { refreshView, state } from './app.js';
import { trailerPlayer } from './extras.js';
import { $, esc, fmtBytes, fmtRuntime, html, icon, modal, mount, popMenu, raw, setCardTools, toast } from './ui.js';

const LABELS = {
  title: 'Title', sort_title: 'Sort title', original_title: 'Original title', edition: 'Edition',
  release_date: 'Originally available', year: 'Year', certification: 'Content rating', rating: 'Rating',
  runtime: 'Runtime (minutes)', studios: 'Studio', tagline: 'Tagline', overview: 'Summary', artist: 'Artist',
  genres: 'Genres', directors: 'Directors', writers: 'Writers', creators: 'Created by',
};
const TAG_FIELDS = ['genres', 'directors', 'writers', 'creators'];
const ART_LABELS = { poster: 'Poster', backdrop: 'Background', logo: 'Logo', thumb: 'Thumbnail' };
const SHORT = ['release_date', 'certification', 'rating', 'year', 'runtime'];

// ---- card tools ---------------------------------------------------------------------------
export function initCardTools() {
  setCardTools(!!state.user?.is_admin);
}

function cardClick(e) {
  const edit = e.target.closest('[data-card-edit]');
  const more = e.target.closest('[data-card-menu]');
  if (!edit && !more) return;
  e.preventDefault();
  e.stopPropagation();
  if (edit) return openEditor(Number(edit.dataset.cardEdit));
  const id = Number(more.dataset.cardMenu);
  popMenu(more, [{ label: 'Refresh Metadata', action: () => refreshMetadata(id) }]);
}
document.addEventListener('click', cardClick, true);
document.addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('[data-card-edit], [data-card-menu]')) cardClick(e);
}, true);

export async function refreshMetadata(id) {
  toast('Refreshing metadata…');
  try {
    const r = await api.post(`/api/admin/items/${id}/refresh-metadata`);
    toast(`Metadata refreshed: ${r.title}`);
    refreshView();
  } catch (ex) { toast(ex.message, 'error'); }
}

// ---- the Edit dialog -------------------------------------------------------------------
export async function openEditor(id, done = refreshView) {
  let d;
  try { d = await api.get(`/api/admin/items/${id}/metadata`); } catch (ex) { return toast(ex.message, 'error'); }
  const values = structuredClone(d.values);
  const locked = new Set(d.locked);
  const pendingArt = {};                     // type -> { ref } | { url } | { file, preview }
  const general = d.fields.filter(f => !TAG_FIELDS.includes(f));
  const tags = d.fields.filter(f => TAG_FIELDS.includes(f));
  const arts = Object.keys(d.artwork);
  const tabs = [['general', 'General', 'text'], ...(tags.length ? [['tags', 'Tags', 'tag']] : []),
    ...arts.map(a => [a, ART_LABELS[a], 'image']),
    ...(d.kind === 'movie' || d.kind === 'show' ? [['trailers', 'Trailers', 'film']] : []), ['info', 'Info', 'info']];
  let tab = 'general';
  let homeTrailer = !!d.home_trailer;          // trailer in the Home banner: off unless ticked

  const m = modal(html`<div class="editor">
    <header><span class="ed-ic">${icon('edit')}</span><h2>Edit ${values.title || ''}</h2>
      <button class="ed-x" data-close aria-label="Close">${icon('close')}</button></header>
    <div class="ed-body"><nav class="ed-tabs">${tabs.map(([k, l, ic]) => html`<button type="button" data-tab="${k}">${icon(ic)}${l}</button>`)}</nav>
      <div class="ed-pane" id="ed-pane"></div></div>
    <footer><span class="ed-note">Changed fields are locked, so Refresh Metadata keeps them.</span>
      <button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" id="ed-save">Save Changes</button></footer>
  </div>`, { wide: true });
  m.box.classList.add('modal-editor');
  const pane = m.box.querySelector('#ed-pane');

  const lockBtn = (f) => html`<button type="button" class="ed-lock ${locked.has(f) ? 'on' : ''}" data-lock="${f}"
    title="${locked.has(f) ? 'Locked: Refresh Metadata keeps this' : 'Unlocked: Refresh Metadata may change this'}">${icon(locked.has(f) ? 'lock' : 'unlock')}</button>`;

  function field(f) {
    const v = values[f];
    let input;
    if (f === 'overview') input = html`<textarea class="input" data-f="${f}" rows="6">${v || ''}</textarea>`;
    else if (f === 'release_date') input = html`<input class="input" type="date" data-f="${f}" value="${v || ''}">`;
    else if (f === 'year' || f === 'runtime') input = html`<input class="input" type="number" data-f="${f}" value="${v ?? ''}">`;
    else if (f === 'rating') input = html`<input class="input" type="number" step="0.1" min="0" max="10" data-f="${f}" value="${v ?? ''}">`;
    else if (f === 'studios') input = html`<input class="input" data-f="${f}" data-list="1" value="${(v || []).join(', ')}">`;
    else input = html`<input class="input" data-f="${f}" value="${v ?? ''}">`;
    return html`<div class="ed-field ${SHORT.includes(f) ? 'short' : ''}"><label>${LABELS[f] || f}</label>
      <div class="ed-row">${lockBtn(f)}${input}</div></div>`;
  }

  function tagField(f) {
    return html`<div class="ed-field"><label>${LABELS[f]}</label><div class="ed-row">${lockBtn(f)}
      <div class="chips-input" data-tags="${f}">${(values[f] || []).map((t, i) => html`<span class="tagchip">${t}<button type="button" data-untag="${i}" aria-label="Remove ${t}">${icon('close')}</button></span>`)}
        <input class="ti" placeholder="Add…" aria-label="Add to ${LABELS[f]}"></div></div></div>`;
  }

  async function artPane(type) {
    const cur = pendingArt[type]?.preview || pendingArt[type]?.url || d.artwork[type];
    mount(pane, html`<div class="ed-art">
      <div class="ed-art-head">${lockBtn(type)}<div><b>${ART_LABELS[type]}</b><div class="sub">Pick one, upload your own, or paste a link.</div></div>
        <label class="btn small"><input type="file" accept="image/jpeg,image/png,image/webp" hidden id="ed-file">${icon('upload')}Upload</label></div>
      <div class="ed-url"><input class="input" id="ed-url" placeholder="https://… image link"><button type="button" class="btn small" id="ed-url-go">Use link</button></div>
      <div class="art-grid ${type}" id="ed-grid">${cur ? artTile({ url: cur, source: pendingArt[type] ? 'Selected' : 'Current' }, true) : ''}<div class="art-loading">Loading choices…</div></div></div>`);
    pane.querySelector('#ed-file').addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      pendingArt[type] = { file, preview: URL.createObjectURL(file) };
      locked.add(type); artPane(type);
    });
    pane.querySelector('#ed-url-go').addEventListener('click', () => {
      const url = pane.querySelector('#ed-url').value.trim();
      if (!/^https?:\/\//.test(url)) return toast('Paste a link that starts with http:// or https://', 'error');
      pendingArt[type] = { url, preview: url }; locked.add(type); artPane(type);
    });
    let choices = [], sources = [];
    try { ({ choices, sources = [] } = await api.get(`/api/admin/items/${d.id}/artwork?type=${type}`)); } catch (ex) { toast(ex.message, 'error'); }
    if (tab !== type) return;
    const grid = pane.querySelector('#ed-grid');
    // what each artwork source found, or why it found nothing (Admin › Settings › Artwork sources)
    grid.insertAdjacentHTML('beforebegin', String(html`<ul class="art-report">${sources.map(r => html`<li class="${r.count ? 'ok' : 'none'}">
      <b>${r.source}</b><span>${r.note}</span></li>`)}</ul>`));
    const sel = pendingArt[type]?.ref;
    mount(grid, choices.length || cur
      ? html`${pendingArt[type]?.preview ? artTile({ url: pendingArt[type].preview, source: 'Selected' }, true) : ''}
          ${choices.map(c => artTile(c, sel ? c.ref === sel : (!pendingArt[type] && c.source === 'Current')))}`
      : html`<p class="sub">No artwork found online. Upload an image or paste a link.</p>`);
    grid.querySelectorAll('[data-ref]').forEach(t => t.addEventListener('click', () => {
      pendingArt[type] = { ref: t.dataset.ref, preview: null };
      locked.add(type);
      grid.querySelectorAll('.art-tile').forEach(x => x.classList.toggle('sel', x === t));
      syncLock(type);
    }));
  }
  const artTile = (c, selected) => html`<button type="button" class="art-tile ${selected ? 'sel' : ''}" ${c.ref ? raw(`data-ref="${esc(c.ref)}"`) : ''}>
    <img loading="lazy" src="${c.url}" alt=""><span>${c.source}${c.size ? ` · ${c.size}` : ''}${c.lang ? ` · ${c.lang}` : ''}</span></button>`;

  // Trailers: up to four from TMDB to choose from, each playable here before choosing.
  let trailerData = null;
  async function trailerPane() {
    mount(pane, html`<div class="ed-art-head">${lockBtn('trailer')}<div><b>Trailer</b>
      <div class="sub">Used on the title's page, in hover previews and in the Home banner. Your choice is locked, so updates keep it.</div></div></div>
      <form class="tr-add" id="tr-add"><input class="input" id="tr-url" type="text" inputmode="url" autocomplete="off"
          placeholder="Paste a YouTube link (youtube.com/watch?v=… or youtu.be/…)" aria-label="YouTube trailer link">
        <button class="btn small">${icon('plus')}Add link</button></form>
      <div class="sub tr-add-hint">Your links are kept with this title and come first: if the trailer in use doesn't play in your country, LENTA uses your link.</div>
      <div class="tr-list" id="tr-list"><div class="art-loading">Loading trailers…</div></div>`);
    pane.querySelector('#tr-add').addEventListener('submit', async (e) => {
      e.preventDefault();
      const input = pane.querySelector('#tr-url'), url = input.value.trim();
      if (!url) return;
      const btn = e.currentTarget.querySelector('button');
      btn.disabled = true;
      try {
        const c = await api.post(`/api/admin/items/${d.id}/trailer-link`, { url });
        if (!trailerData) trailerData = await api.get(`/api/admin/items/${d.id}/trailers`);
        trailerData.choices = [c, ...trailerData.choices.filter(x => x.key !== c.key)];
        const links = values.trailer_links || trailerData.choices.filter(x => x.source === 'link').map(x => ({ key: x.key, name: x.name }));
        values.trailer_links = [{ key: c.key, name: c.name }, ...links.filter(x => x.key !== c.key)];
        values.trailer = c.key; values.trailer_name = c.name; values.trailer_source = 'link';       // use it straight away
        locked.add('trailer'); syncLock('trailer');
        input.value = '';
        trailerPane();
        toast(c.unavailable ? 'Added, but this video was already found not to play here.' : 'Link added and in use. Save Changes to keep it.', c.unavailable ? 'error' : undefined);
      } catch (ex) { toast(ex.message, 'error'); }
      finally { btn.disabled = false; }
    });
    if (!trailerData) {
      try { trailerData = await api.get(`/api/admin/items/${d.id}/trailers`); } catch (ex) { toast(ex.message, 'error'); return; }
    }
    if (tab !== 'trailers') return;
    const chosen = 'trailer' in values ? values.trailer : trailerData.current;
    const list = pane.querySelector('#tr-list');
    const facts = (c) => [c.type, c.language ? c.language.toUpperCase() : '', c.official ? 'Official' : '',
      c.size ? `${c.size}p` : '', c.published || ''].filter(Boolean).join(' · ');
    mount(list, html`${trailerData.note ? html`<p class="sub">${trailerData.note}</p>` : ''}
      ${trailerData.choices.map(c => html`<div class="tr-tile ${c.key === chosen ? 'sel' : ''}" data-key="${c.key}">
        <div class="tr-thumb">${c.key.startsWith('v:') ? html`<div class="tr-file">${c.source === 'local' ? c.file || 'Trailer file' : 'Apple TV'}</div>`
          : html`<img loading="lazy" src="https://i.ytimg.com/vi/${encodeURIComponent(c.key)}/hqdefault.jpg" alt="" onerror="this.style.visibility='hidden'">`}
          <button type="button" class="tr-play" data-play="${c.key}" title="Watch it here" aria-label="Watch ${c.name || 'trailer'}">${icon('play')}</button></div>
        <div class="tr-info"><b><span class="tr-src ${c.source || 'youtube'}">${{ local: 'Your file', link: 'Your link', apple: 'Apple TV', kinocheck: 'KinoCheck', youtube: 'YouTube' }[c.source] || 'YouTube'}</span>${c.name || 'Trailer'}</b>${c.unavailable ? html`<span class="tr-blocked">Doesn't play here (blocked in your country or by the uploader)${c.key === chosen ? ' — LENTA plays the next one instead; pick another to replace it' : ''}</span>` : ''}<span>${facts(c)}</span>
          <button type="button" class="btn small ${c.key === chosen ? 'primary' : ''}" data-use="${c.key}">${c.key === chosen ? html`${icon('check')}In use` : 'Use this trailer'}</button>${c.source === 'link' ? html` <button type="button" class="btn small ghost" data-unlink="${c.key}">Remove link</button>` : ''}</div>
      </div>`)}
      <button type="button" class="tr-none ${chosen === null && 'trailer' in values ? 'sel' : ''}" data-use="">No trailer for this title</button>`);
    list.querySelectorAll('[data-play]').forEach(b => b.addEventListener('click', () => {
      const thumb = b.closest('.tr-thumb');
      list.querySelectorAll('.tr-thumb iframe, .tr-thumb video').forEach(f => f.remove());      // one at a time
      thumb.append(trailerPlayer(b.dataset.play, { muted: false, controls: true, replace: false, skipLead: false, onUnavailable: (key) => {
        const c = trailerData.choices.find(x => x.key === key);
        if (c && !c.unavailable) { c.unavailable = true; trailerPane(); }
      } }).el);
    }));
    list.querySelectorAll('[data-unlink]').forEach(b => b.addEventListener('click', () => {
      const key = b.dataset.unlink;
      const links = values.trailer_links || trailerData.choices.filter(x => x.source === 'link').map(x => ({ key: x.key, name: x.name }));
      values.trailer_links = links.filter(x => x.key !== key);
      trailerData.choices = trailerData.choices.filter(x => !(x.key === key && x.source === 'link'));
      if (chosen === key) { const n = trailerData.choices.find(x => x.key !== key && !x.unavailable); values.trailer = n?.key || null; values.trailer_name = n?.name || ''; values.trailer_source = n?.source || ''; }
      trailerPane();
    }));
    list.querySelectorAll('[data-use]').forEach(b => b.addEventListener('click', () => {
      const key = b.dataset.use || null;
      const c = trailerData.choices.find(x => x.key === key);
      values.trailer = key;
      values.trailer_name = c?.name || '';
      values.trailer_source = c?.source || '';
      locked.add('trailer');
      syncLock('trailer');
      trailerPane();
    }));
  }

  function infoPane() {
    mount(pane, html`<dl class="facts ed-info">
      <dt>Type</dt><dd>${d.kind}</dd>
      <dt>Online match</dt><dd>${d.tmdb_id ? html`<a href="https://www.themoviedb.org/${d.tmdb_kind === 'tv' ? 'tv' : 'movie'}/${d.tmdb_id}" target="_blank" rel="noopener" style="color:var(--accent)">TMDB ${d.tmdb_id}</a>` : 'none'}</dd>
      <dt>Locked fields</dt><dd>${locked.size ? [...locked].map(f => LABELS[f] || ART_LABELS[f] || f).join(', ') : 'none'}</dd>
      ${d.files.map((f, i) => html`<dt>File${d.files.length > 1 ? ` ${i + 1}` : ''}</dt><dd><div class="mono" style="word-break:break-all">${f.path}</div>
        <div class="sub">${[f.width && f.height ? `${f.width}×${f.height}` : '', (f.video_codec || '').toUpperCase(), f.container, f.duration ? fmtRuntime(Math.round(f.duration / 60)) : '', fmtBytes(f.size)].filter(Boolean).join(' · ')}</div></dd>`)}
    </dl>`);
  }

  function readPane() {
    pane.querySelectorAll('[data-f]').forEach(el => {
      values[el.dataset.f] = el.dataset.list ? el.value.split(',').map(s => s.trim()).filter(Boolean) : el.value;
    });
  }
  function syncLock(f) {
    const b = pane.querySelector(`[data-lock="${f}"]`);
    if (!b) return;
    b.classList.toggle('on', locked.has(f));
    mount(b, icon(locked.has(f) ? 'lock' : 'unlock'));
    b.title = locked.has(f) ? 'Locked: Refresh Metadata keeps this' : 'Unlocked: Refresh Metadata may change this';
  }

  function show(k) {
    if (tab === 'general' || tab === 'tags') readPane();
    tab = k;
    m.box.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('on', b.dataset.tab === k));
    if (k === 'general') {
      mount(pane, html`<div class="ed-grid">${general.map(field)}</div>
        ${d.kind === 'movie' || d.kind === 'show' ? html`<label class="check ed-home-trailer"><input type="checkbox" id="ed-home-trailer" ${homeTrailer ? raw('checked') : ''}>
          Show the trailer in the Home banner<span class="sub">Off: the banner shows this title's picture only.</span></label>` : ''}`);
      pane.querySelector('#ed-home-trailer')?.addEventListener('change', (e) => { homeTrailer = e.target.checked; });
    }
    else if (k === 'tags') mount(pane, html`<div class="ed-grid">${tags.map(tagField)}</div>`);
    else if (k === 'info') infoPane();
    else if (k === 'trailers') trailerPane();
    else artPane(k);
    pane.scrollTop = 0;
  }

  m.box.querySelector('.ed-tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) show(b.dataset.tab); });
  pane.addEventListener('input', (e) => {
    const f = e.target.dataset.f;
    if (f) { locked.add(f); syncLock(f); }
  });
  pane.addEventListener('click', (e) => {
    const lb = e.target.closest('[data-lock]');
    if (lb) { const f = lb.dataset.lock; locked.has(f) ? locked.delete(f) : locked.add(f); syncLock(f); return; }
    const un = e.target.closest('[data-untag]');
    if (un) {
      const f = un.closest('[data-tags]').dataset.tags;
      values[f].splice(Number(un.dataset.untag), 1); locked.add(f); show('tags');
    }
    const box = e.target.closest('.chips-input');
    if (box && e.target === box) box.querySelector('.ti').focus();
  });
  pane.addEventListener('keydown', (e) => {
    if (!e.target.classList.contains('ti')) return;
    const f = e.target.closest('[data-tags]').dataset.tags;
    const v = e.target.value.trim().replace(/,$/, '');
    if ((e.key === 'Enter' || e.key === ',') && v) {
      e.preventDefault();
      values[f] = [...(values[f] || []), v]; locked.add(f); show('tags');
      pane.querySelector(`[data-tags="${f}"] .ti`)?.focus();
    } else if (e.key === 'Backspace' && !e.target.value && values[f]?.length) {
      values[f].pop(); locked.add(f); show('tags');
      pane.querySelector(`[data-tags="${f}"] .ti`)?.focus();
    } else if (e.key === 'Enter') e.preventDefault();
  });

  m.box.querySelector('#ed-save').addEventListener('click', async (e) => {
    if (tab === 'general' || tab === 'tags') readPane();
    pane.querySelectorAll('.ti').forEach(t => {        // a tag typed but not yet confirmed with Enter
      const v = t.value.trim(); const f = t.closest('[data-tags]').dataset.tags;
      if (v) { values[f] = [...(values[f] || []), v]; locked.add(f); }
    });
    e.target.disabled = true;
    try {
      await api.put(`/api/admin/items/${d.id}/metadata`, { values: { ...values, ...(d.kind === 'movie' || d.kind === 'show' ? { home_trailer: homeTrailer } : {}) }, locked: [...locked] });
      for (const [type, p] of Object.entries(pendingArt)) {
        if (p.file) {
          const res = await fetch(`/api/admin/items/${d.id}/artwork/upload?type=${type}`, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': p.file.type || 'application/octet-stream' }, body: p.file });
          if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Upload failed');
        } else if (p.ref) await api.post(`/api/admin/items/${d.id}/artwork`, { type, ref: p.ref });
        else if (p.url) await api.post(`/api/admin/items/${d.id}/artwork`, { type, url: p.url });
      }
      m.close();
      window.dispatchEvent(new CustomEvent('lenta:metadata-changed', { detail: { id: d.id } }));
      toast('Changes saved');
      done?.();
    } catch (ex) { toast(ex.message, 'error'); e.target.disabled = false; }
  });
  show('general');
}
