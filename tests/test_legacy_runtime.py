"""The old-TV client, run on a browser with the old TV's DOM taken away from it.

tests/test_legacy_build.py asks whether the bundle *parses* on a 2017 Samsung set. That is only half the
question. Chromium 47 parses `el.append(child)` perfectly well — and then throws, because `append` was
not added until Chromium 54. Nothing in a syntax check can see that, nothing in a desktop browser can
see it either, and the result is a page that dies on its first line with a message no one but the owner
of the television will ever read.

So this takes the browser that is here, removes from it every DOM method a 2017 set does not have, and
loads the legacy bundle into the hole that leaves. If the build's fill-ins are doing their job the app
behaves normally; if one is missing, the page breaks here exactly as it breaks in the living room.

The list below is the point of the file, and it should grow whenever the client reaches for something
newer. Each entry is a method that exists in this browser and did not exist in Chromium 47.
"""
import os, subprocess, sys, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"

# interface, method, the Chromium that brought it
MISSING_IN_47 = [
    ("Element", "append", 54), ("Element", "prepend", 54),
    ("Element", "before", 54), ("Element", "after", 54), ("Element", "replaceWith", 54),
    ("Element", "replaceChildren", 86), ("Element", "toggleAttribute", 69),
    ("Document", "append", 54), ("Document", "prepend", 54), ("Document", "replaceChildren", 86),
    ("DocumentFragment", "append", 54), ("DocumentFragment", "prepend", 54),
    ("DocumentFragment", "replaceChildren", 86),
    ("CharacterData", "before", 54), ("CharacterData", "after", 54), ("CharacterData", "replaceWith", 54),
    ("Element", "scrollTo", 61), ("Element", "scrollBy", 61),
]

# Runs before anything on the page, so the app and its fill-ins both start in a browser without these.
STRIP = """
(function () {
  var want = %s, gone = [];
  for (var i = 0; i < want.length; i++) {
    var iface = window[want[i][0]], name = want[i][1];
    if (iface && iface.prototype && name in iface.prototype) {
      delete iface.prototype[name];
      if (!(name in iface.prototype)) gone.push(want[i][0] + '.' + name);
    }
  }
  // scrollIntoView cannot be taken away — it is as old as the web — but reading a settings object is
  // new. Put back the one-argument form of Chromium 47, which takes a boolean and ignores anything else.
  var real = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (how) { return real.call(this, how === true); };
  window.__stripped = gone;
})();
""" % str([[a, b] for a, b, _ in MISSING_IN_47]).replace("'", '"')

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed here — nothing to run")
    raise SystemExit(0)

subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(STRIP)
    page.goto(f"{BASE}/?legacy=1", wait_until="load")
    page.wait_for_timeout(800)

    print("1. the browser really has been made old, and it really is the old client")
    # Without this the rest of the file would pass on a browser that still had everything, which is the
    # one way a test like this can look healthy while testing nothing at all.
    stripped = page.evaluate("window.__stripped || []")
    check(f"{len(stripped)} of {len(MISSING_IN_47)} methods removed before the page loaded",
          len(stripped) == len(MISSING_IN_47))
    check("and the page is running web/legacy/app.js, not the normal client",
          page.evaluate("!!window.LENTA_LEGACY") and page.evaluate(
              "!!document.querySelector('script[src*=\"legacy/app.js\"]')"))

    print("\n2. the bundle's fill-ins put each one back")
    for iface, name, since in MISSING_IN_47:
        ok = page.evaluate(f"typeof window.{iface}.prototype.{name} === 'function'")
        check(f"{iface}.{name} (Chromium {since})", ok)

    print("\n3. and they behave the way the app expects them to")
    # Not whether they exist — whether they do the job. Strings become text, several arguments keep
    # their order, and a node that is already a child survives being handed back to replaceChildren.
    out = page.evaluate("""() => {
      const r = {};
      try {
      const d = document.createElement('div');
      const span = document.createElement('span'); span.id = 'keep';
      d.append(span, 'tail');
      r.append = d.textContent === 'tail' && d.firstChild === span && d.childNodes.length === 2;
      d.prepend('head');
      r.prepend = d.textContent === 'headtail' && d.firstChild.nodeType === 3;
      const after = document.createElement('i');
      span.after(after);
      r.after = span.nextSibling === after;
      const before = document.createElement('b');
      span.before(before);
      r.before = span.previousSibling === before;
      // the live case: the node being kept is already inside the element being emptied
      d.replaceChildren(span, 'only');
      r.replaceChildren = d.childNodes.length === 2 && d.firstChild === span && d.textContent === 'only';
      const gone = document.createElement('u');
      d.append(gone); gone.replaceWith('x');
      r.replaceWith = !d.contains(gone) && d.textContent === 'onlyx';
      } catch (e) { r.threw = String(e); }
      return r;
    }""")
    if out.get("threw"):
        print(f"        one of them threw: {out['threw']}")
    for name in ("append", "prepend", "before", "after", "replaceWith", "replaceChildren"):
        check(f"{name} does what the standard says", out.get(name) is True)

    # The focus ring asks to be brought into view after every press of the remote. "nearest" must mean
    # nearest: an engine that reads the settings object as plain truthiness scrolls whatever has focus
    # to the top of the screen instead, which is the arrows feeling broken rather than merely slow.
    scrolled = page.evaluate("""() => {
      const box = document.createElement('div');
      box.style.cssText = 'position:fixed;left:0;top:0;width:200px;height:100px;overflow:auto;z-index:9999';
      for (let i = 0; i < 20; i++) {
        const row = document.createElement('div');
        row.style.cssText = 'height:50px'; row.textContent = 'row ' + i;
        box.appendChild(row);
      }
      document.body.appendChild(box);
      const rows = box.children, r = {};
      try {
      // Each row is 50 tall and the box shows 100 at a time, so row i runs from 50i to 50i+50. The
      // numbers below are chosen so that none of them is also what the old one-argument form would do.
      box.scrollTop = 500;
      rows[3].scrollIntoView({ block: 'nearest', inline: 'nearest' });   // 150..200, above the fold
      r.nearest = box.scrollTop === 150;                                  // its top to the box's top
      box.scrollTop = 0;
      rows[8].scrollIntoView({ block: 'center' });                        // 400..450 -> centred at 50
      r.centre = Math.abs(box.scrollTop - 375) <= 2;
      box.scrollTop = 20;
      rows[1].scrollIntoView({ block: 'nearest' });                       // 50..100, already showing
      r.stays = box.scrollTop === 20;
      box.scrollTop = 0;
      box.scrollBy({ top: 40 });
      r.scrollBy = box.scrollTop === 40;
      box.scrollTo({ top: 10 });
      r.scrollTo = box.scrollTop === 10;
      } catch (e) { r.threw = String(e); }
      box.parentNode.removeChild(box);
      return r;
    }""")
    if scrolled.get("threw"):
        print(f"        one of them threw: {scrolled['threw']}")
    check("scrollIntoView 'nearest' scrolls the least it can", scrolled.get("nearest") is True)
    check("scrollIntoView 'center' puts it in the middle", scrolled.get("centre") is True)
    check("and something already on screen is left where it is", scrolled.get("stays") is True)
    check("scrollBy on an element moves it", scrolled.get("scrollBy") is True)
    check("scrollTo on an element places it", scrolled.get("scrollTo") is True)

    print("\n4. the pages a remote can reach all draw")
    page.click('[data-user="admin"]')
    page.fill("#lp", "secret123")
    page.click("#login button.primary")
    page.wait_for_selector(".card", timeout=30000)
    page.wait_for_timeout(1200)
    first_item = page.evaluate(
        "(document.querySelector('.card[data-kind=\"movie\"]')||{}).getAttribute?.('data-id') || ''")
    lib = page.evaluate(
        "(document.querySelector('a[href*=\"#/library/\"]')||{}).getAttribute?.('href') || ''")

    pages = [("#/", "Home", ".card"),
             ("#/mylist", "My list", ".view h1, .view h2, .empty"),
             ("#/guide", "TV guide", ".view"),
             ("#/settings", "Settings", ".panel"),
             ("#/settings/home", "Settings › Home screen", ".panel"),
             ("#/settings/streaming", "Settings › Streaming services", ".panel"),
             ("#/settings/livetv", "Settings › Live TV", ".panel")]
    check("Home offered a film to open", bool(first_item))
    if first_item:
        pages.append((f"#/item/{first_item}", "a title's own page", ".view h1, .view .hero"))
    if lib:
        pages.append((lib, "a library", ".view"))

    for route, label, need in pages:
        page.goto(f"{BASE}/?legacy=1{route if route.startswith('#') else '#' + route.lstrip('#')}",
                  wait_until="load")
        page.wait_for_timeout(1600)
        broke = page.evaluate(
            "((document.querySelector('.view')||document.body).innerText||'').indexOf(\"didn't load\") >= 0"
            " || ((document.querySelector('.view')||document.body).innerText||'').indexOf('didn\\u2019t load') >= 0")
        drawn = page.evaluate(f"!!document.querySelector({need!r})")
        size = page.evaluate("((document.querySelector('.view')||document.body).innerText||'').length")
        check(f"{label} — {'the error page instead' if broke else f'{size} characters of page'}",
              not broke and drawn and size > 20)

    print("\n4b. the Top 10 row, on an engine that has never heard of a custom property")
    # Every measurement in that row is calc() over properties each card sets on itself. Chromium 47 has
    # none, so the big hollow number is drawn at whatever size it happens to be and the poster lands on
    # top of it. The stylesheet carries a plain-poster fallback for those sets; this is that fallback,
    # measured — a tile with a real width and height, and no number left to collide with.
    page.goto(f"{BASE}/?legacy=1#/", wait_until="load")
    page.wait_for_selector(".card", timeout=30000)
    page.wait_for_timeout(1500)
    if not page.evaluate("!!document.querySelector('.track.top10 .card.rank')"):
        print("  skip  no Top 10 row on this server's Home")
    else:
        page.evaluate("document.documentElement.classList.add('no-vars')")
        page.wait_for_timeout(400)
        tile = page.evaluate("""() => {
          const c = document.querySelector('.track.top10 .card.rank');
          const r = c.getBoundingClientRect(), f = c.querySelector('.frame').getBoundingClientRect();
          const n = c.querySelector('.rank-num');
          return { w: Math.round(r.width), h: Math.round(r.height),
                   fw: Math.round(f.width), fh: Math.round(f.height),
                   num: n ? getComputedStyle(n).display : 'gone' };
        }""")
        check(f"the counting numbers are left out there ({tile['num']})", tile["num"] == "none")
        check(f"and the tile is a poster with a size of its own ({tile['w']}x{tile['h']})",
              100 < tile["w"] < 300 and 140 < tile["h"] < 400)
        check(f"the picture fills it rather than hanging off the side ({tile['fw']}x{tile['fh']})",
              abs(tile["fw"] - tile["w"]) <= 4 and abs(tile["fh"] - tile["h"]) <= 6)
        page.evaluate("document.documentElement.classList.remove('no-vars')")

    print("\n5. and the things that are put on top of a page still open")
    # Every dialog, menu and toast in LENTA is placed with append, so one missing fill-in takes the lot:
    # on a television that means the remote presses a button and nothing whatever happens.
    page.goto(f"{BASE}/?legacy=1#/", wait_until="load")
    page.wait_for_selector(".card", timeout=30000)
    page.wait_for_timeout(800)
    opened = False
    if page.evaluate("!!document.querySelector('#account-btn')"):
        page.click("#account-btn")
        page.wait_for_timeout(500)
        opened = page.evaluate("!!document.querySelector('.menu, .sheet-back')")
    check("the account menu opens", opened)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.wait_for_timeout(400)
    check(f"nothing threw while all of that happened{'' if not errors else ': ' + errors[0][:120]}",
          not errors)

    browser.close()

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails[:6])}")
    raise SystemExit(1)
print("the old-TV client runs")
