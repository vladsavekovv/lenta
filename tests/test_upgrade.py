"""Upgrading a server that is already running, which is every server except a brand new one.

Every other test here starts from an empty folder, so the database it gets is whatever the current
schema says — the one shape no real server has. A running server's tables were made by whichever version
installed them, and they keep that shape: `CREATE TABLE IF NOT EXISTS` does nothing to a table that
exists, so a column added later arrives only if something adds it, and a constraint written in an older
version stays exactly as it was written.

That gap has already produced one bug of its own. `stream_art` was created in 1.9.1 holding one picture
per title, with `ref NOT NULL` and no default. 1.9.5 made it two pictures and began inserting the row
first and filling a column after — an insert that named neither picture, which on a fresh database was
fine because the new schema gives both a default, and on every upgraded one broke the constraint. The
insert was `INSERT OR IGNORE`, so the breakage was swallowed; the update then found no row to change;
and the server answered "ok" to a request that had done nothing at all. Nothing in the logs, nothing on
screen, and the test suite entirely happy.

So this file builds the old shapes by hand and works them, which is the only way to see any of that.
"""
import os, sqlite3, sys, tempfile

ROOT = "/home/claude/lenta"
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# The tables as each version really wrote them, before anything was added.
V190 = """CREATE TABLE stream_titles (provider_id INTEGER NOT NULL, kind TEXT NOT NULL,
  tmdb_id INTEGER NOT NULL, rank INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', year INTEGER,
  poster TEXT NOT NULL DEFAULT '', overview TEXT NOT NULL DEFAULT '', rating REAL,
  link TEXT NOT NULL DEFAULT '', fetched_at REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (provider_id, kind, tmdb_id));"""

V191 = V190 + """
CREATE TABLE stream_art (tmdb_id INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
  set_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (tmdb_id, kind));"""


def server_at(schema: str, rows=()):
    """A LENTA data folder whose database is an older version's, then started by this one."""
    folder = tempfile.mkdtemp()
    con = sqlite3.connect(f"{folder}/lenta.db")
    con.executescript(schema)
    for sql, params in rows:
        con.execute(sql, params)
    con.commit()
    con.close()
    out = {}
    code = (
        "import os, sys, json\n"
        f"os.environ['LENTA_DATA_DIR'] = {folder!r}\n"
        f"sys.path.insert(0, {ROOT + '/server'!r})\n"
        "from lenta import db, streaming\n"
        "db.init_db()\n"
        "r = {}\n"
        "try:\n"
        "    streaming.set_art(101, 'movie', 'tmdb:/wide.jpg', 'backdrop')\n"
        "    streaming.set_art(101, 'movie', 'tmdb:/tall.jpg', 'poster')\n"
        "    r['saved'] = streaming.art_overrides().get((101, 'movie'))\n"
        "except Exception as exc:\n"
        "    r['error'] = f'{type(exc).__name__}: {exc}'\n"
        "try:\n"
        "    streaming.clear_art(101, 'movie', 'poster')\n"
        "    r['after_reset'] = streaming.art_overrides().get((101, 'movie'))\n"
        "except Exception as exc:\n"
        "    r['reset_error'] = f'{type(exc).__name__}: {exc}'\n"
        "with db.db() as con:\n"
        "    r['title_columns'] = [c[1] for c in con.execute('PRAGMA table_info(stream_titles)')]\n"
        "    r['kept'] = [dict(x) for x in con.execute('SELECT title, language, backdrop FROM stream_titles')]\n"
        "print('RESULT' + json.dumps(r))\n")
    import subprocess
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT")]
    if not line:
        return {"crashed": (p.stderr or p.stdout)[-400:]}
    import json
    return json.loads(line[0][len("RESULT"):])


print("1. a server that installed 1.9.0 and is now running this one")
got = server_at(V190, rows=[("INSERT INTO stream_titles (provider_id, kind, tmdb_id, rank, title, poster)"
                            " VALUES (8, 'movie', 55, 0, 'Already here', '/old.jpg')", ())])
check(f"it started at all{'' if 'crashed' not in got else ': ' + got['crashed']}", "crashed" not in got)
check(f"the columns added since are there: {got.get('title_columns', [])[-2:]}",
      "language" in got.get("title_columns", []) and "backdrop" in got.get("title_columns", []))
check(f"and the rows it already had are untouched: {got.get('kept')}",
      got.get("kept") and got["kept"][0]["title"] == "Already here")

print("\n2. a server that installed 1.9.1, when stream_art held one picture per title")
got = server_at(V191)
check(f"it started{'' if 'crashed' not in got else ': ' + got['crashed']}", "crashed" not in got)
check(f"choosing a picture is actually saved{'' if not got.get('error') else ' — ' + got['error']}",
      not got.get("error") and got.get("saved"))
check(f"both shapes, separately: {got.get('saved')}",
      (got.get("saved") or {}).get("backdrop") == "tmdb:/wide.jpg"
      and (got.get("saved") or {}).get("poster") == "tmdb:/tall.jpg")
check(f"and one can be put back without losing the other: {got.get('after_reset')}",
      (got.get("after_reset") or {}).get("backdrop") == "tmdb:/wide.jpg"
      and not (got.get("after_reset") or {}).get("poster"))

print("\n3. the same, on a database made by this version")
got = server_at("")          # no tables at all: the current schema creates everything
check(f"it starts{'' if 'crashed' not in got else ': ' + got['crashed']}", "crashed" not in got)
check("and saving works there too", (got.get("saved") or {}).get("backdrop") == "tmdb:/wide.jpg")

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("upgrading ok")
