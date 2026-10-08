// Blocks on a page you can fold, move and resize: the Settings page and the Server admin Dashboard and Settings.
import { $, html, icon, mount, raw, toast } from './ui.js';
import { onCleanup } from './extras.js';

// ---- folding blocks: an arrow on each block's title folds it to the title and opens it again (for
// this visit). Which blocks start open is part of the layout (settings_layout.closed: the ids that start folded),
// chosen with "Starts open" in Reorder mode.
function foldPanels(canvas, closed) {
  [...canvas.children].filter(p => p.matches && p.matches('.panel[data-panel]')).forEach((p) => {   // (no :scope: old TVs)
    const h = p.querySelector('h3');
    if (!h || h.querySelector('.panel-fold')) return;          // done already
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'panel-fold';
    mount(b, icon('chevron-down'));
    h.classList.add('fold-head');
    h.append(b);
    const set = (shut) => {
      p.classList.toggle('collapsed', shut);
      b.setAttribute('aria-expanded', String(!shut));
      const name = h.firstChild?.textContent?.trim() || '';
      b.title = shut ? 'Open' : 'Fold';
      b.setAttribute('aria-label', `${shut ? 'Open' : 'Fold'} ${name}`);
    };
    if (!p.dataset.folded) { p.dataset.folded = '1'; set(closed.includes(p.dataset.panel)); }
    else set(p.classList.contains('collapsed'));
    b.addEventListener('click', () => set(!p.classList.contains('collapsed')));
    h.addEventListener('dblclick', (e) => { if (!e.target.closest('button')) set(!p.classList.contains('collapsed')); });
  });
}

// ---- page layout: Reorder (place and size the blocks freely), Save, Default ----------------------------------
// A free canvas: each block has its own place and size, { x, w } in 1/48ths of the page width (so a layout
// scales with the window) and { y, h } in pixels (h empty = as tall as its content). Blocks go anywhere — left,
// centre, right, side by side, with space between — and never overlap: whatever a block would cover is pushed
// down below it. Kept per user and page as { items: { id: { x, y, w, h } } | null, closed: [ids], log: [{ t, m }] };
// items null = the original layout (columns). Phones always get one column; old TV engines keep the plain page.
const UNITS = 48, YSNAP = 8, GAP = 22, MIN_W = 8, MIN_H = 120, SNAP = 14;

/** The Reorder / Save / Default buttons (for the page head) and the bar shown while reordering. */
export const layoutButtons = () => html`<div class="lay-btns" id="lay-btns">
    <button type="button" class="btn" id="lay-reorder" title="Move and resize the blocks of this page">${icon('grip')}<span>Reorder</span></button>
    <button type="button" class="btn primary" id="lay-save" disabled title="Keep this layout">Save</button>
    <button type="button" class="btn ghost" id="lay-default" title="Back to the original layout">Default</button>
  </div>`;
export const layoutBar = () => html`<div class="lay-bar" id="lay-bar" hidden></div>`;

/** Folding and the free layout for the blocks (.panel[data-panel]) in `canvas`. `value` is the saved layout
 *  (JSON); `save(value)` stores it. Returns { refresh, editing } — call refresh() after the page replaced the
 *  insides of its blocks (the arrows come back, the layout is drawn again). */
export function blockLayout({ root, canvas, value, save, names = {} }) {
  const view = root, cols = canvas;
  const btns = view.querySelector('#lay-btns');
  let closedAtStart = [];
  try { closedAtStart = JSON.parse(value || '{}').closed || []; } catch { /* none */ }
  cols.classList.add('lay-canvas');
  foldPanels(cols, closedAtStart);
  const api0 = { refresh: () => foldPanels(cols, closedAtStart), editing: () => false };
  if (!cols || !btns) return api0;
  if (!window.ResizeObserver || !window.CSS || !CSS.supports || !CSS.supports('display', 'grid')) { btns.hidden = true; return api0; }
  const panels = () => [...cols.children].filter(p => p.matches && p.matches('.panel[data-panel]'));
  const IDS = panels().map(p => p.dataset.panel);
  const el = (id) => cols.querySelector(`.panel[data-panel="${id}"]`);
  const nameOf = (id) => names[id] || el(id)?.querySelector('h3')?.firstChild?.textContent?.trim() || id;
  const phone = () => matchMedia('(max-width: 720px)').matches;
  const parse = (v) => { try { const o = JSON.parse(v || '{}'); return o && typeof o === 'object' ? o : {}; } catch { return {}; } };
  const norm = (o) => ({ items: o.items && typeof o.items === 'object' ? JSON.parse(JSON.stringify(o.items)) : null,
    closed: Array.isArray(o.closed) ? [...o.closed] : [], log: Array.isArray(o.log) ? [...o.log] : [] });
  const clone = norm;
  let stored = norm(parse(value));
  let cur = clone(stored);
  let editing = false, before = null, dragging = false;

  // ---- geometry
  const unit = () => cols.clientWidth / UNITS;
  const place = (p, it) => {
    const u = unit();
    p.style.left = `${it.x * u + GAP / 2}px`;
    p.style.width = `${Math.max(0, it.w * u - GAP)}px`;
    p.style.height = it.h ? `${it.h}px` : '';
    p.classList.toggle('lay-fixed', !!it.h);
  };
  const overlapX = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w;
  // no overlaps: the pinned block (being moved or resized) keeps its place; the others, top to bottom, go below anything they would cover
  const resolve = (boxes, pinned) => {
    const order = [...boxes].sort((a, b) => (b.id === pinned) - (a.id === pinned) || a.y - b.y || a.x - b.x);
    const done = [];
    for (const b of order) {
      let moved = true;
      while (moved) {
        moved = false;
        for (const o of done) {
          if (overlapX(b, o) && b.y < o.y + o.h + GAP && b.y + b.h + GAP > o.y) { b.y = o.y + o.h + GAP; moved = true; }
        }
      }
      done.push(b);
    }
    return boxes;
  };
  // the original arrangement: columns (3 on a wide window, 2 on a smaller one), each block in the shortest column
  const columnsLayout = () => {
    const n = matchMedia('(max-width: 1399px)').matches ? 2 : 3;
    const w = UNITS / n, items = {}, bottoms = new Array(n).fill(0);
    // blocks marked .wide (e.g. "Playing now") take the whole width, below everything before them
    IDS.forEach((id) => { items[id] = { x: 0, y: 0, w: el(id).classList.contains('wide') ? UNITS : w, h: null }; place(el(id), items[id]); });
    IDS.forEach((id) => {
      if (items[id].w === UNITS && n > 1) {
        const top = Math.max(...bottoms);
        items[id].y = top;
        bottoms.fill(top + el(id).offsetHeight + GAP);
        return;
      }
      const c = bottoms.indexOf(Math.min(...bottoms));
      items[id].x = c * w; items[id].y = bottoms[c];
      bottoms[c] += el(id).offsetHeight + GAP;
    });
    return items;
  };
  const stack = (side) => {
    const w = 20, x = side === 'left' ? 0 : side === 'right' ? UNITS - w : (UNITS - w) / 2;
    const items = {};
    let y = 0;
    sorted().forEach((id) => {
      const h = (cur.items?.[id] || {}).h || null;
      items[id] = { x, y, w, h };
      place(el(id), items[id]);
      y += el(id).offsetHeight + GAP;
    });
    return items;
  };
  const sorted = () => {
    const it = cur.items || {};
    return [...IDS].sort((a, b) => ((it[a]?.y ?? 0) - (it[b]?.y ?? 0)) || ((it[a]?.x ?? 0) - (it[b]?.x ?? 0)) || IDS.indexOf(a) - IDS.indexOf(b));
  };
  const itemsNow = () => cur.items || columnsLayout();
  // ---- draw a layout; returns the boxes as drawn (y after pushing blocks apart)
  const render = (items = itemsNow(), pinned = null) => {
    if (phone()) {                       // one column, in reading order
      cols.classList.remove('lay-free');
      cols.style.height = '';
      sorted().forEach(id => { const p = el(id); p.style.cssText = ''; p.classList.remove('lay-fixed'); cols.append(p); });
      return [];
    }
    cols.classList.add('lay-free');
    const boxes = IDS.map((id) => {
      const it = items[id] || { x: 0, y: 99999, w: 16, h: null };
      const p = el(id);
      place(p, it);
      return { id, x: it.x, w: it.w, y: it.y, h: p.offsetHeight, fixed: it.h || null };
    });
    resolve(boxes, pinned);
    // no holes: top to bottom, every block rises to just under whatever is above it (one gap apart), so nothing ever
    // floats with empty space above it — after folding, after content loads and grows or shrinks, after a move.
    // Measured from the heights as they are now. The block being dragged stays where the pointer puts it.
    const done = [];
    [...boxes].sort((a, b) => a.y - b.y || a.x - b.x).forEach((b) => {
      if (b.id !== pinned) b.y = done.filter(o => overlapX(b, o)).reduce((m, o) => Math.max(m, o.y + o.h + GAP), 0);
      done.push(b);
    });
    let bottom = 0;
    boxes.forEach((b) => { el(b.id).style.top = `${b.y}px`; bottom = Math.max(bottom, b.y + b.h); });
    cols.style.height = `${bottom}px`;
    return boxes;
  };
  const fromBoxes = (boxes) => Object.fromEntries(boxes.map(b => [b.id, { x: b.x, y: b.y, w: b.w, h: b.fixed }]));
  let frame = 0;
  const redraw = () => { if (dragging) return; cancelAnimationFrame(frame); frame = requestAnimationFrame(() => render()); };
  cols.addEventListener('click', (e) => { if (e.target.closest('.panel-fold')) redraw(); });
  const ro = new ResizeObserver(redraw);
  panels().forEach(p => ro.observe(p));
  ro.observe(cols);
  const onWin = () => { btns.hidden = phone(); redraw(); };
  window.addEventListener('resize', onWin);
  onCleanup(() => { ro.disconnect(); window.removeEventListener('resize', onWin); document.removeEventListener('keydown', onKey); });
  btns.hidden = phone();
  render();

  // ---- the log and the buttons
  const note = (m) => { cur.log.push({ t: Date.now(), m }); renderBar(); setButtons(); };
  const renderBar = () => {
    const bar = view.querySelector('#lay-bar');
    bar.hidden = !editing;
    if (!editing) return;
    // (every change is still logged with the layout, in the background: its .log)
    mount(bar, html`<div class="lay-hint">${icon('grip')}<span><b>Reorder mode.</b> Drag a block by its bar to put it anywhere; it snaps to the
        edges and the centre of the page and to the other blocks. Drag its edges or corner to resize it. Blocks never overlap: what one
        would cover moves down, and blocks always close up under the ones above them, so there are never empty gaps. <b>Save</b> keeps the layout; <b>Cancel</b> or Esc leaves without saving.</span></div>
      <div class="lay-arrange"><span>Arrange all:</span>
        <button type="button" class="btn small" data-arrange="left">Stack left</button>
        <button type="button" class="btn small" data-arrange="center">Stack centred</button>
        <button type="button" class="btn small" data-arrange="right">Stack right</button>
        <button type="button" class="btn small" data-arrange="columns">Columns</button>
        <button type="button" class="btn small" data-arrange="tidy" title="Move everything up to close the gaps">Tidy up</button></div>`);
  };
  const setButtons = () => {
    const r = view.querySelector('#lay-reorder');
    r.classList.toggle('on', editing);
    r.querySelector('span').textContent = editing ? 'Cancel' : 'Reorder';
    r.title = editing ? 'Leave without saving the layout' : 'Move and resize the blocks of this page';
    view.querySelector('#lay-save').disabled = !editing || JSON.stringify(cur) === JSON.stringify(before);
  };
  const commit = (boxes, msg) => { cur.items = fromBoxes(boxes); if (msg) note(msg); };

  // ---- edit mode: a bar on each block (move, quick placement) and handles on its edges
  const tools = (p) => {
    const t = document.createElement('div');
    t.className = 'lay-tools';
    mount(t, html`<span class="lay-grip" title="Drag to move">${icon('grip')}</span><span class="lay-name" title="Drag to move">${nameOf(p.dataset.panel)}</span>
      <span class="lay-quick">
        <button type="button" class="lay-mini" data-q="left" title="To the left edge">Left</button>
        <button type="button" class="lay-mini" data-q="center" title="To the centre">Centre</button>
        <button type="button" class="lay-mini" data-q="right" title="To the right edge">Right</button>
        <button type="button" class="lay-mini" data-q="full" title="The whole width">Full</button>
        <button type="button" class="lay-mini" data-q="fit" title="As tall as its content">Fit</button></span>
      <label class="lay-open" title="Open when the Settings page opens; untick to start folded (the arrow on its title opens it)">
        <input type="checkbox" data-open ${(cur.closed || []).includes(p.dataset.panel) ? '' : raw('checked')}>Starts open</label>`);
    p.prepend(t);
    for (const h of ['e', 'w', 's', 'se']) {
      const k = document.createElement('span');
      k.className = `lay-h lay-h-${h}`; k.dataset.h = h; k.title = 'Drag to resize';
      p.append(k);
    }
  };
  const enter = () => {
    editing = true;
    cur.items = itemsNow();
    before = clone(cur);
    view.classList.add('lay-editing');
    panels().forEach(tools);
    renderBar(); setButtons(); render();
  };
  const leave = (keep) => {
    if (!keep) cur = clone(before);
    else if (!stored.items && JSON.stringify(cur.items) === JSON.stringify(before.items) && cur.log.length === stored.log.length) cur.items = null;
    editing = false;
    view.classList.remove('lay-editing');
    view.querySelectorAll('.lay-tools, .lay-h, .lay-guide').forEach(e => e.remove());
    renderBar(); setButtons(); render();
  };
  const onKey = (e) => { if (e.key === 'Escape' && editing && !document.querySelector('.modal-back')) leave(false); };
  document.addEventListener('keydown', onKey);
  const persist = async (msg) => {
    if (msg) cur.log.push({ t: Date.now(), m: msg });
    cur.log = cur.log.slice(-100);
    const value = JSON.stringify(cur);
    await save(value);
    stored = clone(cur);
  };
  view.querySelector('#lay-reorder').addEventListener('click', () => (editing ? leave(false) : enter()));
  view.querySelector('#lay-save').addEventListener('click', async () => {
    try { await persist('Saved the layout'); leave(true); toast('Layout saved'); } catch (ex) { toast(ex.message, 'error'); }
  });
  view.querySelector('#lay-default').addEventListener('click', async () => {
    cur = { items: null, closed: [], log: cur.log };
    try {
      await persist('Restored the default layout');
      if (editing) { before = clone(cur); leave(true); } else render();
      toast('Default layout restored');
    } catch (ex) { toast(ex.message, 'error'); }
  });

  view.addEventListener('change', (e) => {
    const c = e.target.closest('[data-open]');
    if (!c || !editing) return;
    const id = c.closest('.panel').dataset.panel;
    cur.closed = (cur.closed || []).filter(x => x !== id);
    if (!c.checked) cur.closed.push(id);
    note(`${nameOf(id)}: ${c.checked ? 'starts open' : 'starts folded'}`);
  });
  // ---- quick placement (a block) and arranging (all blocks)
  view.addEventListener('click', (e) => {
    if (!editing) return;
    const q = e.target.closest('[data-q]');
    const a = e.target.closest('[data-arrange]');
    if (q) {
      const id = q.closest('.panel').dataset.panel;
      const it = { ...cur.items[id] };
      const what = q.dataset.q;
      if (what === 'left') it.x = 0;
      if (what === 'right') it.x = UNITS - it.w;
      if (what === 'center') it.x = Math.round((UNITS - it.w) / 2);
      if (what === 'full') { it.x = 0; it.w = UNITS; }
      if (what === 'fit') it.h = null;
      const items = { ...cur.items, [id]: it };
      commit(render(items, id), `${nameOf(id)}: ${{ left: 'to the left edge', right: 'to the right edge', center: 'centred', full: 'full width', fit: 'height fits its content' }[what]}`);
    } else if (a) {
      const how = a.dataset.arrange;
      let items;
      if (how === 'columns') items = columnsLayout();
      else if (how === 'tidy') {
        // everything moves up as far as it can, keeping its column and the order
        const boxes = render(cur.items).sort((p, q2) => p.y - q2.y || p.x - q2.x);
        const done = [];
        for (const b of boxes) {
          b.y = done.filter(o => overlapX(b, o)).reduce((m, o) => Math.max(m, o.y + o.h + GAP), 0);
          done.push(b);
        }
        items = fromBoxes(boxes);
      } else items = stack(how);
      commit(render(items), { left: 'Stacked all blocks on the left', center: 'Stacked all blocks in the centre', right: 'Stacked all blocks on the right', columns: 'Arranged all blocks in columns', tidy: 'Tidied up: closed the gaps' }[how]);
    }
  });

  // ---- dragging: move by the bar, resize by the edges and the corner
  const down = window.PointerEvent ? 'pointerdown' : 'mousedown';
  const move = window.PointerEvent ? 'pointermove' : 'mousemove';
  const ups = window.PointerEvent ? ['pointerup', 'pointercancel'] : ['mouseup'];
  const guide = (kind, pos) => {
    let g = cols.querySelector(`.lay-guide-${kind}`);
    if (pos === null) { g?.remove(); return; }
    if (!g) { g = document.createElement('span'); g.className = `lay-guide lay-guide-${kind}`; cols.append(g); }
    if (kind === 'v') g.style.left = `${pos}px`; else g.style.top = `${pos}px`;
  };
  cols.addEventListener(down, (e) => {
    if (!editing || (e.button !== undefined && e.button !== 0)) return;
    const bar = e.target.closest('.lay-grip, .lay-name, .lay-tools');
    const handle = e.target.closest('.lay-h');
    if ((!bar && !handle) || e.target.closest('button, label, input')) return;
    e.preventDefault();
    const p = e.target.closest('.panel'), id = p.dataset.panel;
    const base = render(cur.items);                       // where everything is now
    const items0 = fromBoxes(base);
    const me = base.find(b => b.id === id);
    const u = unit(), c0 = cols.getBoundingClientRect();
    const x0 = e.clientX, y0 = e.clientY + window.scrollY;
    const h0 = p.offsetHeight;
    dragging = true;
    p.classList.add(handle ? 'lay-resizing' : 'lay-moving');
    cols.classList.add('lay-dragging');
    let last = base, lastEv = e;
    const update = (ev) => {
      lastEv = ev;
      const dx = ev.clientX - x0, dy = ev.clientY + window.scrollY - y0;
      const it = { ...items0[id] };
      const others = base.filter(b => b.id !== id);
      guide('v', null); guide('h', null);
      if (!handle) {
        // free move, snapped to units, then to the page edges/centre and the other blocks' edges
        let x = Math.round((me.x * u + dx) / u);
        x = Math.max(0, Math.min(UNITS - it.w, x));
        let y = Math.max(0, Math.round((me.y + dy) / YSNAP) * YSNAP);
        const xs = [[0, 0], [UNITS - it.w, UNITS], [(UNITS - it.w) / 2, UNITS / 2]];
        others.forEach(o => { xs.push([o.x, o.x], [o.x + o.w, o.x + o.w], [o.x + o.w - it.w, o.x + o.w], [o.x - it.w, o.x]); });
        const rawX = (me.x * u + dx) / u;
        const sx = xs.filter(([v]) => v >= 0 && v <= UNITS - it.w).sort((m, n) => Math.abs(m[0] - rawX) - Math.abs(n[0] - rawX))[0];
        if (sx && Math.abs(sx[0] - rawX) * u < SNAP) { x = Math.round(sx[0] * 2) / 2; guide('v', sx[1] * u); }
        const rawY = me.y + dy;
        const ys = [0, ...others.flatMap(o => [o.y, o.y + o.h + GAP])];
        const sy = ys.sort((m, n) => Math.abs(m - rawY) - Math.abs(n - rawY))[0];
        if (sy !== undefined && Math.abs(sy - rawY) < SNAP) { y = sy; guide('h', sy); }
        it.x = x; it.y = y;
      } else {
        const k = handle.dataset.h;
        if (k === 'e' || k === 'se') it.w = Math.max(MIN_W, Math.min(UNITS - it.x, Math.round(me.w + dx / u)));
        if (k === 'w') {
          const right = it.x + it.w;
          const nx = Math.max(0, Math.min(right - MIN_W, Math.round(me.x + dx / u)));
          it.x = nx; it.w = right - nx;
        }
        if (k === 's' || k === 'se') it.h = Math.max(MIN_H, Math.round((h0 + dy) / YSNAP) * YSNAP);
      }
      last = render({ ...items0, [id]: it }, id);
    };
    // near the top or bottom of the window the page scrolls along
    const edge = setInterval(() => {
      const y = lastEv.clientY, zone = 70;
      const d = y < zone ? -Math.ceil((zone - y) / 4) : y > innerHeight - zone ? Math.ceil((y - innerHeight + zone) / 4) : 0;
      if (d) { window.scrollBy(0, d); update(lastEv); }
    }, 30);
    const end = () => {
      clearInterval(edge);
      window.removeEventListener(move, update); ups.forEach(u2 => window.removeEventListener(u2, end));
      dragging = false;
      p.classList.remove('lay-moving', 'lay-resizing');
      cols.classList.remove('lay-dragging');
      guide('v', null); guide('h', null);
      const now = last.find(b => b.id === id);
      if (JSON.stringify(fromBoxes(last)) === JSON.stringify(items0)) return;
      const pct = (v) => `${Math.round(v / UNITS * 100)}%`;
      commit(last, handle
        ? `Resized ${nameOf(id)}: ${pct(now.w)} of the width${now.fixed ? `, ${now.fixed}px tall` : ''}`
        : `Moved ${nameOf(id)} to ${pct(now.x)} from the left, ${now.y}px down`);
      render();
    };
    window.addEventListener(move, update); ups.forEach(u2 => window.addEventListener(u2, end));
  });
  setButtons();
  return {
    refresh: () => { foldPanels(cols, closedAtStart); if (editing) panels().forEach(p => { if (!p.querySelector('.lay-tools')) tools(p); }); redraw(); },
    editing: () => editing,
  };
}
