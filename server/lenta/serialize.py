"""Turning database rows into the JSON shapes the web client uses."""
import json

from . import images
from .db import db, jloads

TOP_KIND = {"movies": "movie", "shows": "show", "music": "album", "photos": "photoalbum"}


def resolution_label(width: int | None, height: int | None) -> str:
    if not height:
        return ""
    w = width or 0
    if height >= 2000 or w >= 3800:
        return "4K"
    if height >= 1400 or w >= 2500:
        return "1440p"
    if height >= 1000 or w >= 1900:
        return "1080p"
    if height >= 700 or w >= 1260:
        return "720p"
    if height >= 560:
        return "576p"
    return "SD"


def file_info(row) -> dict:
    from . import subtitles as subs_mod
    from .languages import normalize
    f = dict(row)
    audio = jloads(f.get("audio"))
    subs = jloads(f.get("subtitles")) + subs_mod.downloaded(f["id"])
    return {
        "id": f["id"], "container": f.get("container"), "size": f.get("size"), "duration": f.get("duration"),
        "bitrate": f.get("bitrate"), "video_codec": f.get("video_codec"), "width": f.get("width"),
        "height": f.get("height"), "hdr": bool(f.get("hdr")),
        "resolution": resolution_label(f.get("width"), f.get("height")),
        "filename": (f.get("path") or "").rsplit("/", 1)[-1],
        "audio": [{"index": a["index"], "codec": a.get("codec"), "channels": a.get("channels"),
                   "language": a.get("language"), "lang": normalize(a.get("language")), "title": a.get("title"),
                   "default": a.get("default")} for a in audio],
        "subtitles": [{"key": s["key"], "codec": s.get("codec"), "language": s.get("language"),
                       "lang": normalize(s.get("language")), "title": s.get("title"), "forced": s.get("forced"),
                       "text": s.get("text"), "image": s.get("image"), "external": bool(s.get("external")),
                       "downloaded": bool(s.get("downloaded")), "dl_id": s.get("dl_id"),
                       "hearing_impaired": bool(s.get("hearing_impaired"))} for s in subs],
    }


def _trailer_key(r: dict) -> str | None:
    """The indexed YouTube trailer (see trailers.py), so the client can start it without asking."""
    extra = r.get("extra")
    if not extra or '"trailer"' not in extra:
        return None
    try:
        return json.loads(extra).get("trailer") or None
    except ValueError:
        return None


def _home_trailer(r: dict) -> bool:
    """Ticked in Edit › General. Parsed, not text-matched: SQLite's json_set (IMDb ratings) stores extra compactly."""
    extra = r.get("extra")
    if not extra or '"home_trailer"' not in extra:
        return False
    try:
        return json.loads(extra).get("home_trailer") is True
    except ValueError:
        return False


def _clean() -> set:
    from .metadata import clean_set
    return clean_set()


def card(row, state: dict | None = None) -> dict:
    r = dict(row)
    kind = r["kind"]
    out = {
        "id": r["id"], "kind": kind, "title": r["title"], "year": r.get("year"),
        "library_id": r["library_id"], "parent_id": r.get("parent_id"),
        "rating": round(r["rating"], 1) if r.get("rating") else None,
        "certification": r.get("certification") or None, "runtime": r.get("runtime"),
        "genres": jloads(r.get("genres")), "overview": r.get("overview"), "tagline": r.get("tagline"),
        "poster": images.url(r.get("poster")), "backdrop": images.url(r.get("backdrop")),
        "logo": images.url(r.get("logo")), "thumb": images.url(r.get("thumb")),
        "backdrop_clean": bool(r.get("backdrop")) and r.get("backdrop") in _clean(),
        "home_trailer": kind in ("movie", "show") and _home_trailer(r),
        "artist": r.get("artist"), "index": r.get("index_number"), "season": r.get("parent_index"),
        "added_at": r.get("added_at"), "release_date": r.get("release_date"), "matched": bool(r.get("matched")),
        "trailer": _trailer_key(r) if kind in ("movie", "show") else None,
    }
    if kind == "photo":
        out["thumb"] = f"/api/photos/{r['id']}/thumb"
        out["full"] = f"/api/photos/{r['id']}/full"
        extra = jloads(r.get("extra"), {})
        out.update(width=extra.get("width"), height=extra.get("height"), taken=r.get("release_date"),
                   camera=extra.get("camera"))
    if "cover_photo" in r and r["cover_photo"]:
        out["thumb"] = f"/api/photos/{r['cover_photo']}/thumb"
    for extra_key in ("show_id", "show_title", "child_count", "unwatched"):
        if extra_key in r and r[extra_key] is not None:
            out[extra_key] = r[extra_key]
    if r.get("show_backdrop") or r.get("show_poster"):
        out["show_backdrop"] = images.url(r.get("show_backdrop"))
        out["show_poster"] = images.url(r.get("show_poster"))
        # the logo only goes on the show's background picture when that picture has no title written on it
        out["show_logo"] = images.url(r.get("show_logo")) if r.get("show_backdrop_clean") else None
        if not out["backdrop"]:
            out["backdrop"] = out["show_backdrop"]
    if state:
        out["progress"] = {"position": state.get("position") or 0, "duration": state.get("duration") or 0,
                           "completed": bool(state.get("completed")), "updated_at": state.get("updated_at")}
    return out


def states_for(user_id: int, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    out = {}
    with db() as con:
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            for r in con.execute(f"SELECT * FROM watch_state WHERE user_id = ? AND item_id IN "
                                 f"({','.join('?' * len(part))})", (user_id, *part)):
                out[r["item_id"]] = dict(r)
    return out


def cards(rows, user_id: int) -> list[dict]:
    rows = [dict(r) for r in rows]
    states = states_for(user_id, [r["id"] for r in rows if r["kind"] in ("movie", "episode")])
    return [card(r, states.get(r["id"])) for r in rows]


EPISODE_JOIN = """
    SELECT e.*, sh.id AS show_id, sh.title AS show_title, sh.backdrop AS show_backdrop,
           sh.poster AS show_poster, sh.logo AS show_logo,
           EXISTS(SELECT 1 FROM clean_art c WHERE c.ref = sh.backdrop) AS show_backdrop_clean
    FROM items e JOIN items se ON se.id = e.parent_id JOIN items sh ON sh.id = se.parent_id
"""

# Shows and seasons get counts of episodes the user has not finished.
UNWATCHED_SHOW = """
    (SELECT COUNT(*) FROM items e JOIN items se ON se.id = e.parent_id
     WHERE se.parent_id = i.id AND e.kind = 'episode' AND se.parent_index > 0
       AND e.id NOT IN (SELECT item_id FROM watch_state WHERE user_id = :uid AND completed = 1)) AS unwatched
"""

PHOTO_COVER = "(SELECT p.id FROM items p WHERE p.parent_id = i.id ORDER BY p.sort_title DESC LIMIT 1) AS cover_photo"
CHILD_COUNT = "(SELECT COUNT(*) FROM items c WHERE c.parent_id = i.id) AS child_count"


def dumps(value) -> str:
    return json.dumps(value)
