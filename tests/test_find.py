"""Starting a film by voice: `/api/find`, `/api/now-playing`, `/api/items/{id}/playable`, `#/pick` (1.24.0).

Asked for by the voice assistant on skynet, built with Vlad's go on 31 Oct 2026. The thing this file
exists to hold is **the rule, not the matching**: TMDB's terms forbid using their content with an AI
application, and every title, overview, genre and translated name LENTA shows came from TMDB. So the
assistant sends its words here, the server does the comparing, and the answer is ids, counts and flags.
Section 3 asks every endpoint the assistant uses and fails on any title, or any key or word it does not
expect, in what comes back. **If a field is ever added to those answers, it has to be added to the
lists below on purpose** — which is the moment to ask whether it came from TMDB.

What else is held here:
  1. the word handling on its own — numbers spoken as words, Roman numerals and Bulgarian ordinals all
     become digits, so "Rocky two", "Rocky II" and "Роки втора част" are one film;
  2. matching against a library made for this test (and removed after): a sequel is not its first film,
     one film kept twice is not a choice, two films of one name are, "the original" picks the earlier,
     a show gives its next episode, a spoken season and episode give that one, a year narrows;
  4. permissions: a library the account cannot see finds nothing, plays nothing and draws nothing, and
     another account's playback is not reported;
  5. the names in other languages (`extra.aka`) are used for matching and **never** sent to a client;
  6. **the player fault the assistant found**: switching from `#/play/A` to `#/play/B` before A had
     finished starting let A's answer into B's player and left a conversion running for nobody. Shown
     by holding A's start back in the browser, which is how a slow conversion on his server behaves;
  7. `#/pick?ids=` draws the titles in the order given, numbered, and choosing one plays it.

Needs /tmp/claude-0/intro/uplive.sh. It is an environment dependency, not part of the repo.
"""
import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
DB = Path("/tmp/claude-0/intro/data/lenta.db")
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ROOT / "server"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


class Client:
    """A signed-in account. Every answer is read defensively: on an older server these endpoints do not
    exist, and that has to show as red checks rather than as a traceback."""

    def __init__(self, user):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.ok = self.post("/api/auth/login", {"username": user, "password": "secret123"})[0] == 200

    def _do(self, req):
        try:
            with self.op.open(req, timeout=60) as r:
                body = r.read().decode()
                return r.status, body
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")
        except Exception as e:                       # noqa: BLE001 — a dead server is a failed check
            return 0, str(e)

    def post(self, path, data):
        return self._do(urllib.request.Request(BASE + path, data=json.dumps(data).encode(), method="POST",
                                               headers={"Content-Type": "application/json"}))

    def raw(self, path):
        return self._do(urllib.request.Request(BASE + path))

    def get(self, path):
        code, body = self.raw(path)
        try:
            return json.loads(body) if code == 200 else {}
        except ValueError:
            return {}


# ---------------------------------------------------------------------------------------------------
print("1. spoken words become comparable words")

try:
    from lenta import find as F
except Exception as e:                                # noqa: BLE001
    print(f"  (the find module does not load: {e})")
    F = None

check("there is a find module on the server", F is not None)
if F:
    check("'Rocky two', 'Rocky II' and 'Rocky 2' are the same words",
          F.tokens("Rocky two") == F.tokens("Rocky II") == F.tokens("Rocky 2") == ["rocky", "2"])
    check("Bulgarian ordinals too: 'Роки втора част' → роки 2", F.tokens("Роки втора част") == ["роки", "2"])
    check("articles and 'part' carry no title", F.tokens("The Lord of the Rings Part One") ==
          ["lord", "of", "rings", "1"])
    check("accents and punctuation do not matter", F.tokens("Amélie!") == F.tokens("amelie"))
    check("'v' and 'x' stay letters ('Malcolm X' is not 'Malcolm 10')", F.tokens("Malcolm X") == ["malcolm", "x"])
    check("an exact name scores highest", F.score_name(["rocky"], ["rocky"]) == 100)
    check("a sequel is not its first film: 'rocky' does not match 'rocky 2'",
          F.score_name(["rocky"], ["rocky", "2"]) == 0)
    check("but a longer title still matches: 'rocky' fits 'rocky balboa'",
          0 < F.score_name(["rocky"], ["rocky", "balboa"]) < 100)
    check("a misheard long word still matches ('inceptoin')", F.score_name(["inceptoin"], ["inception"]) > 0)
    check("a short word must be exact ('ups' is not 'up')", F.score_name(["ups"], ["up"]) == 0)
    check("every spoken word has to be in the name", F.score_name(["rocky", "horror"], ["rocky"]) == 0)
    tr = {"translations": {"translations": [{"data": {"title": "Роки"}}, {"data": {"title": "Rocky"}},
                                            {"data": {"name": "Роки"}}, {"data": {"title": ""}}]}}
    from lenta.metadata import _aka
    check("TMDB's translations become one list of names, without repeats", _aka(tr) == ["Роки", "Rocky"])

meta = src("server/lenta/metadata.py")
check("a film's details ask TMDB for its translations", "keywords,translations\"" in meta
      and meta.count(",translations\"") == 2)
check("and both films and shows keep them as `aka`", meta.count('"aka": _aka(d)') == 2)

# ---------------------------------------------------------------------------------------------------
print("\n2. matching against a library made for this test")

subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

SCRATCH = "Find test (temporary)"
SHOWS = "Find test shows (temporary)"
CLIPS = Path(tempfile.mkdtemp(prefix="lenta-find-"))
made = {}
saved_guest = None
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row


def tidy():
    for name in (SCRATCH, SHOWS):
        con.execute("DELETE FROM items WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (name,))
        con.execute("DELETE FROM files WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (name,))
        con.execute("DELETE FROM libraries WHERE name = ?", (name,))
    con.commit()


tidy()
admin_id = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
guest_id = con.execute("SELECT id FROM users WHERE username = 'guest'").fetchone()[0]
lib = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'movies', '[]')", (SCRATCH,)).lastrowid
slib = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'shows', '[]')", (SHOWS,)).lastrowid


def item(key, title, year=None, kind="movie", library=None, parent=None, aka=None, tmdb=None, genres=None,
         season=None, number=None, file=True, missing=False):
    extra = {"parsed_title": title}
    if aka:
        extra["aka"] = aka
    iid = con.execute(
        "INSERT INTO items(library_id, kind, parent_id, key, title, sort_title, year, tmdb_id, genres, extra, "
        "parent_index, index_number, added_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (library or lib, kind, parent, f"findtest:{key}", title, title, year, tmdb,
         json.dumps(genres or ["Comedy"]), json.dumps(extra), season, number, time.time())).lastrowid
    if file:
        path = CLIPS / f"{key}.mp4"
        if not missing:
            path.write_bytes(b"")
        con.execute("INSERT INTO files(item_id, library_id, path) VALUES(?,?,?)", (iid, library or lib, str(path)))
    made[key] = iid
    return iid


item("z1", "Zorblax", 1976, aka=["Зорблакс"], genres=["Drama"])
item("z2", "Zorblax II", 1979, aka=["Зорблакс II"])
item("z3", "Zorblax Returns", 2006)
item("tw1", "Quillfeather Twin", 2001, tmdb=990001)
item("tw2", "Quillfeather Twin", 2001, tmdb=990001)             # the same film, kept twice
item("sn1", "Vellichor", 1990)
item("sn2", "Vellichor", 2015)                                   # a remake: a real choice
item("gone", "Brindlemoor", 2012, missing=True)
show = item("show", "Marrowgate", 2020, kind="show", library=slib, file=False)
s1 = item("s1", "Season 1", kind="season", library=slib, parent=show, season=1, file=False)
e1 = item("e1", "Pilot", kind="episode", library=slib, parent=s1, season=1, number=1)
e2 = item("e2", "Second", kind="episode", library=slib, parent=s1, season=1, number=2)
e3 = item("e3", "Third", kind="episode", library=slib, parent=s1, season=1, number=3)
# The admin finished the pilot and watched part of one copy of the twin.
con.execute("INSERT OR REPLACE INTO watch_state(user_id, item_id, position, duration, completed, updated_at) "
            "VALUES(?,?,0,100,1,?)", (admin_id, e1, time.time()))
con.execute("INSERT OR REPLACE INTO watch_state(user_id, item_id, position, duration, completed, updated_at) "
            "VALUES(?,?,40,100,0,?)", (admin_id, made["tw2"], time.time()))
con.commit()
check("a library of invented titles, made for this test", len(made) == 13)

admin = Client("admin")
check("the administrator signs in", admin.ok)


def find(**params):
    return admin.get("/api/find?" + urllib.parse.urlencode(params))


r = find(q="zorblax")
check(f"'zorblax' is the first film, not a sequel ({r.get('item_id')})", r.get("item_id") == made["z1"])
check("and that is one answer, not a question", r.get("ambiguous") is False and r.get("matches") == 2)
check("'zorblax two' is the sequel", find(q="zorblax two").get("item_id") == made["z2"])
check("'Zorblax II' is the sequel", find(q="Zorblax II").get("item_id") == made["z2"])
check("'Зорблакс втора част' is the sequel, through its Bulgarian name",
      find(q="Зорблакс втора част").get("item_id") == made["z2"])
check("'Зорблакс' alone is the first", find(q="Зорблакс").get("item_id") == made["z1"])
check("'zorblax returns' is the third", find(q="zorblax returns").get("item_id") == made["z3"])
check("a misheard word still finds it ('zorblax retunrs')", find(q="zorblax retunrs").get("item_id") == made["z3"])

r = find(q="quillfeather twin")
check("one film kept twice is not a choice", r.get("ambiguous") is False and r.get("matches") == 1)
check("and the copy chosen is the one being watched", r.get("item_id") == made["tw2"])
check("which it says it will resume", r.get("action") == "resume")

r = find(q="vellichor")
check("two films of one name are a choice", r.get("ambiguous") is True and r.get("item_id") is None)
check("and the choice is given as ids, oldest first",
      r.get("candidate_ids") == [made["sn1"], made["sn2"]] and r.get("matches") == 2)
check("'the original vellichor' is the older", find(q="the original vellichor").get("item_id") == made["sn1"])
check("'vellichor 2015' is the newer", find(q="vellichor 2015").get("item_id") == made["sn2"])
check("a year nothing has finds nothing", find(q="vellichor 1950").get("item_id") is None)
check("'random' between the two picks one of them",
      find(q="vellichor", random=1).get("item_id") in (made["sn1"], made["sn2"]))
check("excluding one leaves the other", find(q="vellichor", exclude=str(made["sn1"])).get("item_id") == made["sn2"])

r = find(q="marrowgate")
check(f"a show gives its next episode — the pilot is watched, so the second ({r.get('item_id')})",
      r.get("item_id") == e2 and r.get("kind") == "episode" and r.get("show_id") == show)
check("a spoken season and episode give that one", find(q="marrowgate", season=1, episode=3).get("item_id") == e3)
r = find(q="marrowgate", season=4, episode=1)
check("an episode that is not there says so, with no id", r.get("item_id") is None and r.get("action") == "missing")
check("'kind=film' does not answer with a show", find(q="marrowgate", kind="film").get("item_id") is None)
check("'kind=show' does not answer with a film", find(q="zorblax", kind="show").get("item_id") is None)
check("words that fit nothing find nothing", find(q="qwxzv").get("matches") == 0)

r = find(random=1, genre="драма", kind="film")
check("a Bulgarian genre narrows a random pick ('драма' → Drama)", r.get("item_id") is not None and
      "Drama" in json.loads(con.execute("SELECT genres FROM items WHERE id = ?", (r.get("item_id") or 0,))
                            .fetchone()[0] if r.get("item_id") else "[]"))
picks = {find(random=1, unwatched=1, kind="film").get("item_id") for _ in range(40)}
watched_film = con.execute("SELECT item_id FROM watch_state WHERE user_id = ? AND completed = 1",
                           (admin_id,)).fetchall()
check("an unwatched random pick never picks a finished film",
      None not in picks and not picks & {w[0] for w in watched_film})

# ---------------------------------------------------------------------------------------------------
print("\n3. the answers carry ids, counts and flags — never a title")

# "players" added on purpose in 1.25.0 (player control): a list of the account's own devices with ids,
# states, seconds and the names Vlad gave them — nothing from TMDB. test_players.py checks its insides.
# "channel_id" added on purpose in 1.26.0: find can name a live channel ("put Nova on"); null otherwise.
# A channel's name is Vlad's playlist's or the broadcaster's, and it is not in this answer either.
KEYS = {"item_id", "kind", "show_id", "action", "matches", "ambiguous", "candidate_ids", "state", "playable",
        "players", "channel_id"}
WORDS = {None, "movie", "episode", "start", "resume", "next", "rewatch", "missing",
         "playing", "paused", "buffering", "ended", "stopped"}
titles = {r[0] for r in con.execute("SELECT title FROM items WHERE title IS NOT NULL AND length(title) > 3")}
answers = [find(q="zorblax"), find(q="vellichor"), find(q="marrowgate"), find(random=1),
           find(random=1, genre="Drama"), admin.get("/api/now-playing"),
           admin.get(f"/api/items/{made['z1']}/playable"), admin.get(f"/api/items/{show}/playable")]
seen_keys = set().union(*(a.keys() for a in answers))
stray = [(k, v) for a in answers for k, v in a.items()
         if isinstance(v, str) and v not in WORDS or isinstance(v, (dict,))]
check(f"every answer came back ({sum(1 for a in answers if a)} of {len(answers)})", all(answers))
check(f"no key beyond the agreed ones ({sorted(seen_keys - KEYS)})", seen_keys <= KEYS)
check(f"no string beyond the agreed words ({stray[:3]})", not stray)
text = json.dumps(answers, ensure_ascii=False)
check("and no title of anything in the library appears in them", not any(t in text for t in titles))

# ---------------------------------------------------------------------------------------------------
print("\n4. what an account may not see, it cannot find, play or be told about")

saved_guest = [r[0] for r in con.execute("SELECT library_id FROM user_libraries WHERE user_id = ?", (guest_id,))]
con.execute("DELETE FROM user_libraries WHERE user_id = ?", (guest_id,))
con.execute("INSERT INTO user_libraries(user_id, library_id) VALUES(?, 1)", (guest_id,))
con.commit()
guest = Client("guest")
check("the viewer signs in (and sees only library 1 for this section)", guest.ok)
check("the viewer finds nothing in a library it cannot see",
      guest.get("/api/find?q=zorblax").get("matches") == 0)
check("not even at random", guest.get(f"/api/find?random=1&exclude=").get("item_id") not in made.values())
check("and is told it cannot play it — the same answer as an id that does not exist",
      guest.get(f"/api/items/{made['z1']}/playable") == guest.get("/api/items/987654321/playable")
      == {"playable": False, "item_id": None, "kind": None})
check("the pick page leaves it out", guest.get(f"/api/pick?ids={made['z1']}").get("titles") == [])
code = 0
try:
    urllib.request.urlopen(BASE + "/api/find?q=zorblax", timeout=10)
except urllib.error.HTTPError as e:
    code = e.code
check(f"a request without a sign-in is refused ({code})", code == 401)

play = f"np-{int(time.time())}"
admin.post("/api/progress", {"item_id": made["z1"], "play_id": play, "position": 30, "duration": 100,
                                     "state": "playing", "mode": "direct", "quality": "original"})
# Only the two fields this check is about: 1.25.0 added "players" beside them (section 3 lists it).
np = admin.get("/api/now-playing")
check("the administrator's own playback is reported by id",
      {k: np.get(k) for k in ("item_id", "state")} == {"item_id": made["z1"], "state": "playing"})
check("and is not reported to another account", guest.get("/api/now-playing").get("item_id") is None)
admin.post("/api/progress", {"item_id": made["z1"], "play_id": play, "position": 30, "duration": 100,
                                     "state": "stopped", "mode": "direct", "quality": "original"})
# Not "nothing is playing": a browser closed less than a minute ago (section 6 of the last run) is
# still on the server's list, and that is right — the list forgets after sixty seconds of silence.
check("once stopped, it is no longer reported", admin.get("/api/now-playing").get("item_id") != made["z1"])
con.execute("DELETE FROM watch_state WHERE item_id IN (%s)" % ",".join(str(v) for v in made.values()))
con.commit()

r = admin.get(f"/api/items/{made['gone']}/playable")
check("a film whose file has gone from the disk is not playable", r.get("playable") is False)
r = admin.get(f"/api/items/{made['z1']}/playable")
check("a film that is there is, and says which id to play",
      r == {"playable": True, "item_id": made["z1"], "kind": "movie"})
r = admin.get(f"/api/items/{show}/playable")
check("a show is, through its next episode", r.get("playable") is True and r.get("item_id") == e1)

# ---------------------------------------------------------------------------------------------------
print("\n5. names in other languages are used to match, and kept off every page")

d = admin.get(f"/api/items/{made['z1']}")
check("the title's own page does not carry them", d and "aka" not in (d.get("extra") or {}) and
      "Зорблакс" not in json.dumps(d, ensure_ascii=False))
check("the source says why", "never sent anywhere" in src("server/lenta/metadata.py"))

# ---------------------------------------------------------------------------------------------------
print("\n6. switching films before the first has started does not let the first in")

player = src("web/js/player.js")
check("the load count is shared by every player the page opens", "const seq = P.loadSeq = ++loads;" in player)
check("a superseded start is handed back to the server", "Superseded while the server was starting it" in player)

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
    print("  skip  playwright not installed here")

FILM_A, FILM_B = 41, 45          # two fixture films with real clips behind them


async def race():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"])
        pg = await b.new_page()
        answers, hls = {}, []

        async def hold_a(route, req):
            item_id = json.loads(req.post_data or "{}").get("item_id")
            resp = await route.fetch()
            try:
                answers[item_id] = json.loads(await resp.text()).get("session_id")
            except ValueError:
                pass
            if item_id == FILM_A:
                await asyncio.sleep(8)          # A is a conversion that takes a while to start
            await route.fulfill(response=resp)

        await pg.route("**/api/playback", hold_a)
        pg.on("request", lambda r: hls.append((r.method, r.url)) if "/api/hls/" in r.url else None)
        await pg.goto(BASE, wait_until="load")
        await pg.wait_for_timeout(400)
        await pg.click('[data-user="admin"]')
        if await pg.query_selector("#lp"):
            await pg.fill("#lp", "secret123")
            await pg.click("#login button.primary")
        await pg.wait_for_selector(".card", timeout=45000)
        await pg.evaluate(f"location.hash = '#/play/{FILM_A}'")
        await pg.wait_for_timeout(1000)
        await pg.evaluate(f"location.hash = '#/play/{FILM_B}'")
        await pg.wait_for_timeout(16000)
        playing = await pg.evaluate("() => { const v = document.querySelector('#pv'); "
                                    "return v ? {paused: v.paused, t: v.currentTime} : null; }")
        await b.close()
        return answers, hls, playing


if async_playwright:
    answers, hls, playing = asyncio.run(race())
    a, b_ = answers.get(FILM_A), answers.get(FILM_B)
    check("both films were asked for", bool(a) and bool(b_))
    fetched = {m for m, u in hls if a and a in u}
    check(f"film A's stream was never played into film B's player (A: {sorted(fetched)})",
          bool(a) and "GET" not in fetched)
    check("film A's conversion was handed back the moment its answer arrived", "DELETE" in fetched)
    check("film B's stream is the one being fetched", any(b_ and b_ in u for m, u in hls if m == "GET"))
    check(f"and B is playing ({playing})", bool(playing) and not playing["paused"] and playing["t"] > 0)
    sessions = admin.get("/api/admin/overview").get("sessions") or []
    check("on the server, A's conversion is gone and B's is there",
          a not in {s.get("id") for s in sessions} and b_ in {s.get("id") for s in sessions})

# ---------------------------------------------------------------------------------------------------
print("\n7. #/pick shows the titles asked for, in that order, and plays the one chosen")

r = admin.get(f"/api/pick?ids={made['sn2']},{made['sn1']},999999999,{show}")
check("the cards come back in the order asked, without the id that does not exist",
      [c.get("id") for c in r.get("titles", [])] == [made["sn2"], made["sn1"], show])
check("there is a #/pick route", "views.pick(q)" in src("web/js/app.js"))

if async_playwright:
    async def page_pick():
        async with async_playwright() as p:
            b = await p.chromium.launch(args=["--disable-dev-shm-usage"])
            pg = await b.new_page(viewport={"width": 1440, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto(BASE, wait_until="load")
            await pg.wait_for_timeout(400)
            await pg.click('[data-user="admin"]')
            if await pg.query_selector("#lp"):
                await pg.fill("#lp", "secret123")
                await pg.click("#login button.primary")
            await pg.wait_for_selector(".card", timeout=45000)
            await pg.evaluate(f"location.hash = '#/pick?ids={made['sn2']},{made['sn1']},{show}'")
            await pg.wait_for_timeout(1500)
            cards = await pg.evaluate("() => [...document.querySelectorAll('#pick-grid .card')].map(c => "
                                      "({id: Number(c.dataset.id), s: c.querySelector('.caption .s')?.textContent}))")
            second = await pg.query_selector(f"#pick-grid .card[data-id='{made['sn1']}'] a.frame-link, "
                                             f"#pick-grid .card[data-id='{made['sn1']}'] a")
            if second:
                await second.click()
                await pg.wait_for_timeout(1500)
            after = await pg.evaluate("location.hash")
            await pg.evaluate(f"location.hash = '#/pick?ids={show}'")
            await pg.wait_for_timeout(1200)
            link = await pg.query_selector(f"#pick-grid .card[data-id='{show}'] a")
            if link:
                await link.click()
                await pg.wait_for_timeout(1500)
            after_show = await pg.evaluate("location.hash")
            await b.close()
            return cards, after, after_show, errs

    cards, after, after_show, errs = asyncio.run(page_pick())
    check(f"the page draws the three, in order ({[c['id'] for c in cards]})",
          [c["id"] for c in cards] == [made["sn2"], made["sn1"], show])
    check(f"numbered, so 'the second one' can be said ({[c['s'] for c in cards]})",
          [str(c["s"] or "").split(" · ")[0] for c in cards] == ["1", "2", "3"])
    check(f"choosing the second plays it ({after})", after.startswith(f"#/play/{made['sn1']}"))
    check(f"choosing a show plays its next episode ({after_show})", after_show.startswith(f"#/play/{e1}"))
    check(f"no script errors ({errs[:1]})", not errs)

# ---------------------------------------------------------------------------------------------------
if saved_guest is not None:
    con.execute("DELETE FROM user_libraries WHERE user_id = ?", (guest_id,))
    con.executemany("INSERT INTO user_libraries(user_id, library_id) VALUES(?, ?)",
                    [(guest_id, x) for x in saved_guest])
tidy()
left = con.execute("SELECT COUNT(*) FROM libraries WHERE name IN (?, ?)", (SCRATCH, SHOWS)).fetchone()[0]
con.close()
check("the temporary libraries are removed again, and the viewer's libraries put back", left == 0)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
