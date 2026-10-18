"""The old-TV client, run on a browser whose JavaScript is as old as a 2017 set's.

tests/test_legacy_runtime.py takes away the DOM. This takes away the language.

The bundle used to carry `core-js/stable`: every polyfill core-js has, 433 of them, a quarter of a
megabyte once built — and a television must read and compile all of it before the first picture is
drawn. Most of it is for language this client never writes. It now ships only the ones Babel can see
being used, which took 130 KB off the bundle and nearly two seconds off a cold start on a slow set.

That trade is only safe if something checks it, and nothing in an ordinary test run can. A browser
here has every one of those built-ins natively, so a polyfill left out by mistake is invisible: the
page works perfectly, and then dies in a living room in Gabrovo on a television nobody can attach a
console to.

So: every built-in newer than Chromium 47 is deleted before the page loads, and the bundle's own
core-js is the only thing that can put any of it back. If the list it ships is short of something the
client needs, the client breaks here.

It has already earned this. The first trimmed build looked perfect and threw "Invalid flags supplied
to RegExp constructor 'undefinedy'" on its first line: core-js's own String.split polyfill calls the
engine's native split while deciding whether it is needed, and that algorithm reads
RegExp.prototype.flags — which a 2017 engine has not got, and which nothing in LENTA mentions by
name, so no amount of reading the source would have found it. The whole es.regexp family is included
for that reason, used or not.
"""
import json, os, subprocess, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
BUILTINS = json.loads((ROOT / "tests" / "tv-engines.json").read_text())["builtins"]

# Both built clients, each against the oldest engine it is ever given to. The second one ships even
# fewer polyfills than the first — 18 against 54 — because a Chromium 63 engine is missing far less,
# and it drops the regexp family the older build needs. Each claim is checked here rather than assumed.
BUILDS = [
    ("the 2017-2018 build", "legacy=1", "47", "app.js"),
    ("the 2019-2022 build", "legacy=2", "63", "mid.js"),
]

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. both builds ship a chosen few polyfills, not all of them")
build = (ROOT / "tools" / "build-legacy.mjs").read_text()
check("the build works out which ones each client uses",
      build.count("usage-global") == 2 and "polyfill-corejs3" in build)
check("it does not simply import the lot any more", "import 'core-js/stable'" not in build)
check("the older build adds the regexp family whether or not it was spotted",
      "FOUNDATION" in build and "es\\.regexp\\." in build)
# The second build must NOT carry it. RegExp.prototype.flags — the whole reason the family is there —
# arrived in Chromium 49, and nothing given the second build is older than 63.
mid = build[build.index("the same client for 2019-2022"):]
check("and the newer one does not, because Chromium 63 has RegExp.prototype.flags",
      "FOUNDATION.test" not in mid)

# Deleting a whole global takes the test harness with it (Playwright's own code wants BigInt64Array),
# and the only ones in that list LENTA could use are Proxy and Reflect, which it does not.
STRIP = """
(() => {
  const gone = [];
  const drop = (obj, key, label) => {
    try {
      if (obj && Object.prototype.hasOwnProperty.call(obj, key)) {
        delete obj[key];
        if (!(key in obj)) gone.push(label);
      }
    } catch (e) { /* not removable here; never the point */ }
  };
  for (const [name, key] of __MEMBERS__) {
    const C = window[name];
    if (!C) continue;
    drop(C, key, name + '.' + key);
    drop(C.prototype, key, name + '.prototype.' + key);
  }
  for (const name of ['Proxy', 'Reflect']) {
    try { if (name in window) { delete window[name]; gone.push(name); } } catch (e) {}
  }
  window.__goneJs = gone;
})();
"""

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed here — nothing to run")
    raise SystemExit(1 if fails else 0)

subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--disable-dev-shm-usage"])
    for n, (label, qs, floor, filename) in enumerate(BUILDS, start=2):
        B = BUILTINS[floor]
        members = [[a, k] for a, k in B["statics"]] + [[a, k] for a, k in B["protos"]]
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STRIP.replace("__MEMBERS__", json.dumps(members)))
        page.goto(f"{BASE}/?tv=1&{qs}", wait_until="load")
        page.wait_for_timeout(900)

        print(f"\n{n}. {label}, on a Chromium {floor} engine's JavaScript")
        gone = page.evaluate("(window.__goneJs || []).length")
        # Without this the rest of the section passes on a browser that still has everything — the
        # one way a test like this looks healthy while testing nothing at all.
        check(f"{gone} built-ins newer than Chromium {floor} removed before the page loaded", gone > 30)
        check(f"and the page is running {filename}",
              page.evaluate(f"!!document.querySelector('script[src*=\"legacy/{filename}\"]')"))

        # A polyfill short of what the client needs shows up here, before anything is clicked: the
        # bundle throws on its first line and the page never finishes starting. Say so plainly rather
        # than letting the sign-in time out thirty seconds later with a stack trace.
        early = [e for e in errors if "Failed to load" not in e]
        check(f"the bundle started without throwing{'' if not early else ': ' + early[0][:120]}", not early)

        back = page.evaluate("""() => ({
          'Object.assign': typeof Object.assign === 'function',
          'Object.entries': typeof Object.entries === 'function',
          'Object.fromEntries': typeof Object.fromEntries === 'function',
          'Array.prototype.includes': typeof [].includes === 'function',
          'Array.prototype.flatMap': typeof [].flatMap === 'function',
          'String.prototype.padStart': typeof ''.padStart === 'function',
          'RegExp.prototype.flags': 'flags' in RegExp.prototype,
          'queueMicrotask': typeof queueMicrotask === 'function',
          'URL': typeof URL === 'function',
          'URLSearchParams': typeof URLSearchParams === 'function',
        })""")
        missing = [k for k, v in back.items() if v is not True]
        check(f"the language the client needs is all there{'' if not missing else ': missing ' + ', '.join(missing)}",
              not missing)

        try:
            page.click('[data-user="admin"]', timeout=20000)
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
            page.wait_for_selector(".card", timeout=45000)
            page.wait_for_timeout(1500)
            href = page.evaluate("(document.querySelector('.card a[href*=\"#/item/\"]') || {})"
                                 ".getAttribute?.('href') || ''")
            routes = [("#/", ".card", "Home"), ("#/mylist", ".view", "My list"),
                      ("#/guide", ".view", "the guide"), ("#/settings", ".panel", "Settings"),
                      ("#/settings/about", ".panel, .view", "About")]
            if href:
                routes.append((href, ".view h1, .view .hero", "a title"))
            bad = []
            for route, need, name in routes:
                page.goto(f"{BASE}/?tv=1&{qs}{route}", wait_until="load")
                page.wait_for_timeout(1700)
                text = page.evaluate("((document.querySelector('.view') || document.body).innerText || '')")
                if "didn't load" in text or "didn’t load" in text:
                    bad.append(f"{name} showed the error page")
                elif not page.evaluate(f"!!document.querySelector({need!r})"):
                    bad.append(f"{name} did not draw")
            check(f"every page a remote can reach draws{'' if not bad else ': ' + '; '.join(bad[:3])}", not bad)

            if href:
                page.goto(f"{BASE}/?tv=1&{qs}#/play/{href.split('/')[-1]}", wait_until="load")
                page.wait_for_timeout(3500)
                check("and the player opens", page.evaluate("!!document.querySelector('.player')"))
        except Exception as e:
            # A page that never finished starting cannot be signed into. That is the failure, not a
            # fault in the test, so it is reported as one.
            check(f"the client could be used at all — {str(e).splitlines()[0][:110]}", False)

        thrown = [e for e in errors if "Failed to load" not in e]
        check(f"nothing threw while all of that happened{'' if not thrown else ': ' + thrown[0][:110]}",
              not thrown)
        page.close()
    browser.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("both built clients run on their own engine's JavaScript")
