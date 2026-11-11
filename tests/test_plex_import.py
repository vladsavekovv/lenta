"""The Plex importer, checked against LENTA's real schema and a database shaped like Plex's.

What has to hold, because each one is a way of quietly losing somebody's history:

  * a record belongs to the LENTA title that owns **the same file**; a renamed film falls back to its
    TMDB number; a renamed episode is reported, never guessed at;
  * Plex's resume point is in **milliseconds**; a film watched once and restarted is "part way through";
  * **LENTA wins** wherever its own record is newer, and keeps the higher play count when Plex's wins;
  * an account nobody mapped is left alone; a mapping naming an account that does not exist stops it;
  * the **dry run writes nothing**; --apply takes a backup first; a failure half way **writes nothing**;
  * Plex's database is opened only as a copy.
"""
import hashlib, os, sqlite3, sys, tempfile
from pathlib import Path

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from lenta import db as lenta_db           # noqa: E402  — the real schema, after LENTA_DATA_DIR is set
from lenta import plex_import as pi        # noqa: E402

fails = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


T0 = 1_700_000_000.0
DAY = 86400

# ---- LENTA, built by LENTA's own init_db ------------------------------------------------------------
lenta_db.init_db()
LDB = Path(lenta_db.DB_PATH)
con = sqlite3.connect(LDB, isolation_level=None)
con.executescript("""
INSERT INTO users(id, username, password_hash, is_admin) VALUES (1, 'savek', 'x', 1), (2, 'alex', 'x', 0);
INSERT INTO libraries(id, name, kind, paths) VALUES (1, 'Films', 'movies', '["/m/films"]'),
                                                   (2, 'Shows', 'shows', '["/m/shows"]');
INSERT INTO items(id, library_id, kind, key, title, tmdb_id) VALUES
  (10, 1, 'movie', 'heat', 'Heat', 949),
  (11, 1, 'movie', 'alien', 'Alien', 348),
  (12, 1, 'movie', 'ronin', 'Ronin', 8195),
  (13, 1, 'movie', 'renamed', 'Twelve Monkeys', 63),
  (20, 2, 'show', 'wire', 'The Wire', NULL),
  (21, 2, 'season', 'wire:1', 'Season 1', NULL),
  (22, 2, 'episode', 'ep:wire:1:1', 'The Target', NULL),
  (23, 2, 'episode', 'ep:wire:1:2', 'The Detail', NULL);
UPDATE items SET parent_id = 20 WHERE id = 21;
UPDATE items SET parent_id = 21 WHERE id IN (22, 23);
INSERT INTO files(item_id, library_id, path, duration) VALUES
  (10, 1, '/m/films/Heat (1995)/Heat.mkv', 10200),
  (11, 1, '/m/films/Alien (1979)/Alien.mkv', 7020),
  (12, 1, '/m/films/Ronin (1998)/Ronin.mkv', 7320),
  (13, 1, '/m/films/12 Monkeys (1995)/12.Monkeys.mkv', 7740),
  (22, 2, '/m/shows/The Wire/Season 01/S01E01.mkv', 3600),
  (22, 2, '/m/shows/The Wire - Prequel/Season 01/S01E01.mkv', 3600),
  (23, 2, '/m/shows/The Wire/Season 01/S01E02.mkv', 3600);
""")
# savek already has records in LENTA: Alien newer than Plex's, Ronin older than Plex's.
con.execute("INSERT INTO watch_state VALUES (1, 11, 600, 7020, 0, 0, ?)", (T0 + 10 * DAY,))
con.execute("INSERT INTO watch_state VALUES (1, 12, 0, 7320, 1, 3, ?)", (T0 - 10 * DAY,))
con.close()

# ---- Plex ----------------------------------------------------------------------------------------
PDIR = Path(tempfile.mkdtemp())
PDB = PDIR / "com.plexapp.plugins.library.db"
p = sqlite3.connect(PDB)
p.executescript("""
CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE metadata_items (id INTEGER PRIMARY KEY, guid TEXT, metadata_type INTEGER, title TEXT);
CREATE TABLE media_items (id INTEGER PRIMARY KEY, metadata_item_id INTEGER);
CREATE TABLE media_parts (id INTEGER PRIMARY KEY, media_item_id INTEGER, file TEXT);
CREATE TABLE metadata_item_settings (id INTEGER PRIMARY KEY, account_id INTEGER, guid TEXT, rating REAL,
  view_offset INTEGER, view_count INTEGER, last_viewed_at INTEGER, updated_at INTEGER);
CREATE TABLE tags (id INTEGER PRIMARY KEY, tag TEXT, tag_type INTEGER);
CREATE TABLE taggings (id INTEGER PRIMARY KEY, metadata_item_id INTEGER, tag_id INTEGER);
INSERT INTO accounts VALUES (0, ''), (1, 'saveka1'), (42216512, 'Alex'), (119023289, 'Tedi');
INSERT INTO metadata_items VALUES
  (1, 'plex://movie/heat', 1, 'Heat'), (2, 'plex://movie/alien', 1, 'Alien'),
  (3, 'plex://movie/ronin', 1, 'Ronin'), (4, 'plex://movie/12monkeys', 1, 'Twelve Monkeys'),
  (5, 'plex://episode/wire101', 4, 'The Target'), (6, 'plex://episode/wire102', 4, 'The Detail'),
  (7, 'plex://episode/gone', 4, 'Renamed episode'), (8, 'plex://movie/notthere', 1, 'Not in LENTA'),
  (9, 'plex://episode/prequel101', 4, 'Prequel 1');
INSERT INTO media_items VALUES (1,1),(2,2),(3,3),(4,4),(5,5),(6,6),(7,7),(8,8),(9,9);
INSERT INTO media_parts VALUES
  (1, 1, '/m/films/Heat (1995)/Heat.mkv'), (2, 2, '/m/films/Alien (1979)/Alien.mkv'),
  (3, 3, '/m/films/Ronin (1998)/Ronin.mkv'), (4, 4, '/m/films/Twelve.Monkeys.1995/old-name.mkv'),
  (5, 5, '/m/shows/The Wire/Season 01/S01E01.mkv'), (6, 6, '/m/shows/The Wire/Season 01/S01E02.mkv'),
  (7, 7, '/m/shows/Gone/S01E01-old.mkv'), (8, 8, '/m/films/Elsewhere/x.mkv'),
  (9, 9, '/m/shows/The Wire - Prequel/Season 01/S01E01.mkv');
INSERT INTO tags VALUES (1, 'tmdb://63', 314), (2, 'imdb://tt0114746', 314);
INSERT INTO taggings VALUES (1, 4, 1), (2, 4, 2);
""")
S = "INSERT INTO metadata_item_settings(account_id, guid, view_offset, view_count, last_viewed_at) VALUES (?,?,?,?,?)"
rows = [
    (1, 'plex://movie/heat', None, 2, T0),               # watched twice
    (1, 'plex://movie/alien', 3_000_000, 1, T0),         # LENTA newer -> keep
    (1, 'plex://movie/ronin', 1_234_500, 1, T0),         # Plex newer, rewatch in progress
    (1, 'plex://movie/12monkeys', None, 1, T0),          # renamed -> TMDB fallback
    (1, 'plex://episode/wire101', None, 1, T0),
    (1, 'plex://episode/prequel101', 2_000_000, 0, T0 + DAY),  # LENTA merged it onto S01E01: newer wins
    (1, 'plex://episode/wire102', 1_800_000, 0, T0),     # part way, never finished
    (1, 'plex://episode/gone', None, 1, T0),             # renamed episode -> unmatched
    (1, 'plex://movie/notthere', None, 1, T0),           # not in LENTA -> unmatched
    (42216512, 'plex://movie/heat', 600_000, 0, T0),
    (119023289, 'plex://movie/heat', None, 5, T0),       # Tedi: not mapped -> left alone
    (1, 'plex://show/whatever', None, 1, T0),            # not a film or an episode -> ignored
]
p.executemany(S, rows)
p.commit()
p.close()
plex_before = sha(PDB)

MAP = {"saveka1": "savek", "Alex": "alex"}

# ---- the plan --------------------------------------------------------------------------------------
lc = sqlite3.connect(LDB, isolation_level=None)
plan = pi.plan_import(PDB, lc, MAP)
by = {(c.user_id, c.item_id): c for c in plan.changes}

h = by.get((1, 10))
check(h and h.completed == 1 and h.play_count == 2 and h.position == 0 and h.action == "new"
      and h.updated_at == T0, "a film watched twice in Plex arrives watched, played twice, on Plex's date")
check(h and h.duration == 10200, "the duration comes from LENTA's own file")
a = by.get((1, 11))
check(a and a.action == "keep", "LENTA's newer record is kept (Alien)")
r = by.get((1, 12))
check(r and r.action == "update" and r.completed == 0 and abs(r.position - 1234.5) < 1e-6,
      "a newer Plex record wins, and its resume point is milliseconds turned into seconds (Ronin)")
check(r and r.play_count == 3, "the higher play count survives when Plex's record wins")
m = by.get((1, 13))
check(m and m.source == "tmdb" and m.completed == 1, "a renamed film is found by its TMDB number")
e1, e2 = by.get((1, 22)), by.get((1, 23))
check(e1 and e1.completed == 0 and abs(e1.position - 2000) < 1e-6 and e1.updated_at == T0 + DAY,
      "two Plex records on one LENTA title (LENTA merged them): the newer one is used")
check(set(plan.shared.get((1, 22), [])) == {"/m/shows/The Wire/Season 01/S01E01.mkv",
      "/m/shows/The Wire - Prequel/Season 01/S01E01.mkv"} and len(plan.shared) == 1,
      "and that title is reported, with both files")
check(e2 and e2.completed == 0 and abs(e2.position - 1800) < 1e-6, "an episode part way through keeps its place")
un = {f[0] if f else g for _a, g, f in plan.unmatched}
check("/m/shows/Gone/S01E01-old.mkv" in un, "a renamed episode is reported, not guessed at")
check("/m/films/Elsewhere/x.mkv" in un, "a film LENTA does not have is reported")
check(len(plan.unmatched) == 2, "nothing else is unmatched (shows and seasons are not records to import)")
x = by.get((2, 10))
check(x and x.completed == 0 and abs(x.position - 600) < 1e-6, "Alex's record goes to Alex")
check("Tedi" in plan.ignored_accounts and not any(c.play_count == 5 for c in plan.changes),
      "an account nobody mapped is left alone")

# ---- mistakes in the mapping stop it ---------------------------------------------------------------
for bad, why in (({"Nobody": "savek"}, "a Plex account that does not exist"),
                 ({"saveka1": "nobody"}, "a LENTA account that does not exist")):
    try:
        pi.plan_import(PDB, lc, bad)
        check(False, f"{why} stops the import")
    except SystemExit:
        check(True, f"{why} stops the import")
check(pi.plan_import(PDB, lc, {"SAVEKA1": "SAVEK"}).changes != [], "account names match regardless of case")

# ---- a path map --------------------------------------------------------------------------------------
check(pi.remap("/plex/films/a.mkv", [("/plex", "/m")]) == "/m/films/a.mkv"
      and pi.remap("/plexy/a.mkv", [("/plex", "/m")]) == "/plexy/a.mkv",
      "a path map moves whole folders, not prefixes of names")
lc.close()

# ---- dry run, apply, backup ------------------------------------------------------------------------
before = sha(LDB)
pi.main(["--plex-db", str(PDB), "--lenta-db", str(LDB), "--map", "saveka1=savek", "--map", "Alex=alex"])
check(sha(LDB) == before, "the dry run writes nothing")

# a failure half way through writes nothing
lc = sqlite3.connect(LDB, isolation_level=None)
plan = pi.plan_import(PDB, lc, MAP)
# a user that does not exist, written last: the foreign key refuses it after the real records went in
plan.changes.append(pi.Change(999, 999, 0, None, 1, 1, T0, "new", "path"))
lc.execute("PRAGMA foreign_keys = ON")
try:
    pi.apply(lc, plan)
    check(False, "a failing write is rolled back")
except sqlite3.IntegrityError:
    n = lc.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0]
    check(n == 2, "a failure half way through rolls everything back")
lc.close()

pi.main(["--plex-db", str(PDB), "--lenta-db", str(LDB), "--map", "saveka1=savek", "--map", "Alex=alex", "--apply"])
baks = sorted(LDB.parent.glob("lenta.db.pre-plex-import-*"))
check(len(baks) == 1, "--apply takes a backup first")
if baks:
    b = sqlite3.connect(baks[0])
    check(b.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0] == 2, "the backup is the database as it was")
    b.close()
lc = sqlite3.connect(LDB)
ws = {(r[0], r[1]): r[2:] for r in lc.execute("SELECT user_id, item_id, position, completed, play_count, updated_at FROM watch_state")}
check(ws.get((1, 11)) == (600, 0, 0, T0 + 10 * DAY), "after --apply, LENTA's newer Alien is untouched")
check(ws.get((1, 10)) == (0, 1, 2, T0), "after --apply, Heat is watched twice")
check(ws.get((2, 10), (None,))[0] == 600, "after --apply, Alex has his place in Heat")
check(len(ws) == 2 + 5, "after --apply, exactly the planned records exist (2 there before, 5 new)")
lc.close()
check(sha(PDB) == plex_before, "Plex's database is byte-for-byte untouched")

# running it again changes nothing: every record is now LENTA's and as new as Plex's
lc = sqlite3.connect(LDB, isolation_level=None)
again = pi.plan_import(PDB, lc, MAP)
check(all(c.action == "keep" for c in again.changes), "a second run keeps everything")
lc.close()

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
