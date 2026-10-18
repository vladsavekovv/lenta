"""Settings opens at the top, and a viewer gets the Home screen the administrator arranged.

Three things, all reported together.

**Settings remembered where you were.** Leaving Settings for Home and coming back opened whichever page
you had been on last — Network, say — because the page was written into localStorage and read back on
the way in. That is a reasonable thing for a browser to do with a long form and the wrong thing for a
menu: the gear is one button, and pressing it should put you at the top of its own menu, not somewhere
in the middle of the server's settings. It now always opens on the first page of the sidebar this user
can see, which is Appearance, and it does not remember the sub-menu it had open either.

**A viewer was offered the shape of Home.** Every preference in LENTA was the signed-in person's own,
including the rows on Home: their order, their names, which libraries fed the big banner, how often it
changed. So each viewer could rearrange a Home screen of his own — which nobody wanted, and which meant
a viewer's Home bore no relation to the one the house had been shown. There is one Home screen here:
the owner's. Those preferences are read from the first administrator for everybody else, and his
Settings › Home screen offers only the one setting that is really his, the sound.

That inheritance has to be careful about permission, and the two places it could have leaked are
checked here: a section pointing at a library the viewer cannot see must come up empty rather than
showing it, and a section made of live TV channels names channels, not libraries, so the library filter
cannot see into it — a viewer with no live TV must not be shown the owner's channels row.

**And the sound is still his.** `home_trailer` is inherited as a starting point, not imposed: a viewer
who has never touched it hears what the administrator chose, and one who mutes it stays muted.

Needs /tmp/claude-0/intro/uplive.sh. It is an environment dependency, not part of the repo.
"""
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
FIXTURE = Path("/tmp/claude-0/intro")
DB = FIXTURE / "data" / "lenta.db"

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------- 1. Settings' way in
print("1. Settings does not remember the page you were on")
shell = (ROOT / "web" / "js" / "settings-shell.js").read_text()
check("nothing writes the page down", "localStorage.setItem('lenta.settingsPage'" not in shell
      and "settingsPage" not in shell)
check("the way in works the first page out of the menu rather than naming it",
      "function firstPage()" in shell and "for (const g of NAV)" in re.sub(r"//.*", "", shell))
first = shell.split("function firstPage()")[1] if "function firstPage()" in shell else ""
check("an administrator's extra groups cannot become the first page",
      "if (g.admin && !state.user?.is_admin) continue;" in first)
check("and the open sub-menu is forgotten too",
      re.search(r"opened\.clear\(\);\s*\n\s*location\.replace", shell) is not None)
check("no page named in the address still means the first one",
      re.search(r"location\.replace\(`#/settings/\$\{firstPage\(\)\}`\)", shell) is not None)

# ---------------------------------------------------------------- 2. whose preferences they are
print("\n2. the Home screen's shape is the owner's")
prefs = (ROOT / "server" / "lenta" / "api_prefs.py").read_text()
check("the owner's keys are named in one place", "OWNER_HOME = (" in prefs)
found = re.search(r"OWNER_HOME = \(([^)]*)\)", prefs, re.S)
owner_home = found.group(1) if found else ""
for key in ("home_sections", "home_row_order", "home_banner_libraries", "home_row_libraries",
            "home_banner_interval", "home_trailer_30"):
    check(f"  {key} comes from the owner", f'"{key}"' in owner_home)
check("the trailer's sound is inherited, not imposed", 'OWNER_SEEDED = ("home_trailer",)' in prefs)
check("the owner is the first administrator",
      "SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1" in prefs)
check("a viewer's own saved value is ignored for the owner's keys", "mine.pop(key, None)" in prefs)
check("and kept for the inherited one", "key in owner and key not in mine" in prefs)
check("a viewer cannot write them either", "keys -= set(OWNER_HOME)" in prefs)
check("an administrator still can", "if not user[\"is_admin\"]:" in prefs)

print("\n3. a viewer is not offered them")
settings_js = (ROOT / "web" / "js" / "settings.js").read_text()
check("the page knows who is asking", "const owns = !!state.user.is_admin;" in settings_js)
home_panel = settings_js[settings_js.index('data-panel="home"'):settings_js.index('data-panel="media"')]
check("an administrator's half of the panel is marked off", "${owns ?" in home_panel)
gate = home_panel.index("${owns ?") if "${owns ?" in home_panel else len(home_panel)
check("the sound setting is outside it", "p-home-trailer" in home_panel[:gate])
for name, what in (("p-home-30", "the 30 seconds limit"), ("p-rotate", "how often it changes"),
                   ("data-banner-lib", "which libraries the banner uses"), ("p-sections", "the sections list")):
    check(f"  {what} is only for an administrator",
          name in home_panel and home_panel.index(name) > gate)
check("a control that is not there is not an error", "$(id)?.addEventListener" in settings_js)
lib = (ROOT / "server" / "lenta" / "api_library.py").read_text()
check("a channels section needs live TV of one's own",
      'c["rule"] != "channels" or live_ok' in lib and 'live_ok = any(l["kind"] == "livetv"' in lib)

# ---------------------------------------------------------------- the test server
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("\n  skip  playwright not installed here")
    raise SystemExit(1 if fails else 0)
if not (FIXTURE / "uplive.sh").exists():
    print(f"\n  skip  no {FIXTURE}/uplive.sh: the measuring half needs the test server")
    raise SystemExit(1 if fails else 0)
subprocess.run(["bash", str(FIXTURE / "uplive.sh")], check=True, timeout=180)


def fixture():
    """An administrator with a Home screen worth inheriting, and a viewer who can see some of it.

    Returns (viewer name, a row title only the administrator's settings can explain, the library the
    viewer may see), or None if the fixture cannot provide them.
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    admin = con.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
    viewer = con.execute("SELECT id, username FROM users WHERE is_admin = 0 ORDER BY id LIMIT 1").fetchone()
    # a library with films in it: an empty one makes every section empty, and an empty section is
    # dropped, so the test would pass or fail for a reason that has nothing to do with inheritance
    movies = con.execute("SELECT l.id, l.name, COUNT(i.id) n FROM libraries l LEFT JOIN items i "
                         "ON i.library_id = l.id WHERE l.kind = 'movies' GROUP BY l.id "
                         "HAVING n >= 4 ORDER BY n DESC LIMIT 1").fetchone()
    live = con.execute("SELECT id FROM libraries WHERE kind = 'livetv' ORDER BY id LIMIT 1").fetchone()
    chan = [r["id"] for r in con.execute("SELECT id FROM tv_channels ORDER BY id LIMIT 2")] if live else []
    if not (admin and viewer and movies):
        con.close()
        return None
    # the viewer sees that one movie library and nothing else — live TV above all
    con.execute("UPDATE users SET all_libraries = 0 WHERE id = ?", (viewer["id"],))
    con.execute("DELETE FROM user_libraries WHERE user_id = ?", (viewer["id"],))
    con.execute("INSERT INTO user_libraries(user_id, library_id) VALUES(?, ?)", (viewer["id"], movies["id"]))
    # the administrator's Home: a section of his own, a renamed one, and (if there is live TV) a
    # channels section the viewer must not be shown
    custom = f'{{"id": "c-test1", "title": "Only mine", "libs": [{movies["id"]}], "rule": "shuffle", "genre": ""}}'
    chans = (f', {{"id": "c-test2", "title": "Owner channels", "libs": [{live["id"]}], "rule": "channels", '
             f'"genre": "", "channels": {chan}}}') if chan else ""
    cfg = ('{"custom": [' + custom + chans + '], "titles": {"top": "Best of the house"}, '
           '"libs": {}, "hidden": []}')
    for key, value in (("home_sections", cfg), ("home_trailer", "sound"),
                       ("home_banner_interval", "0"), ("home_trailer_30", "1")):
        con.execute("INSERT INTO user_prefs(user_id, key, value) VALUES(?, ?, ?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (admin["id"], key, value))
    # and the viewer starts with nothing of his own, plus one Home key he should not be able to keep
    con.execute("DELETE FROM user_prefs WHERE user_id = ?", (viewer["id"],))
    con.execute("INSERT INTO user_prefs(user_id, key, value) VALUES(?, 'home_row_order', '[\"mylist\"]')",
                (viewer["id"],))
    con.commit()
    con.close()
    return viewer["username"], "Only mine", bool(chan)


got = fixture()
print("\n4. measured on the real page")
if not got:
    check("a fixture with an administrator, a viewer and a movie library", False)
else:
    viewer_name, own_row, has_live = got
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        errs = []

        def sign_in(page, who):
            page.on("pageerror", lambda e: errs.append(f"{who}: {e}"))
            page.goto(BASE, wait_until="load")
            page.wait_for_timeout(400)
            page.click(f'[data-user="{who}"]')
            if page.query_selector("#lp"):
                page.fill("#lp", "secret123")
                page.click("#login button.primary")
            page.wait_for_selector(".card", timeout=45000)
            page.wait_for_timeout(800)

        # --- Settings always opens at the top
        page = b.new_page(viewport={"width": 1440, "height": 900})
        sign_in(page, "admin")
        # The order the Settings page would save after dragging the two new sections to the top: it
        # names every section, which is what makes it an order and not a few preferences.
        page.evaluate("""async () => {
          const got = await (await fetch('/api/home/sections?default=1')).json();
          const ids = got.sections.map(s => s.id);
          const mine = ids.filter(i => i.startsWith('c-test'));
          const order = [...mine, ...ids.filter(i => !mine.includes(i))];
          await fetch('/api/me/prefs', { method: 'PUT', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ home_row_order: JSON.stringify(order) }) });
        }""")
        page.wait_for_timeout(600)
        page.evaluate("location.hash = '#/settings/network'")
        page.wait_for_timeout(2500)
        check("an administrator can go straight to Network", page.evaluate("location.hash").endswith("/network"))
        page.evaluate("location.hash = '#/'")
        page.wait_for_timeout(1200)
        page.evaluate("location.hash = '#/settings'")
        page.wait_for_timeout(1800)
        landed = page.evaluate("location.hash")
        check(f"and coming back opens Appearance, not where he was ({landed})", landed.endswith("/appearance"))
        check("with no sub-menu left open",
              page.evaluate("!document.querySelector('.sset-parent[aria-expanded=true]')"))
        admin = page.evaluate("""async () => { const h = await (await fetch('/api/home')).json();
          return { ids: h.rows.map(r => r.id), titles: h.rows.map(r => r.title) }; }""")
        admin_rows = admin["titles"]
        print(f"       his: {admin['ids']}")
        page.close()

        # --- the viewer
        v = b.new_page(viewport={"width": 1440, "height": 900})
        sign_in(v, viewer_name)
        check("a viewer's Settings also opens on Appearance",
              v.evaluate("""async () => { location.hash = '#/settings';
                await new Promise(r => setTimeout(r, 1500)); return location.hash; }""").endswith("/appearance"))
        v.evaluate("location.hash = '#/settings/home'")
        v.wait_for_timeout(2500)
        shown = v.evaluate("""() => {
          const p = document.querySelector('[data-panel=home]');
          return { trailer: !!document.querySelector('#p-home-trailer'),
                   thirty: !!document.querySelector('#p-home-30'),
                   rotate: !!document.querySelector('#p-rotate'),
                   pick: !!document.querySelector('[data-banner-lib]'),
                   sections: !!document.querySelector('#p-sections'),
                   word: /Sections/.test(p ? p.textContent : '') };
        }""")
        check("he is offered the trailer's sound", shown["trailer"])
        for key, what in (("thirty", "the 30 seconds limit"), ("rotate", "how often it changes"),
                          ("pick", "the banner's libraries"), ("sections", "the sections list"),
                          ("word", "even the word Sections")):
            check(f"  not {what}", not shown[key])

        prefs = v.evaluate("async () => (await (await fetch('/api/me/prefs')).json()).prefs")
        check("the sound he has never set is the administrator's", prefs["home_trailer"] == "sound")
        check("the 30 seconds limit is the administrator's", prefs["home_trailer_30"] == "1")
        check("a Home order of his own is ignored", prefs["home_row_order"] != '["mylist"]')
        check("and it is the administrator's", "c-test1" in (prefs["home_row_order"] or ""))
        check("the administrator's own section is there", "c-test1" in (prefs["home_sections"] or ""))

        his = v.evaluate("""async () => { const h = await (await fetch('/api/home')).json();
          return { ids: h.rows.map(r => r.id), titles: h.rows.map(r => r.title) }; }""")
        rows = his["titles"]
        print(f"       and his: {his['ids']}")
        # the rows he can see, in the order the administrator put them in — not merely the same set
        check("his rows come in the administrator's order",
              his["ids"] == [i for i in admin["ids"] if i in set(his["ids"])])
        check(f"he sees the administrator's own section ({own_row})", own_row in rows)
        check("and his renamed one", "Best of the house" in rows or "Best of the house" not in admin_rows)
        if has_live:
            check("but not a channels row, with no live TV of his own", "Owner channels" not in rows)
            check("while the administrator does see it", "Owner channels" in admin_rows)

        # he can still mute it for himself, and that sticks
        v.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
          headers: { 'content-type': 'application/json' }, body: JSON.stringify({ home_trailer: 'muted' }) }); }""")
        mine = v.evaluate("async () => (await (await fetch('/api/me/prefs')).json()).prefs")
        check("muting it himself is his own to keep", mine["home_trailer"] == "muted")
        check("and changes nothing about the shape", "c-test1" in (mine["home_row_order"] or ""))
        # and writing one of the owner's keys is quietly ignored rather than refused
        wrote = v.evaluate("""async () => { const r = await fetch('/api/me/prefs', { method: 'PUT',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ home_banner_interval: '600' }) }); return { code: r.status,
          prefs: (await r.json()).prefs }; }""")
        check("asking for one of the owner's settings is not an error", wrote["code"] == 200)
        check("and does not take", wrote["prefs"]["home_banner_interval"] == "0")
        check(f"no errors on either page ({errs[:1]})", not errs)
        b.close()

    # put him back as he was found: a viewer who has never chosen, which is what another test needs
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM user_prefs WHERE key = 'home_trailer' "
                "AND user_id IN (SELECT id FROM users WHERE is_admin = 0)")
    con.commit()
    con.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
