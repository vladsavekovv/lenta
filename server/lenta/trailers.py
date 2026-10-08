"""Trailer index: finds the YouTube trailer of every movie and show in the background and keeps it in
the database (items.extra.trailer), so previews and media pages can start it immediately instead of
asking TMDB first.

Runs shortly after the server starts, after every library scan and once a day. Titles without a
trailer on TMDB are asked again after two weeks (api_extras.RECHECK).
"""
import threading
import time

from . import logs, metadata
from .db import db
from .trailer_sources import KEY_VERSION

log = logs.get("trailers")
PACE = 0.07                     # ~14 lookups a second, well inside TMDB's limits

state = {"running": False, "done": 0, "total": 0, "found": 0, "started": None, "finished": None}
_lock = threading.Lock()


def _pending() -> list[int]:
    from .api_extras import RECHECK
    cutoff = time.time() - RECHECK
    with db() as con:
        return [r["id"] for r in con.execute(
            "SELECT id FROM items WHERE kind IN ('movie','show') AND ("
            " (COALESCE(json_extract(extra, '$.trailer'), '') = '' AND COALESCE(json_extract(extra, '$.trailer_checked'), 0) < ?)"
            " OR (COALESCE(json_extract(extra, '$.trailer_v'), 0) < ? AND NOT EXISTS ("
            "   SELECT 1 FROM json_each(COALESCE(extra, '{}'), '$.locked_fields') WHERE value = 'trailer'))"
            ") ORDER BY added_at DESC", (cutoff, KEY_VERSION))]


def counts() -> dict:
    from . import kinocheck
    from .trailer_sources import apple_enabled, apple_state
    with db() as con:
        row = con.execute(
            "SELECT COUNT(*) AS titles, SUM(COALESCE(json_extract(extra, '$.trailer'), '') != '') AS with_trailer, "
            "SUM(json_extract(extra, '$.trailer') LIKE 'v:http%') AS apple, "
            "SUM(json_extract(extra, '$.trailer') LIKE 'v:/api/%') AS files, "
            "SUM(json_extract(extra, '$.trailer_source') = 'kinocheck' AND json_extract(extra, '$.trailer') NOT LIKE 'v:%') AS kinocheck "
            "FROM items WHERE kind IN ('movie','show')").fetchone()
    return {"titles": row["titles"] or 0, "with_trailer": row["with_trailer"] or 0, "apple": row["apple"] or 0,
            "files": row["files"] or 0, "apple_enabled": apple_enabled(), "apple_error": apple_state["error"],
            "kinocheck": row["kinocheck"] or 0, "kinocheck_enabled": kinocheck.enabled(),
            "kinocheck_error": kinocheck.state["error"], "kinocheck_usage": kinocheck.usage(), **state}


def _run() -> None:
    from .api_extras import trailer_for
    try:
        ids = _pending()
        state.update(done=0, total=len(ids), found=0, started=time.time())
        if ids:
            log.info("Indexing trailers for %d titles", len(ids))
        for n, item_id in enumerate(ids, 1):
            with db() as con:
                row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if row:
                try:
                    if trailer_for(dict(row), background=True):
                        state["found"] += 1
                except Exception as exc:          # one title must not stop the rest
                    log.info("Trailer lookup for item %s failed: %s", item_id, exc)
            state["done"] = n
            time.sleep(PACE)
        if ids:
            log.info("Trailer index updated: %d of %d titles had a trailer", state["found"], len(ids))
        _pick_up_new_files()
    finally:
        state.update(running=False, finished=time.time())
        from . import about
        about.request_refresh()          # then the About panel details, Wikipedia and OMDb


def request_index() -> None:
    """Start indexing in the background (does nothing if it is already running)."""
    with _lock:
        if state["running"]:
            return
        state["running"] = True
    threading.Thread(target=_run, name="trailer-index", daemon=True).start()


def _pick_up_new_files() -> None:
    """A trailer file added next to a title that already had an Apple or YouTube trailer: use the file
    (unless a trailer was chosen by hand in the editor)."""
    from .api_extras import _save_extra
    from .trailer_sources import KEY_VERSION, local_choices
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM items WHERE kind IN ('movie','show') "
            "AND COALESCE(json_extract(extra, '$.trailer'), '') NOT LIKE 'v:/api/%' "
            "AND NOT EXISTS (SELECT 1 FROM json_each(COALESCE(extra, '{}'), '$.locked_fields') WHERE value = 'trailer')")]
    n = 0
    for item in rows:
        try:
            files = local_choices(item)
        except Exception:
            continue
        if files:
            _save_extra(item["id"], trailer=files[0]["key"], trailer_name=files[0]["name"], trailer_v=KEY_VERSION)
            n += 1
    if n:
        log.info("Using %d newly added trailer files", n)
