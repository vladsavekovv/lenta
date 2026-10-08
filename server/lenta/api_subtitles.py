"""Online subtitles: search and download from OpenSubtitles, automatic download during playback."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, languages, logs, metadata, opensubtitles, subtitles
from .api_library import get_item
from .api_playback import _file_row
from .config import DATA_DIR
from .db import db, now

router = APIRouter(prefix="/api/subtitles")
log = logs.get("subtitles")
_misses: dict[tuple[int, str], float] = {}     # (file, language) -> time a search found nothing
MISS_TTL = 6 * 3600


def _context(item_id: int, file_id: int | None, user: dict) -> tuple[dict, dict, dict]:
    """Item, file row and the search parameters that identify it on OpenSubtitles."""
    item = get_item(item_id, user)
    if item["kind"] not in ("movie", "episode"):
        raise HTTPException(400, "Subtitles are for movies and episodes")
    with db() as con:
        if file_id:
            row = con.execute("SELECT id FROM files WHERE id = ? AND item_id = ?", (file_id, item_id)).fetchone()
        else:
            row = con.execute("SELECT id FROM files WHERE item_id = ? ORDER BY height DESC LIMIT 1",
                              (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No file for this title")
        f = _file_row(row["id"])
        params = {"moviehash": opensubtitles.movie_hash(f["path"])}
        if item["kind"] == "episode":
            show = con.execute("SELECT sh.* FROM items se JOIN items sh ON sh.id = se.parent_id WHERE se.id = ?",
                               (item["parent_id"],)).fetchone()
            params.update(kind="episode", parent_tmdb_id=show["tmdb_id"] if show else None,
                          query=(show["original_title"] or show["title"]) if show else None,
                          season=item["parent_index"], episode=item["index_number"])
        else:
            params.update(kind="movie", tmdb_id=item["tmdb_id"] if metadata.tmdb_kind_of(item) == "movie" else None, query=item["original_title"] or item["title"],
                          year=item["year"])
    return item, f, params


def _client() -> opensubtitles.OpenSubtitles:
    client = opensubtitles.OpenSubtitles()
    if not client.enabled:
        raise HTTPException(400, "Online subtitles are off. An administrator can add an OpenSubtitles API key "
                                 "in Server admin › Settings.")
    return client


def _entry(sub: dict) -> dict:
    return {"key": sub["key"], "codec": sub.get("codec"), "language": sub.get("language"),
            "lang": languages.normalize(sub.get("language")), "title": sub.get("title"),
            "forced": sub.get("forced"), "text": sub.get("text"), "image": sub.get("image"),
            "external": True, "downloaded": True, "dl_id": sub.get("dl_id"),
            "hearing_impaired": sub.get("hearing_impaired", False)}


@router.get("/search")
def search(item_id: int, language: str, file_id: int | None = None, user: dict = Depends(auth.current_user)):
    lang = languages.normalize(language)
    if lang not in languages.CODES:
        raise HTTPException(400, "Choose a language")
    item, f, params = _context(item_id, file_id, user)
    client = _client()
    try:
        results = client.search(language=lang, **params)
        if not results and (params.get("tmdb_id") or params.get("parent_tmdb_id")):
            # Some subtitles are only indexed by title: try once more by name.
            fallback = {**params, "tmdb_id": None, "parent_tmdb_id": None}
            results = client.search(language=lang, **fallback)
    except opensubtitles.SubtitleError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        log.warning("Subtitle search failed: %s", exc)
        raise HTTPException(502, "OpenSubtitles could not be reached. Check the server's internet connection.")
    with db() as con:
        have = {r["provider_id"] for r in con.execute(
            "SELECT provider_id FROM subtitle_downloads WHERE file_id = ?", (f["id"],))}
    for r in results:
        r["have"] = r["provider_id"] in have
    return {"file_id": f["id"], "language": lang, "results": results[:40]}


class DownloadBody(BaseModel):
    item_id: int
    file_id: int | None = None
    provider_id: str
    language: str
    release: str | None = None
    hearing_impaired: bool = False


def _store(f: dict, user: dict, provider_id: str, language: str, release: str | None, hi: bool,
           client: opensubtitles.OpenSubtitles) -> dict:
    with db() as con:
        existing = con.execute("SELECT id FROM subtitle_downloads WHERE file_id = ? AND provider_id = ?",
                               (f["id"], provider_id)).fetchone()
    if existing:
        return next(s for s in subtitles.downloaded(f["id"]) if s["dl_id"] == existing["id"])
    data, name = client.download(provider_id)
    ext = (name.rsplit(".", 1)[-1] if "." in name else "srt").lower()
    if ext not in ("srt", "vtt", "ass", "ssa"):
        ext = "srt"
    folder = DATA_DIR / "subtitles" / str(f["id"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{provider_id}.{ext}"
    path.write_bytes(data)
    with db() as con:
        cur = con.execute("INSERT INTO subtitle_downloads(file_id, language, path, provider, provider_id, release, "
                          "hearing_impaired, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (f["id"], languages.normalize(language), str(path), "opensubtitles", provider_id,
                           release, int(hi), user["id"], now()))
    return next(s for s in subtitles.downloaded(f["id"]) if s["dl_id"] == cur.lastrowid)


@router.post("/download")
def download(body: DownloadBody, user: dict = Depends(auth.current_user)):
    item, f, _ = _context(body.item_id, body.file_id, user)
    client = _client()
    try:
        sub = _store(f, user, body.provider_id, body.language, body.release, body.hearing_impaired, client)
    except opensubtitles.SubtitleError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        log.warning("Subtitle download failed: %s", exc)
        raise HTTPException(502, "OpenSubtitles could not be reached. Check the server's internet connection.")
    _misses.pop((f["id"], languages.normalize(body.language)), None)
    return {"subtitle": _entry(sub)}


class AutoBody(BaseModel):
    item_id: int
    file_id: int | None = None
    language: str


@router.post("/auto")
def auto(body: AutoBody, user: dict = Depends(auth.current_user)):
    """Make sure a subtitle in this language exists for the file: use one we have, else fetch the best match."""
    lang = languages.normalize(body.language)
    item, f, params = _context(body.item_id, body.file_id, user)
    for s in f["subtitles"]:
        if s.get("text") and languages.same(s.get("language"), lang) and not s.get("forced"):
            return {"status": "existing", "subtitle": None}
    client = opensubtitles.OpenSubtitles()
    if not client.enabled:
        return {"status": "disabled", "subtitle": None}
    if time.time() - _misses.get((f["id"], lang), 0) < MISS_TTL:
        return {"status": "none", "subtitle": None}
    try:
        results = client.search(language=lang, **params)
        if not results and (params.get("tmdb_id") or params.get("parent_tmdb_id")):
            results = client.search(language=lang, **{**params, "tmdb_id": None, "parent_tmdb_id": None})
        results = [r for r in results if not r["machine_translated"]] or results
        if not results:
            _misses[(f["id"], lang)] = time.time()
            return {"status": "none", "subtitle": None}
        best = results[0]
        sub = _store(f, user, best["provider_id"], lang, best["release"], best["hearing_impaired"], client)
        log.info("Auto-downloaded %s subtitles for '%s' (%s)", lang, item["title"],
                 "hash match" if best["hash_match"] else f"{best['downloads']} downloads")
        return {"status": "downloaded", "subtitle": _entry(sub), "hash_match": best["hash_match"]}
    except opensubtitles.SubtitleError as exc:
        return {"status": "error", "subtitle": None, "message": str(exc)}
    except Exception as exc:
        log.warning("Automatic subtitle download failed: %s", exc)
        return {"status": "error", "subtitle": None, "message": "OpenSubtitles could not be reached"}


@router.delete("/{dl_id}")
def remove(dl_id: int, user: dict = Depends(auth.current_user)):
    with db() as con:
        row = con.execute("SELECT * FROM subtitle_downloads WHERE id = ?", (dl_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Subtitle not found")
        if row["created_by"] != user["id"] and not user["is_admin"]:
            raise HTTPException(403, "Only the person who downloaded it or an administrator can remove it")
        con.execute("DELETE FROM subtitle_downloads WHERE id = ?", (dl_id,))
    try:
        from pathlib import Path
        Path(row["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}
