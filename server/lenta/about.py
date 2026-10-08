"""The extended About panel of a movie or show.

Three sources fill it:
  * TMDB (the key you already have): composer, cinematography, producers, countries, languages,
    budget, box office, the collection a film belongs to, keywords, IMDb and Wikidata ids.
    Stored in items.extra when a title is matched; older titles are refreshed in the background.
  * Wikipedia (no key): the article's opening summary, found through the title's Wikidata id,
    in the metadata language when that wiki has the article, otherwise in English.
  * OMDb (optional, free key from https://www.omdbapi.com/apikey.aspx, 1,000 lookups a day):
    IMDb rating and votes, Rotten Tomatoes, Metacritic, awards and box office.

Wikipedia and OMDb answers are cached in items.extra ("wiki", "omdb") and refreshed now and then.
"""
import json
import threading
import time
import urllib.parse
from datetime import date

import httpx
from fastapi import APIRouter, Depends, HTTPException

from . import auth, logs, metadata
from .config import VERSION
from .db import db, get_setting, jloads

log = logs.get("about")
router = APIRouter(prefix="/api", tags=["about"])

WIKI_REFRESH = 60 * 86400          # articles change slowly
OMDB_REFRESH = 14 * 86400          # ratings and votes move
RETRY_AFTER_ERROR = 86400          # a failed lookup (network, limit) is tried again the next day
OMDB_DAILY = 1000                  # OMDb's free plan
OMDB_BACKGROUND_SHARE = 800        # the background job leaves the rest for pages you open
PACE = 0.25
HEADERS = {"User-Agent": f"LENTA/{VERSION} (self-hosted media server; https://github.com/vladsavekovv/lenta)", "Accept": "application/json"}

_omdb_used = {"day": None, "count": 0}
_omdb_lock = threading.Lock()


class LookupError_(Exception):
    pass


def _save(item_id: int, **values) -> None:
    with db() as con:
        row = con.execute("SELECT extra FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return
        extra = jloads(row["extra"], {})
        extra.update(values)
        con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(extra), item_id))


def _client() -> httpx.Client:
    return httpx.Client(timeout=8, headers=HEADERS, follow_redirects=True)


def _stale(entry: dict | None, refresh: int) -> bool:
    if not entry:
        return True
    age = time.time() - (entry.get("checked") or 0)
    return age > (RETRY_AFTER_ERROR if entry.get("error") else refresh)


# ---- Wikipedia ------------------------------------------------------------------------------

def _wiki_languages() -> list[str]:
    lang = (get_setting("metadata_language") or "en-US").split("-")[0].lower()
    return [lang, "en"] if lang != "en" else ["en"]


def _wikidata_id(client: httpx.Client, extra: dict) -> str | None:
    if extra.get("wikidata_id"):
        return extra["wikidata_id"]
    imdb = extra.get("imdb_id")
    if not imdb:
        return None
    r = client.get("https://www.wikidata.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": f"haswbstatement:P345={imdb}", "format": "json"})
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def fetch_wiki(extra: dict) -> dict:
    """{"lang", "title", "extract", "url"} or {"none": True} when no article exists."""
    with _client() as client:
        qid = _wikidata_id(client, extra)
        if not qid:
            return {"none": True}
        langs = _wiki_languages()
        r = client.get("https://www.wikidata.org/w/api.php", params={
            "action": "wbgetentities", "ids": qid, "props": "sitelinks", "format": "json",
            "sitefilter": "|".join(f"{l}wiki" for l in langs)})
        r.raise_for_status()
        links = (r.json().get("entities", {}).get(qid) or {}).get("sitelinks", {})
        for lang in langs:
            title = (links.get(f"{lang}wiki") or {}).get("title")
            if not title:
                continue
            page = urllib.parse.quote(title.replace(" ", "_"), safe="")
            s = client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{page}")
            if s.status_code == 404:
                continue
            s.raise_for_status()
            d = s.json()
            if d.get("type") == "disambiguation" or not d.get("extract"):
                continue
            return {"lang": lang, "title": d.get("title") or title, "extract": d["extract"],
                    "url": (d.get("content_urls", {}).get("desktop", {}) or {}).get("page")
                    or f"https://{lang}.wikipedia.org/wiki/{page}", "qid": qid}
        return {"none": True, "qid": qid}


def wiki_enabled() -> bool:
    return (get_setting("wikipedia_summaries") or "1") != "0"


def wiki_for(item: dict, extra: dict) -> dict | None:
    if not wiki_enabled():
        return None
    cached = extra.get("wiki")
    if not _stale(cached, WIKI_REFRESH) or not (extra.get("wikidata_id") or extra.get("imdb_id")):
        return cached
    try:
        entry = fetch_wiki(extra)
    except Exception as exc:
        log.info("Wikipedia lookup for '%s' failed: %s", item.get("title"), exc)
        entry = dict(cached or {}, error=True)        # keep the old article if there was one
    entry["checked"] = time.time()
    _save(item["id"], wiki=entry)
    return entry


# ---- OMDb -----------------------------------------------------------------------------------

def omdb_key() -> str:
    return (get_setting("omdb_api_key") or "").strip()


def omdb_used_today() -> int:
    with _omdb_lock:
        if _omdb_used["day"] != date.today():
            _omdb_used.update(day=date.today(), count=0)
        return _omdb_used["count"]


def _omdb_spend() -> bool:
    with _omdb_lock:
        if _omdb_used["day"] != date.today():
            _omdb_used.update(day=date.today(), count=0)
        if _omdb_used["count"] >= OMDB_DAILY:
            return False
        _omdb_used["count"] += 1
        return True


def _na(v):
    return None if v in (None, "", "N/A") else v


def omdb_request(params: dict, key: str | None = None) -> dict:
    key = key or omdb_key()
    if not key:
        raise LookupError_("No OMDb key saved")
    with _client() as client:
        r = client.get("https://www.omdbapi.com/", params={**params, "apikey": key})
    if r.status_code == 401:
        raise LookupError_("OMDb rejected the key. Check it, and that you clicked the activation link OMDb e-mailed you.")
    r.raise_for_status()
    d = r.json()
    if d.get("Response") == "False":
        err = d.get("Error") or "OMDb error"
        if "limit" in err.lower():
            with _omdb_lock:
                _omdb_used["count"] = OMDB_DAILY
        if "key" in err.lower() or "limit" in err.lower():
            raise LookupError_(err)
    return d


def fetch_omdb(imdb_id: str) -> dict:
    d = omdb_request({"i": imdb_id, "tomatoes": "true"})
    if d.get("Response") == "False":
        return {"none": True}
    ratings = {x.get("Source"): x.get("Value") for x in d.get("Ratings") or []}
    votes = _na(d.get("imdbVotes"))
    return {
        "imdb_rating": _na(d.get("imdbRating")),
        "imdb_votes": int(votes.replace(",", "")) if votes and votes.replace(",", "").isdigit() else None,
        "rotten_tomatoes": _na(ratings.get("Rotten Tomatoes")),
        "metacritic": _na(ratings.get("Metacritic")) or (f"{d['Metascore']}/100" if _na(d.get("Metascore")) else None),
        "awards": _na(d.get("Awards")),
        "box_office": _na(d.get("BoxOffice")),
        "rated": _na(d.get("Rated")),
    }


def omdb_for(item: dict, extra: dict, budget: int = OMDB_DAILY) -> dict | None:
    cached = extra.get("omdb")
    imdb = extra.get("imdb_id")
    if not imdb or not omdb_key() or not _stale(cached, OMDB_REFRESH):
        return cached
    if omdb_used_today() >= budget or not _omdb_spend():
        return cached
    try:
        entry = fetch_omdb(imdb)
    except Exception as exc:
        log.info("OMDb lookup for '%s' failed: %s", item.get("title"), exc)
        entry = dict(cached or {}, error=True)
    entry["checked"] = time.time()
    _save(item["id"], omdb=entry)
    return entry


# ---- API ------------------------------------------------------------------------------------

def _textless_backdrop(item: dict, data: dict) -> None:
    """Swap the background picture for one without the title on it (TMDB pictures only, never one chosen by hand)."""
    new = data.get("backdrop")
    old = item.get("backdrop") or ""
    if not new or new == old or (old and not old.startswith("tmdb:")):
        return
    if "backdrop" in metadata.locked_fields(item.get("extra")):
        return
    with db() as con:
        con.execute("UPDATE items SET backdrop = ? WHERE id = ?", (new, item["id"]))


def _details_now(item: dict, extra: dict) -> dict | None:
    """A title opened before the background refresh reached it: fetch its extended TMDB fields now."""
    if (extra.get("details_v") or 0) >= metadata.DETAILS_VERSION or not item.get("tmdb_id"):
        return None
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        return None
    try:
        kind = metadata.tmdb_kind_of(item)
        data = tmdb.movie(item["tmdb_id"]) if kind == "movie" else tmdb.show(item["tmdb_id"])
    except Exception as exc:
        log.info("Detail lookup for '%s' failed: %s", item.get("title"), exc)
        return None
    fresh = {k: v for k, v in data["extra"].items() if k in metadata.DETAIL_KEYS}
    _save(item["id"], **fresh)
    _textless_backdrop(item, data)
    extra.update(fresh)                 # so Wikipedia and OMDb can use the new IMDb / Wikidata ids right away
    return fresh


def _public(entry: dict | None) -> dict | None:
    if not entry or entry.get("none") or (entry.get("error") and len(entry) <= 2):
        return None
    return {k: v for k, v in entry.items() if k not in ("checked", "error", "none")}


@router.get("/items/{item_id}/about")
def item_about(item_id: int, user: dict = Depends(auth.current_user)):
    """Wikipedia summary, OMDb ratings and the rest of the collection. Looked up on first view, then cached."""
    from .api_library import get_item
    from .serialize import cards
    item = get_item(item_id, user)
    if item["kind"] == "season":
        item = get_item(item["parent_id"], user)
    if item["kind"] not in ("movie", "show"):
        raise HTTPException(404, "Only movies and shows have an About panel")
    extra = jloads(item.get("extra"), {})
    details = _details_now(item, extra)
    out = {"details": details, "wiki": _public(wiki_for(item, extra)), "omdb": _public(omdb_for(item, extra)), "collection": None}
    coll = extra.get("collection")
    if coll and coll.get("id"):
        with db() as con:
            rows = con.execute(
                "SELECT * FROM items WHERE kind = 'movie' AND id != ? AND json_extract(extra, '$.collection.id') = ? "
                "ORDER BY year, sort_title LIMIT 24", (item["id"], coll["id"])).fetchall()
        out["collection"] = {"name": coll.get("name"), "items": cards(rows, user["id"])}
    return out


# ---- background refresh ---------------------------------------------------------------------

state = {"running": False, "phase": None, "done": 0, "total": 0, "started": None, "finished": None}
_lock = threading.Lock()


def counts() -> dict:
    with db() as con:
        row = con.execute(
            "SELECT COUNT(*) AS titles, "
            "SUM(COALESCE(json_extract(extra, '$.details_v'), 0) >= ?) AS details, "
            "SUM(json_extract(extra, '$.wiki.extract') IS NOT NULL) AS wiki, "
            "SUM(json_extract(extra, '$.omdb.checked') IS NOT NULL AND json_extract(extra, '$.omdb.none') IS NULL "
            "    AND json_extract(extra, '$.omdb.error') IS NULL) AS omdb "
            "FROM items WHERE kind IN ('movie','show') AND tmdb_id IS NOT NULL",
            (metadata.DETAILS_VERSION,)).fetchone()
    return {k: row[k] or 0 for k in ("titles", "details", "wiki", "omdb")} | {
        "omdb_enabled": bool(omdb_key()), "omdb_used_today": omdb_used_today(), **state}


def _rows(where: str, *args) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute(
            f"SELECT * FROM items WHERE kind IN ('movie','show') AND tmdb_id IS NOT NULL AND {where} "
            f"ORDER BY added_at DESC", args)]


def _refresh_details(tmdb: metadata.TMDB) -> None:
    """Older titles were matched before the extended fields existed: fetch just those fields."""
    rows = _rows("COALESCE(json_extract(extra, '$.details_v'), 0) < ?", metadata.DETAILS_VERSION)
    state.update(phase="details", done=0, total=len(rows))
    for n, item in enumerate(rows, 1):
        try:
            kind = metadata.tmdb_kind_of(item)
            data = tmdb.movie(item["tmdb_id"]) if kind == "movie" else tmdb.show(item["tmdb_id"])
            fresh = {k: v for k, v in data["extra"].items() if k in metadata.DETAIL_KEYS}
            _save(item["id"], **fresh)
            _textless_backdrop(item, data)
        except Exception as exc:
            log.info("Detail refresh for '%s' failed: %s", item["title"], exc)
            if "rejected" in str(exc):
                return
        state["done"] = n
        time.sleep(0.07)


def _refresh_online(phase: str, rows: list[dict], fn) -> None:
    state.update(phase=phase, done=0, total=len(rows))
    for n, item in enumerate(rows, 1):
        try:
            fn(item, jloads(item.get("extra"), {}))
        except Exception as exc:
            log.info("%s refresh for '%s' failed: %s", phase, item["title"], exc)
        state["done"] = n
        time.sleep(PACE)


def _run() -> None:
    try:
        state.update(started=time.time())
        tmdb = metadata.TMDB()
        if tmdb.enabled:
            _refresh_details(tmdb)
        now_ = time.time()
        wiki_due = [] if not wiki_enabled() else [r for r in _rows("(json_extract(extra, '$.imdb_id') IS NOT NULL "
                                     "OR json_extract(extra, '$.wikidata_id') IS NOT NULL)")
                    if _stale(jloads(r["extra"], {}).get("wiki"), WIKI_REFRESH)]
        _refresh_online("wikipedia", wiki_due, wiki_for)
        if omdb_key():
            omdb_due = [r for r in _rows("json_extract(extra, '$.imdb_id') IS NOT NULL")
                        if _stale(jloads(r["extra"], {}).get("omdb"), OMDB_REFRESH)]
            room = max(0, OMDB_BACKGROUND_SHARE - omdb_used_today())
            _refresh_online("omdb", omdb_due[:room],
                            lambda item, extra: omdb_for(item, extra, budget=OMDB_BACKGROUND_SHARE))
        if wiki_due:
            log.info("About panel refresh took %.0f s", time.time() - now_)
    finally:
        state.update(running=False, phase=None, finished=time.time())
        from . import imdb
        imdb.request_refresh()           # ratings for titles that now have an IMDb number


def request_refresh() -> None:
    """Start the background refresh (does nothing if it is already running)."""
    with _lock:
        if state["running"]:
            return
        state["running"] = True
    threading.Thread(target=_run, name="about-refresh", daemon=True).start()
