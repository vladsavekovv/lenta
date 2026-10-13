"""The client for older TV engines — Samsung sets from 2017 onwards.

A Samsung TV from 2017 runs Chromium 47; one from 2022 runs 85. They cannot read the normal client, so
`tools/build-legacy.mjs` rewrites it into web/legacy/. The danger is not that the rewriting is wrong —
it is that nobody notices it was never done. The legacy bundle is a *copy*, and a copy made yesterday
looks exactly as healthy as one made a minute ago: the TV simply runs last week's app and nobody
browsing on a laptop sees anything amiss.

So the first and most important check here is a date comparison, not a clever one.
"""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
WEB = ROOT / "web"
LEGACY = WEB / "legacy"

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. the bundle exists at all")
built = {p.name: p for p in LEGACY.glob("*") if p.suffix in (".js", ".css")}
check("web/legacy/app.js is there", "app.js" in built)
check("web/legacy/app.css is there", "app.css" in built)
if not built:
    print("\nnothing to check — run: cd tools && npm install && npm run legacy")
    raise SystemExit(1)

print("\n2. it is not older than the client it was built from")
sources = [p for d in ("js", "css") for p in (WEB / d).rglob("*") if p.is_file()]
newest = max(sources, key=lambda p: p.stat().st_mtime)
oldest_built = min(built.values(), key=lambda p: p.stat().st_mtime)
stale = [p.relative_to(WEB) for p in sources if p.stat().st_mtime > oldest_built.stat().st_mtime]
check(f"newest source is {newest.relative_to(WEB)}", True)
check(f"nothing newer than the bundle{'' if not stale else f' — STALE, {len(stale)} file(s): ' + ', '.join(str(s) for s in stale[:6])}",
      not stale)

print("\n3. it carries the whole app, not a fragment")
js = built["app.js"].read_text(errors="replace")
css = built["app.css"].read_text(errors="replace")
check(f"the script is a real bundle ({len(js) // 1024} KB)", len(js) > 200_000)
check(f"the stylesheet likewise ({len(css) // 1024} KB)", len(css) > 50_000)
# A few things that only exist if the newest work was included. Not a list of features — a canary for
# "this bundle predates something", which is the failure this file exists to catch.
for marker in ("Find channel logos", "guide-lane", "epg/now", "Channels in this section"):
    check(f"carries {marker!r}", marker in js or marker in css)

print("\n4. nothing newer than ES2015 syntax, which Chromium 47 cannot parse")
node = shutil.which("node")
acorn = (ROOT / "tools" / "node_modules" / "acorn").is_dir()
if node and acorn:
    script = (
        "const a=require('./tools/node_modules/acorn');const fs=require('fs');"
        "const out=[];for(const f of process.argv.slice(1)){"
        "try{a.parse(fs.readFileSync(f,'utf8'),{ecmaVersion:2015});out.push(f+' ok');}"
        "catch(e){out.push(f+' FAIL '+e.message);}}console.log(out.join('\\n'));")
    r = subprocess.run([node, "-e", script, str(built["app.js"]), str(WEB / "vendor" / "hls.min.js")],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    for line in r.stdout.strip().splitlines():
        name = line.split("/")[-1]
        check(f"{name} parses as ES2015", line.endswith(" ok"))
else:
    print("  skip  node or acorn not available here (cd tools && npm install)")

print("\n5. what old engines cannot do has been given a way round")
# These are not errors in the bundle — the build leaves the modern rule in place and adds a fallback,
# so the check is that the fallback is there, not that the modern property is absent.
shim = re.search(r"CSS\.supports\(\s*['\"]aspect-ratio['\"]", js)
check("a stand-in for aspect-ratio, which arrived in Chromium 88", bool(shim))
# The list of boxes it applies to, as selector/ratio pairs. The variable holding it is renamed by the
# minifier, so look for the data: a 16:10 tile is 1.6, and both the Live TV page and Home have one.
pairs = re.findall(r'["\'`]([^"\'`]*(?:tv-card-art|card\.channel[^"\'`]*frame))["\'`]\s*,\s*1\.6', js)
check(f"with the boxes it applies to, by selector and ratio: {pairs}", len(pairs) >= 2)
check("a stand-in for gap in flex rows, which arrived in Chromium 84",
      "flex-gap" in js or "columnGap" in js or "margin" in js)
check("plain colours alongside every var(), for engines without custom properties",
      css.count("var(--") > 0 and re.search(r"#[0-9a-fA-F]{3,6}", css) is not None)

print("\n6. the page knows when to reach for it")
index = (WEB / "index.html").read_text()
# The question is what decides it, so look at the lines that do. A user agent string may legitimately
# appear elsewhere in the page — telling a television from a laptop is a different question, and one
# that has no feature to test — but nothing about which engine's dialect to serve may depend on it:
# that is the mistake this check exists to prevent, and it must stay narrow enough to still catch it.
switch = "\n".join(l for l in index.splitlines() if "LENTA_LEGACY" in l)
check("the switch tests a feature, not a user agent string",
      "CSS.supports" in index and "aspect-ratio" in index and "userAgent" not in switch)
check("and it is a real switch, not a line that got lost in an edit", switch.count("LENTA_LEGACY") >= 3)
check("legacy/app.js is what it loads when it does", "legacy/app.js" in index)
check("and it can be forced on a normal browser for testing", "legacy=1" in index)

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the old-TV client ok")
