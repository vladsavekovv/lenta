"""IMDb ratings without a key, from IMDb's own Non-Commercial Datasets (personal, non-commercial use):
https://datasets.imdbws.com/title.ratings.tsv.gz — every title's average rating and number of votes,
updated daily by IMDb (about 8 MB).

Once a day LENTA downloads the file (only when IMDb has a newer one) and copies the rating and votes of
the titles in your libraries into items.extra (imdb_rating, imdb_votes). Titles get their IMDb number
from TMDB (about.py), so new titles are rated on the next run.
"""
import gzip
import threading
import time

import httpx

from . import logs
from .config import CACHE_DIR, VERSION
from .db import db, get_setting, set_setting

log = logs.get("imdb")
URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
FILE = CACHE_DIR / "imdb" / "title.ratings.tsv.gz"
MAX_AGE = 20 * 3600

state = {"running": False, "updated": None, "rated": 0, "error": None}
_lock = threading.Lock()


def enabled() -> bool:
    return (get_setting("imdb_ratings") or "1") != "0"


def _download() -> bool:
    """Fetch the file when IMDb has a newer one. True when a (new or cached) file is available."""
    FILE.parent.mkdir(parents=True, exist_ok=True)
    if FILE.exists() and time.time() - FILE.stat().st_mtime < MAX_AGE:
        return True
    headers = {"User-Agent": f"LENTA/{VERSION} (personal media server)"}
    if FILE.exists():
        headers["If-Modified-Since"] = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(FILE.stat().st_mtime))
    tmp = FILE.with_suffix(".part")
    with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as c:
        with c.stream("GET", URL) as r:
            if r.status_code == 304:
                FILE.touch()
                return True
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
    tmp.replace(FILE)
    return True


def _apply() -> int:
    with db() as con:
        wanted = {r["i"] for r in con.execute(
            "SELECT DISTINCT json_extract(extra, '$.imdb_id') AS i FROM items "
            "WHERE kind IN ('movie','show') AND json_extract(extra, '$.imdb_id') IS NOT NULL")}
    if not wanted:
        return 0
    found = {}
    with gzip.open(FILE, "rt", encoding="utf-8") as f:
        next(f, None)                                    # tconst  averageRating  numVotes
        for line in f:
            tconst, _, rest = line.partition("\t")
            if tconst in wanted:
                rating, _, votes = rest.rstrip("\n").partition("\t")
                try:
                    found[tconst] = (float(rating), int(votes))
                except ValueError:
                    continue
    with db() as con:
        for tconst, (rating, votes) in found.items():
            con.execute("UPDATE items SET extra = json_set(COALESCE(extra, '{}'), '$.imdb_rating', ?, '$.imdb_votes', ?) "
                        "WHERE kind IN ('movie','show') AND json_extract(extra, '$.imdb_id') = ?", (rating, votes, tconst))
    return len(found)


def _run() -> None:
    try:
        _download()
        state["rated"] = _apply()
        state.update(updated=time.time(), error=None)
        set_setting("imdb_ratings_updated", str(int(state["updated"])))
        log.info("IMDb ratings updated for %d titles", state["rated"])
    except Exception as exc:
        state["error"] = str(exc)
        log.warning("IMDb ratings could not be updated: %s", exc)
    finally:
        state["running"] = False


def request_refresh() -> None:
    if not enabled():
        return
    with _lock:
        if state["running"]:
            return
        state["running"] = True
    threading.Thread(target=_run, name="imdb-ratings", daemon=True).start()


def status() -> dict:
    with db() as con:
        rated = con.execute("SELECT COUNT(*) FROM items WHERE kind IN ('movie','show') "
                            "AND json_extract(extra, '$.imdb_rating') IS NOT NULL").fetchone()[0]
    return {**state, "enabled": enabled(), "rated": rated,
            "updated": state["updated"] or float(get_setting("imdb_ratings_updated") or 0) or None}
