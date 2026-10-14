"""The mini player survives a refresh and a closed tab — paused, where it was.

A browser keeps nothing of a <video> across a page load, so pressing F5 with a film running in the bar
at the bottom simply lost it: the bar went, and getting back meant finding the title again and pressing
Resume. What is kept now is the little it takes to rebuild one — which title, which file, and the
position at the moment the page went away — in localStorage, which outlives the tab.

Outliving the tab is what the awkward cases in here are about. A store that outlives a tab is shared
with every other tab, and with everyone else who signs in on that browser, so this checks the three
rules that keep it from being a nuisance: a second tab opened while the first still has the film leaves
it alone; a tab closed and reopened picks it straight up; and the record names the user it belongs to,
because a household shares a browser and the next person to sign in should not be handed what the last
one was watching. Anything older than a day is not "where I left off" and is dropped.

And it comes back **paused** — a page that starts making noise by itself is not what anybody opening it
asked for — but ready, so one press of play carries on.

Live TV is deliberately not kept: a broadcast has no position to return to, and the tuner was handed
back when the page went away.
"""
import json, os, re, subprocess, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
MOVIE = 43                      # Gladiator, in the seeded test library

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. where it is kept, and when it is forgotten")
src = (ROOT / "web" / "js" / "player.js").read_text()
check("the place outlives the tab (localStorage, not sessionStorage)",
      "localStorage.setItem(KEEP" in src and "sessionStorage.setItem(KEEP" not in src)
check("the tab's own id is the one thing that dies with the tab",
      "sessionStorage.setItem(TAB" in src)
check("every read and write is wrapped, for a browser in private mode",
      len(re.findall(r"try \{[^}]*(local|session)Storage", src)) >= 4)
check("the record names the user it belongs to", "user: state.user?.id" in src)
check("and a film is never handed to another user", "k.user !== state.user.id" in src)
check("a record older than a day is dropped", "STALE" in src and "24 * 60 * 60 * 1000" in src)
check("a tab asks the others rather than guessing from a clock",
      "anotherTabHasIt" in src and "addEventListener('storage'" in src)
check("and only a tab actually showing the film answers",
      re.search(r"e\.key !== PING \|\| !P \|\| !P\.mini", src) is not None)
check("a broadcast is never kept", re.search(r"function keepMini\([^)]*\)[^}]*P\.live", src) is not None)
def within(name, needle, lines=12):
    """Is `needle` in the first few lines of the function called `name`?"""
    at = src.find(f"function {name}(")
    return at >= 0 and needle in "\n".join(src[at:].splitlines()[:lines])


check("opening the player out to full screen forgets it", within("expandPlayer", "forgetMini()"))
check("and closing it forgets it", within("teardown", "forgetMini()"))
check("the page going away is the last chance to write the position down",
      "addEventListener('pagehide', keepMini)" in src)
check("and that covers a closed tab as much as a refresh", "pagehide" in src)
check("so is the page being hidden", "visibilitychange" in src and "document.hidden" in src)
app = (ROOT / "web" / "js" / "app.js").read_text()
check("the restore runs after the page it belongs to is drawn",
      re.search(r"await route\(\);\s*(//[^\n]*\n\s*)*restoreMiniPlayer\(\)", app) is not None)
check("and a failure to restore never stops the app starting",
      re.search(r"restoreMiniPlayer\(\)\.catch\(", app) is not None)

print("\n2. the real thing, in a browser")
try:
    from playwright.sync_api import TimeoutError as PwTimeout, sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    raise SystemExit(1 if fails else 0)

subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=180)


def sign_in(page):
    """A second tab of the same browser is already signed in — its cookie came with it."""
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector('.card, [data-user="admin"]', timeout=30000)
    if page.query_selector('[data-user="admin"]'):
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
    page.wait_for_selector(".card", timeout=30000)


def minimised(page, item=MOVIE):
    """Play something from the beginning, let it get going, then minimise it to the bar.

    From the beginning deliberately: earlier runs of this test leave a watch position behind, and a
    film resumed four seconds from its end finishes in the middle of the test."""
    page.evaluate("""(id) => fetch('/api/progress', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_id: id, position: 0, duration: 600, state: 'paused',
                               play_id: 'test-reset', mode: 'direct', quality: 'original' }) })""", item)
    page.wait_for_timeout(300)
    page.evaluate(f"location.hash = '#/play/{item}'")
    page.wait_for_selector("#pv", timeout=30000)
    page.wait_for_function("document.querySelector('#pv').currentTime > 2", timeout=40000)
    page.evaluate("document.querySelector('[data-a=\"minimize\"]').click()")
    page.wait_for_selector(".player.mini", timeout=10000)
    page.wait_for_timeout(600)


def bar(page):
    return page.evaluate(r"""() => {
      const v = document.querySelector('#pv'), b = document.querySelector('#musicbar');
      return { bar: !!(b && !b.hidden), mini: !!document.querySelector('.player.mini'),
               paused: v ? v.paused : null, pos: v ? v.currentTime : null,
               ready: v ? v.readyState : 0,
               clock: (() => { const t = (document.querySelector('#musicbar .dk-time') || {}).textContent || '';
                   const m = /(\d+):(\d\d)/.exec(t); return m ? Number(m[1]) * 60 + Number(m[2]) : null; })(),
               title: (document.querySelector('#musicbar .dk-t') || {}).textContent || '',
               play: document.querySelector('#musicbar .dk-play')?.getAttribute('aria-label') || '' };
    }""")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    sign_in(page)
    minimised(page)
    was = bar(page)
    check(f"it is playing in the bar before the refresh ({was['title']} at {was['clock']}s)",
          was["bar"] and was["mini"] and not was["paused"] and was["clock"] is not None)

    asked = []
    page.on("request", lambda r: asked.append(json.loads(r.post_data or "{}"))
            if r.url.endswith("/api/playback") and r.method == "POST" else None)
    page.reload(wait_until="domcontentloaded")
    came_back = True
    try:                       # on a version without this, nothing ever appears: say so and carry on
        page.wait_for_selector(".player.mini", timeout=30000)
        page.wait_for_function("document.querySelector('#pv') && document.querySelector('#pv').readyState >= 2",
                               timeout=40000)
    except PwTimeout:
        came_back = False
    check("the mini player comes back at all", came_back)
    now = bar(page) if came_back else {"bar": False, "mini": False, "paused": None, "pos": 0, "ready": 0,
                                       "clock": None, "title": "", "play": ""}
    check("after the refresh the bar is back", now["bar"])
    check("still minimised, not full screen", now["mini"])
    check("paused", now["paused"] is True)
    check("the button offers Play", now["play"] == "Play")
    check(f"it is the same title ({now['title']})", now["title"] and now["title"] == was["title"])
    check("there is a picture, not a black rectangle (the frame is decoded)", now["ready"] >= 2)
    # It asks the server for the position the page had reached, not the one it had four seconds earlier.
    start = asked[0].get("start") if asked else None
    check(f"it asks to start where the page left off ({start if start is None else round(start, 1)}s"
          f" against the {was['clock']}s on the bar)",
          start is not None and abs(start - was["clock"]) < 2)
    check(f"and the bar shows that place, not the beginning ({now['clock']}s)",
          now["clock"] is not None and abs(now["clock"] - was["clock"]) < 4)

    # The whole point: one press of play and it carries on.
    if came_back:
        page.evaluate("document.querySelector('#musicbar .dk-play').click()")
        page.wait_for_timeout(2500)
        went = bar(page)
        check(f"one press of play and it goes on ({went['clock']}s)",
              not went["paused"] and went["clock"] > now["clock"])

    print("\n3. one film, however many tabs")
    # A second tab opened while this one still has the film: it must leave it alone.
    other = ctx.new_page()
    sign_in(other)
    other.wait_for_timeout(3000)
    check("a second tab does not take the film off the first", not bar(other)["bar"])
    check("and the first tab still has it", bar(page)["bar"])
    other.close()

    print("\n4. closing the tab and coming back")
    page.close()                     # the tab goes: pagehide says so, and localStorage outlives it
    fresh = ctx.new_page()
    sign_in(fresh)
    try:
        fresh.wait_for_selector(".player.mini", timeout=30000)
        fresh.wait_for_function("document.querySelector('#pv') && document.querySelector('#pv').readyState >= 2",
                                timeout=40000)
        back = bar(fresh)
    except PwTimeout:
        back = {"bar": False, "mini": False, "paused": None, "clock": None, "title": "", "play": ""}
    check("a new tab picks the film up where the closed one left it", back["bar"] and back["mini"])
    check("paused, as before", back["paused"] is True)
    check(f"the same title ({back['title']})", back["title"] == was["title"])
    page = fresh                     # carry on in the tab that has the player

    print("\n5. and it stays gone when it should")
    # Stopped by hand, then refreshed: it should stay gone.
    page.evaluate("document.querySelector('#musicbar [data-dk=\"stop\"], #musicbar [data-dk=\"close\"]')?.click()")
    page.wait_for_timeout(600)
    if bar(page)["bar"]:                       # no stop button in this layout: close it the way the app does
        page.evaluate("window.dispatchEvent(new CustomEvent('lenta:music-start'))")
        page.wait_for_timeout(600)
    kept = page.evaluate("sessionStorage.getItem('lenta.miniPlayer')")
    check("closing the mini player forgets the place", kept in (None, "null"))
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector(".card", timeout=30000)
    page.wait_for_timeout(2500)
    check("so a refresh after that brings nothing back", not bar(page)["bar"])

    # A refresh of the full-screen player is the router's business and must stay that way.
    page.evaluate(f"location.hash = '#/play/{MOVIE}'")
    page.wait_for_selector("#pv", timeout=30000)
    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#pv", timeout=30000)
    page.wait_for_timeout(1500)
    full = bar(page)
    check("refreshing the full-screen player still opens it full screen, not in the bar",
          not full["mini"])
    ctx.close()
    b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
