// Fallbacks for the oldest engines LENTA supports (Samsung TVs from 2017, Chromium 47): no CSS variables and no
// CSS grid. Works on the finished (minified) stylesheet, so nothing after it removes the extra lines again.
//
//  * every value that uses var(--x) gets a plain copy in front of it, worked out from the default theme
//    (engines without variables keep the plain one, newer ones the var() one);
//  * every display:grid gets "display:flex" in front of it plus flex settings that mimic the grid
//    (engines without grid keep the flex box), and rules for the children under html.no-grid.

// ---- a small CSS reader/writer -----------------------------------------------------------------------------
function splitTop(s, sep) {
  const out = []; let depth = 0, q = null, cur = '';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (q) { cur += ch; if (ch === '\\') { cur += s[++i] ?? ''; continue; } if (ch === q) q = null; continue; }
    if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === sep && depth === 0) { out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur);
  return out;
}

function parse(css) {
  let i = 0;
  function block() {               // reads until the matching }
    const nodes = [];
    while (i < css.length) {
      while (i < css.length && /\s/.test(css[i])) i++;
      if (css[i] === '}') { i++; return nodes; }
      if (i >= css.length) break;
      // prelude up to { or ; (strings and parens respected)
      let start = i, depth = 0, q = null;
      for (; i < css.length; i++) {
        const ch = css[i];
        if (q) { if (ch === '\\') i++; else if (ch === q) q = null; continue; }
        if (ch === '"' || ch === "'") { q = ch; continue; }
        if (ch === '(') depth++; else if (ch === ')') depth--;
        else if (depth === 0 && (ch === '{' || ch === ';')) break;
      }
      const prelude = css.slice(start, i).trim();
      if (css[i] === ';') { i++; nodes.push({ type: 'raw', text: prelude + ';' }); continue; }
      i++;                                                   // past {
      if (prelude.startsWith('@')) {
        const name = /^@([a-z-]+)/i.exec(prelude)[1].toLowerCase();
        if (['media', 'supports', 'document', 'layer'].includes(name)) {
          nodes.push({ type: 'at', prelude, children: block() });
        } else {                                             // @font-face, @keyframes, ...: kept as they are
          let d = 1, s2 = i, q2 = null;
          for (; i < css.length && d; i++) {
            const ch = css[i];
            if (q2) { if (ch === '\\') i++; else if (ch === q2) q2 = null; continue; }
            if (ch === '"' || ch === "'") q2 = ch; else if (ch === '{') d++; else if (ch === '}') d--;
          }
          nodes.push({ type: 'raw', text: prelude + '{' + css.slice(s2, i - 1) + '}' });
        }
        continue;
      }
      // a rule: declarations up to }
      let s3 = i, q3 = null, d3 = 0;
      for (; i < css.length; i++) {
        const ch = css[i];
        if (q3) { if (ch === '\\') i++; else if (ch === q3) q3 = null; continue; }
        if (ch === '"' || ch === "'") q3 = ch; else if (ch === '(') d3++; else if (ch === ')') d3--;
        else if (ch === '}' && d3 === 0) break;
      }
      const body = css.slice(s3, i); i++;
      const decls = splitTop(body, ';').map(d => d.trim()).filter(Boolean).map(d => {
        const c = d.indexOf(':');
        return [d.slice(0, c).trim(), d.slice(c + 1).trim()];
      });
      nodes.push({ type: 'rule', sel: prelude, decls });
    }
    return nodes;
  }
  return block();
}

function write(nodes) {
  return nodes.map(n => n.type === 'raw' ? n.text
    : n.type === 'at' ? `${n.prelude}{${write(n.children)}}`
    : `${n.sel}{${n.decls.map(([p, v]) => `${p}:${v}`).join(';')}}`).join('');
}

// ---- CSS variables ------------------------------------------------------------------------------------------
function varsOf(nodes) {
  const root = {}, scoped = {};
  for (const n of nodes) {
    if (n.type !== 'rule') continue;
    const isRoot = splitTop(n.sel, ',').some(s => s.trim() === ':root' || s.trim() === 'html');
    for (const [p, v] of n.decls) {
      if (!p.startsWith('--')) continue;
      if (isRoot) root[p] = v;
      else if (!(p in scoped)) scoped[p] = v;
    }
  }
  return { ...scoped, ...root };
}

function resolve(value, vars, depth = 0) {
  if (depth > 10) return null;
  let out = '', i = 0;
  while (i < value.length) {
    const k = value.indexOf('var(', i);
    if (k < 0) { out += value.slice(i); break; }
    out += value.slice(i, k);
    let j = k + 4, d = 1;
    while (j < value.length && d) { if (value[j] === '(') d++; else if (value[j] === ')') d--; j++; }
    const inner = value.slice(k + 4, j - 1);
    const [name, ...fb] = splitTop(inner, ',');
    let v = vars[name.trim()];
    if (v === undefined) v = fb.length ? fb.join(',').trim() : null;
    if (v === null) return null;
    const r = resolve(v, vars, depth + 1);
    if (r === null) return null;
    out += r;
    i = j;
  }
  return out;
}

// ---- grid as flex -------------------------------------------------------------------------------------------
function tracks(v) {
  // "40px minmax(150px,230px) 1fr auto" -> list; repeat(N, x) expanded; repeat(auto-fill, ...) kept as one item
  const out = [];
  for (const t of splitTop(v.replace(/!important/g, '').replace(/\s+/g, ' ').trim(), ' ')) {
    const rep = /^repeat\((\d+),(.+)\)$/.exec(t.replace(/\s/g, ''));
    if (rep) for (let k = 0; k < +rep[1]; k++) out.push(rep[2]); else if (t) out.push(t.replace(/\s/g, ''));
  }
  return out;
}

function flexFor(t) {
  if (/fr\)?$/.test(t)) {                                       // 1fr, minmax(x,1fr)
    const m = /^minmax\(([^,]+),/.exec(t);
    return `flex:1 1 0px;min-width:${m && m[1] !== '0' ? m[1] : '0'}`;
  }
  if (/^(auto|max-content|min-content|fit-content.*)$/.test(t)) return 'flex:0 0 auto';
  const mm = /^minmax\(([^,]+),([^)]+)\)$/.exec(t);
  if (mm) return `flex:1 1 ${mm[1]};min-width:${mm[1]};max-width:${mm[2]}`;
  return `flex:0 0 ${t};width:${t}`;
}

export function oldEngines(css) {
  const tree = parse(css);
  const vars = varsOf(tree);
  const extra = [];
  let nVar = 0, nGrid = 0;

  // selectors that give grid columns (often a different rule from the one with display:grid: ".track.poster")
  const colSels = [];
  (function collect(nodes) {
    for (const n of nodes) {
      if (n.type === 'at') collect(n.children);
      else if (n.type === 'rule' && n.decls.some(([p]) => p === 'grid-template-columns' || p === 'grid-auto-columns' || p === 'grid-template'))
        splitTop(n.sel, ',').forEach(x => colSels.push(x.trim()));
    }
  })(tree);
  const hasColsElsewhere = (sel) => splitTop(sel, ',').some(x => colSels.some(c => {
    x = x.trim();
    return c !== x && c.startsWith(x) && /^[.:\[ >]/.test(c.slice(x.length));
  }));

  // variables changed for part of the page ("body.rail-collapsed { --rail: 68px }"): copies of the rules that use them,
  // under that selector (the themes are left out: engines without variables keep the default theme)
  const overrides = [];
  (function collect(nodes, wrap) {
    for (const n of nodes) {
      if (n.type === 'at') { collect(n.children, (x) => wrap(`${n.prelude}{${x}}`)); continue; }
      if (n.type !== 'rule' || /data-theme|:root/.test(n.sel) || /^html$/.test(n.sel.trim())) continue;
      const v = {};
      for (const [p, val] of n.decls) if (p.startsWith('--')) v[p] = val;
      if (Object.keys(v).length) overrides.push({ sels: splitTop(n.sel, ',').map(x => x.trim()), vars: v, wrap });
    }
  })(tree, (x) => x);

  function visit(nodes, wrap) {
    for (const n of nodes) {
      if (n.type === 'at') { visit(n.children, (s) => wrap(`${n.prelude}{${s}}`)); continue; }
      if (n.type !== 'rule') continue;
      const get = (p) => { const d = n.decls.filter(x => x[0] === p).pop(); return d ? (resolve(d[1], vars) ?? d[1]) : undefined; };
      const sels = splitTop(n.sel, ',').map(x => x.trim());
      const kids = (suffix, body) => extra.push(wrap(sels.map(x => `html.no-grid ${x} > ${suffix}`).join(',') + `{${body}}`));
      const selfRule = (props) => { if (props.length) extra.push(wrap(sels.map(x => `html.no-grid ${x}`).join(',') + `{${props.map(([a, b]) => a + ':' + b).join(';')}}`)); };

      const displayGrid = n.decls.some(([p, v]) => p === 'display' && /^(inline-)?grid(\s*!important)?$/.test(v));
      let cols = get('grid-template-columns'), rows = get('grid-template-rows');
      const tpl = get('grid-template');
      if (tpl && tpl.includes('/')) { const [r, c] = splitTop(tpl, '/'); rows = rows || r.trim(); cols = cols || c.trim(); }
      const flow = get('grid-auto-flow') || '';
      const autoCol = get('grid-auto-columns');
      const gap = (get('gap') || get('grid-gap') || '0').split(/\s+/);
      const rowGap = get('row-gap') || gap[0], colGap = get('column-gap') || gap[1] || gap[0];
      const place = get('place-items');
      const ji = get('justify-items') || (place ? (place.split(/\s+/)[1] || place.split(/\s+/)[0]) : undefined);
      const colList = cols ? tracks(cols) : [];
      const auto = colList.length === 1 && /^repeat\(auto-(fill|fit),minmax\((.+?),[^,]*\)\)$/.exec(colList[0]);
      const self = [];

      // the children's sizes (wherever the columns are given)
      const sizeKids = (kidsFn, colsV, autoColV) => {
        const cl = colsV ? tracks(colsV) : [];
        const au = cl.length === 1 && /^repeat\(auto-(fill|fit),minmax\((.+?),[^,]*\)\)$/.exec(cl[0]);
        if (autoColV) kidsFn('*', `flex:0 0 ${autoColV};width:${autoColV}`);
        else if (au) kidsFn('*', `flex:1 0 ${au[2]};max-width:calc(${au[2]} * 1.6)`);
        else if (cl.length > 1 || (cl.length === 1 && !/^minmax\(0,1fr\)$|^1fr$/.test(cl[0])))
          cl.forEach((t, k) => kidsFn(`:nth-child(${cl.length}n+${k + 1})`, flexFor(t)));
      };
      sizeKids(kids, cols, autoCol);
      const rawCols = (n.decls.filter(x => x[0] === 'grid-template-columns').pop() || [])[1] || '';
      const rawAuto = (n.decls.filter(x => x[0] === 'grid-auto-columns').pop() || [])[1] || '';
      for (const o of overrides) {                        // the same, where a variable in the columns is changed
        if (!Object.keys(o.vars).some(k => (rawCols + rawAuto).includes(`var(${k}`))) continue;
        const vv = { ...vars, ...o.vars };
        const combo = (suffix) => o.sels.flatMap(a => sels.map(b => `html.no-grid ${b.startsWith(a) ? b : a + ' ' + b} > ${suffix}`)).join(',');
        sizeKids((suffix, body) => extra.push(o.wrap(wrap(`${combo(suffix)}{${body}}`))),
          rawCols ? resolve(rawCols, vv) : undefined, rawAuto ? resolve(rawAuto, vv) : undefined);
      }

      if (displayGrid) {
        nGrid++;
        const sideways = /column/.test(flow) || !!autoCol;
        const multiCol = !!auto || colList.length > 1 || (colList.length === 1 && !/^minmax\(0,1fr\)$|^1fr$/.test(colList[0]));
        if (sideways) {
          self.push(['flex-wrap', 'nowrap']);
          if (!get('align-items')) self.push(['align-items', 'flex-start']);
          if (colGap !== '0') kids('*:not(:last-child)', `margin-right:${colGap}`);
        } else if (multiCol || hasColsElsewhere(n.sel)) {
          const wrapRow = !!auto || hasColsElsewhere(n.sel) || /repeat/.test(cols || '');
          self.push(['flex-wrap', wrapRow ? 'wrap' : 'nowrap']);
          if (!get('align-items') && !place) self.push(['align-items', wrapRow ? 'flex-start' : 'stretch']);
          if (place && /center/.test(place)) self.push(['align-items', 'center']);
          if (colGap !== '0') kids(wrapRow ? '*' : '*:not(:last-child)', `margin-right:${colGap}`);
          if (wrapRow && rowGap !== '0') kids('*', `margin-bottom:${rowGap}`);
        } else {                                                       // one column: a stack
          self.push(['flex-direction', 'column']);
          const rowList = rows ? tracks(rows) : [];
          rowList.forEach((t, k) => { if (/fr$/.test(t)) kids(`:nth-child(${k + 1})`, 'flex:1 1 0px;min-height:0'); });
          if (ji) self.push(['align-items', ji === 'start' ? 'flex-start' : ji === 'end' ? 'flex-end' : ji]);
          if ((place && /center/.test(place)) || get('align-items') === 'center') self.push(['justify-content', 'center']);
          if (rowGap !== '0') kids('*:not(:last-child)', `margin-bottom:${rowGap}`);
        }
        selfRule(self);
      }

      const out = [];
      for (const [p, v] of n.decls) {
        if (!p.startsWith('--') && v.includes('var(')) {
          const r = resolve(v, vars);
          if (r !== null && r !== v) { out.push([p, r]); nVar++; }
          for (const o of overrides) {
            if (!Object.keys(o.vars).some(k => v.includes(`var(${k}`))) continue;
            const r2 = resolve(v, { ...vars, ...o.vars });
            if (r2 === null || r2 === r) continue;
            const combo = o.sels.flatMap(a => sels.map(b => `html.no-vars ${b.startsWith(a) ? b : `${a} ${b}`}`)).join(',');
            extra.push(o.wrap(wrap(`${combo}{${p}:${r2}}`)));
          }
        }
        if (p === 'display' && /^(inline-)?grid(\s*!important)?$/.test(v))
          out.push(['display', (v.startsWith('inline') ? 'inline-flex' : 'flex') + (v.includes('!important') ? '!important' : '')]);
        out.push([p, v]);
      }
      n.decls = out;
    }
  }
  visit(tree, s => s);
  return { css: write(tree) + '\n/* grid as flex for engines without grid (html.no-grid) */\n' + extra.join('\n'), nVar, nGrid };
}
