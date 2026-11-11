"""A random pick by years, without some genres or libraries (1.26.3).

Jarvis, 11 Nov 2026: "Play me some movie from the 1980s" picked from the whole library (an animation),
and "not animation" then gave Freddy vs. Jason (2003): `/api/find` could not narrow by years, leave a
genre out, or leave a library out. Vlad has a library called Boxing, so "not boxing" is said by leaving
that library out — and a boxing film also kept in another library must stay out too.

This test builds two small libraries of invented films (removed afterwards), and holds:
  1. years: a range keeps only titles inside it, and a title with no known year is left out;
  2. genres to leave out, by their English and Bulgarian names; a title with no genres stays in;
  3. libraries: `library` picks only from those, `no_library` leaves them out — the same film kept in
     another library included;
  4. Jarvis's own question, asked many times, never answers anything outside it, and `matches` counts
     exactly what fits; nothing fitting is `matches: 0`;
  5. what is wrong is refused, naming it — an unknown genre, an unknown or forbidden library, years
     the wrong way round — never dropped quietly;
  6. nothing that worked before changes: the same requests without the new parameters give the same
     answers they gave before this test touched anything, and capabilities name the new filters.
"""
import json
import os
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

BASE = os.environ.get("LENTA_TEST_BASE", "http://127.0.0.1:8597")
DB = Path(os.environ.get("LENTA_TEST_DB", "/tmp/claude-0/intro/data/lenta.db"))
FILMS, BOXING = "Filter test films", "Filter test boxing"
CLIPS = Path(tempfile.mkdtemp(prefix="lenta-filters-"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


class Client:
    def __init__(self, user, password="secret123"):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.ok = self.get_status("POST", "/api/auth/login", {"username": user, "password": password}) == 200

    def call(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except ValueError:
                return e.code, {}
        except Exception:                               # noqa: BLE001 — a dead server is a red check
            return 0, {}

    def get_status(self, method, path, body=None):
        return self.call(method, path, body)[0]

    def find(self, **params):
        return self.call("GET", "/api/find?" + urllib.parse.urlencode(params))


if BASE.endswith(":8597"):
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
admin, guest = Client("admin"), Client("guest")
check("both accounts sign in", admin.ok and guest.ok)

# ---- before anything is added: the answers that must not change
JARVIS_OLD = [dict(q="", random=1, kind="film"), dict(q="zzzz no such film"), dict(q="", genre="Drama", kind="film"),
              dict(q="", kind="any", unwatched=1)]
before = [admin.find(**p) for p in JARVIS_OLD]


def tidy():
    for name in (FILMS, BOXING):
        con.execute("DELETE FROM files WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (name,))
        con.execute("DELETE FROM items WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (name,))
        con.execute("DELETE FROM libraries WHERE name = ?", (name,))
    con.commit()


tidy()
films = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'movies', '[]')", (FILMS,)).lastrowid
boxing = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'movies', '[]')", (BOXING,)).lastrowid
made = {}


def film(key, title, year, genres, lib, tmdb=None):
    iid = con.execute(
        "INSERT INTO items(library_id, kind, key, title, sort_title, year, tmdb_id, genres, extra, added_at) "
        "VALUES(?, 'movie', ?, ?, ?, ?, ?, ?, ?, ?)",
        (lib, f"filtertest:{key}", title, title, year, tmdb, json.dumps(genres) if genres is not None else None,
         json.dumps({"parsed_title": title}), time.time())).lastrowid
    path = CLIPS / f"{key}.mp4"
    path.write_bytes(b"")
    con.execute("INSERT INTO files(item_id, library_id, path) VALUES(?,?,?)", (iid, lib, str(path)))
    made[key] = iid


film("a1", "Neon Harbour", 1982, ["Drama"], films)
film("a2", "Glass Orchard", 1986, ["Animation"], films)
film("a3", "Copper Lantern", 1989, ["Animation", "Comedy"], films)
film("a4", "Velvet Static", 1985, [], films)                    # no genres at all: stays in
film("a5", "Tin Meridian", 1991, ["Drama"], films)               # the 1990s
film("a6", "Paper Comet", None, ["Drama"], films)                # year unknown
film("a7", "Salt Engine", 1979, ["Drama"], films)                # the 1970s
film("a8", "Iron Knuckle", 1984, ["Drama"], films, tmdb=880001)  # a boxing film kept here too
film("b1", "Iron Knuckle", 1984, ["Drama"], boxing, tmdb=880001)
film("b2", "Ring of Ash", 1987, ["Documentary"], boxing)
film("b3", "Glove Theory", 1983, ["Drama"], boxing)
film("a9", "Glove Theory", 1983, ["Drama"], films)               # same title and year, no TMDB id: same film
con.commit()
check(f"two libraries of invented films, made for this test ({len(made)})", len(made) == 12)
both = f"{films},{boxing}"
name_of = {v: k for k, v in made.items()}


def picks(n=40, **params):
    seen = set()
    for _ in range(n):
        code, r = admin.find(random=1, kind="film", **params)
        if code != 200:
            return code, seen, r
        seen.add(r.get("item_id"))
    return 200, seen, r


# ---------------------------------------------------------------- 1. years
print("\n1. years")
code, seen, r = picks(library=both, year_from=1980, year_to=1989)
names = {name_of.get(i, i) for i in seen}
check(f"only 1980s films come up ({sorted(map(str, names))})",
      code == 200 and seen and names <= {"a1", "a2", "a3", "a4", "a8", "b1", "b2", "b3", "a9"})
check("the 1970s, the 1990s and the film with no year never do", not names & {"a5", "a6", "a7"})
_, r = admin.find(random=1, kind="film", library=both, year_from=1980, year_to=1989)
# a1 a2 a3 a4 + (a8 = b1) + b2 + (b3 = a9): one film kept twice counts once
check(f"matches counts them, a film kept twice once ({r.get('matches')})", r.get("matches") == 7)
_, r = admin.find(random=1, kind="film", library=films, year_to=1979)
check(f"year_to alone: 'older than 1980' is Salt Engine ({name_of.get(r.get('item_id'))}, {r.get('matches')})",
      r.get("item_id") == made["a7"] and r.get("matches") == 1)
_, r = admin.find(random=1, kind="film", library=films, year_from=1990)
check(f"year_from alone ({name_of.get(r.get('item_id'))})", r.get("item_id") == made["a5"] and r.get("matches") == 1)

# ---------------------------------------------------------------- 2. genres to leave out
print("\n2. genres to leave out")
code, seen, _ = picks(library=films, year_from=1980, year_to=1989, no_genre="Animation")
names = {name_of.get(i, i) for i in seen}
check(f"no animation: Glass Orchard and Copper Lantern never come up ({sorted(map(str, names))})",
      code == 200 and not names & {"a2", "a3"} and names)
check("a film with no genres at all stays in (Velvet Static)", "a4" in names)
_, r1 = admin.find(random=1, kind="film", library=films, no_genre="анимация")
_, r2 = admin.find(random=1, kind="film", library=films, no_genre="animation")
_, r3 = admin.find(random=1, kind="film", library=films, no_genre="Animation")
check(f"the Bulgarian name and any case mean the same ({r1.get('matches')}, {r2.get('matches')}, {r3.get('matches')})",
      r1.get("matches") == r2.get("matches") == r3.get("matches") == 7)
_, r = admin.find(random=1, kind="film", library=films, no_genre="Animation,Drama")
check(f"two genres at once leave only Velvet Static ({name_of.get(r.get('item_id'))})",
      r.get("item_id") == made["a4"] and r.get("matches") == 1)
_, r = admin.find(random=1, kind="film", library=films, genre="Comedy", no_genre="Drama")
check(f"with genre too: a comedy, but no drama — Copper Lantern ({name_of.get(r.get('item_id'))})",
      r.get("item_id") == made["a3"])

# ---------------------------------------------------------------- 3. libraries
print("\n3. libraries")
code, seen, _ = picks(library=boxing)
check(f"library: only the Boxing library's films ({sorted(str(name_of.get(i, i)) for i in seen)})",
      code == 200 and seen and {name_of.get(i) for i in seen} <= {"b1", "b2", "b3"})
code, seen, _ = picks(library=both, no_library=boxing)
names = {name_of.get(i, i) for i in seen}
check(f"no_library: none of Boxing's films ({sorted(map(str, names))})", code == 200 and not names & {"b1", "b2", "b3"})
check("and not Iron Knuckle kept in the other library too (same TMDB id)", "a8" not in names)
check("nor Glove Theory kept there too (same title and year)", "a9" not in names)

# ---------------------------------------------------------------- 4. Jarvis's own question
print("\n4. 'any film from the 1980s, not boxing or animation'")
_, r = admin.find(random=1, kind="film", library=both, year_from=1980, year_to=1989, no_genre="Animation",
                  no_library=boxing)
check(f"in the test libraries exactly two films fit, Neon Harbour and Velvet Static ({r.get('matches')})",
      r.get("matches") == 2)
code, seen, _ = picks(60, library=both, year_from=1980, year_to=1989, no_genre="Animation", no_library=boxing)
check(f"and only those two ever come up ({sorted(str(name_of.get(i, i)) for i in seen)})",
      {name_of.get(i) for i in seen} == {"a1", "a4"})
# The question as Jarvis will send it — across every library, not only the test's.
bad = []
for _ in range(40):
    code, r = admin.find(random=1, kind="film", year_from=1980, year_to=1989, no_genre="Animation", no_library=boxing)
    i = r.get("item_id")
    if code != 200 or not i:
        bad.append(("no answer", code))
        continue
    row = con.execute("SELECT year, genres, library_id, tmdb_id FROM items WHERE id = ?", (i,)).fetchone()
    genres = [g.lower() for g in json.loads(row["genres"] or "[]")]
    if not (row["year"] and 1980 <= row["year"] <= 1989) or "animation" in genres or row["library_id"] == boxing:
        bad.append((i, row["year"], genres))
check(f"across all of LENTA, 40 picks: every one a 1980s film, no animation, not boxing ({bad[:2]})", not bad)
_, r = admin.find(random=1, kind="film", library=films, year_from=1950, year_to=1959)
check(f"nothing fits: matches 0 and no id ({r.get('matches')}, {r.get('item_id')})",
      r.get("matches") == 0 and r.get("item_id") is None)
_, r = admin.find(q="neon harbour", library=films, year_to=1979)
check(f"words and years together: Neon Harbour is not 'before 1980' ({r.get('matches')})", r.get("matches") == 0)
_, r = admin.find(q="neon harbour", library=films, year_from=1980)
check(f"but is 'from 1980' ({name_of.get(r.get('item_id'))})", r.get("item_id") == made["a1"])

# ---------------------------------------------------------------- 5. refused, and said why
print("\n5. what is wrong is refused, naming it")
code, r = admin.find(random=1, kind="film", no_genre="Sport")
check(f"a genre no title has: 400, naming it ({code}, {r.get('detail')})", code == 400 and "Sport" in str(r.get("detail")))
code, _ = admin.find(random=1, kind="film", year_from=1990, year_to=1980)
check(f"years the wrong way round: 400 ({code})", code == 400)
code, _ = admin.find(random=1, kind="film", no_library="999999")
check(f"a library that does not exist: 400 ({code})", code == 400)
code, _ = admin.find(random=1, kind="film", library="abc")
check(f"a library that is not an id: 400 ({code})", code == 400)
# The guest is given the films library for these two checks only, and it is taken away again after.
gid = con.execute("SELECT id FROM users WHERE username = 'guest'").fetchone()[0]
had = con.execute("SELECT 1 FROM user_libraries WHERE user_id = ? AND library_id = ?", (gid, films)).fetchone()
con.execute("INSERT OR IGNORE INTO user_libraries(user_id, library_id) VALUES(?, ?)", (gid, films))
con.commit()
code, _ = guest.find(random=1, kind="film", library=boxing)
check(f"a library the account may not see: 400, the same as one that does not exist ({code})", code == 400)
code, r = guest.find(random=1, kind="film", library=films, no_genre="Animation")
check(f"in a library it may see, the guest leaves a genre out like anyone ({code}, {r.get('matches')})",
      code == 200 and r.get("matches") == 7)
if not had:
    con.execute("DELETE FROM user_libraries WHERE user_id = ? AND library_id = ?", (gid, films))
    con.commit()

# ---------------------------------------------------------------- 6. nothing that worked before changes
print("\n6. nothing that worked before changes")
tidy()
after = [admin.find(**p) for p in JARVIS_OLD]
same = True
for (c0, a), (c1, b), p in zip(before, after, JARVIS_OLD):
    chosen_at_random = p.get("random") or p.get("genre") or p.get("unwatched")     # as LENTA always has
    if c0 != c1 or set(a) != set(b) or (not chosen_at_random and a != b) or a.get("matches") != b.get("matches"):
        same = False
        print("        differs:", p, a, b)
check("the same requests without the new parameters answer exactly as before", same)
code, caps = admin.call("GET", "/api/capabilities")
f = (caps.get("features") or {}).get("find") or {}
check(f"capabilities: find version 3, naming the new filters ({f.get('version')})",
      f.get("version") == 3 and {"year_from", "year_to", "no_genre", "library", "no_library"} <= set(f.get("filters") or []))
contract = json.loads((Path(__file__).resolve().parent.parent / "server/lenta/contract/assistant.json").read_text())
fp = next((e for e in contract.get("endpoints", []) if e.get("id") == "find"), {}).get("params", {})
check("the contract names them too", {"year_from", "year_to", "no_genre", "library", "no_library"} <= set(fp))

print(f"\n{len(fails)} failed" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
