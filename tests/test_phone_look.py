"""The phone look, and the fact that it is no longer the Netflix theme's alone.

`html.nfx` is the layout LENTA shows on a phone held upright: poster tiles with no title repeated
under them, a header carrying the page name and the libraries as chips, and a floating tab bar of
four. It was written for the Netflix theme and gated on that theme by name in `syncNfx()`.

Since 1.21.0 **HBO Max gets it too** — asked for directly: "the Android app with an HBO theme must
have exact same tile structure style as the Netflix theme". Three decisions came with that, and each
is checked here:

  **The billboard is not shared.** `nfxBillboard()` is Netflix-only, so Home on HBO keeps its own
  banner and the trailer that plays on it. That was the one thing he asked to leave alone, and it is
  why the predicate exists at all rather than everything hanging off `nfx()`.

  **The colours and the lettering stay HBO's.** The structure is shared exactly; the handful of places
  where the Netflix rules speak in red, in flat black or in bold are overridden in theme-hbo.css. A
  shared layout that repainted the theme would just be the Netflix theme under another name.

  **Two themes, not all of them.** Projector, Noir, Abyss, Velvet and Aurora keep the ordinary phone
  layout, because that is what was asked for. `PHONE_LOOK` in ui.js is the list, and adding to it is
  a deliberate act.

Needs /tmp/claude-0/intro/uplive.sh. It is an environment dependency, not part of the repo.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
PHONE = {"width": 412, "height": 915}          # a Galaxy held upright, which is what he uses
TABLET = {"width": 1024, "height": 768}
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------------------------
print("1. who gets the phone look, and who keeps the billboard")

ui = src("web/js/ui.js")
check("the themes that get it are a named list, not one theme hard-coded in the test",
      "const PHONE_LOOK = ['netflix', 'hbo'];" in ui)
check("and `syncNfx` asks that list rather than comparing to 'netflix'",
      "PHONE_LOOK.indexOf(document.documentElement.dataset.theme) >= 0" in ui
      and "dataset.theme === 'netflix' && window.matchMedia" not in ui)
check("the billboard has a predicate of its own, and it is Netflix-only",
      "export const nfxBillboard = () => nfx() && document.documentElement.dataset.theme === 'netflix';" in ui)

views = src("web/js/views.js")
check("Home's billboard is drawn from that predicate, not from the phone look",
      "if (nfxBillboard()) return nfxHero(h);" in views and "if (nfx()) return nfxHero(h);" not in views)
check("and so is the decision to run a trailer behind the banner — twice, the first draw and the swap",
      views.count("nfxBillboard()") == 3 and "if (!nfx()) startHomeTrailer" not in views)

hbo = src("web/css/theme-hbo.css")
for what, rule in [("its own background rather than the Netflix flat black",
                    'html.nfx[data-theme="hbo"] body,'),
                   ("Max Blue on a progress line, not Netflix red",
                    'html.nfx[data-theme="hbo"] .nfx-prog i { background: linear-gradient(90deg, #002BE7, #71B8F2); }'),
                   ("its own panel colour behind a tile with no artwork",
                    'html.nfx[data-theme="hbo"] .nfx-card .frame,'),
                   ("light headings, which is what this theme is",
                    'html.nfx[data-theme="hbo"] .row h2 {'),
                   ("a Recently-added badge sized for a phone tile",
                    'html.nfx[data-theme="hbo"] .nfx-card .new-badge {')]:
    check(f"the HBO theme keeps {what}", rule in hbo)
check("those overrides can win, because theme-hbo.css is loaded after theme-netflix.css",
      src("web/index.html").index("theme-netflix.css") < src("web/index.html").index("theme-hbo.css"))

# ---------------------------------------------------------------------------------------------------
print("\n2. on a real phone-sized browser")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    sync_playwright = None

if sync_playwright:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        ctx = b.new_context(viewport=PHONE, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE, wait_until="load")
        page.wait_for_timeout(600)
        page.click('[data-user="admin"]')
        if page.query_selector("#lp"):
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=45000)
        was = page.evaluate("async () => (await (await fetch('/api/me/prefs')).json()).card_style")

        def wear(theme):
            # card_style is forced because it decides portrait against landscape art on its own, and
            # this is about the layout, not about that preference.
            page.evaluate("""async (t) => { await fetch('/api/me/prefs', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: t, card_style: 'poster' }) }); }""", theme)
            page.goto(f"{BASE}/#/", wait_until="load")
            page.reload(wait_until="load")
            page.wait_for_selector(".card", timeout=30000)
            page.wait_for_timeout(1500)

        def home():
            return page.evaluate("""() => {
              const h = document.documentElement;
              const c = document.querySelector('.card');
              const f = c && c.querySelector('.frame');
              const n = c && c.querySelector('.new-badge');
              const tb = document.querySelector('.tabbar');
              const r = e => e && e.getBoundingClientRect();
              const cs = e => e && getComputedStyle(e);
              const fb = r(f), nb = r(n);
              return {
                nfx: h.classList.contains('nfx'),
                card: c ? c.className : null,
                caption: c ? !!c.querySelector('.caption') : null,
                ratio: fb ? +(fb.width / fb.height).toFixed(2) : null,
                // The header is in the page whatever the layout — CSS is what shows it — so this has
                // to ask whether it is actually on screen, not whether the markup exists.
                chips: (() => { const c = document.querySelector('.mh-chips .mh-chip');
                                return !!(c && c.getBoundingClientRect().width > 0); })(),
                headShown: (() => { const m = document.querySelector('.mhead');
                                    return m ? cs(m).display : null; })(),
                tabs: tb ? [...tb.children].filter(x => cs(x).display !== 'none').length : null,
                tabCols: tb ? cs(tb).gridTemplateColumns.split(' ').length : null,
                myLenta: tb ? tb.innerText.includes('My LENTA') : null,
                hero: !!document.querySelector('.hero'),
                billboard: !!document.querySelector('.hero.nfx-hero'),
                trailer: !!document.querySelector('.hero video, .hero iframe, .trailer-on, #home-trailer'),
                badgeFits: (nb && fb) ? nb.width <= fb.width + 1 : null,
                badgeBg: n ? cs(n).backgroundColor : null,
                badge: n ? { bg: cs(n).backgroundColor, border: cs(n).border, radius: cs(n).borderRadius,
                             tt: cs(n).textTransform, weight: cs(n).fontWeight,
                             x: Math.round(nb.left - fb.left), y: Math.round(nb.top - fb.top) } : null,
                // The rows sit a fixed distance apart in the phone layout whatever the theme: the base
                // stylesheet spaces them by viewport height and the Netflix theme by viewport width,
                // which is how HBO ended up with twice the gap once it wore the same layout.
                rowGap: (() => { const r = document.querySelectorAll('.row');
                  return r.length > 1 ? Math.round(r[1].getBoundingClientRect().top
                                                   - r[0].getBoundingClientRect().bottom) : null; })(),
                // A streaming tile carries two pictures: the poster, which fills the frame, and the
                // service's mark, which is a small badge in the corner.
                svLogo: (() => { const s = document.querySelector('.card.streaming .sv-logo');
                  const sf = s && s.closest('.frame');
                  if (!s || !sf) return null;
                  const sb = s.getBoundingClientRect(), sfb = sf.getBoundingClientRect();
                  return { w: Math.round(sb.width), frame: Math.round(sfb.width),
                           swallowsTile: sb.width / sfb.width > 0.8 }; })(),
                progBg: (() => { const p = document.querySelector('.nfx-prog i');
                                 return p ? cs(p).backgroundImage || cs(p).backgroundColor : null; })(),
                bodyBg: cs(document.body).backgroundImage.slice(0, 28),
                h2weight: (() => { const x = document.querySelector('.row h2'); return x ? cs(x).fontWeight : null; })(),
              };
            }""")

        print("\n  the Netflix theme — unchanged, and the thing being matched")
        wear("netflix")
        n = home()
        check("it has the phone look", n["nfx"] is True)
        check(f"its tiles are nfx cards ({n['card']})", "nfx-card" in (n["card"] or ""))
        check("with no title repeated under the poster", n["caption"] is False)
        check(f"a 2:3 poster ({n['ratio']})", abs((n["ratio"] or 0) - 0.67) < 0.03)
        check(f"the chips header, actually on screen ({n['headShown']})",
              n["chips"] is True and n["headShown"] == "block")
        check(f"a tab bar of four ({n['tabs']} shown, {n['tabCols']} columns)",
              n["tabs"] == 4 and n["tabCols"] == 4)
        check("its own framed billboard", n["billboard"] is True)
        check("and red on the Recently-added badge", n["badgeBg"] == "rgb(229, 9, 20)")

        print("\n  the HBO theme — the same layout, its own voice")
        wear("hbo")
        h = home()
        check("it now has the phone look too", h["nfx"] is True)
        check(f"its tiles are the same nfx cards ({h['card']})", h["card"] == n["card"])
        check("with no title repeated under the poster either", h["caption"] is False)
        check(f"the same 2:3 poster ({h['ratio']})", h["ratio"] == n["ratio"])
        check(f"the same chips header, actually on screen ({h['headShown']})",
              h["chips"] is True and h["headShown"] == n["headShown"])
        check(f"the same tab bar of four ({h['tabs']} shown, {h['tabCols']} columns)",
              h["tabs"] == 4 and h["tabCols"] == 4)
        check("named 'My LENTA', as the phone layout names it", h["myLenta"] is True and n["myLenta"] is True)

        check("but NOT the Netflix billboard — it keeps its own banner", h["hero"] is True and h["billboard"] is False)
        check("and the trailer still runs on it, which is what he asked to leave alone",
              h["trailer"] is True and n["trailer"] is False)

        check(f"its progress line is Max Blue, not Netflix red ({str(h['progBg'])[:46]})",
              h["progBg"] is not None and "229, 9, 20" not in str(h["progBg"])
              and ("0, 43, 231" in str(h["progBg"]) or "002BE7" in str(h["progBg"])))
        # The badge is the theme's, not the layout's — he asked for "the same style as from the web gui
        # version" after 1.21.0 gave it the Netflix tiles' red/blue bottom tab. So it is compared
        # against this very theme on a desktop-width browser further down, token by token.
        check(f"its Recently-added badge is this theme's dark pill, not a coloured tab "
              f"({h['badge']['bg']}, {h['badge']['radius']})",
              h["badge"]["bg"] == "rgba(0, 0, 0, 0.62)" and h["badge"]["radius"] == "100px")
        check(f"in the top-left corner, where this theme puts it ({h['badge']['x']},{h['badge']['y']})",
              h["badge"]["x"] <= 10 and h["badge"]["y"] <= 10)
        check("and the Netflix theme keeps its own, which is the red tab along the bottom edge",
              n["badge"]["bg"] == "rgb(229, 9, 20)")
        check(f"the badge fits the tile rather than wrapping across its corner "
              f"(it is what forced the size down)", h["badgeFits"] is True)

        check(f"the rows sit the same distance apart in both themes ({h['rowGap']} vs {n['rowGap']}px)",
              h["rowGap"] is not None and h["rowGap"] == n["rowGap"])

        # Not a difference between the themes — it was wrong in both, from the day the phone look was
        # written. `html.nfx .nfx-card .frame img` out-specifies the 28px badge rule in app.css and
        # stretched the service's mark over the whole tile: a row of giant Netflix N's where the
        # posters should be. He saw it on HBO because that is the theme he runs.
        for name, d in (("HBO", h), ("Netflix", n)):
            if d["svLogo"]:
                check(f"[{name}] a streaming tile's service mark is a badge, not the whole tile "
                      f"({d['svLogo']['w']}px on a {d['svLogo']['frame']}px tile)",
                      d["svLogo"]["swallowsTile"] is False)
            else:
                check(f"[{name}] there is a streaming row on Home to check the service mark on", False)
        check("the page keeps this theme's own glow rather than the Netflix flat black",
              "radial-gradient" in (h["bodyBg"] or ""))
        check(f"and its headings stay light ({h['h2weight']} against Netflix's {n['h2weight']})",
              h["h2weight"] == "400" and n["h2weight"] == "700")

        print("\n  the themes that were not asked for, and the screen sizes it is not for")
        wear("abyss")
        a = home()
        check("another theme on the same phone does NOT get the phone look", a["nfx"] is False)
        check("and keeps the title under its cards", a["caption"] is True)

        page.set_viewport_size(TABLET)
        for theme in ("netflix", "hbo"):
            wear(theme)
            t = page.evaluate("() => document.documentElement.classList.contains('nfx')")
            check(f"on a tablet the {theme} theme does not get it either", t is False)

        # "I want the same style as from the web gui version" — so the phone badge is held against
        # this theme's badge on a wide browser, which is the web interface he means. Everything but
        # the size has to match: the size is smaller on purpose, because the desktop pill is ~126px
        # against a 116px tile and wrapped.
        desk = page.evaluate("""() => {
          const n = [...document.querySelectorAll('.new-badge')].find(x => x.getBoundingClientRect().width > 0);
          if (!n) return null;
          const f = n.closest('.frame'), cs = getComputedStyle(n);
          const nb = n.getBoundingClientRect(), fb = f.getBoundingClientRect();
          return { bg: cs.backgroundColor, border: cs.border, radius: cs.borderRadius,
                   tt: cs.textTransform, weight: cs.fontWeight,
                   x: Math.round(nb.left - fb.left), y: Math.round(nb.top - fb.top) };
        }""")
        check("the web interface shows this theme's badge to compare against", desk is not None)
        if desk and h["badge"]:
            for token in ("bg", "border", "radius", "tt", "weight", "x", "y"):
                check(f"the phone badge matches the web interface on {token} "
                      f"({h['badge'][token]} vs {desk[token]})", h["badge"][token] == desk[token])
        page.set_viewport_size(PHONE)

        check("no page errors anywhere in all that", not errs)
        if errs:
            for e in errs[:4]:
                print(f"        {e}")
        # Put the fixture back as it was found.
        page.evaluate("""async (cs) => { await fetch('/api/me/prefs', { method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ theme: '', card_style: cs || 'wide' }) }); }""", was)
        b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
