"""Trailers and theme music for detail pages.

Trailer: YouTube key from TMDB, looked up on first visit and cached in the item.
Theme music, in order:
  1. a local file next to the media — theme.mp3 / theme.m4a / theme.flac … or a theme-music/ folder
     (the Plex / Jellyfin / Emby convention), streamed from this server;
  2. if enabled in Server admin › Settings, a 30-second soundtrack preview from the Apple iTunes Search
     API (public, no key), played by the browser straight from Apple with an Apple Music link.
"""
import json
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from . import auth, logs, metadata
from .api_library import get_item
from .db import db, get_setting, jloads

router = APIRouter(prefix="/api")
log = logs.get("extras")
RECHECK = 14 * 86400                  # retry lookups that found nothing after two weeks
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav")
ITUNES = "https://itunes.apple.com"
KEYWORDS = ("soundtrack", "motion picture", "original score", "music from", "original series",
            "television series", "theme", "score", "ost")


def _owner(item: dict) -> dict:
    """Episodes and seasons use their show's trailer and theme."""
    if item["kind"] not in ("episode", "season"):
        return item
    with db() as con:
        if item["kind"] == "episode":
            row = con.execute("SELECT sh.* FROM items se JOIN items sh ON sh.id = se.parent_id WHERE se.id = ?",
                              (item["parent_id"],)).fetchone()
        else:
            row = con.execute("SELECT * FROM items WHERE id = ?", (item["parent_id"],)).fetchone()
    return dict(row) if row else item


def _save_extra(item_id: int, **values) -> None:
    with db() as con:
        row = con.execute("SELECT extra FROM items WHERE id = ?", (item_id,)).fetchone()
        extra = jloads(row["extra"], {}) if row else {}
        extra.update(values)
        con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(extra), item_id))


# ---- trailer ------------------------------------------------------------------------------

def trailer_for(item: dict, background: bool = False) -> dict | None:
    """The title's trailer: a local trailer file, else Apple's, else YouTube's (trailer_sources.py)."""
    from . import trailer_sources
    extra = jloads(item.get("extra"), {})
    locked = "trailer" in metadata.locked_fields(extra)
    if extra.get("trailer") and (locked or (extra.get("trailer_v") or 0) >= trailer_sources.KEY_VERSION):
        return {"key": extra["trailer"], "name": extra.get("trailer_name")}
    if item["kind"] not in ("movie", "show") or locked:  # locked without a key: "No trailer" chosen in the editor
        return None
    better = trailer_sources.best(item, wait=background)
    if better == "later":                                # Apple not asked yet: don't mark this title as done
        if extra.get("trailer"):
            return {"key": extra["trailer"], "name": extra.get("trailer_name")}
        better = None
        later = True
    else:
        later = False
    if better:
        _save_extra(item["id"], trailer=better["key"], trailer_name=better["name"], trailer_checked=time.time(),
                    trailer_source=better.get("source"), trailer_v=trailer_sources.KEY_VERSION)
        return {"key": better["key"], "name": better["name"]}
    if extra.get("trailer"):                             # keep the YouTube trailer it already had
        if not later:
            _save_extra(item["id"], trailer_v=trailer_sources.KEY_VERSION)
        return {"key": extra["trailer"], "name": extra.get("trailer_name")}
    if not item.get("tmdb_id"):
        if not later:
            _save_extra(item["id"], trailer_checked=time.time(), trailer_v=trailer_sources.KEY_VERSION)
        return None
    if time.time() - (extra.get("trailer_checked") or 0) < RECHECK:
        return None
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        return None
    try:
        vids = tmdb.videos("movie" if metadata.tmdb_kind_of(item) == "movie" else "show", item["tmdb_id"])
    except Exception as exc:
        log.info("Trailer lookup for '%s' failed: %s", item["title"], exc)
        return None
    if not vids:
        _save_extra(item["id"], trailer_checked=time.time(), **({} if later else {"trailer_v": trailer_sources.KEY_VERSION}))
        return None
    _save_extra(item["id"], trailer=vids[0]["key"], trailer_name=vids[0]["name"], trailer_checked=time.time(), trailer_source="youtube",
                **({} if later else {"trailer_v": trailer_sources.KEY_VERSION}))
    return {"key": vids[0]["key"], "name": vids[0]["name"]}


@router.post("/trailers/unavailable")
def trailer_unavailable(body: dict, user: dict = Depends(auth.current_user)):
    """The player couldn't play a YouTube trailer here (blocked in this country, embedding off, removed).
    Remember it, give the titles using it their next trailer, and answer with the replacement to play now."""
    from . import trailer_sources
    key = str(body.get("key") or "")
    if not trailer_sources.mark_unavailable(key):
        raise HTTPException(400, "Not a YouTube trailer")
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM items WHERE kind IN ('movie','show') AND json_extract(extra, '$.trailer') = ?", (key,))]
    replacement = None
    for item in rows:
        extra = jloads(item.get("extra"), {})
        if "trailer" in metadata.locked_fields(extra):
            # chosen by hand: keep the choice (the editor marks it), but play something that works now
            found = _next_trailer(item)
        else:
            extra.pop("trailer", None)
            extra.update(trailer_checked=0, trailer_v=0)
            _save_extra(item["id"], trailer=None, trailer_name=None, trailer_source=None, trailer_checked=0, trailer_v=0)
            item["extra"] = json.dumps({k: v for k, v in extra.items() if v is not None})
            try:
                found = trailer_for(item)
            except Exception as exc:
                log.info("No replacement trailer for item %s: %s", item["id"], exc)
                found = None
            if not found:
                _save_extra(item["id"], trailer_checked=time.time(), trailer_v=trailer_sources.KEY_VERSION)
        replacement = replacement or found
    return {"key": replacement["key"] if replacement else None, "name": replacement.get("name") if replacement else None}


def _next_trailer(item: dict) -> dict | None:
    from . import kinocheck, trailer_sources
    better = trailer_sources.best(item)
    if isinstance(better, dict):
        return better
    try:
        if item.get("tmdb_id") and metadata.TMDB().enabled:
            vids = metadata.TMDB().videos("movie" if metadata.tmdb_kind_of(item) == "movie" else "show", item["tmdb_id"])
            return vids[0] if vids else None
    except Exception:
        return None
    return None


@router.get("/items/{item_id}/trailer-file")
def trailer_file(item_id: int, n: int = 0, user: dict = Depends(auth.current_user)):
    """A local trailer file (Movie-trailer.mp4, trailers/…), streamed with seeking."""
    from . import trailer_sources
    item = get_item(item_id, user)
    files = trailer_sources.local_files(item)
    if not 0 <= n < len(files):
        raise HTTPException(404, "No trailer file")
    return FileResponse(files[n], headers={"Cache-Control": "private, max-age=3600"})


# ---- theme music --------------------------------------------------------------------------

def _item_dirs(item: dict) -> list[str]:
    """Folders that may hold a theme file: the movie's own folder, or the show's top folder."""
    with db() as con:
        lib = con.execute("SELECT paths FROM libraries WHERE id = ?", (item["library_id"],)).fetchone()
        if item["kind"] == "movie":
            rows = con.execute("SELECT path FROM files WHERE item_id = ?", (item["id"],)).fetchall()
        else:
            rows = con.execute("SELECT f.path FROM files f JOIN items e ON e.id = f.item_id JOIN items se "
                               "ON se.id = e.parent_id WHERE se.parent_id = ? LIMIT 1", (item["id"],)).fetchall()
    roots = [os.path.normpath(p) for p in jloads(lib["paths"] if lib else "[]")]
    dirs = []
    for r in rows:
        d = os.path.dirname(r["path"])
        if item["kind"] == "show":
            root = next((rt for rt in roots if d.startswith(rt + os.sep)), None)
            if root:
                rel = os.path.relpath(d, root).split(os.sep)
                d = os.path.join(root, rel[0])
        if os.path.normpath(d) not in roots and d not in dirs:
            dirs.append(d)
    return dirs


def local_theme(item: dict) -> str | None:
    for d in _item_dirs(item):
        try:
            names = {n.lower(): n for n in os.listdir(d)}
        except OSError:
            continue
        for ext in AUDIO_EXT:
            if f"theme{ext}" in names:
                return os.path.join(d, names[f"theme{ext}"])
        if "theme-music" in names:
            folder = os.path.join(d, names["theme-music"])
            try:
                files = sorted(n for n in os.listdir(folder) if n.lower().endswith(AUDIO_EXT))
            except OSError:
                files = []
            if files:
                return os.path.join(folder, files[0])
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _search_terms(item: dict) -> list[str]:
    titles = []
    for t in (item.get("original_title"), item.get("title")):
        if t and t not in titles and re.search(r"[A-Za-z]", t):     # the iTunes store indexes Latin titles
            titles.append(t)
    return titles


def apple_theme(item: dict) -> dict | None:
    """Best soundtrack album on iTunes for this title, and its main-theme preview."""
    client = httpx.Client(timeout=15, follow_redirects=True)
    year = item.get("year")
    for title in _search_terms(item):
        want = _norm(title)
        r = client.get(f"{ITUNES}/search", params={"term": f"{title} soundtrack", "media": "music",
                                                   "entity": "album", "limit": 25})
        if r.status_code != 200:
            return None
        best, best_score = None, 0
        for a in r.json().get("results", []):
            name = _norm(a.get("collectionName"))
            if want not in name:
                continue
            score = 3
            score += 3 if a.get("primaryGenreName", "").lower() in ("soundtrack", "original score") else 0
            score += 2 if any(k in name for k in KEYWORDS) else 0
            try:
                album_year = int((a.get("releaseDate") or "")[:4])
                score += 2 if year and abs(album_year - year) <= 1 else (-2 if year and abs(album_year - year) > 3 else 0)
            except ValueError:
                pass
            score -= 1 if any(k in name for k in ("karaoke", "tribute", "lullaby", "cover", "piano version")) else 0
            if score > best_score:
                best, best_score = a, score
        if not best or best_score < 6:
            continue
        r = client.get(f"{ITUNES}/lookup", params={"id": best["collectionId"], "entity": "song"})
        tracks = [t for t in r.json().get("results", []) if t.get("wrapperType") == "track" and t.get("previewUrl")]
        if not tracks:
            continue
        tracks.sort(key=lambda t: (t.get("discNumber") or 1, t.get("trackNumber") or 0))

        def pick(t):
            n = _norm(t.get("trackName"))
            return ("main title" in n or "main theme" in n or n.startswith("theme") or want in n,)
        main = max(tracks, key=pick) if any(pick(t)[0] for t in tracks) else tracks[0]
        return {"url": main["previewUrl"], "title": main.get("trackName"), "artist": main.get("artistName"),
                "album": best.get("collectionName"), "link": main.get("trackViewUrl") or best.get("collectionViewUrl"),
                "source": "apple"}
    return None


def theme_for(item: dict) -> dict | None:
    path = local_theme(item)
    if path:
        return {"url": f"/api/items/{item['id']}/theme", "title": Path(path).stem, "artist": None,
                "album": None, "link": None, "source": "local"}
    if get_setting("theme_music_online") != "1" or item["kind"] not in ("movie", "show"):
        return None
    extra = jloads(item.get("extra"), {})
    if extra.get("theme_online"):
        return extra["theme_online"]
    if time.time() - (extra.get("theme_checked") or 0) < RECHECK:
        return None
    try:
        found = apple_theme(item)
    except Exception as exc:
        log.info("Theme music lookup for '%s' failed: %s", item["title"], exc)
        return None
    if found:
        _save_extra(item["id"], theme_online=found, theme_checked=time.time())
        log.info("Theme music for '%s': %s — %s", item["title"], found["album"], found["title"])
    else:
        _save_extra(item["id"], theme_checked=time.time())
    return found


@router.get("/items/{item_id}/extras")
def extras(item_id: int, only: str | None = None, user: dict = Depends(auth.current_user)):
    owner = _owner(get_item(item_id, user))
    if only == "trailer":                      # the Home banner needs no theme music
        return {"trailer": trailer_for(owner), "theme": None}
    return {"trailer": trailer_for(owner), "theme": theme_for(owner)}


@router.get("/items/{item_id}/theme")
def theme_file(item_id: int, user: dict = Depends(auth.current_user)):
    owner = _owner(get_item(item_id, user))
    path = local_theme(owner)
    if not path:
        raise HTTPException(404, "No theme music file")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
