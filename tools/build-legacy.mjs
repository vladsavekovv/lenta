// Builds web/legacy/: LENTA's web client for older TV web engines (Samsung TVs from 2017 to 2022, Chromium 47-87).
//
//   cd tools && npm install && npm run legacy
//
// The normal client (web/js, web/css) uses current JavaScript and CSS. This turns it into one script and one
// stylesheet those engines understand: newer syntax is rewritten (esbuild), missing functions are added (core-js
// and a few small fill-ins), and CSS the old engines drop is given fallbacks (Lightning CSS plus the rules below).
// index.html picks the legacy files by itself when the browser can't run the normal ones.
// Run it again after changing anything in web/js or web/css.
import { build } from 'esbuild';
import { transformSync as babel } from '@babel/core';
import { transform, browserslistToTargets } from 'lightningcss';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { oldEngines } from './css-old.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, '..', 'web');
const OUT = join(WEB, 'legacy');
const CHROME = 47;
mkdirSync(OUT, { recursive: true });

// ---- CSS ----------------------------------------------------------------------------------------------
const files = ['css/fonts.css', 'css/app.css', 'css/mobile.css', 'css/theme-redline.css'];
let css = files.map(f => readFileSync(join(WEB, f), 'utf8')).join('\n');

// split "a, b" at top level only
function args(s) {
  const out = []; let depth = 0, cur = '';
  for (const ch of s) {
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
  }
  out.push(cur.trim());
  return out;
}
// replace clamp()/min()/max() by one plain value, for a big screen (the TV): clamp -> its largest, min -> its
// first (usually the fixed size), max -> its last
function plainSizes(v) {
  for (let guard = 0; guard < 50; guard++) {
    const m = /(?<![a-z-])(clamp|min|max)\(/.exec(v);
    if (!m) break;
    let i = m.index + m[0].length, depth = 1;
    while (i < v.length && depth) { if (v[i] === '(') depth++; else if (v[i] === ')') depth--; i++; }
    const inner = v.slice(m.index + m[0].length, i - 1);
    const a = args(inner);
    const pick = m[1] === 'clamp' ? a[2] : m[1] === 'min' ? a[0] : a[a.length - 1];
    v = v.slice(0, m.index) + (pick ?? a[0]) + v.slice(i);
  }
  return v;
}

const aspect = [];          // [selector, ratio] for the small aspect-ratio script
const gaps = [];            // extra rules for flex gap
css = css.replace(/([^{}@]+)\{([^{}]*)\}/g, (all, sel, body) => {
  const s = sel.trim();
  let b = body;
  // these files are only for old engines: plain sizes instead of clamp()/min()/max(), vh instead of dvh
  if (/(?<![a-z-])(clamp|min|max)\(/.test(b)) b = b.replace(/([a-z-]+\s*:\s*)([^;]*)/g, (x, p, v) => p + plainSizes(v));
  b = b.replace(/(\d)(dvh|svh|lvh)\b/g, '$1vh');
  const ar = /aspect-ratio\s*:\s*([0-9.]+)\s*(?:\/\s*([0-9.]+))?/.exec(b);
  if (ar && !/^\s*\d/.test(s)) aspect.push([s, Number(ar[1]) / Number(ar[2] || 1)]);
  if (/aspect-ratio\s*:\s*auto/.test(b)) aspect.push([s, 0]);
  const gap = /(?:^|;)\s*gap\s*:\s*([^;]+)/.exec(b);
  if (gap) {
    const [row, col = row] = gap[1].trim().split(/\s+/);
    if (/display\s*:\s*(inline-)?grid|grid-template/.test(b)) b += `; grid-gap: ${gap[1].trim()}`;
    else if (!/display\s*:\s*grid/.test(b)) {
      const kids = args(s).map(x => `html.no-flexgap ${x} > *`).join(', ');
      if (/flex-direction\s*:\s*column/.test(b)) gaps.push(`${kids} { margin-top: 0; margin-bottom: ${row}; }`);
      else {
        gaps.push(`${kids} { margin-right: ${col}; }`);
        if (/flex-wrap\s*:\s*wrap|flex-flow\s*:[^;]*wrap/.test(b)) gaps.push(`${kids} { margin-bottom: ${row}; }`);
      }
    }
  }
  return `${sel}{${b}}`;
});
// fonts: the variable fonts as ordinary ones (old engines skip 'woff2-variations' and weight ranges)
css = css.replace(/format\('woff2-variations'\)/g, "format('woff2')").replace(/font-weight:\s*\d+\s+\d+\s*;/g, '');
// colours written as "rgb(var(--bg-rgb) / .5)" with "--bg-rgb: 23 18 31": the comma form old engines know
css = css.replace(/(--[a-z0-9-]*rgb\s*:\s*)(\d+)\s+(\d+)\s+(\d+)/g, '$1$2, $3, $4')
  .replace(/rgb\(\s*var\((--[a-z0-9-]+)\)\s*\/\s*([^)]+)\)/g, 'rgba(var($1), $2)');
// :focus-visible is newer than these engines and would throw away the whole rule
css = css.replace(/:focus-visible/g, ':focus');
css += `
/* engines without position:sticky (html.no-sticky): the menu on the left stays put */
@media (min-width: 900px) {
  html.no-sticky .rail { position: fixed; left: 0; top: 0; bottom: 0; width: 228px; }
  html.no-sticky .view { margin-left: 228px; }
  html.no-sticky body.rail-collapsed .rail { width: 68px; }
  html.no-sticky body.rail-collapsed .view { margin-left: 68px; }
}
`;
css += '\n/* flex gap for older engines (LENTA adds html.no-flexgap when needed) */\n' + gaps.join('\n') + '\n';

const targets = { chrome: CHROME << 16 };
const res = transform({ filename: 'legacy.css', code: Buffer.from(css), targets, minify: true, errorRecovery: true });
for (const w of res.warnings.slice(0, 5)) console.warn('css:', w.message);
const old = oldEngines(res.code.toString());
writeFileSync(join(OUT, 'app.css'), old.css);
console.log('css: plain copies of', old.nVar, 'var() values,', old.nGrid, 'grids given a flex fallback');

// ---- JavaScript ---------------------------------------------------------------------------------------
const fills = `
import 'core-js/stable';
(function () {
  var E = Element.prototype;
  if (!E.replaceChildren) E.replaceChildren = function () { while (this.firstChild) this.removeChild(this.firstChild); this.append.apply(this, arguments); };
  if (!E.toggleAttribute) E.toggleAttribute = function (n, f) { var on = f === undefined ? !this.hasAttribute(n) : !!f; if (on) this.setAttribute(n, ''); else this.removeAttribute(n); return on; };
  if (!('isConnected' in Node.prototype)) Object.defineProperty(Node.prototype, 'isConnected', { get: function () { return document.documentElement.contains(this); } });
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = function (cb) { this.cb = cb; this.els = []; };
    window.IntersectionObserver.prototype.observe = function (el) { var cb = this.cb, me = this; setTimeout(function () { cb([{ target: el, isIntersecting: true, intersectionRatio: 1 }], me); }, 50); };
    window.IntersectionObserver.prototype.unobserve = function () {};
    window.IntersectionObserver.prototype.disconnect = function () {};
  }
  if (!window.ResizeObserver) {
    window.ResizeObserver = function (cb) { var me = this; this.els = []; this.cb = cb; window.addEventListener('resize', function () { cb(me.els.map(function (t) { return { target: t, contentRect: t.getBoundingClientRect() }; }), me); }); };
    window.ResizeObserver.prototype.observe = function (el) { this.els.push(el); };
    window.ResizeObserver.prototype.unobserve = function (el) { this.els = this.els.filter(function (x) { return x !== el; }); };
    window.ResizeObserver.prototype.disconnect = function () { this.els = []; };
  }
  // KeyboardEvent.key (Chromium 51+): worked out from keyCode on older engines
  if (window.KeyboardEvent && !('key' in KeyboardEvent.prototype)) {
    var NAMES = { 8: 'Backspace', 9: 'Tab', 13: 'Enter', 16: 'Shift', 17: 'Control', 18: 'Alt', 27: 'Escape', 32: ' ',
      33: 'PageUp', 34: 'PageDown', 35: 'End', 36: 'Home', 37: 'ArrowLeft', 38: 'ArrowUp', 39: 'ArrowRight', 40: 'ArrowDown',
      46: 'Delete', 186: ';', 187: '=', 188: ',', 189: '-', 190: '.', 191: '/', 219: '[', 220: String.fromCharCode(92), 221: ']', 222: "'" };
    Object.defineProperty(KeyboardEvent.prototype, 'key', { configurable: true, get: function () {
      var c = this.keyCode || this.which || 0;
      if (NAMES[c]) return NAMES[c];
      if (c >= 112 && c <= 123) return 'F' + (c - 111);
      if (c >= 48 && c <= 57) return String.fromCharCode(c);
      if (c >= 65 && c <= 90) { var ch = String.fromCharCode(c); return this.shiftKey ? ch : ch.toLowerCase(); }
      if (this.keyIdentifier && this.keyIdentifier.indexOf('U+') !== 0) return this.keyIdentifier;
      return 'Unidentified';
    } });
  }
  if (!(window.CSS && CSS.supports && CSS.supports('--a', '0'))) document.documentElement.classList.add('no-vars');
  if (!(window.CSS && CSS.supports && (CSS.supports('position', 'sticky') || CSS.supports('position', '-webkit-sticky')))) document.documentElement.classList.add('no-sticky');
  if (!(window.CSS && CSS.supports && CSS.supports('display', 'grid'))) document.documentElement.classList.add('no-grid');
  // flex gap: test once
  var d = document.createElement('div'); d.style.cssText = 'display:flex;flex-direction:column;row-gap:1px;position:absolute';
  d.appendChild(document.createElement('div')); d.appendChild(document.createElement('div'));
  document.documentElement.appendChild(d);
  if (d.scrollHeight !== 1) document.documentElement.classList.add('no-flexgap');
  d.remove();
  // aspect-ratio: give such boxes a height from their width
  if (!(window.CSS && CSS.supports && CSS.supports('aspect-ratio', '1'))) {
    var RULES = ${JSON.stringify(aspect)};
    var queued = false;
    var run = function () {
      queued = false;
      var want = new Map();
      RULES.forEach(function (r) { try { document.querySelectorAll(r[0]).forEach(function (el) { want.set(el, r[1]); }); } catch (e) {} });
      document.querySelectorAll('[data-ar]').forEach(function (el) { if (!want.get(el)) { el.style.height = ''; el.removeAttribute('data-ar'); } });
      want.forEach(function (ratio, el) {
        if (!ratio) return;
        if (el.style.height && !el.hasAttribute('data-ar')) return;          // the page set its own height
        var w = el.getBoundingClientRect().width;
        if (w > 0) { el.style.height = (w / ratio) + 'px'; el.setAttribute('data-ar', ''); }
      });
    };
    var later = function () { if (!queued) { queued = true; requestAnimationFrame(run); } };
    new MutationObserver(later).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', later);
    later();
  }
})();
`;
writeFileSync(join(HERE, '.fills.js'), fills);

const bundled = await build({
  stdin: { contents: `import '../web/js/app.js';\n`, resolveDir: HERE, sourcefile: 'legacy-entry.js' },
  bundle: true, format: 'iife', target: ['chrome56'], minify: false, write: false, outfile: join(OUT, 'app.js'),
  nodePaths: [join(HERE, 'node_modules')], legalComments: 'none', logLevel: 'warning',
  supported: { 'dynamic-import': false },
});
// the fill-ins go first, on their own: the app's helpers (Object.getOwnPropertyDescriptors...) need them at once
const fillsBuilt = await build({
  stdin: { contents: `import './.fills.js';\n`, resolveDir: HERE, sourcefile: 'fills-entry.js' },
  bundle: true, format: 'iife', target: ['chrome56'], write: false, outfile: join(OUT, 'fills.js'),
  nodePaths: [join(HERE, 'node_modules')], legalComments: 'none', logLevel: 'warning',
});
// esbuild gathers everything into one file; Babel then rewrites what Chromium 47 lacks (async, destructuring...)
const js = babel(fillsBuilt.outputFiles[0].text + '\n' + bundled.outputFiles[0].text, {
  babelrc: false, configFile: false, compact: true, comments: false, sourceType: 'script',
  presets: [['@babel/preset-env', { targets: { chrome: String(CHROME) }, modules: false, exclude: ['transform-regenerator'] }]],
}).code;
const min = await (await import('esbuild')).transform(js, { minify: !process.env.DEBUG, target: 'es2015', legalComments: 'none' });
writeFileSync(join(OUT, 'app.js'), '"use strict";' + min.code);
console.log('web/legacy/app.js and app.css built for Chrome', CHROME, '| aspect-ratio rules:', aspect.length, '| flex-gap rules:', gaps.length);
