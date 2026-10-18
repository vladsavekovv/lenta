// Builds web/legacy/: LENTA's web client for older TV web engines, in two builds rather than one.
//
//   cd tools && npm install && npm run legacy
//
// A Samsung television is not one browser. 2017 runs Chromium 47 and 2018 runs 56; 2019 to 2022 run 63,
// 69, 76 and 85. Those two halves want completely different things, and sending the older one's build to
// both is what made a 2022 set read half a megabyte of ES5 and polyfills it has had for years.
//
//   web/legacy/app.js + app.css   2017-2018  (Chromium 47-56)  everything rewritten to ES5 by Babel,
//                                 core-js for the language, plain copies of every var() and a flex
//                                 stand-in for every grid (tools/css-old.mjs), because these engines
//                                 have neither custom properties nor grid.
//   web/legacy/mid.js + mid.css   2019-2022  (Chromium 63-85)  grid, custom properties, sticky and
//                                 modern JavaScript all work. No Babel, almost no polyfills, and the
//                                 stylesheet keeps its variables and its real grids. It needs only
//                                 what came later: aspect-ratio (88), flex gap (84), clamp() (79),
//                                 :focus-visible (86), and a handful of DOM methods.
//
// The normal client (web/js, web/css) is what 2023 and newer get, untouched. index.html picks between
// the three by asking the engine what it can do — never by its user agent.
// Run this again after changing anything in web/js or web/css.
import { build } from 'esbuild';
import { transformSync as babel } from '@babel/core';
import { transform, browserslistToTargets } from 'lightningcss';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import corejsCompat from 'core-js-compat';
import { oldEngines } from './css-old.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, '..', 'web');
const OUT = join(WEB, 'legacy');
const CHROME = 47;          // the oldest set LENTA supports: 2017, Tizen 3.0
// The second build's floor. 2019 is Chromium 63, so 60 is a little below the oldest set that will ever
// be given it — deliberately, so that the feature test in index.html can be a plain one (object spread
// arrived in 60) and still be certain nothing in the file is newer than the engine reading it.
const MID = 60;
mkdirSync(OUT, { recursive: true });

// ---- CSS ----------------------------------------------------------------------------------------------
const files = ['css/fonts.css', 'css/app.css', 'css/mobile.css', 'css/theme-netflix.css', 'css/theme-hbo.css', 'css/tv.css'];
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
  // What this regex calls the selector is everything since the last brace, so a rule written under a
  // comment carries that comment with it. Harmless in the stylesheet below, where `sel` is put back
  // untouched — but the fallbacks built from it are selectors in their own right, and a comment in the
  // middle of one makes it match nothing at all. That is a silent failure: the rule is simply ignored
  // by the old engine and the box it was meant to size has no height.
  const s = sel.replace(/\/\*[\s\S]*?\*\//g, ' ').trim();
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
// media queries with not (…) are newer than these engines, which could drop the whole rule: TVs have no touch screen
css = css.replace(/ and \(not \(any-pointer: coarse\)\)/g, '');
// Everything above is common to both builds. What follows differs: the stand-ins below are for engines
// without sticky positioning (Chromium 56) or custom properties (49), and every set given the second
// build has both. They are switched on by a class the fill-ins only add when the engine fails the test,
// so carrying them there would be dead weight and nothing more.
const sharedCss = css;
css += `
/* engines without position:sticky (html.no-sticky): the menu on the left stays put */
@media (min-width: 900px) {
  html.no-sticky .rail { position: fixed; left: 0; top: 0; bottom: 0; width: 228px; }
  html.no-sticky .view { margin-left: 228px; }
  html.no-sticky body.rail-collapsed .rail { width: 68px; }
  html.no-sticky body.rail-collapsed .view { margin-left: 68px; }
}
`;
css += `
/* The Top 10 row on engines with no custom properties (html.no-vars). Every measurement in that row is
   calc() over properties the card sets on itself — how wide the tile is, how wide the big hollow
   number is, how much of it the poster covers. An engine that has never heard of them is left with no
   width and no height at all: the number is drawn at whatever size it happens to be and the poster
   lands on top of it, which is the mess on a 2017 set. Plain posters there instead. The row still says
   what is best rated; it just does not count them out in six-foot numerals. */
html.no-vars .card.rank .rank-num { display: none; }
html.no-vars .card.rank { width: 132px; }
html.no-vars .card.rank .frame-link { position: relative; display: block; height: 198px; }
html.no-vars .card.rank .frame { position: static; width: 132px; height: 198px; box-shadow: none; }
html.no-vars .track.top10 { gap: 16px; }
`;
// Flex gap arrived in Chromium 84, so both builds need this: 2022 (85) is the only set in either that
// has it, and the rules are behind html.no-flexgap, which that set never gets given.
const gapRules = '\n/* flex gap for older engines (LENTA adds html.no-flexgap when needed) */\n'
  + gaps.join('\n') + '\n';
css += gapRules;

const targets = { chrome: CHROME << 16 };
const res = transform({ filename: 'legacy.css', code: Buffer.from(css), targets, minify: true, errorRecovery: true });
for (const w of res.warnings.slice(0, 5)) console.warn('css:', w.message);
const old = oldEngines(res.code.toString());
writeFileSync(join(OUT, 'app.css'), old.css);
console.log('css 2017-2018: plain copies of', old.nVar, 'var() values,', old.nGrid, 'grids given a flex fallback');

// The same stylesheet for 2019-2022, and the difference is what is *not* done to it: no plain copy in
// front of every var(), no flex stand-in for every grid. That is 800 duplicated declarations and 100
// extra rules these engines have no use for.
const midRes = transform({ filename: 'mid.css', code: Buffer.from(sharedCss + gapRules),
  targets: { chrome: MID << 16 }, minify: true, errorRecovery: true });
for (const w of midRes.warnings.slice(0, 5)) console.warn('css (mid):', w.message);
const midCss = midRes.code.toString();
writeFileSync(join(OUT, 'mid.css'), midCss);
console.log('css 2019-2022: variables and grids kept as written —',
  ((old.css.length - midCss.length) / 1024).toFixed(0), 'KB smaller');

// ---- JavaScript ---------------------------------------------------------------------------------------
const fills = `
__POLYFILLS__
(function () {
  var E = Element.prototype;
  // append/prepend/before/after/replaceWith arrived in Chromium 54 — a 2017 Samsung set runs 47 and has
  // none of them. They are not exotic: this is how the app puts anything on the page, so without these
  // whole views die on their first line. Each takes nodes or strings, so the arguments are gathered into
  // a fragment first — which also lifts out any node that was already a child, before the clearing below
  // could throw it away.
  var frag = function (args) {
    var f = document.createDocumentFragment();
    for (var i = 0; i < args.length; i++) {
      var n = args[i];
      f.appendChild(n && n.nodeType ? n : document.createTextNode(String(n)));
    }
    return f;
  };
  var parents = [E, Document.prototype, DocumentFragment.prototype];
  for (var pi = 0; pi < parents.length; pi++) {
    var P = parents[pi];
    if (!P.append) P.append = function () { this.appendChild(frag(arguments)); };
    if (!P.prepend) P.prepend = function () { this.insertBefore(frag(arguments), this.firstChild); };
    // Chromium 86, and it must not be written in terms of append: on these engines that is the fill above
    if (!P.replaceChildren) P.replaceChildren = function () {
      var f = frag(arguments);
      while (this.firstChild) this.removeChild(this.firstChild);
      this.appendChild(f);
    };
  }
  var kids = [E, CharacterData.prototype];
  for (var ki = 0; ki < kids.length; ki++) {
    var C = kids[ki];
    if (!C.before) C.before = function () { if (this.parentNode) this.parentNode.insertBefore(frag(arguments), this); };
    if (!C.after) C.after = function () { if (this.parentNode) this.parentNode.insertBefore(frag(arguments), this.nextSibling); };
    if (!C.replaceWith) C.replaceWith = function () { if (this.parentNode) this.parentNode.replaceChild(frag(arguments), this); };
  }
  if (!E.toggleAttribute) E.toggleAttribute = function (n, f) { var on = f === undefined ? !this.hasAttribute(n) : !!f; if (on) this.setAttribute(n, ''); else this.removeAttribute(n); return on; };
  // Scrolling an element by script arrived in Chromium 61, and so did the settings object for
  // scrollIntoView. Both matter on a television: the arrows at the end of a row call scrollBy, and the
  // focus ring asks to be brought into view "nearest" after every press. An engine that cannot read the
  // settings takes the object as plain truthiness and yanks whatever has focus to the top of the screen.
  var scroll = function (node, x, y, by) {
    if (x && typeof x === 'object') { y = x.top; x = x.left; }
    if (typeof x === 'number') node.scrollLeft = by ? node.scrollLeft + x : x;
    if (typeof y === 'number') node.scrollTop = by ? node.scrollTop + y : y;
  };
  if (!E.scrollTo) E.scrollTo = function (x, y) { scroll(this, x, y, false); };
  if (!E.scrollBy) E.scrollBy = function (x, y) { scroll(this, x, y, true); };
  var readsSettings = false;
  try {
    document.createElement('div').scrollIntoView({ get block() { readsSettings = true; return 'nearest'; } });
  } catch (e) { /* an engine that refuses the object outright: it cannot read it either */ }
  if (!readsSettings) {
    var plain = E.scrollIntoView;
    E.scrollIntoView = function (how) {
      if (how == null || typeof how !== 'object') return plain.call(this, how);
      var midY = how.block === 'center', midX = how.inline === 'center', box;
      // every scrolling box between here and the page, innermost first
      for (var p = this.parentNode; p && p.nodeType === 1; p = p.parentNode) {
        if (p === document.body || p === document.documentElement) break;
        var canY = p.scrollHeight > p.clientHeight, canX = p.scrollWidth > p.clientWidth;
        if (!canY && !canX) continue;
        var frame = p.getBoundingClientRect();
        box = this.getBoundingClientRect();
        if (canY) p.scrollTop += midY ? (box.top + box.height / 2) - (frame.top + frame.height / 2)
          : box.top < frame.top ? box.top - frame.top : box.bottom > frame.bottom ? box.bottom - frame.bottom : 0;
        if (canX) p.scrollLeft += midX ? (box.left + box.width / 2) - (frame.left + frame.width / 2)
          : box.left < frame.left ? box.left - frame.left : box.right > frame.right ? box.right - frame.right : 0;
      }
      box = this.getBoundingClientRect();
      var h = window.innerHeight, w = window.innerWidth;
      var dy = midY ? (box.top + box.height / 2) - h / 2 : box.top < 0 ? box.top : box.bottom > h ? box.bottom - h : 0;
      var dx = midX ? (box.left + box.width / 2) - w / 2 : box.left < 0 ? box.left : box.right > w ? box.right - w : 0;
      if (dx || dy) window.scrollBy(dx, dy);
    };
  }
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
const bundled = await build({
  stdin: { contents: `import '../web/js/app.js';\n`, resolveDir: HERE, sourcefile: 'legacy-entry.js' },
  bundle: true, format: 'iife', target: ['chrome56'], minify: false, write: false, outfile: join(OUT, 'app.js'),
  nodePaths: [join(HERE, 'node_modules')], legalComments: 'none', logLevel: 'warning',
  supported: { 'dynamic-import': false },
});

// core-js/stable is every polyfill core-js has: 433 of them, a quarter of a megabyte once built, and
// a 2017 television must read and compile all of it before the first picture can be drawn. Most of it
// is for language this client never writes. Babel can see which ones it does reach for — reading the
// finished bundle, so nothing is missed by looking at the files one at a time — and only those are
// shipped. tests/test_legacy_builtins.py is what makes that safe: it deletes from a browser every
// built-in a Chromium 47 engine lacks and runs the client in the hole that leaves, so a polyfill left
// out by mistake fails there instead of in somebody's living room.
const seen = babel(bundled.outputFiles[0].text + '\n' + fills.replace('__POLYFILLS__', ''), {
  babelrc: false, configFile: false, filename: 'legacy.js', sourceType: 'script', compact: true,
  plugins: [['polyfill-corejs3', { method: 'usage-global', targets: { chrome: String(CHROME) }, version: '3' }]],
}).code;
// Reading the source cannot see everything. core-js's own string polyfills call the engine's native
// String.split and String.replace while they work out whether they are needed, and those algorithms
// read RegExp.prototype.flags — which a 2017 engine does not have and which nothing in this client
// mentions by name. Left out, the bundle died on its first line with "Invalid flags supplied to
// RegExp constructor 'undefinedy'". The whole regexp family is a few kilobytes and is what the rest
// of core-js stands on, so it goes in whether or not it was spotted being used.
const FOUNDATION = /^es\.regexp\./;
const compat = typeof corejsCompat === 'function' ? corejsCompat : (corejsCompat.compat ?? corejsCompat.default);
const foundation = compat({ targets: { chrome: String(CHROME) } }).list.filter(m => FOUNDATION.test(m));
const polyfills = [...new Set([
  ...[...seen.matchAll(/require\(["']core-js\/modules\/([^"']+?)(?:\.js)?["']\)/g)].map(m => m[1]),
  ...foundation,
])].sort();
writeFileSync(join(HERE, '.fills.js'),
  fills.replace('__POLYFILLS__', polyfills.map(m => `import 'core-js/modules/${m}.js';`).join('\n')));
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
const { transform: esTransform } = await import('esbuild');
const min = await esTransform(js, { minify: !process.env.DEBUG, target: 'es2015', legalComments: 'none' });
writeFileSync(join(OUT, 'app.js'), '"use strict";' + min.code);
console.log('web/legacy/app.js  2017-2018, Chrome', CHROME, '| aspect-ratio rules:', aspect.length,
  '| flex-gap rules:', gaps.length, '| core-js polyfills:', polyfills.length, 'of 433',
  '|', (min.code.length / 1024).toFixed(0), 'KB');

// ---- the same client for 2019-2022 --------------------------------------------------------------------
// Everything above exists because Chromium 47 cannot read modern JavaScript. Chromium 63 can: async
// functions, classes, arrow functions, destructuring, spread, template literals, for-of — all of it
// native since before 2018. So this build skips Babel entirely and esbuild emits the code very nearly
// as it was written. That is where the saving is; the polyfills were never the biggest part of it.
//
// What these engines still want is small: a few DOM methods that came later (replaceChildren is
// Chromium 86, toggleAttribute 69, ResizeObserver 64), the aspect-ratio stand-in, and whatever core-js
// modules the client is seen to use. The fill-ins are the same block of code — every one of them is
// written as "if the engine hasn't got it", so on an engine that has it nothing happens at all.
const midBundled = await build({
  stdin: { contents: `import '../web/js/app.js';\n`, resolveDir: HERE, sourcefile: 'mid-entry.js' },
  bundle: true, format: 'iife', target: [`chrome${MID}`], minify: false, write: false,
  nodePaths: [join(HERE, 'node_modules')], legalComments: 'none', logLevel: 'warning',
  supported: { 'dynamic-import': false },
});
const midSeen = babel(midBundled.outputFiles[0].text + '\n' + fills.replace('__POLYFILLS__', ''), {
  babelrc: false, configFile: false, filename: 'mid.js', sourceType: 'script', compact: true,
  plugins: [['polyfill-corejs3', { method: 'usage-global', targets: { chrome: String(MID) }, version: '3' }]],
}).code;
// No regexp foundation here. It is in the older build because Chromium 47 has no
// RegExp.prototype.flags and core-js's string polyfills read it; that property arrived in 49, so every
// engine given this build already has it. Checked, not assumed: tests/test_legacy_builtins.py runs
// this bundle with a Chromium 63 engine's built-ins taken away.
const midPolyfills = [...new Set(
  [...midSeen.matchAll(/require\(["']core-js\/modules\/([^"']+?)(?:\.js)?["']\)/g)].map(m => m[1]),
)].sort();
writeFileSync(join(HERE, '.fills.js'),
  fills.replace('__POLYFILLS__', midPolyfills.map(m => `import 'core-js/modules/${m}.js';`).join('\n')));
const midFills = await build({
  stdin: { contents: `import './.fills.js';\n`, resolveDir: HERE, sourcefile: 'mid-fills.js' },
  bundle: true, format: 'iife', target: [`chrome${MID}`], write: false,
  nodePaths: [join(HERE, 'node_modules')], legalComments: 'none', logLevel: 'warning',
});
const midMin = await esTransform(midFills.outputFiles[0].text + '\n' + midBundled.outputFiles[0].text,
  { minify: !process.env.DEBUG, target: [`chrome${MID}`], legalComments: 'none' });
writeFileSync(join(OUT, 'mid.js'), '"use strict";' + midMin.code);
console.log('web/legacy/mid.js  2019-2022, Chrome', MID, '| core-js polyfills:', midPolyfills.length,
  '|', (midMin.code.length / 1024).toFixed(0), 'KB,',
  (((min.code.length - midMin.code.length) / min.code.length) * 100).toFixed(0) + '% smaller than the 2017 build');
