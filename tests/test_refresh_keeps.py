"""What a person set by hand survives Refresh metadata (1.24.1).

He reported it on 6 Nov 2026: "my trailers on the main page have stopped running". He had pressed
Refresh metadata on his libraries (on advice given on 31 Oct, to fetch the titles' Bulgarian names),
and every "Show the trailer in the Home banner" tick was gone, so the banner found nothing ticked and
showed pictures only.

The cause is `metadata._update()`, which a refresh calls for every title. It rebuilds the title's
`extra` from what TMDB just sent and copies across only a fixed list from the old one: locked fields,
the edition, locked lockable fields, a locked trailer, the Wikipedia/OMDb cache and the found trailer.
Two things a person sets by hand were not on that list, and TMDB never sends either:
  - `home_trailer` — the banner tick (Edit › General);
  - `trailer_links` — trailer links pasted in the editor (Edit › Trailers).

This test drives `_update` exactly as a refresh does, on a throwaway database, and checks both survive,
that a refresh still replaces what TMDB owns, and that a title never ticked is not ticked by it.
It needs no test server and no media, so it runs anywhere.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("LENTA_ROOT", Path(__file__).resolve().parent.parent))
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp(prefix="lenta-refresh-")
sys.path.insert(0, str(ROOT / "server"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


from lenta import config, db as dbmod, metadata  # noqa: E402

config.ensure_dirs()
dbmod.init_db()


def make(title, extra):
    with dbmod.db() as con:
        lib = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'movies', '[]')",
                          (f"lib {title}",)).lastrowid
        return con.execute(
            "INSERT INTO items(library_id, kind, key, title, sort_title, year, extra, added_at) "
            "VALUES(?, 'movie', ?, ?, ?, 2022, ?, 0)",
            (lib, f"movie:{title}:2022", title, title, json.dumps(extra))).lastrowid


def refresh(item_id, tmdb_extra):
    """What `apply()` does for one title after fetching it from TMDB."""
    with dbmod.db() as con:
        metadata._update(con, item_id, {"title": "Elvis", "overview": "fresh from TMDB"}, dict(tmdb_extra))
        row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row), json.loads(row["extra"] or "{}")


LINKS = [{"key": "dQw4w9WgXcQ", "name": "My own pick"}]
FRESH = {"trailer": "tmdb-trailer-key", "trailer_name": "Official Trailer", "cast": [{"name": "A"}],
         "aka": ["Елвис"]}

print("1. the banner tick and pasted trailer links survive a refresh")
elvis = make("Elvis", {"home_trailer": True, "trailer_links": LINKS, "cast": [{"name": "old"}],
                       "trailer": "old-key"})
row, extra = refresh(elvis, FRESH)
check(f"the Home banner tick is still on ({extra.get('home_trailer')!r})", extra.get("home_trailer") is True)
check("the trailer links pasted in the editor are still there", extra.get("trailer_links") == LINKS)

print("\n2. a refresh still does its job")
check("the overview is TMDB's new one", row["overview"] == "fresh from TMDB")
check("the cast is TMDB's new one", extra.get("cast") == [{"name": "A"}])
check("the found trailer is TMDB's new one (it was not locked)", extra.get("trailer") == "tmdb-trailer-key")
check("the new names in other languages arrived", extra.get("aka") == ["Елвис"])

print("\n3. a title never ticked is not ticked by a refresh")
plain = make("Plain", {"cast": []})
_, extra2 = refresh(plain, FRESH)
check("no banner tick appears", "home_trailer" not in extra2)
check("no trailer links appear", "trailer_links" not in extra2)

print("\n4. it survives a second refresh too, and an untick is respected")
_, extra3 = refresh(elvis, FRESH)
check("still ticked after another refresh", extra3.get("home_trailer") is True)
with dbmod.db() as con:                                   # what the editor does when he unticks it
    e = json.loads(con.execute("SELECT extra FROM items WHERE id = ?", (elvis,)).fetchone()[0])
    e.pop("home_trailer", None)
    con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(e), elvis))
_, extra4 = refresh(elvis, FRESH)
check("unticked by hand, a refresh does not tick it back", "home_trailer" not in extra4)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
