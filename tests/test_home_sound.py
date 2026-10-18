"""The Home banner trailer's sound: it stops when you scroll away, and it does not come back by itself.

Two faults, both reported from the sofa and both about sound arriving when nobody asked for it.

**It came back after an edit.** Scrolled down the Home page, the banner's trailer had gone quiet, as it
should. Opening a tile's editor and pressing Save or Cancel brought the sound back — quieter than
before, but there. The reason was not the editor: closing it redraws Home, a redraw builds a new
banner trailer, and a trailer player starts playing the instant it is made. `sync()` then decided
whether it should be playing, and its "stop it" branch was written as `else if (this.playing)` — only
ever pausing a player it believed it had started itself. The new one it had not started, so nothing
paused it. It played on, off screen. On a video file that is silent, because the volume is set to 0 as
it is made; on a YouTube embed it is audible, because the commands that mute it and set its volume are
`postMessage`s into an iframe that is not listening yet, and one sent too early is dropped with no
error of any kind. YouTube's own volume is what you then hear.

So two things are checked here. `sync()` drives the player to silence whether or not it thinks it
started it; and the embed's commands wait until the embed has spoken to us, which is the only proof it
is listening.

**And it faded with the scroll.** The volume used to follow how much of the banner was still on
screen, which is why "it stops" and "it is quiet" were hard to tell apart. It is full volume while the
banner is in view and nothing when it is not: it fades out, pauses, and goes on from the same moment
when you come back.

The third thing: a viewer's banner was always silent, whatever the administrator had chosen, because a
viewer who has never opened Settings has no preferences at all and fell through to the muted default.
See test_viewer_home.py for the rest of that; the sound itself is measured here.

Needs /tmp/claude-0/intro/uplive.sh, the test server used by the other Playwright tests. It is an
environment dependency, not part of the repo; without it the measuring half cannot run.
"""
import json
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


# ---------------------------------------------------------------- 1. the code
print("1. sync() stops a player it did not start")
src = (ROOT / "web" / "js" / "extras.js").read_text()


# There is more than one trailer controller in this file (a media page has its own, and so has theme
# music), and they have methods of the same names. Ask the Home banner's class, not the file.
HOME = src[src.index("class HomeTrailer {"):]
HOME = HOME[:HOME.index("\nconst FADE_END_S")]


def body(name, lines=40, where=HOME):
    """The first `lines` lines of a method, to ask questions of one method rather than the file."""
    m = re.search(rf"^  {re.escape(name)}\(", where, re.M)
    if not m:
        return ""
    return "\n".join(where[m.start():].split("\n")[:lines])


def code(text):
    """The same text with its comments taken out, so a comment quoting the old code cannot answer for it."""
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


sync = body("sync")
check("there is a sync()", bool(sync))
# The fault, exactly: a stop branch that only runs when it believes it started the player.
check("the stop branch is not conditional on having started it",
      "else if (this.playing)" not in code(sync))
check("it pauses and silences in the same branch",
      re.search(r"\} else \{", code(sync)) is not None and "p.pause()" in sync)
check("a player it did not start is stopped at once, with no fade", "!was" in code(sync))
check("it measures the banner itself rather than trusting the last observation",
      "this.seen = this.onScreen()" in code(sync))
check("there is an onScreen() that asks the page",
      "getBoundingClientRect()" in body("onScreen", 12))
check("a banner that is not laid out counts as off screen", "offsetParent" in body("onScreen", 12))
check("a new controller does not assume it is in view", "this.inView = false;" in HOME)
check("the volume is full in view and nothing out of it",
      re.search(r"return this\.inView \? 80 : 0;", HOME) is not None)
check("the last seconds before the 30 seconds limit still fade",
      "this.limit && (this.played || 0) >= this.limit - FADE_END_S" in body("level", 6))
check("the observer only asks sync() to look again",
      re.search(r"new IntersectionObserver\(\(\) => this\.sync\(\)", HOME) is not None)

print("\n2. commands to a YouTube embed are not thrown away")
check("they are queued until it answers", "const queued = []" in src and "const cmd = (func" in src)
check("nothing is sent before it has spoken to us", re.search(r"if \(ready\) ytCommand", src) is not None)
check("anything at all from the embed counts as an answer", "onAny?.()" in src)
check("the mute and the volume are said again at that moment",
      re.search(r"ytCommand\(f, isMuted \? 'mute' : 'unMute'\);\s*\n\s*sent = -1;", src) is not None)
check("play, pause and mute all go through the queue",
      re.search(r"play: \(\) => cmd\('playVideo'\)", src) is not None
      and re.search(r"pause: \(\) => cmd\('pauseVideo'\)", src) is not None
      and re.search(r"setMuted: \(m\) => \{ isMuted = m; cmd\(m \? 'mute' : 'unMute'\)", src) is not None)
check("loading another video starts listening again", "ready = false; queued.length = 0; sent = -1;" in src)

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


def banner_fixture():
    """One film in the banner, with a trailer that is a local file so its sound can be measured.

    A YouTube embed cannot be measured from here (no network, and nothing to read the volume of), so
    the banner is given a short video file made with ffmpeg. Returns the item id, or None.
    """
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT i.id, i.title, f.path FROM items i JOIN files f ON f.item_id = i.id "
                      "WHERE i.kind = 'movie' ORDER BY i.id LIMIT 1").fetchone()
    if not row:
        con.close()
        return None
    folder = Path(row["path"]).parent
    trailer = folder / "trailer.webm"
    if not trailer.exists():
        try:
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=20",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                            "-c:v", "libvpx", "-b:v", "300k", "-c:a", "libvorbis", str(trailer)],
                           capture_output=True, timeout=180, check=True)
        except (OSError, subprocess.SubprocessError):
            con.close()
            return None
    # this film is the banner, and it is the only one that can be
    con.execute("UPDATE items SET extra = json_remove(COALESCE(extra, '{}'), '$.home_trailer') "
                "WHERE json_extract(extra, '$.home_trailer') IS NOT NULL")
    extra = json.loads(con.execute("SELECT COALESCE(extra, '{}') e FROM items WHERE id = ?",
                                   (row["id"],)).fetchone()[0] or "{}")
    extra.update({"home_trailer": True, "trailer": f"v:/api/items/{row['id']}/trailer-file",
                  "trailer_name": "Local trailer"})
    con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(extra), row["id"]))
    for key, value in (("home_trailer", "sound"), ("home_banner_interval", "0"), ("home_trailer_30", "0")):
        con.execute("INSERT INTO user_prefs(user_id, key, value) SELECT id, ?, ? FROM users WHERE is_admin = 1 "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (key, value))
    # The viewer below is meant to be someone who has never opened Settings, so take away any choice of
    # his own about the sound. Said rather than assumed: another test signs in as him and mutes it, and
    # without this the two would pass or fail depending on which ran first.
    con.execute("DELETE FROM user_prefs WHERE key = 'home_trailer' "
                "AND user_id IN (SELECT id FROM users WHERE is_admin = 0)")
    con.commit()
    con.close()
    return row["id"]


STATE = """() => {
  const v = document.querySelector('.hero-trailer video');
  const hero = document.querySelector('.hero');
  return v ? { has: true, paused: v.paused, muted: v.muted, volume: Math.round(v.volume * 100) / 100,
               t: Math.round(v.currentTime * 10) / 10, trailer: hero && hero.dataset.trailer }
           : { has: false, trailer: hero && hero.dataset.trailer };
}"""

item = banner_fixture()
print(f"\n3. measured on the real page (banner item {item})")
if not item:
    check("a film with a trailer file to measure", False)
else:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"])
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE, wait_until="load")
        page.wait_for_timeout(400)
        page.click('[data-user="admin"]')
        if page.query_selector("#lp"):
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=45000)
        try:
            page.wait_for_selector(".hero-trailer video", timeout=30000)
            page.wait_for_timeout(4000)
            got = True
        except Exception:
            got = False
        check("the banner plays its trailer", got)
        if got:
            on = page.evaluate(STATE)
            check(f"with sound ({on['volume']})", not on["muted"] and on["volume"] > 0.5)

            page.evaluate("window.scrollTo({ top: 1600, behavior: 'instant' })")
            page.wait_for_timeout(2500)
            away = page.evaluate(STATE)
            check(f"scrolled away it is paused and silent ({away['paused']}, {away['volume']})",
                  away["paused"] and away["volume"] == 0)
            page.wait_for_timeout(2500)
            still = page.evaluate(STATE)
            check(f"and it stays where it stopped ({away['t']} -> {still['t']})",
                  abs(still["t"] - away["t"]) < 0.4)

            # the fault: closing a tile's editor redraws Home, which builds a new player
            page.evaluate("""async () => {
              const mod = await import('/js/app.js');
              mod.refreshView && mod.refreshView();
            }""")
            page.wait_for_timeout(3000)
            redrawn = page.evaluate(STATE)
            page.wait_for_timeout(3000)
            later = page.evaluate(STATE)
            check("a redraw while scrolled away leaves nothing playing",
                  not later["has"] or later["paused"])
            check(f"and nothing to hear ({later.get('volume')})",
                  not later["has"] or later["muted"] or later["volume"] == 0)
            check(f"the picture does not advance off screen ({redrawn.get('t')} -> {later.get('t')})",
                  not later["has"] or abs(later["t"] - redrawn["t"]) < 0.4)

            page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
            page.wait_for_timeout(2500)
            backon = page.evaluate(STATE)
            check(f"scrolling back brings it to full volume ({backon.get('volume')})",
                  backon["has"] and not backon["paused"] and backon["volume"] > 0.5)

            # away and back again, with nothing else happening: it goes on from the same moment
            page.evaluate("window.scrollTo({ top: 1600, behavior: 'instant' })")
            page.wait_for_timeout(2200)
            off = page.evaluate(STATE)
            page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
            page.wait_for_timeout(2500)
            again = page.evaluate(STATE)
            check(f"it continues rather than starting over ({off['t']} -> {again['t']})",
                  again["has"] and again["t"] + 0.5 >= off["t"])

        # a viewer who has never opened Settings hears what the administrator chose
        v = b.new_page(viewport={"width": 1440, "height": 900})
        v.on("pageerror", lambda e: errs.append(str(e)))
        v.goto(BASE, wait_until="load")
        v.wait_for_timeout(400)
        who = v.query_selector('[data-user="guest"]')
        if not who:
            print("  skip  no viewer in the fixture")
        else:
            who.click()
            if v.query_selector("#lp"):
                v.fill("#lp", "secret123")
                v.click("#login button.primary")
            v.wait_for_selector(".card", timeout=45000)
            try:
                v.wait_for_selector(".hero-trailer video", timeout=30000)
                v.wait_for_timeout(4500)
                vs = v.evaluate(STATE)
            except Exception:
                vs = {"has": False}
            check("a viewer's banner trailer plays", vs["has"])
            if vs["has"]:
                check(f"and starts with sound, as the administrator chose ({vs.get('volume')})",
                      not vs["muted"] and vs["volume"] > 0.5)
        check(f"no errors on the page ({errs[:1]})", not errs)
        b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
