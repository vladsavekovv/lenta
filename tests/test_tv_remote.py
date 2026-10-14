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
# This file is loaded by every browser and is full of !important: one selector in it that forgets to
# say html.tv would rewrite the laptop's player as well, and nothing here would notice.
loose = [s.strip() for block in re.split(r"\}", re.sub(r"/\*.*?\*/", "", tv, flags=re.S))
         for s in block.split("{")[0].split(",")
         if s.strip() and not s.strip().startswith("@") and not s.strip().startswith("html.tv")]
check(f"every rule in it is scoped to a television{'' if not loose else ': ' + ', '.join(loose[:4])}",
      not loose)

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
    # Asking once each is still asking four hundred times on Home. A card eight rows below the screen
    # cannot be the next one, and its row can be ruled out with a single question, so most of the page
    # should never be measured at all. Half is a generous ceiling for "most".
    check(f"and most of the page is not measured at all ({counts['rects']} <= a fifth of {focusable})",
          counts["rects"] <= focusable / 5)
    check(f"styles too ({counts['styles']} <= a fifth of {focusable})", counts["styles"] <= focusable / 5)

    # A remote held down sends twenty presses a second. Nothing about the page changes while the ring
    # crosses it, so a burst can be answered from one set of measurements and scrolled once at the end
    # — otherwise the presses queue up behind frames that have not been drawn, and the ring arrives
    # somewhere unexpected a second after the thumb stopped.
    with sync_playwright() as pw2:
        bb = pw2.chromium.launch()
        pg = bb.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        try:
            pg.click('[data-user="admin"]', timeout=8000)
            pg.fill("#lp", "secret123")
            pg.click("#login button.primary")
        except Exception:
            pass
        pg.wait_for_selector(".card", timeout=30000)
        pg.wait_for_timeout(2000)
        pg.evaluate("""() => {
          window.__n = 0; window.__siv = 0;
          const real = Element.prototype.getBoundingClientRect;
          Element.prototype.getBoundingClientRect = function () { window.__n++; return real.call(this); };
          const siv = Element.prototype.scrollIntoView;
          Element.prototype.scrollIntoView = function () { window.__siv++; return siv.apply(this, arguments); };
        }""")
        pg.evaluate("document.querySelector('.card .frame-link')?.focus()")
        pg.wait_for_timeout(300)
        pg.evaluate("window.__n = 0; window.__siv = 0;")
        # Eight presses inside one frame, which is what a slow set sees when the key is held down.
        # Sending them through the keyboard instead would measure how fast this machine draws frames
        # rather than whether the presses are batched at all.
        pg.evaluate("""() => {
          for (let i = 0; i < 8; i++) {
            const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true });
            Object.defineProperty(e, 'key', { value: 'ArrowRight' });
            Object.defineProperty(e, 'keyCode', { value: 39 });
            window.dispatchEvent(e);
          }
        }""")
        pg.wait_for_timeout(1500)
        burst = pg.evaluate("({rects: window.__n, scrolls: window.__siv})")
        bb.close()
    print(f"       eight presses in one frame: {burst['rects']} position questions,"
          f" {burst['scrolls']} scrolls")
    check(f"eight presses in one frame cost about what one does ({burst['rects']} for eight,"
          f" against {counts['rects'] * 8} if each were answered on its own)",
          burst["rects"] <= counts["rects"] * 2)
    check(f"and the page is scrolled a few times, not eight ({burst['scrolls']})", burst["scrolls"] <= 4)

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

        # A film, named as such. "The first card on Home" is not a film: it depends on what rows this
        # server happens to have, and with a Live TV row present the first card is a channel — which
        # has no file, so the test failed on a page that was behaving perfectly.
        page.click('.card[data-kind="movie"] .frame-link, .card[data-kind="show"] .frame-link')
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

print("\n4b. can the remote actually reach the menu?")
# The question that matters most on a television and is the easiest to never ask: with nothing but the
# four arrows, can you get to Settings? Two themes put the menu as a bar across the top and the rest run
# it down the side, and the navigation assumed the column everywhere — so in those two the first ▶ in
# the menu jumped straight back into the page and the gear at the end of the bar could not be reached
# at all. Everything else about the remote worked, which is why it went unnoticed.
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)

        def walk_to_settings(bar):
            """Arrows only, as a remote has: into the menu, then along it."""
            into, along = ("ArrowUp", "ArrowRight") if bar else ("ArrowLeft", "ArrowDown")
            for i in range(45):
                if page.evaluate("document.activeElement?.getAttribute('href') === '#/settings'"):
                    return True
                page.keyboard.press(into if i < 6 else along)
                page.wait_for_timeout(110)
            return False

        for theme in ("netflix", "projector"):          # one of each kind of menu
            page.evaluate("""async (t) => { await fetch('/api/me/prefs', { method: 'PUT',
                credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: t }) }); }""", theme)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".card", timeout=30000)
            page.wait_for_timeout(2500)
            bar = page.evaluate("document.documentElement.classList.contains('topnav')")
            page.evaluate("document.querySelector('.card .frame-link')?.focus()")
            check(f"{theme}: the menu is {'a bar across the top' if bar else 'a column down the side'},"
                  f" and the arrows reach Settings", walk_to_settings(bar))
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: 'projector' }) }); }""")
        b.close()

print("\n4c. and can the ring always get back out again?")
# Reaching the menu from the first card is the easy case. The hard ones are a page where the first
# thing above the pictures is a drop-down — the arrows belonged to the <select> for ever, so the ring
# went in and never came out — and Home scrolled a few rows down, where arriving back at the top bar
# with the page still half scrolled leaves the banner cut through the middle underneath it.
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: 'netflix' }) }); }""")
        lib = page.evaluate("(document.querySelector('a[href*=\"#/library/\"]')||{}).getAttribute?.('href')") or ""
        page.reload(wait_until="domcontentloaded")      # a goto that only changes the hash does not reload
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(2500)
        check("there is a library to try this on, with the menu across the top",
              bool(lib) and page.evaluate("document.documentElement.classList.contains('topnav')"))

        def in_rail():
            return page.evaluate("!!document.activeElement?.closest('.rail')")

        if lib:
            page.evaluate("location.hash = %r" % lib.lstrip("#"))
            page.wait_for_selector(".card", timeout=30000)
            page.wait_for_timeout(2500)
            page.evaluate("document.querySelector('.card .frame-link')?.focus()")
            page.wait_for_timeout(300)
            seen = []
            for _ in range(6):
                page.keyboard.press("ArrowUp")
                page.wait_for_timeout(320)
                seen.append("menu" if in_rail() else page.evaluate("document.activeElement?.tagName || ''"))
                if in_rail():
                    break
            check(f"from a picture, ▲ reaches the menu past the drop-downs: {' > '.join(seen)}", in_rail())

            # and the drop-down is still usable with nothing but a remote
            page.evaluate("document.querySelector('.page-head select')?.focus()")
            page.wait_for_timeout(300)
            had = page.evaluate("document.activeElement?.tagName")
            page.keyboard.press("Enter")
            page.wait_for_timeout(700)
            opts = page.evaluate("[...document.querySelectorAll('.menu button')].map(x => x.textContent.trim())")
            check(f"OK on a drop-down offers its choices ({had}): {opts[:3]}", len(opts) >= 2)
            before = page.evaluate("document.querySelector('.page-head select')?.value")
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(250)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            after = page.evaluate("document.querySelector('.page-head select')?.value")
            check(f"and choosing one changes it: {before!r} -> {after!r}",
                  after != before and not page.evaluate("!!document.querySelector('.menu')"))

        # Home, a few rows down, then back up to the menu
        page.evaluate("location.hash = '/'")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(2500)

        # The banner is a row like any other: ◀ ▶ walk its own buttons and stop at the end of them,
        # ▼ goes into the pictures below. Most of what one press costs is deciding which things on the
        # page could possibly be next, and that answer comes from the block the ring is standing in.
        where = """() => { const a = document.activeElement;
          return { text: (a.textContent || '').trim().slice(0, 18),
                   hero: !!a.closest('.hero'), row: !!a.closest('.row') }; }"""
        page.evaluate("document.querySelector('.hero .btn')?.focus()")
        page.wait_for_timeout(300)
        first = page.evaluate(where)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(450)
        second = page.evaluate(where)
        check(f"▶ on the banner walks the banner's own buttons: {first['text']} -> {second['text']}",
              second["hero"] and second["text"] != first["text"])
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(450)
        check("and stops at the end of them rather than leaping into the pictures",
              page.evaluate(where)["hero"])
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(450)
        check("▼ goes from the banner into the first row", page.evaluate(where)["row"])
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(450)
        check("and ▲ comes back to it", page.evaluate(where)["hero"])
        page.evaluate("document.querySelector('.card .frame-link')?.focus()")
        for _ in range(5):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(300)
        down = page.evaluate("Math.round((document.scrollingElement || document.documentElement).scrollTop)")
        check(f"five rows down the page has scrolled ({down}px)", down > 400)
        for _ in range(10):
            page.keyboard.press("ArrowUp")
            page.wait_for_timeout(300)
            if in_rail():
                break
        check("▲ gets back to the menu from down the page", in_rail())
        check(f"and the page comes back to the top with it "
              f"({page.evaluate('Math.round((document.scrollingElement||document.documentElement).scrollTop)')}px)",
              page.evaluate("Math.round((document.scrollingElement||document.documentElement).scrollTop)") == 0)

        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: 'projector' }) }); }""")
        b.close()

print("\n4d. the two columns of Settings")
# Settings is a column of pages down the left and the panels for the chosen page on the right. A
# vertical move that slips sideways from halfway down a panel into that list — because the next thing
# below happened to be a few pixels nearer over there — is what made settings feel strange to use:
# it works, and it is never where you meant to go.
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("location.hash = '/settings/appearance'")
        page.wait_for_timeout(3000)
        spot = """() => { const a = document.activeElement;
          return a && a.closest ? (a.closest('.sset-nav') ? 'list' : a.closest('.panel') ? 'panel'
            : a.closest('.rail') ? 'menu' : 'other') : 'none'; }"""
        check("the page has both columns",
              page.evaluate("!!document.querySelector('.sset-nav') && !!document.querySelector('.panel')"))
        page.evaluate("document.querySelector('.sset-nav a')?.focus()")
        page.wait_for_timeout(300)
        check("starting in the list of settings pages", page.evaluate(spot) == "list")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(400)
        check("▼ walks the list", page.evaluate(spot) == "list")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        check("▶ leaves it for the panels", page.evaluate(spot) == "panel")
        seen = []
        for _ in range(4):
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(400)
            seen.append(page.evaluate(spot))
        check(f"and ▼ then stays in the panels rather than slipping back into the list: {seen}",
              all(x == "panel" for x in seen))
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(400)
        check("◀ comes back to the list", page.evaluate(spot) == "list")
        b.close()

print("\n5. what a television is asked to paint while you browse")
# The single most expensive thing Home did on a TV was play a trailer behind the banner — a second
# video decoding while you scroll, on a machine working hard to decode one — and then swap the banner
# for another title every minute and start again. Worse, the stylesheet hides the artwork the moment a
# trailer starts, so when the embed failed (a YouTube embed on a TV often does) what was left was an
# empty panel where the banner should be.
if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        said = []
        page.on("console", lambda m: said.append(m.text))
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(2500)
        check("no trailer is playing behind the banner",
              page.evaluate("!document.querySelector('.hero-trailer, .hc-trailer')"))
        check("and no video or YouTube frame is loaded on Home at all",
              page.evaluate("!document.querySelector('iframe[src*=\"youtube\"], .hero video')"))
        art = page.evaluate("(() => { const a = document.querySelector('.hero .art');"
                            "  return a ? getComputedStyle(a).opacity : null; })()")
        check(f"the banner's own picture is visible, not hidden for a trailer that never came: {art}",
              art is None or float(art) > 0.9)
        # Rotation is a per-user setting and this server has it off, so asking "is it rotating?" here
        # would answer no whatever the code did. Switch it on first, then the answer means something.
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ home_banner_interval: 60 }) }); }""")
        page.goto(f"{BASE}/?tv=0", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1500)
        on_desktop = page.evaluate("!!document.documentElement.dataset.bannerRotating")
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1500)
        on_tv = page.evaluate("!!document.documentElement.dataset.bannerRotating")
        check(f"with rotation switched on it does rotate in a browser ({on_desktop})", on_desktop)
        check(f"and still does not on a television ({on_tv}) — a redraw every minute throws away "
              f"wherever the remote had got to", not on_tv)
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ home_banner_interval: 0 }) }); }""")

        # ?keys=1: what this remote really sends. The sets disagree about Back's number and no amount
        # of reasoning from here settles which one a given set uses.
        page.goto(f"{BASE}/?tv=1&keys=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1200)
        check("?keys=1 shows what the remote sends", page.evaluate("!!document.getElementById('lenta-keys')"))
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(300)
        log = page.evaluate("document.getElementById('lenta-keys')?.textContent || ''")
        check(f"with the number, the name and what LENTA made of it: {log.splitlines()[0] if log else ''}",
              "code 39" in log and "ArrowRight" in log)

        # The switch is a saved preference, not something this browser remembers, because the set whose
        # remote is misbehaving is the worst place to go hunting for it. Tick it on a laptop; it appears
        # on the television.
        page.goto(f"{BASE}/?tv=0", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tv_key_watch: '0' }) }); }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("location.hash = '#/settings/app'")
        page.wait_for_timeout(2000)
        check("the switch is there in an ordinary browser too, not only on the TV",
              page.evaluate("(() => { const b = document.querySelector('#p-keywatch-box');"
                            "  return !!b && !b.hidden; })()"))
        check("and starts unticked", page.evaluate("!document.querySelector('#p-keywatch').checked"))
        page.evaluate("document.querySelector('#p-keywatch').click()")
        page.wait_for_timeout(1800)
        saved = page.evaluate("(async () => { const r = await fetch('/api/auth/me',"
                              " { credentials: 'same-origin' }); return (await r.json()).prefs.tv_key_watch; })()")
        check(f"ticking it saves a preference rather than a note to this browser: {saved!r}", saved == "1")
        check("and puts no panel of key codes on the laptop, where nobody asked the question",
              page.evaluate("!document.getElementById('lenta-keys')"))
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1500)
        check("but the television shows it, which is where the remote is",
              page.evaluate("!!document.getElementById('lenta-keys')"))
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
            credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tv_key_watch: '0' }) }); }""")

        # A television has no address bar, so ?keys=1 is not something anybody can type on one. The
        # same thing has to be reachable with the remote that is being diagnosed.
        # Start from off. The ?keys=1 visit above turned it on for this tab, and a test that clicks a
        # switch without knowing which way it is pointing proves nothing about which way it moved.
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("try { sessionStorage.removeItem('lenta-keys'); } catch (e) {}")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.evaluate("location.hash = '#/settings/app'")
        page.wait_for_timeout(2000)
        check("it starts off", page.evaluate("!document.getElementById('lenta-keys')")
              and page.evaluate("!document.querySelector('#p-keywatch').checked"))
        check("the switch for it is in Settings › LENTA app on a television",
              page.evaluate("(() => { const b = document.querySelector('#p-keywatch-box');"
                            "  return !!b && !b.hidden; })()"))
        page.evaluate("document.querySelector('#p-keywatch').click()")
        page.wait_for_timeout(500)
        check("turning it on shows the panel at once", page.evaluate("!!document.getElementById('lenta-keys')"))
        page.evaluate("location.hash = '#/'")
        page.wait_for_timeout(1200)
        check("and it stays while you move about, which is when you press the key that fails",
              page.evaluate("!!document.getElementById('lenta-keys')"))
        page.evaluate("location.hash = '#/settings/app'")
        page.wait_for_timeout(1800)
        page.evaluate("document.querySelector('#p-keywatch').click()")
        page.wait_for_timeout(400)
        check("and off again", page.evaluate("!document.getElementById('lenta-keys')"))
        # Samsung's Back key, as the set sends it.
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1000)
        page.goto(f"{BASE}/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1200)
        page.evaluate("location.hash = '#/settings/appearance'")
        page.wait_for_timeout(1200)
        before = page.evaluate("location.hash")
        page.evaluate("""() => {
          const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true });
          Object.defineProperty(e, 'keyCode', { value: 10009 });   // Samsung's Return
          window.dispatchEvent(e);
        }""")
        page.wait_for_timeout(1200)
        check(f"Samsung's Back (10009) goes back a page: {before} -> {page.evaluate('location.hash')}",
              page.evaluate("location.hash") != before)
        b.close()

print("\n6. one press of Back is one Back")
# A Samsung set reports a single press of Back three times over: twice as a Tizen hardware event, once
# as a key. The watcher on the user's own television showed all three, in that order, for every press.
# Acting on each of them walks back three pages at once — from anywhere shallow that is straight out of
# the app, which is exactly what "the Back key does nothing" looks like from the sofa.
if sync_playwright:
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1&keys=1", wait_until="domcontentloaded")
        try:
            page.click('[data-user="admin"]', timeout=8000)
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
        except Exception:
            pass
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1200)
        # Three steps with nothing clever about them: Settings redirects to its first tab, which would
        # make "one page back" ambiguous for no good reason.
        for h in ("#/mylist", "#/guide"):
            page.evaluate(f"location.hash = '{h}'")
            page.wait_for_timeout(1000)
        deep = page.evaluate("location.hash")
        check(f"three pages deep to start from: {deep}", deep == "#/guide")

        # exactly what the set sends, in the order the watcher recorded it
        press = """() => {
          const hw = new CustomEvent('tizenhwkey', { bubbles: true, detail: { keyName: 'back' } });
          document.dispatchEvent(hw);                 // the listener on document, then the one on window
          const k = new KeyboardEvent('keydown', { bubbles: true, cancelable: true });
          Object.defineProperty(k, 'keyCode', { value: 10009 });
          Object.defineProperty(k, 'key', { value: 'XF86Back' });
          window.dispatchEvent(k);
        }"""
        page.evaluate(press)
        page.wait_for_timeout(1500)
        landed = page.evaluate("location.hash")
        check(f"one press steps back one page: {deep} -> {landed} (not three at once)",
              landed == "#/mylist")
        panel = page.evaluate("document.getElementById('lenta-keys')?.textContent || ''")
        check("the watcher says the repeats were recognised as one press",
              "ignored" in panel)
        check(f"and says what LENTA did with it: {[l for l in panel.splitlines() if l.startswith('back:')][:1]}",
              "back: went back a page" in panel)

        # a second, deliberate press a moment later must still count
        page.wait_for_timeout(600)
        page.evaluate(press)
        page.wait_for_timeout(1500)
        check(f"a real second press still works: {landed} -> {page.evaluate('location.hash') or '(Home)'}",
              page.evaluate("location.hash") in ("#/", ""))

        # Back while a film is playing. The player has no Back button of its own — the only way out was
        # the … menu — and the old code clicked for one that is only drawn on the error screen, so it
        # reported success and left the picture exactly where it was. On a TV that is the whole app
        # stuck on one film.
        page.goto(f"{BASE}/?tv=1&keys=1", wait_until="domcontentloaded")
        page.wait_for_selector('.card[data-kind="movie"]', timeout=30000)
        page.wait_for_timeout(1500)
        mid = page.evaluate("document.querySelector('.card[data-kind=\"movie\"]').getAttribute('data-id')")
        page.evaluate(f"location.hash = '/item/{mid}'")
        page.wait_for_timeout(2500)
        page.evaluate("""() => { const b = [...document.querySelectorAll('button, a')]
          .find(x => /^\\s*(play|resume)/i.test(x.textContent || '')); if (b) b.click(); }""")
        page.wait_for_timeout(8000)                 # past the watcher's first look, six seconds in
        check("a film is playing", page.evaluate("!!document.querySelector('.player:not(.mini)')"))
        # The panel is the only window into a television. "It doesn't play" is as far as anyone can get
        # from the sofa; this turns it into which of the four routes was taken and what the picture said.
        watch = page.evaluate("document.getElementById('lenta-keys')?.textContent || ''")
        plays = [l for l in watch.splitlines() if l.startswith("play:")]
        check(f"the watcher says how the film was handed to the screen: {plays[-1:] or '(nothing)'}",
              any("via" in l for l in plays))
        check(f"and whether it ever arrived: {[l for l in plays if 'playing after' in l or 'still nothing' in l][:1]}",
              any("playing after" in l or "still nothing" in l for l in plays))
        # Watching, with the ring nowhere near the controls — which is the ordinary state a minute into
        # a film. ▲ should bring the controls up and put the ring on one of them. It used to look for a
        # button by an id that had been renamed, find nothing, and leave the ring where it was: the
        # player then decided the ring was not on its controls, so every arrow fell through to seeking.
        # ◀ ▶ jumped ten seconds and nothing else on the remote did anything at all.
        on_controls = """() => { const p = document.querySelector('.player:not(.mini)'), a = document.activeElement;
          return !!(a && p && p.contains(a) && a !== p); }"""
        who = """() => { const a = document.activeElement;
          return a ? (a.id || a.getAttribute('data-a') || (a.className || '').slice(0, 14) || a.tagName) : '(none)'; }"""
        idle = "document.querySelector('.player:not(.mini)')?.classList.contains('idle') === true"
        # Back to the beginning first: the sample film is forty seconds long and the checks below take
        # several of them, so a session that resumed near the end would end mid-test and close itself.
        page.evaluate("() => { const v = document.querySelector('video'); if (v) v.currentTime = 0; }")
        page.wait_for_timeout(800)
        page.evaluate("document.activeElement?.blur?.(); document.querySelector('.player')?.classList.add('idle');")
        page.wait_for_timeout(500)
        check("watching, with the ring off the controls", not page.evaluate(on_controls))
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(900)
        check(f"▲ brings the controls up and the ring onto them ({page.evaluate(who)})",
              page.evaluate(on_controls) and not page.evaluate(idle))
        first = page.evaluate(who)
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(600)
        check(f"and ▶ walks along them: {first} -> {page.evaluate(who)}",
              page.evaluate(who) != first and page.evaluate(on_controls))
        page.wait_for_timeout(4200)                 # longer than the controls' own three seconds
        check("they do not fade out from under the ring while it is on them",
              not page.evaluate(idle) and page.evaluate(on_controls))
        # But they must let go in the end, or they sit on the picture for the whole film. A remote has
        # nothing to rest on them and nothing to take away, so the player has to decide for itself.
        page.evaluate("() => { const v = document.querySelector('video'); if (v) v.currentTime = 0; }")
        page.wait_for_timeout(6000)
        check("but they do let go in the end, and take the ring with them",
              page.evaluate(idle) and not page.evaluate(on_controls))
        page.keyboard.press("ArrowUp")              # and come straight back
        page.wait_for_timeout(900)
        check("and ▲ brings them back", page.evaluate(on_controls) and not page.evaluate(idle))

        # The player's own settings panel. It is drawn inside the player, so the layer on top was the
        # player itself: the ring stayed on the controls behind the panel and the arrows went on
        # meaning what they mean over the picture. The panel sat there taking nothing.
        # Pause first. The sample film is forty seconds long and the stream starts wherever the last
        # session left off, so a running clip can reach the end in the middle of these checks and the
        # player closes itself — which looks exactly like the bug being tested for.
        page.evaluate("document.querySelector('video')?.pause()")
        page.wait_for_timeout(400)
        page.evaluate("document.querySelector('[data-dk=\"settings\"]')?.click()")
        page.wait_for_timeout(1200)
        in_panel = "(() => { const p = document.querySelector('.ppanel'), a = document.activeElement;" \
                   "  return !!(a && p && p.contains(a)); })()"
        check("the settings panel opens", page.evaluate("!!document.querySelector('.ppanel')"))
        # What it looks like once it is there. On a laptop it is a neat grid in the lower middle of
        # the screen; on a television that was thirteen-pixel labels with two-thirds of a 1920-wide
        # screen empty either side, and on a 2017 set — which has no CSS grid — the label/value pairs
        # ran off the side of the screen in a single row. So: a tall panel down one side, each setting
        # on one line with its value beside it, and type you can read from a sofa.
        look = page.evaluate("""() => {
          const panel = document.querySelector('.ppanel'); if (!panel) return null;
          const p = panel.getBoundingClientRect();
          const ls = [...document.querySelectorAll('.pset-l')];
          const pairs = ls.map(l => {
            const v = l.nextElementSibling;
            const a = l.getBoundingClientRect(), b = v.getBoundingClientRect();
            return { level: Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) < 8,
                     beside: b.left >= a.right - 1 };
          });
          return { h: Math.round(p.height), w: Math.round(p.width),
                   tall: p.height >= innerHeight * 0.9,
                   onScreen: p.left >= -1 && p.right <= innerWidth + 1,
                   narrow: p.width <= innerWidth * 0.6,
                   rows: pairs.length, level: pairs.filter(x => x.level).length,
                   beside: pairs.filter(x => x.beside).length,
                   label: parseFloat(getComputedStyle(ls[0]).fontSize) };
        }""")
        check(f"it is a tall panel down one side, not a strip across the middle "
              f"({look['w']}x{look['h']} of {1920}x{1080})", look["tall"] and look["narrow"])
        check("and all of it is on the screen", look["onScreen"])
        check(f"every setting is level with its own value ({look['level']}/{look['rows']})",
              look["rows"] >= 6 and look["level"] == look["rows"])
        check(f"with the value beside the label rather than under it ({look['beside']}/{look['rows']})",
              look["beside"] == look["rows"])
        check(f"and type that can be read from a sofa ({look['label']:.0f}px)", look["label"] >= 20)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(600)
        check("and the first arrow puts the ring inside it, not on the controls behind",
              page.evaluate(in_panel))
        fill = page.evaluate("""() => {
          const a = document.activeElement, panel = document.querySelector('.ppanel');
          if (!panel || !a || a.nodeType !== 1) return { bg: 'gone', panel: 'gone' };
          return { bg: getComputedStyle(a).backgroundColor, panel: getComputedStyle(panel).backgroundColor };
        }""")
        # An outline alone is a hairline from three metres. The line you are on is filled in.
        check(f"the line the ring is on is filled, not merely outlined ({fill['bg']})",
              fill["bg"] not in (fill["panel"], "rgba(0, 0, 0, 0)", "transparent"))
        row = page.evaluate("document.activeElement?.getAttribute('data-ps') || ''")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(500)
        row2 = page.evaluate("document.activeElement?.getAttribute('data-ps') || ''")
        check(f"the arrows walk its rows: {row} -> {row2}", page.evaluate(in_panel))
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
        choices = page.evaluate("[...document.querySelectorAll('.menu button')].map(x => x.textContent.trim())")
        menu_px = page.evaluate("parseFloat(getComputedStyle(document.querySelector('.menu button')).fontSize)")
        check(f"OK on a row offers its choices: {choices[:2]}",
              len(choices) >= 2 and page.evaluate("!!document.activeElement?.closest('.menu')"))
        check(f"at a size a television can show ({menu_px:.0f}px)", menu_px >= 20)
        page.evaluate(press)
        page.wait_for_timeout(900)
        check("Back closes the choices and leaves the panel open",
              not page.evaluate("!!document.querySelector('.menu')")
              and page.evaluate("!!document.querySelector('.ppanel')"))
        page.evaluate(press)
        page.wait_for_timeout(900)
        check("Back again closes the panel and leaves the film playing",
              not page.evaluate("!!document.querySelector('.ppanel')")
              and page.evaluate("!!document.querySelector('.player:not(.mini)')"))

        page.evaluate(press)
        page.wait_for_timeout(2000)
        check("Back leaves the player, rather than saying it did",
              not page.evaluate("!!document.querySelector('.player:not(.mini)')"))
        b.close()

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the remote ok")
