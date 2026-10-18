"""The trailers 1.19.0 lost, and why a security header took them.

LENTA shows trailers in a YouTube embed. **YouTube decides whether an embed may play partly on the
Referer header**, and when there is none it refuses — with error 101 or 150, which are the same codes it
returns for a video the uploader has genuinely blocked. There is no way for the page to tell the two
apart.

1.19.0 added `Referrer-Policy: no-referrer`, to stop a stream URL carrying a `?token=` to another site.
That did work. It also meant every YouTube embed arrived at YouTube anonymous, and YouTube started
refusing videos that had played for years. LENTA believed it, because it had no reason not to:

  1. the key went on the **sixty-day skip list** (`trailer_sources.mark_unavailable`), which is global —
     every title that used that video lost it, not just the one being watched;
  2. the title's stored trailer was **cleared** and a replacement looked for;
  3. the replacement was fetched and embedded the same way, so it was refused too;
  4. the title was stamped "looked recently, found nothing", which stops it looking again.

So an afternoon of browsing could strip a library of working trailers, and leave the titles unable to
find them again. Both halves of that are undone by a one-time repair in `db.init_db`, and the header now
sends the origin — which is what YouTube needs and is not what was being protected. The path, where a
token would be, is still never sent to another site.

What this checks is the repair, run against a database poisoned the way his was. What it cannot check is
YouTube's behaviour: nothing here can reach YouTube, so "no Referer makes YouTube refuse" is reasoning
from the error codes and the timing, not something measured. His library is the proof.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path("/home/claude/lenta")
DATA = tempfile.mkdtemp()
os.environ["LENTA_DATA_DIR"] = DATA
sys.path.insert(0, str(ROOT / "server"))
from lenta import db                                                       # noqa: E402

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------------------------
print("1. the header that caused it sends the origin, and still not the path")

main = src("server/lenta/main.py")
check("Referrer-Policy is strict-origin-when-cross-origin",
      '"Referrer-Policy": "strict-origin-when-cross-origin"' in main)
check("it is not no-referrer, which is what YouTube could not live with",
      '"Referrer-Policy": "no-referrer"' not in main)
check("and the comment says why it changed, so nobody tightens it back",
      "error 101/150" in main or "101/150" in main)
# The point of the original header was that a stream URL may carry ?token=. That still holds:
# strict-origin-when-cross-origin sends the origin only, never the path or query.
check("a token in a stream URL is still never sent to another site, which was the original point",
      "QUERY_TOKEN_PATHS" in main and "only the origin" in main)

# ---------------------------------------------------------------------------------------------------
print("\n2. the repair, run against a database poisoned the way his was")

db.init_db()                                        # fresh database: the repair records itself as done
with db.db() as con:
    check("a fresh database is marked repaired, so it never runs twice",
          con.execute("SELECT 1 FROM settings WHERE key = 'trailer_repair_1193'").fetchone() is not None)

# Now make it look like a library that has been through 1.19.0: videos on the skip list, one title whose
# trailer was cleared and stamped as a failed search, one that still has a trailer, one chosen by hand.
banned = {f"yt{n:02d}abcdefg": time.time() for n in range(37)}
with db.db() as con:
    con.execute("DELETE FROM settings WHERE key = 'trailer_repair_1193'")
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('yt_unavailable', ?)",
                (json.dumps(banned),))
    con.execute("INSERT INTO libraries(id, name, kind, paths) VALUES(1, 'Films', 'movies', '[]')")
    rows = [
        (1, "Lost its trailer", json.dumps({"trailer": None, "trailer_checked": time.time(), "trailer_v": 0})),
        (2, "Also lost it", json.dumps({"trailer_checked": time.time() - 10})),
        (3, "Still has one", json.dumps({"trailer": "abcdefghijk", "trailer_checked": time.time()})),
        (4, "Never looked", json.dumps({"trailer_checked": 0})),
        (5, "No extra at all", None),
    ]
    for iid, title, extra in rows:
        con.execute("INSERT INTO items(id, library_id, kind, key, title, extra) "
                    "VALUES(?, 1, 'movie', ?, ?, ?)", (iid, f"k{iid}", title, extra))

db.init_db()                                        # the upgrade runs

with db.db() as con:
    left = con.execute("SELECT value FROM settings WHERE key = 'yt_unavailable'").fetchone()
    stamps = {r["id"]: json.loads(r["extra"]).get("trailer_checked") if r["extra"] else None
              for r in con.execute("SELECT id, extra FROM items ORDER BY id")}
    trailer3 = json.loads(con.execute("SELECT extra FROM items WHERE id = 3").fetchone()["extra"]).get("trailer")
    done = con.execute("SELECT 1 FROM settings WHERE key = 'trailer_repair_1193'").fetchone()

check(f"the sixty-day skip list is emptied (it held {len(banned)} videos)", not (left and left[0]))
check("a title left with no trailer will look again", stamps[1] == 0)
check("including one whose trailer key was dropped entirely", stamps[2] == 0)
check("a title that still has a trailer is left alone — its stamp is not reset",
      stamps[3] not in (0, None) and trailer3 == "abcdefghijk")
check("a title that had never looked is untouched", stamps[4] == 0)
check("a title with no extra at all does not break the upgrade", stamps[5] is None)
check("and the repair records itself, so a restart does not empty the list again", done is not None)

# Run it once more with a freshly poisoned list: nothing should happen, because it is marked done.
with db.db() as con:
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('yt_unavailable', ?)",
                (json.dumps({"zzzzzzzzzzz": time.time()}),))
db.init_db()
with db.db() as con:
    still = con.execute("SELECT value FROM settings WHERE key = 'yt_unavailable'").fetchone()
check("a video genuinely found to be blocked after the repair stays on the list",
      bool(still and "zzzzzzzzzzz" in still[0]))

# ---------------------------------------------------------------------------------------------------
print("\n3. the mechanism that did the damage is still there, because it is the right mechanism")

ts = src("server/lenta/trailer_sources.py")
check("a video that really will not play is still remembered", "def mark_unavailable(" in ts)
check("for a limited time rather than forever", "UNAVAILABLE_DAYS" in ts)
extras = src("server/lenta/api_extras.py")
check("a trailer chosen by hand is still never cleared by this path",
      'if "trailer" in metadata.locked_fields(extra)' in extras)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
