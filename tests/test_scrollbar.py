"""The scroll bar is slim and wears the theme (1.21.4).

He asked for "slim matching the themes". The thumb is `var(--accent)` at 60%, so it is red in
Netflix, Max Blue in HBO Max and teal in Abyss with no rule per theme.

**The thing this test exists to catch is silent.** There are two ways to style a scroll bar and they
do not compose: Chromium ignores `::-webkit-scrollbar` the moment `scrollbar-width` is set on the
same element. Measured in Chromium 141 while this was being written — an 8px bar came back as the
browser's own 10px. `.sset-nav` had carried `scrollbar-width: thin` since the Settings shell was
built, so the sidebar would have quietly refused the whole thing. Nothing would have errored; it
would simply have stayed grey.

**Headless Chromium draws no scroll bar at all** — every gutter measures 0, overlay scrollbars on or
off — so the browser sections run under Xvfb with `--disable-features=OverlayScrollbar`. Without
that this file passes on broken code, which is the same trap `test_scroll_after_save.py` documents.
If Xvfb is missing, those sections say so and are skipped rather than reporting a false pass.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


css = (ROOT / "web/css/app.css").read_text()

# ---------------------------------------------------------------------------------------------------
print("1. the stylesheet says it once, for every theme")

block = re.search(r"@supports selector\(::-webkit-scrollbar\)\s*\{(.+?)\n\}", css, re.S)
check("the WebKit rules are behind a @supports gate", block is not None)
body = block.group(1) if block else ""
check("the thumb is the theme's accent, not a fixed colour",
      "var(--accent)" in body and "::-webkit-scrollbar-thumb" in body)
check("it is slim — 10px, not the browser's ~15", re.search(r"::-webkit-scrollbar\s*\{[^}]*width:\s*10px", body) is not None)
check("and has no track, which is where the other 5px went",
      re.search(r"::-webkit-scrollbar-track[^{]*\{[^}]*background:\s*transparent", body) is not None)
check("hovering gives the full accent", "::-webkit-scrollbar-thumb:hover" in body)

other = re.search(r"@supports not selector\(::-webkit-scrollbar\)\s*\{(.+?)\n\}", css, re.S)
check("Firefox has a branch of its own", other is not None)
check("and the two can never both apply, which is the whole point",
      other is not None and "scrollbar-width" not in body and "scrollbar-width" in other.group(1))
check("the Firefox branch leaves televisions alone too", other is not None and ":not(.tv)" in other.group(1))

# The regression that would be invisible. `.sset-nav` setting scrollbar-width makes Chromium throw
# away every ::-webkit-scrollbar rule for that element and draw its own.
nav = re.search(r"\.sset-nav\s*\{([^}]*)\}", css)
check("**the Settings sidebar no longer sets scrollbar-width**, which would silently cancel the theme",
      nav is not None and "scrollbar-width" not in nav.group(1))
# `scrollbar-width: none` elsewhere is harmless and deliberate — the rows hide their bar with it, and
# a hidden bar is hidden either way. It is `thin` and `auto` that are the trap: they leave a bar
# showing *and* make Chromium throw away the themed rules for that element.
visible = [m.group(0) for m in re.finditer(r"scrollbar-width:\s*(\w+)", css)
           if m.group(1) != "none" and not (other and other.start() < m.start() < other.end())]
check(f"nothing else asks for a visible scrollbar-width, which would cancel the theme ({visible or 'none'})",
      not visible)

check("a television still hides every bar", "html.tv ::-webkit-scrollbar { display: none; }" in css)
check("and the rows keep hiding theirs — a class beats a bare pseudo-element",
      all(f"{s}::-webkit-scrollbar {{ display: none; }}" in css for s in (".track", ".tabs", ".cast-row")))

for name in ("app", "mid"):
    built = (ROOT / f"web/legacy/{name}.css").read_text()
    check(f"the {name} build carries it too ({built.count('webkit-scrollbar')} rules)",
          "webkit-scrollbar" in built)

# ---------------------------------------------------------------------------------------------------
print("\n2. what a real browser actually draws")

if not shutil.which("xvfb-run"):
    print("  -- no Xvfb: skipped. Headless Chromium draws no scroll bar, so measuring here would")
    print("     report a false pass. Install xvfb to run this section.")
else:
    probe = ROOT / "tests" / ".scrollbar_probe.py"
    probe.write_text('''
import asyncio, json, sys
from playwright.async_api import async_playwright
BASE = "''' + BASE + '''"
THEMES = {"netflix": (229, 9, 20), "hbo": (113, 184, 242), "abyss": (92, 214, 200)}

async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False, args=["--disable-features=OverlayScrollbar"])
        pg = await (await b.new_context(viewport={"width": 1200, "height": 800})).new_page()
        await pg.goto(BASE, wait_until="networkidle")
        await pg.click('[data-user="admin"]'); await pg.fill("#lp", "secret123")
        await pg.click("#login button.primary"); await pg.wait_for_timeout(2500)
        out["gate"] = await pg.evaluate("() => CSS.supports('selector(::-webkit-scrollbar)')")
        for theme in THEMES:
            await pg.evaluate("""async (t) => { await fetch('/api/me/prefs', { method:'PUT',
                headers:{'Content-Type':'application/json'}, body: JSON.stringify({theme:t}) }); }""", theme)
            await pg.goto(BASE + "/#/", wait_until="load"); await pg.reload(wait_until="load")
            await pg.wait_for_selector(".card", timeout=30000); await pg.wait_for_timeout(1600)
            out[theme] = await pg.evaluate("""() => {
                const cs = getComputedStyle(document.documentElement);
                return { gutter: window.innerWidth - document.documentElement.clientWidth,
                         accent: cs.getPropertyValue('--accent').trim(),
                         stdWidth: cs.scrollbarWidth };
            }""")
        await b.close()
    print(json.dumps(out))

asyncio.run(main())
''')
    try:
        r = subprocess.run(["xvfb-run", "-a", sys.executable, str(probe)],
                           capture_output=True, text=True, timeout=420)
        data = {}
        for line in r.stdout.splitlines():
            if line.startswith("{"):
                import json as _j
                data = _j.loads(line)
        if not data:
            check(f"the probe ran ({(r.stderr or r.stdout)[-160:].strip()})", False)
        else:
            check("Chromium answers the @supports gate true, so the rules are live",
                  data.get("gate") is True)
            widths = {t: d["gutter"] for t, d in data.items() if isinstance(d, dict)}
            check(f"the bar is 10px wide, not the browser's default ({widths})",
                  bool(widths) and all(w == 10 for w in widths.values()))
            check("and `scrollbar-width` stays `auto`, i.e. the Firefox branch really is inert",
                  all(d["stdWidth"] == "auto" for d in data.values() if isinstance(d, dict)))
            accents = {t: d["accent"] for t, d in data.items() if isinstance(d, dict)}
            check(f"each theme hands it a different accent ({accents})",
                  len(set(accents.values())) == len(accents))
    except subprocess.TimeoutExpired:
        check("the browser probe finished inside its timeout", False)
    finally:
        probe.unlink(missing_ok=True)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
