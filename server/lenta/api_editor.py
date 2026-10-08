"""Edit metadata by hand (Plex style): every field, artwork, and per-field locks.

A field edited here is locked: online refreshes keep it (metadata.locked_fields / _update).
"""
import io
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import auth, images, logs, metadata
from .db import db, jloads, now

log = logs.get("editor")
router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.admin_user)])

# Which fields each kind of item offers. Columns unless listed in metadata.EXTRA_LOCKABLE.
FIELDS = {
    "movie": ["title", "sort_title", "original_title", "edition", "release_date", "year", "certification", "rating",
              "runtime", "studios", "tagline", "overview", "genres", "directors", "writers"],
    "show": ["title", "sort_title", "original_title", "release_date", "year", "certification", "rating", "studios",
             "tagline", "overview", "genres", "creators"],
    "season": ["title", "year", "overview"],
    "episode": ["title", "release_date", "year", "rating", "runtime", "overview"],
    "album": ["title", "sort_title", "artist", "year", "genres", "overview"],
    "track": ["title", "artist"],
    "photoalbum": ["title", "overview"],
    "photo": ["title"],
}
TRAILER_CHOICES = 4             # the editor offers at most this many trailers

ARTWORK = {
    "movie": ["poster", "backdrop", "logo"], "show": ["poster", "backdrop", "logo"], "season": ["poster"],
    "episode": ["thumb"], "album": ["poster"], "photoalbum": ["poster"],
}
LIST_FIELDS = {"genres", "studios", "directors", "writers", "creators"}
TEXT_LIMITS = {"overview": 10000, "tagline": 500}


def _item(item_id: int):
    with db() as con:
        row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    return row


def _files(item_id: int) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT path, size, container, duration, video_codec, width, height, hdr FROM files WHERE item_id = ?",
            (item_id,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/items/{item_id}/metadata")
def get_metadata(item_id: int):
    row = _item(item_id)
    extra = jloads(row["extra"], {}) if row["extra"] else {}
    kind = row["kind"]
    values = {}
    for f in FIELDS.get(kind, ["title"]):
        if f == "genres":
            values[f] = jloads(row["genres"], []) if row["genres"] else []
        elif f in metadata.EXTRA_LOCKABLE:
            values[f] = extra.get(f, [] if f in LIST_FIELDS else "")
        else:
            values[f] = row[f]
    art = {a: images.url(row[a]) for a in ARTWORK.get(kind, [])}
    return {
        "id": row["id"], "kind": kind, "fields": FIELDS.get(kind, ["title"]), "values": values,
        "locked": sorted(metadata.locked_fields(extra)), "artwork": art,
        "tmdb_id": row["tmdb_id"], "tmdb_kind": metadata.tmdb_kind_of(row) if row["tmdb_id"] else None,
        "matched": bool(row["matched"]), "files": _files(item_id) if kind in ("movie", "episode", "track") else [],
        "added_at": row["added_at"], "updated_at": row["updated_at"],
        "home_trailer": bool(extra.get("home_trailer")),
    }


class MetadataBody(BaseModel):
    values: dict
    locked: list[str] = []


def _clean(field: str, value):
    if field in LIST_FIELDS:
        if isinstance(value, str):
            value = value.split(",")
        return [str(v).strip() for v in (value or []) if str(v).strip()][:30]
    if value in (None, ""):
        return None
    if field in ("year", "runtime"):
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{field.title()} must be a whole number")
        if field == "year" and not 1870 <= v <= 2100:
            raise HTTPException(400, "Year looks wrong")
        return v
    if field == "rating":
        try:
            v = round(float(value), 1)
        except (TypeError, ValueError):
            raise HTTPException(400, "Rating must be a number from 0 to 10")
        if not 0 <= v <= 10:
            raise HTTPException(400, "Rating must be from 0 to 10")
        return v
    if field == "release_date":
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            raise HTTPException(400, "Use the date format YYYY-MM-DD")
        return str(value)
    return str(value).strip()[:TEXT_LIMITS.get(field, 300)]


@router.put("/items/{item_id}/metadata")
def save_metadata(item_id: int, body: MetadataBody):
    row = _item(item_id)
    kind = row["kind"]
    allowed = FIELDS.get(kind, ["title"])
    extra = jloads(row["extra"], {}) if row["extra"] else {}
    columns: dict = {}
    for field, raw in body.values.items():
        if field not in allowed:
            continue
        value = _clean(field, raw)
        if field == "title" and not value:
            raise HTTPException(400, "Title can't be empty")
        if field == "genres":
            columns["genres"] = json.dumps(value)
        elif field in metadata.EXTRA_LOCKABLE:
            extra[field] = value or ([] if field in LIST_FIELDS else "")
        else:
            columns[field] = value
    if "title" in columns and not (body.values.get("sort_title") or "").strip() and "sort_title" in allowed:
        columns["sort_title"] = metadata.sort_title(columns["title"])
    elif "sort_title" in columns:
        columns["sort_title"] = (columns["sort_title"] or metadata.sort_title(columns.get("title") or row["title"])).lower()
    artwork = set(ARTWORK.get(kind, []))
    if kind in ("movie", "show") and "trailer" in body.values:
        key = body.values.get("trailer")
        if key:
            from . import trailer_sources
            if not trailer_sources.valid_key(key):
                raise HTTPException(400, "That isn't a trailer LENTA can play")
            extra["trailer"] = key
            extra["trailer_name"] = str(body.values.get("trailer_name") or "")[:200]
            src = body.values.get("trailer_source")
            extra["trailer_source"] = src if src in ("local", "link", "apple", "kinocheck", "youtube") else None
        else:                                      # "No trailer": stays off until unlocked
            extra.pop("trailer", None)
            extra.pop("trailer_name", None)
            extra.pop("trailer_source", None)
            import time as _t
            extra["trailer_checked"] = _t.time()
        artwork = artwork | {"trailer"}
    if kind in ("movie", "show") and isinstance(body.values.get("trailer_links"), list):
        from . import trailer_sources
        links = []
        for x in body.values["trailer_links"][:10]:
            key = trailer_sources.youtube_id(str((x or {}).get("key") if isinstance(x, dict) else x))
            if key and all(l["key"] != key for l in links):
                links.append({"key": key, "name": str((x.get("name") if isinstance(x, dict) else "") or "")[:200]})
        if links:
            extra["trailer_links"] = links
        else:
            extra.pop("trailer_links", None)
    if kind in ("movie", "show") and "home_trailer" in body.values:     # trailer in the Home banner (off unless ticked)
        if body.values.get("home_trailer") in (True, "1", 1, "true", "on"):
            extra["home_trailer"] = True
        else:
            extra.pop("home_trailer", None)
    extra["locked_fields"] = sorted(f for f in body.locked if f in allowed or f in artwork)
    columns["extra"] = json.dumps(extra)
    sets = ", ".join(f"{k} = ?" for k in columns)
    with db() as con:
        con.execute(f"UPDATE items SET {sets}, updated_at = ? WHERE id = ?", (*columns.values(), now(), item_id))
    return get_metadata(item_id)


# ---- artwork ---------------------------------------------------------------------------

@router.get("/items/{item_id}/artwork")
def artwork_choices(item_id: int, type: str):
    row = _item(item_id)
    if type not in ARTWORK.get(row["kind"], []):
        raise HTTPException(400, "This item has no such artwork")
    choices, report = [], []
    current = row[type]
    if current:
        choices.append({"ref": current, "url": images.url(current), "source": "Current"})
    if row["kind"] in ("movie", "show") and type in ("poster", "backdrop", "logo"):
        from . import artwork          # every switched-on artwork source, in their order
        seen = {current}
        found, report = artwork.choices(dict(row), type)
        for c in found:
            if c["ref"] not in seen:
                seen.add(c["ref"])
                choices.append({**c, "url": images.url(c["ref"])})
    if row["thumb"] and type in ("poster", "backdrop") and row["thumb"] != current:
        choices.append({"ref": row["thumb"], "url": images.url(row["thumb"]), "source": "Frame from the video"})
    return {"type": type, "choices": choices, "sources": report}


def _set_art(item_id: int, kind: str, ref: str) -> dict:
    row = _item(item_id)
    if kind not in ARTWORK.get(row["kind"], []):
        raise HTTPException(400, "This item has no such artwork")
    extra = jloads(row["extra"], {}) if row["extra"] else {}
    locks = metadata.locked_fields(extra) | {kind}
    extra["locked_fields"] = sorted(locks)
    with db() as con:
        con.execute(f"UPDATE items SET {kind} = ?, extra = ?, updated_at = ? WHERE id = ?",
                    (ref, json.dumps(extra), now(), item_id))
    return {"ok": True, "url": images.url(ref)}


def _store_image(data: bytes) -> str:
    from PIL import Image
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "The image is larger than 25 MB")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").lower()
            im.verify()
    except Exception:
        raise HTTPException(400, "That file isn't an image LENTA can read (use JPG, PNG or WebP)")
    ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}.get(fmt)
    if not ext:
        raise HTTPException(400, "Use a JPG, PNG or WebP image")
    return images.save_local(data, ext)


class ArtBody(BaseModel):
    type: str
    ref: str | None = None      # a choice from artwork_choices
    url: str | None = None      # an image on the web


@router.post("/items/{item_id}/artwork")
def set_artwork(item_id: int, body: ArtBody):
    if body.ref:
        if not body.ref.startswith(("tmdb:/", "local:", "web:")):
            raise HTTPException(400, "Unknown image")
        return _set_art(item_id, body.type, body.ref)
    if body.url:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "Paste a link that starts with http:// or https://")
        try:
            r = httpx.get(body.url, timeout=30, follow_redirects=True, headers={"User-Agent": "LENTA"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(400, f"Couldn't download that image: {exc}")
        return _set_art(item_id, body.type, _store_image(r.content))
    raise HTTPException(400, "Choose an image")


@router.post("/items/{item_id}/artwork/upload")
async def upload_artwork(item_id: int, type: str, request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, "No file received")
    return _set_art(item_id, type, _store_image(data))


# ---- trailers ----------------------------------------------------------------------------

@router.get("/items/{item_id}/trailers")
def trailer_choices(item_id: int):
    """Local trailer files and Apple's trailer first, then up to four YouTube trailers from TMDB."""
    row = _item(item_id)
    if row["kind"] not in ("movie", "show"):
        raise HTTPException(400, "Only movies and shows have trailers")
    extra = jloads(row["extra"], {}) if row["extra"] else {}
    current = extra.get("trailer")
    choices, note = [], None
    if not row["tmdb_id"]:
        note = "This title has no online match yet, so there are no trailers to choose from. Use Fix match first."
    else:
        tmdb = metadata.TMDB()
        if not tmdb.enabled:
            note = "Add a TMDB API key in Server admin › Settings to see trailers."
        else:
            try:
                kind = "movie" if metadata.tmdb_kind_of(row) == "movie" else "show"
                choices = tmdb.videos(kind, row["tmdb_id"])[:TRAILER_CHOICES]
            except metadata.TMDBError as exc:
                note = f"TMDB didn't answer: {exc}"
    from . import trailer_sources
    try:
        apple = trailer_sources.apple_choices(dict(row), wait=True)   # waits its turn (a few seconds at most)
    except trailer_sources.Later:
        apple = []
    from . import kinocheck
    try:
        kc = kinocheck.choices(dict(row))
    except kinocheck.Later:
        kc = []
    own, keys = [], set()
    for c in trailer_sources.local_choices(dict(row)) + trailer_sources.link_choices(dict(row)) + apple + kc:
        if c["key"] not in keys:
            own.append(c)
            keys.add(c["key"])
    choices = own + [{**c, "source": "youtube"} for c in choices if c["key"] not in keys]
    if own and note:
        note = None
    blocked = trailer_sources.unavailable()
    if current in blocked and all(c["key"] != current for c in choices):
        choices = [{"key": current, "name": extra.get("trailer_name") or "Current trailer", "type": "Trailer",
                    "source": extra.get("trailer_source") or "youtube"}] + choices
    for c in choices:
        if c["key"] in blocked:
            c["unavailable"] = True
    if current and all(c["key"] != current for c in choices):     # keep the one in use visible
        choices = ([{"key": current, "name": extra.get("trailer_name") or "Current trailer", "type": "Trailer"}]
                   + choices)[:TRAILER_CHOICES + len(own)]
    if not choices and not note:
        note = "No trailer found: no trailer file next to the title, nothing on Apple TV or KinoCheck, and none on TMDB."
    return {"current": current, "locked": "trailer" in metadata.locked_fields(extra), "choices": choices, "note": note}


# ---- refresh -----------------------------------------------------------------------------

@router.post("/items/{item_id}/trailer-link")
def trailer_link(item_id: int, body: dict):
    """Check a YouTube link pasted in the editor; the editor saves it with the title on Save Changes."""
    from . import trailer_sources
    row = _item(item_id)
    if row["kind"] not in ("movie", "show"):
        raise HTTPException(400, "Only movies and shows have trailers")
    key = trailer_sources.youtube_id(str(body.get("url") or ""))
    if not key:
        raise HTTPException(400, "That isn't a YouTube video link. Paste a link like https://www.youtube.com/watch?v=… or https://youtu.be/…")
    check = trailer_sources.youtube_check(key)
    if not check["ok"]:
        raise HTTPException(400, check["error"])
    return {"key": key, "name": check["name"] or "Your YouTube link", "type": "Trailer", "source": "link",
            "unavailable": key in trailer_sources.unavailable()}


@router.post("/items/{item_id}/refresh-metadata")
def refresh_metadata(item_id: int):
    """Fetch metadata again (Plex "Refresh Metadata"). Locked fields stay as they are. Seasons and
    episodes refresh their show; a title without a match is searched for again."""
    row = _item(item_id)
    target = row
    if row["kind"] in ("season", "episode"):
        with db() as con:
            show_id = row["parent_id"] if row["kind"] == "season" else con.execute(
                "SELECT parent_id FROM items WHERE id = ?", (row["parent_id"],)).fetchone()["parent_id"]
        target = _item(show_id)
    if target["kind"] not in ("movie", "show"):
        raise HTTPException(400, "Only movies and TV shows have online metadata")
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        raise HTTPException(400, "Add a TMDB API key in Server admin › Settings first")
    ok = metadata.match_item(target["id"], tmdb, tmdb_id=target["tmdb_id"],
                             tmdb_kind=metadata.tmdb_kind_of(target) if target["tmdb_id"] else None)
    if not ok:
        raise HTTPException(400, "No match found online. Use Fix match to pick the right title."
                            if not target["tmdb_id"] else "TMDB didn't answer. Try again in a moment.")
    return {"ok": True, "title": _item(target["id"])["title"]}
