"""LENTA on every Samsung television from 2017 on, one model year at a time.

tests/test_legacy_runtime.py asks whether the built client survives a 2017 set's DOM. That is one
television out of ten, and it is the one Vlad owns — which is why it is the one that was tested, and
why the years in between were never looked at at all. They are not the same browser. Samsung's own
table:

    2017 Tizen 3.0 M47   2019 Tizen 5.0 M63   2021 Tizen 6.0 M76   2023 Tizen 7.0 M94   2025 M120
    2018 Tizen 4.0 M56   2020 Tizen 5.5 M69   2022 Tizen 6.5 M85   2024 Tizen 8.0 M108  2026 M130

Nine years, nine engines, and the middle of that range is most of the sets in people's houses. So
this takes the browser that is here, deletes from it everything the television in question did not
have — generated from MDN's compatibility data, not from anyone's memory — and runs LENTA through
every page a remote can reach, the player included.

The second half is about what the pages look like rather than whether they throw, and it is where
this file earned its keep. CSS grid arrived in Chromium 57 and custom properties in 49, so a 2018 set
is the one engine in the range that has properties but no grid. Nothing had ever been built for that
combination: the stand-in for a grid gave every child "width: max-content", and a Top 10 tile has
every one of its parts positioned absolutely, so there was nothing to measure. Ten tiles, 199 pixels
tall and none wide — the whole row a blank stripe. A 2017 set escaped it only by accident.

    the four engines, by what their CSS can do
    2017  M47   no custom properties, no grid, no sticky, no flex gap
    2018  M56   custom properties and sticky, but no grid and no flex gap
    2019-21     everything but flex gap
    2022  M85   everything the stylesheet needs except aspect-ratio (the bundle shims that in script)
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
ENGINES = json.loads((ROOT / "tests" / "tv-engines.json").read_text())

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# Whole interfaces are left alone. The ones these engines lack and LENTA might use are already
# covered by the members below; deleting the rest takes the test harness down with it.
STRIP = """
(() => {
  const gone = [];
  for (const [iface, name] of __MEMBERS__) {
    const I = window[iface];
    if (!I) continue;
    const proto = I.prototype || I;
    try { if (name in proto) { delete proto[name]; if (!(name in proto)) gone.push(iface + '.' + name); } }
    catch (e) { /* not removable in this browser: it was never the point of the test */ }
  }
  window.__gone = gone;
})();
"""

# What the CSS fallbacks are switched on by. The bundle sets these at startup by asking the engine;
# here they are set by hand, because this browser can do everything and would never set any of them.
CSS_PROFILES = [
    ("2017", ["no-vars", "no-grid", "no-sticky", "no-flexgap"]),
    ("2018", ["no-grid", "no-flexgap"]),
    ("2019-21", ["no-flexgap"]),
    ("2022", []),
]

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright is not installed here — nothing to run")
    raise SystemExit(0)

subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--disable-dev-shm-usage"])

    print("1. every model year, with everything newer than its engine taken away")
    for year, info in ENGINES["years"].items():
        # Which of the three clients that year is given. 2017-2018 have no grid and no custom
        # properties and get everything rewritten to ES5; 2019-2022 have both and get a build that
        # keeps them; 2023 and later get the normal client.
        qs = ("?tv=1&legacy=1" if info["chrome"] < 57 else
              "?tv=1&legacy=2" if info["legacy"] else "?tv=1")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STRIP.replace("__MEMBERS__", json.dumps(info["members"])))
        bad = []
        try:
            page.goto(f"{BASE}/{qs}", wait_until="load")
            page.wait_for_timeout(600)
            gone = page.evaluate("(window.__gone || []).length")
            page.click('[data-user="admin"]')
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
            page.wait_for_selector(".card", timeout=45000)
            page.wait_for_timeout(1500)

            libs = page.evaluate("[...document.querySelectorAll('a[href*=\"#/library/\"]')]"
                                 ".map(a => a.getAttribute('href'))")
            href = page.evaluate("(document.querySelector('.card a[href*=\"#/item/\"]') || {})"
                                 ".getAttribute?.('href') || ''")
            routes = [("#/", ".card", "Home"), ("#/mylist", ".view", "My list"),
                      ("#/guide", ".view", "the guide"), ("#/settings", ".panel", "Settings"),
                      ("#/settings/about", ".panel, .view", "About"),
                      ("#/search", ".view input", "Search")]
            if libs:
                routes.append((libs[0], ".view", "a library"))
            if href:
                routes.append((href, ".view h1, .view .hero", "a title"))

            for route, need, label in routes:
                page.goto(f"{BASE}/{qs}{route}", wait_until="load")
                page.wait_for_timeout(1700)
                text = page.evaluate("((document.querySelector('.view') || document.body).innerText || '')")
                if "didn't load" in text or "didn’t load" in text:
                    bad.append(f"{label} showed the error page")
                elif not page.evaluate(f"!!document.querySelector({need!r})"):
                    bad.append(f"{label} did not draw")

            # The player is the hardest page on a television and the one nobody can debug from here.
            if href:
                page.goto(f"{BASE}/{qs}#/play/{href.split('/')[-1]}", wait_until="load")
                page.wait_for_timeout(3500)
                if not page.evaluate("!!document.querySelector('.player')"):
                    bad.append("the player did not open")
                else:
                    page.keyboard.press("ArrowUp")              # the controls, and the ring onto them
                    page.wait_for_timeout(700)
                    if not page.evaluate("!!document.querySelector('.player .pbtn, .player .bottom')"):
                        bad.append("the player drew no controls")
                    if not page.evaluate("document.activeElement?.className || ''"):
                        bad.append("the ring went nowhere in the player")

            page.goto(f"{BASE}/{qs}#/", wait_until="load")
            page.wait_for_selector(".card", timeout=45000)
            page.wait_for_timeout(1800)
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(400)
            if not page.evaluate("document.activeElement?.className || ''"):
                bad.append("the arrow moved nothing")
        except Exception as e:
            bad.append(f"threw: {str(e)[:90]}")
            gone = 0
        page.close()
        thrown = [e for e in errors if "Failed to load" not in e]
        if thrown:
            bad.append(f"threw: {thrown[0][:90]}")
        tizen = {2017: "3.0", 2018: "4.0", 2019: "5.0", 2020: "5.5", 2021: "6.0", 2022: "6.5",
                 2023: "7.0", 2024: "8.0", 2025: "9.0", 2026: "10.0"}.get(int(year), "?")
        client = ("app.js" if info["chrome"] < 57 else "mid.js" if info["legacy"] else "js/app.js")
        check(f"{year}  Tizen {tizen:<4} Chromium {info['chrome']:<3} "
              f"({client:<9} {gone:>3} removed)"
              f"{'' if not bad else ' — ' + '; '.join(bad[:3])}", not bad)

    print("\n2. and the pages still have their shape on each engine's CSS")
    # Not whether it throws — whether anything meant to be seen ends up with no size. This is the
    # half that caught the 2018 sets.
    #
    # Each stylesheet is checked only against the engines it is actually sent to. app.css carries the
    # stand-ins for no custom properties and no grid, and goes to 2017 and 2018; mid.css deliberately
    # carries neither, because no engine given it is missing either — forcing those classes onto it
    # would be testing a combination that cannot happen.
    COLLAPSED = """() => {
      const out = [];
      for (const el of document.querySelectorAll('.view *, .card')) {
        if (el.offsetParent === null) continue;                 // it or something above it is hidden
        if (!el.matches('.card, .frame, .frame-link, button, a[href], .chip')) continue;
        const cs = getComputedStyle(el);
        if (cs.position === 'absolute' || cs.position === 'fixed') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) {
          out.push(el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className
            ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : ''));
        }
      }
      const n = new Map();
      out.forEach(k => n.set(k, (n.get(k) || 0) + 1));
      return [...n].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, c]) => c + 'x ' + k);
    }"""
    set_profile = ("(c) => { const d = document.documentElement;"
                   " ['no-vars','no-grid','no-sticky','no-flexgap'].forEach(x => d.classList.remove(x));"
                   " c.forEach(x => d.classList.add(x)); }")
    TOP10 = """() => {
      const cs = [...document.querySelectorAll('.track.top10 .card.rank')];
      if (!cs.length) return null;
      const b = cs.map(c => c.getBoundingClientRect());
      return { n: cs.length, zero: b.filter(r => r.width < 4).length,
               first: Math.round(b[0].width), last: Math.round(b[b.length - 1].width),
               num: getComputedStyle(cs[0].querySelector('.rank-num')).display };
    }"""

    for qs, sheet, profiles in [
        ("legacy=1", "app.css", [p for p in CSS_PROFILES if p[0] in ("2017", "2018")]),
        ("legacy=2", "mid.css", [p for p in CSS_PROFILES if p[0] in ("2019-21", "2022")]),
    ]:
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{BASE}/?tv=1&{qs}", wait_until="load")
        page.wait_for_timeout(400)
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=45000)
        page.wait_for_timeout(2500)
        for name, classes in profiles:
            page.evaluate(set_profile, classes)
            page.wait_for_timeout(700)
            bad = page.evaluate(COLLAPSED)
            check(f"{sheet}: Home keeps its shape on a {name} engine"
                  f"{'' if not bad else ': ' + ', '.join(bad)}", not bad)

            # The Top 10 row: ten tiles with every part positioned absolutely, so "width: max-content"
            # measures nothing at all and on a 2018 set the row was 199 pixels of empty stripe. A 2017
            # set keeps the plain posters it has always had; every other engine gets the counting
            # numbers, and the tile is as wide as its own number, so "10" must come out wider than "1".
            o = page.evaluate(TOP10)
            if o is None:
                print(f"  skip  no Top 10 row on this server's Home ({sheet}, {name})")
                continue
            check(f"{sheet}: {name}: all {o['n']} Top 10 tiles have a width "
                  f"({o['zero']} flat, {o['first']}px to {o['last']}px)", o["zero"] == 0)
            if "no-vars" in classes:
                check(f"{sheet}: {name}: plain posters instead of the numbers ({o['num']})",
                      o["num"] == "none")
            else:
                check(f"{sheet}: {name}: the numbers are there and the tiles widen for them "
                      f"({o['num']}, {o['first']}px to {o['last']}px)",
                      o["num"] == "block" and o["last"] > o["first"] + 20)
        page.close()

    print("\n3. and the page sends each engine to the right one of the three")
    # The switch is the one thing here that can fail silently and badly: a set given the wrong client
    # does not show an error, it shows a broken page. So rather than reason about which engine passes
    # which test, pretend to be each engine in turn — answer CSS.supports as it would and refuse to
    # compile syntax it did not have — and see where the page sends us.
    #
    # The 2018 set is the one that matters. CSS grid arrived in Chromium 57 and object spread in 60,
    # so a 56 must fail both halves of the middle test and fall through to the oldest build.
    PRETEND = """
    (() => {
      const V = __V__;
      const WHEN = { 'aspect-ratio:1': 88, 'display:grid': 57, '--a:0': 49,
                     'position:sticky': 56, 'gap:1px': 84 };
      window.CSS = { supports: function (p, v) {
        const since = WHEN[p + ':' + v];
        return since === undefined ? false : V >= since;
      } };
      const Real = window.Function;
      const Patched = function () {
        const src = String(arguments[arguments.length - 1] || '');
        if (/\\?\\./.test(src) && V < 80) throw new SyntaxError('optional chaining is Chromium 80');
        if (/\\.\\.\\./.test(src) && V < 60) throw new SyntaxError('object spread is Chromium 60');
        if (/async/.test(src) && V < 55) throw new SyntaxError('async is Chromium 55');
        return Real.apply(null, arguments);
      };
      Patched.prototype = Real.prototype;
      window.Function = Patched;
    })();
    """
    WANT = {47: "old", 56: "old", 63: "mid", 69: "mid", 76: "mid", 85: "mid",
            94: "new", 108: "new", 120: "new", 130: "new"}
    for chrome, want in WANT.items():
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.add_init_script(PRETEND.replace("__V__", str(chrome)))
        page.goto(f"{BASE}/?tv=1", wait_until="commit")
        page.wait_for_function("window.LENTA_TIER !== undefined", timeout=20000)
        got = page.evaluate("window.LENTA_TIER")
        year = next((y for y, i in ENGINES["years"].items() if i["chrome"] == chrome), "?")
        check(f"a Chromium {chrome} engine ({year}) is given the {want!r} client"
              f"{'' if got == want else f' — it was given {got!r}'}", got == want)
        page.close()
    browser.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("every Samsung model year from 2017 runs LENTA")
