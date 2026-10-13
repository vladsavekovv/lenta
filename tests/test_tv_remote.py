"""How much work one press of the remote costs, and whether the focus ring is ever invisible.

A television has perhaps a twentieth of the processor a laptop has, and the remote control is the only
way in. Those two facts meet in one place: the arrow keys. Moving the ring one card to the right means
comparing where the focused card is against where every other card is, and asking the engine where an
element is forces it to work out the whole page's layout before it can answer. Ask a few thousand times
and the answer arrives after the next press — the set looks broken, and the remote gets the blame.

So this measures the asking. The number is not interesting in itself; what matters is that it stays
roughly constant as the page grows, rather than multiplying by the number of cards on it.

The second half is about being able to see where you are. On a TV the focus ring is the cursor, and a
stylesheet with a typo in its selector hides it just as effectively as no rule at all — silently, and
only in the one theme nobody tested with a remote in their hand.
"""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. the focus ring is actually reachable in every theme")
# A selector is not code: nothing warns you, nothing throws, the rule is simply dropped. These are the
# ones that matter, because they are the only thing telling you where the remote is pointing.
css = {p.name: p.read_text() for p in (ROOT / "web" / "css").glob("*.css")}
all_css = "\n".join(css.values())
# Any html.tv rule whose class list does not parse as html + .tv is a rule that matches nothing.
broken = re.findall(r"html\.tv[a-z][a-z0-9-]*", all_css)
check(f"no html.tv rule is misspelt{'' if not broken else ': ' + ', '.join(sorted(set(broken)))}", not broken)
for theme in ("theme-netflix.css", "theme-hbo.css"):
    has = re.search(r"html\.tv\[data-theme=\"[a-z]+\"\][^{]*\.rail a:focus", css.get(theme, ""))
    check(f"{theme} gives the menu a visible focus style", bool(has))
check("and the TV layer states one last, in case a theme paints it away",
      ".rail a:focus" in css.get("tv.css", "") and "outline" in css.get("tv.css", ""))

print("\n2. the TV layer turns off what a television cannot afford to paint")
tv = css.get("tv.css", "")
check("backdrop-filter is off on a TV", "backdrop-filter: none !important" in tv)
check("the drifting blurred backdrop stops drifting", "animation: none !important" in tv)
check("and stops being blurred", "filter: none !important" in tv)
check("it is in the page's stylesheets", 'css/tv.css' in (ROOT / "web" / "index.html").read_text())
check("and in the bundle for older sets",
      "'css/tv.css'" in (ROOT / "tools" / "build-legacy.mjs").read_text())

print("\n3. one arrow press, and what it asks the engine")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    sync_playwright = None

if sync_playwright:
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)
    counts = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1500)                       # let tv.js settle the focus

        # Count the questions put to the engine during exactly one key press.
        page.evaluate("""() => {
          window.__n = 0;
          const real = Element.prototype.getBoundingClientRect;
          Element.prototype.getBoundingClientRect = function () { window.__n++; return real.call(this); };
          const rs = window.getComputedStyle;
          window.__s = 0;
          window.getComputedStyle = function (...a) { window.__s++; return rs.apply(window, a); };
        }""")
        cards = page.evaluate("document.querySelectorAll('.card').length")
        focusable = page.evaluate(
            "document.querySelectorAll('a[href], button, [tabindex]').length")
        page.evaluate("window.__n = 0; window.__s = 0;")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(250)
        counts = page.evaluate("({ rects: window.__n, styles: window.__s })")
        moved = page.evaluate("document.activeElement?.className || ''")
        b.close()

    print(f"       the page: {cards} cards, {focusable} focusable elements")
    print(f"       one ArrowRight: {counts['rects']} position questions, {counts['styles']} style questions")
    check("the press moved the focus somewhere", bool(moved))
    # Without the cache this was three or more per focusable element plus a style each. With it, the
    # engine is asked about each element at most once, so the ceiling is the count itself plus a margin
    # for the focused element and the scroll afterwards.
    check(f"positions asked at most once each ({counts['rects']} <= {focusable} + 40)",
          counts["rects"] <= focusable + 40)
    check(f"styles likewise ({counts['styles']} <= {focusable} + 40)",
          counts["styles"] <= focusable + 40)

print("\n4. a television starts a film and a channel without hls.js having been loaded first")
# The page leaves the library out on a TV because the set can normally play HLS itself. This browser
# cannot, which makes it the right place to test the other half: that when the set turns out not to be
# able to, the library is fetched then and playback still happens. Both paths must work, because which
# one a given Samsung model takes is not something that can be known from here.
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        oops = []
        page.on("pageerror", lambda e: oops.append(str(e)))
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1200)
        check("the TV stylesheet and key handling are switched on",
              page.evaluate("document.documentElement.classList.contains('tv')"))
        check("half a megabyte of hls.js was not loaded at startup", page.evaluate("!window.Hls") is True)
        check("but the player knows where to find it", bool(page.evaluate("window.LENTA_HLS_SRC")))

        def plays(what):
            for _ in range(40):
                page.wait_for_timeout(500)
                st = page.evaluate("(() => { const v = document.querySelector('.player video');"
                                   "  return v ? { t: v.currentTime, rs: v.readyState } : null; })()")
                if st and st["t"] > 0.3:
                    return st
            return st

        page.click(".card .frame-link")
        page.wait_for_timeout(1500)
        page.evaluate("document.querySelector('.btn.primary, .hero .btn')?.click()")
        st = plays("a film")
        check(f"a film reaches the picture: {st}", bool(st and st["t"] > 0.3))
        check("the library was fetched when it was needed", page.evaluate("!!window.Hls") is True)

        page.evaluate("document.querySelector('.player [data-a=\"close\"]')?.click()")
        page.wait_for_timeout(500)
        ch = page.evaluate("(async () => { const r = await fetch('/api/livetv/channels',"
                           " { credentials: 'same-origin' }); const j = await r.json();"
                           " return (j.channels || [])[0]?.id ?? null; })()")
        if ch:
            page.evaluate(f"location.hash = '#/play/live/{ch}'")
            st = plays("a channel")
            check(f"a live channel reaches the picture: {st}", bool(st and st["t"] > 0.3))
        else:
            check("a channel was there to try", False)
        check(f"and nothing threw along the way{'' if not oops else ': ' + oops[0][:120]}", not oops)
        b.close()

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the remote ok")
