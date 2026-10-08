"""Artwork sources (like Jellyfin's image fetchers): where posters, backgrounds and logos come from.

Each media type (movies, TV shows) has its own ordered list of sources, each switched on or off in
Admin › Settings › Artwork sources. For every picture type the first source in the list that has one wins;
the editor's Poster / Background / Logo tabs offer the pictures of every switched-on source.

  local     Local images: poster.jpg, fanart.jpg, logo.png … next to the files
  embedded  Embedded images: cover art stored inside MKV (attachments) and MP4 files
  tmdb      TheMovieDb
  fanart    Fanart.tv (free personal API key): HD posters, backgrounds and clear logos
  omdb      The Open Movie Database (the OMDb key from About panel extras): posters
  anilist   AniList (no key): anime covers and banners
  screen    Screen grabber: a frame from the video when nothing else has a picture

Pictures from the web are stored as web: references and downloaded the first time they are shown.
Pictures you pick or upload in the editor are locked and never replaced.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time

import httpx

from . import images, logs, metadata
from .config import FFMPEG, FFPROBE, VERSION
from .db import db, get_setting, jloads

log = logs.get("artwork")
TYPES = ("poster", "backdrop", "logo")

SOURCES = {
    "local":    {"name": "Local images", "hint": "poster.jpg, fanart.jpg, logo.png … next to the files", "types": TYPES},
    "embedded": {"name": "Embedded images", "hint": "cover art stored inside MKV and MP4 files", "types": ("poster", "backdrop")},
    "tmdb":     {"name": "TheMovieDb", "hint": "needs the TMDB key (Metadata)", "types": TYPES},
    "fanart":   {"name": "Fanart.tv", "hint": "HD posters, backgrounds and clear logos — needs a free Fanart.tv key", "types": TYPES},
    "omdb":     {"name": "The Open Movie Database", "hint": "posters — uses the OMDb key (About panel extras)", "types": ("poster",)},
    "anilist":  {"name": "AniList", "hint": "anime covers and banners, no key needed", "types": ("poster", "backdrop")},
    "screen":   {"name": "Screen grabber", "hint": "a frame from the video when no source has a picture", "types": ()},
}
DEFAULT_ORDER = {
    "movie": [("local", True), ("tmdb", True), ("fanart", True), ("omdb", True), ("embedded", True), ("anilist", False), ("screen", True)],
    "show": [("local", True), ("tmdb", True), ("fanart", True), ("omdb", True), ("embedded", False), ("anilist", False), ("screen", True)],
}
HEADERS = {"User-Agent": f"LENTA/{VERSION} (personal media server)"}
POSTER_NAMES = ("poster", "folder", "cover", "movie", "show")
BACKDROP_NAMES = ("fanart", "backdrop", "background", "art")
LOGO_NAMES = ("logo", "clearlogo")


# ---- settings ----------------------------------------------------------------------------------

def order(kind: str) -> list[dict]:
    """[{id, name, hint, enabled, types}] for 'movie' or 'show', in the saved order (new sources appended)."""
    kind = "show" if kind == "show" else "movie"
    saved = jloads(get_setting(f"artwork_sources_{kind}") or "", None)
    pairs = [(s["id"], bool(s.get("enabled"))) for s in saved if s.get("id") in SOURCES] if isinstance(saved, list) else []
    known = {p[0] for p in pairs}
    pairs += [p for p in DEFAULT_ORDER[kind] if p[0] not in known]
    return [{"id": sid, "enabled": on, **{k: v for k, v in SOURCES[sid].items() if k != "types"},
             "types": list(SOURCES[sid]["types"])} for sid, on in pairs]


def validate_order(value) -> str:
    if not isinstance(value, list) or not all(isinstance(s, dict) and s.get("id") in SOURCES for s in value):
        raise ValueError("Unknown artwork source")
    return json.dumps([{"id": s["id"], "enabled": bool(s.get("enabled"))} for s in value])


def enabled(kind: str, source: str) -> bool:
    return any(s["id"] == source and s["enabled"] for s in order(kind))


def fanart_key() -> str:
    """The project API key (Fanart.tv's api_key); a personal key alone is tried as a last resort."""
    return (get_setting("fanart_api_key") or "").strip() or fanart_client_key()


def fanart_client_key() -> str:
    return (get_setting("fanart_client_key") or "").strip()


def _lang() -> str:
    return (get_setting("metadata_language") or "en-US").split("-")[0].lower()


class Skip(Exception):
    """A source that can't look this title up, and why (shown in the editor)."""


# ---- the sources -------------------------------------------------------------------------------
# Each returns {type: [ {ref, source, size?, lang?}, … best first ]}

def _client() -> httpx.Client:
    return httpx.Client(timeout=12, headers=HEADERS, follow_redirects=True)


def _item_files(item: dict) -> list[str]:
    with db() as con:
        if item["kind"] == "movie":
            rows = con.execute("SELECT path FROM files WHERE item_id = ? ORDER BY size DESC", (item["id"],)).fetchall()
        else:
            rows = con.execute("SELECT f.path FROM files f JOIN items e ON e.id = f.item_id JOIN items se ON se.id = e.parent_id "
                               "WHERE se.parent_id = ? ORDER BY (se.parent_index = 0), se.parent_index, e.index_number LIMIT 1",
                               (item["id"],)).fetchall()
    return [r["path"] for r in rows]


def _find(directory: str, names: tuple, stem: str | None) -> str | None:
    try:
        entries = {e.lower(): e for e in os.listdir(directory)}
    except OSError:
        return None
    bases = ([f"{stem}-{n}" for n in names] if stem else []) + list(names)
    for base in bases:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            hit = entries.get(f"{base.lower()}{ext}")
            if hit:
                return os.path.join(directory, hit)
    return None


def src_local(item: dict) -> dict:
    files = _item_files(item)
    if not files:
        return {}
    with db() as con:
        lib = con.execute("SELECT paths FROM libraries WHERE id = ?", (item["library_id"],)).fetchone()
    roots = [os.path.normpath(p) for p in jloads(lib["paths"] if lib else "[]")]
    path = files[0]
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0] if item["kind"] == "movie" else None
    if item["kind"] == "show":                    # the show's top folder
        root = next((r for r in roots if directory.startswith(r + os.sep)), None)
        if root:
            directory = os.path.join(root, os.path.relpath(directory, root).split(os.sep)[0])
    own_folder = os.path.normpath(directory) not in roots
    out = {}
    for typ, names in (("poster", POSTER_NAMES), ("backdrop", BACKDROP_NAMES), ("logo", LOGO_NAMES)):
        hit = _find(directory, names if own_folder else (), stem)
        if hit:
            ref = images.save_local_file(hit)
            if ref:
                out[typ] = [{"ref": ref, "source": "Local images", "size": os.path.basename(hit)}]
    return out


def src_embedded(item: dict) -> dict:
    """MKV attachments (cover.jpg = poster, cover_land.jpg = background) and attached pictures (MP4 covr)."""
    files = _item_files(item)
    if not files:
        return {}
    path = files[0]
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-of", "json", path],
                           capture_output=True, text=True, timeout=30)
        streams = json.loads(r.stdout or "{}").get("streams", [])
    except Exception:
        return {}
    out = {}
    for s in streams:
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        name = (tags.get("filename") or "").lower()
        mime = (tags.get("mimetype") or "").lower()
        attached = (s.get("disposition") or {}).get("attached_pic") == 1
        if not ((s.get("codec_type") == "attachment" and mime.startswith("image/")) or attached):
            continue
        # Matroska cover art names: cover.jpg / small_cover.jpg = poster, cover_land.jpg = background
        typ = "backdrop" if re.search(r"land|backdrop|fanart|background", name) else "poster"
        data = _extract_stream(path, s["index"], attached)
        if data:
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            out.setdefault(typ, []).append({"ref": images.save_local(data, ext), "source": "Embedded images",
                                            "size": name or "attached picture"})
    return out


def _extract_stream(path: str, index: int, attached: bool) -> bytes | None:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "art")
        if attached:
            cmd = [FFMPEG, "-v", "error", "-y", "-i", path, "-map", f"0:{index}", "-frames:v", "1", "-c", "copy",
                   "-f", "image2", target]
        else:
            cmd = [FFMPEG, "-v", "error", "-y", f"-dump_attachment:{index}", target, "-i", path]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)     # dump_attachment ends with an "no output" error
            data = open(target, "rb").read() if os.path.exists(target) else None
        except Exception:
            return None
    return data if data and len(data) < 25 * 1024 * 1024 else None


def src_tmdb(item: dict, primary: dict | None = None) -> dict:
    """primary: the title's own TMDB pictures (just fetched with its details) — otherwise TMDB's lists."""
    if primary is not None:
        return {t: [{"ref": primary[t], "source": "TheMovieDb"}] for t in TYPES if primary.get(t)}
    if not item.get("tmdb_id"):
        raise Skip("this title isn't matched on TMDB")
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        raise Skip("no TMDB key saved")
    kind = "movie" if metadata.tmdb_kind_of(item) == "movie" else "show"
    lists = tmdb.images(kind, item["tmdb_id"])
    return {t: [{"ref": c["ref"], "source": "TheMovieDb", "size": f"{c['width']}×{c['height']}" if c.get("width") else "",
                 "lang": c.get("lang")} for c in lists.get(t, [])] for t in TYPES}


FANART_KEYS = {
    "movie": {"poster": ("movieposter",), "backdrop": ("moviebackground",), "logo": ("hdmovielogo", "movielogo")},
    "show": {"poster": ("tvposter",), "backdrop": ("showbackground",), "logo": ("hdtvlogo", "clearlogo")},
}


def fanart_request(kind: str, ident: str, key: str | None = None, client: str | None = None) -> dict | None:
    key = key or fanart_key()
    client = fanart_client_key() if client is None else client
    path = "movies" if kind == "movie" else "tv"
    params = {"api_key": key}
    if client and client != key:
        params["client_key"] = client          # personal key: pictures approved recently show up sooner
    with _client() as c:
        r = c.get(f"https://webservice.fanart.tv/v3/{path}/{ident}", params=params)
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise PermissionError("Fanart.tv rejected the key")
    r.raise_for_status()
    return r.json()


def src_fanart(item: dict) -> dict:
    if not fanart_key():
        raise Skip("no Fanart.tv project API key saved (Admin › Settings › Artwork sources)")
    extra = jloads(item.get("extra"), {})
    kind = "movie" if item["kind"] == "movie" else "show"
    ident = (item.get("tmdb_id") if kind == "movie" else extra.get("tvdb_id")) or (extra.get("imdb_id") if kind == "movie" else None)
    if not ident:
        raise Skip("this show has no TVDB number yet (fetched in the background after the update)"
                   if kind == "show" else "this title has no TMDB or IMDb number")
    try:
        d = fanart_request(kind, str(ident))
    except PermissionError:
        raise Skip("Fanart.tv rejected the key — it needs the project API key; check with Test Fanart.tv")
    except httpx.HTTPError as exc:
        raise Skip(f"Fanart.tv could not be reached ({exc.__class__.__name__})")
    if not d:
        raise Skip("Fanart.tv has no pictures for this title")
    lang = _lang()
    out = {}
    for typ, keys in FANART_KEYS[kind].items():
        rows = [x for k in keys for x in d.get(k, []) if x.get("url")]
        # metadata language first, then English, then pictures without text; most liked first
        rank = lambda x: (x.get("lang") == lang, x.get("lang") == "en", x.get("lang") in ("00", "", None),
                          int(x.get("likes") or 0))
        if typ == "backdrop":
            rank = lambda x: (x.get("lang") in ("00", "", None), x.get("lang") == lang, int(x.get("likes") or 0))
        rows.sort(key=rank, reverse=True)
        if typ == "backdrop":                       # backgrounds marked "00" have no words on them
            metadata.mark_clean(images.web_ref(x["url"]) for x in rows if x.get("lang") in ("00", "", None))
        if rows:
            out[typ] = [{"ref": images.web_ref(x["url"]), "source": "Fanart.tv", "lang": x.get("lang"),
                         "size": f"♥ {x.get('likes') or 0}"} for x in rows[:30]]
    return out


def src_omdb(item: dict) -> dict:
    from . import about
    extra = jloads(item.get("extra"), {})
    poster = (extra.get("omdb") or {}).get("poster")
    if not poster:
        if not about.omdb_key():
            raise Skip("no OMDb key saved (Admin › Settings › About panel extras)")
        if not extra.get("imdb_id"):
            raise Skip("this title has no IMDb number yet (fetched in the background after the update)")
        try:
            d = about.omdb_request({"i": extra["imdb_id"]})
        except about.LookupError_ as exc:
            raise Skip(f"OMDb: {exc}")
        except httpx.HTTPError as exc:
            raise Skip(f"OMDb could not be reached ({exc.__class__.__name__})")
        poster = d.get("Poster") if d.get("Poster") not in (None, "", "N/A") else None
        if not poster:
            raise Skip("OMDb has no poster for this title")
    full = re.sub(r"\._V1_.*?\.(jpg|png)$", r"._V1_.\1", poster)      # the full-size picture, not the 300 px one
    return {"poster": [{"ref": images.web_ref(full), "source": "The Open Movie Database"}]}


def _norm(t: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (t or "").casefold())


ANILIST_QUERY = """query ($s: String) { Page(perPage: 8) { media(search: $s, type: ANIME) {
  title { romaji english } format startDate { year } coverImage { extraLarge } bannerImage } } }"""


def src_anilist(item: dict) -> dict:
    title, year = item.get("title"), item.get("year")
    if not title:
        return {}
    with _client() as c:
        r = c.post("https://graphql.anilist.co", json={"query": ANILIST_QUERY, "variables": {"s": title}})
    if r.status_code != 200:
        raise Skip(f"AniList answered HTTP {r.status_code}")
    media = (((r.json().get("data") or {}).get("Page") or {}).get("media")) or []
    want_movie = item["kind"] == "movie"
    def fits(m):
        y = (m.get("startDate") or {}).get("year")
        names = {_norm(t) for t in (m.get("title") or {}).values() if t}
        return _norm(title) in names and (not year or not y or abs(y - year) <= 1) \
            and ((m.get("format") == "MOVIE") == want_movie)
    hits = [m for m in media if fits(m)]
    if not hits:
        raise Skip("no anime with this title and year on AniList")
    m = hits[0]
    out = {}
    if (m.get("coverImage") or {}).get("extraLarge"):
        out["poster"] = [{"ref": images.web_ref(m["coverImage"]["extraLarge"]), "source": "AniList"}]
    if m.get("bannerImage"):
        out["backdrop"] = [{"ref": images.web_ref(m["bannerImage"]), "source": "AniList", "size": "banner"}]
    return out


FETCH = {"local": src_local, "embedded": src_embedded, "tmdb": src_tmdb, "fanart": src_fanart,
         "omdb": src_omdb, "anilist": src_anilist}


def _fetch(source: str, item: dict, status: dict | None = None, **kw) -> dict:
    try:
        out = FETCH[source](item, **kw) or {}
        if status is not None:
            status[source] = None
        return out
    except Skip as why:
        if status is not None:
            status[source] = str(why)
        return {}
    except Exception as exc:
        log.info("%s artwork for '%s' failed: %s", SOURCES[source]["name"], item.get("title"), exc)
        if status is not None:
            status[source] = f"failed: {exc}"
        return {}


# ---- choosing -----------------------------------------------------------------------------------

def pick(item: dict, tmdb_primary: dict | None = None, types=TYPES) -> dict:
    """{type: ref} for each picture type, from the first switched-on source that has one."""
    kind = "show" if item["kind"] == "show" else "movie"
    cache, out = {}, {}
    for typ in types:
        for s in order(kind):
            if not s["enabled"] or typ not in SOURCES[s["id"]]["types"]:
                continue
            if s["id"] not in cache:
                cache[s["id"]] = _fetch(s["id"], item, **({"primary": tmdb_primary} if s["id"] == "tmdb" else {}))
            hits = cache[s["id"]].get(typ)
            if hits:
                out[typ] = hits[0]["ref"]
                break
    return out


def choices(item: dict, typ: str) -> tuple[list[dict], list[dict]]:
    """Every switched-on source's pictures of one type, in the source order (for the editor), and a line
    per source saying what it found or why it found nothing."""
    kind = "show" if item["kind"] == "show" else "movie"
    out, report = [], []
    for s in order(kind):
        if typ not in SOURCES[s["id"]]["types"]:
            continue
        if not s["enabled"]:
            report.append({"source": s["name"], "note": "switched off"})
            continue
        status = {}
        found = _fetch(s["id"], item, status=status).get(typ, [])
        out += found
        why = status.get(s["id"])
        report.append({"source": s["name"], "count": len(found),
                       "note": why or (f"{len(found)} picture{'s' if len(found) != 1 else ''}" if found else "nothing for this picture type")})
    return out, report


# ---- apply the order to the whole library -----------------------------------------------------------

state = {"running": False, "done": 0, "total": 0, "changed": 0, "started": None, "finished": None}
_lock = threading.Lock()


def _run(kinds: tuple) -> None:
    try:
        with db() as con:
            rows = [dict(r) for r in con.execute(
                f"SELECT * FROM items WHERE kind IN ({','.join('?' * len(kinds))}) AND matched = 1 ORDER BY added_at DESC", kinds)]
        state.update(done=0, total=len(rows), changed=0, started=time.time())
        for n, item in enumerate(rows, 1):
            locks = metadata.locked_fields(item.get("extra"))
            types = tuple(t for t in TYPES if t not in locks)
            if types:
                new = pick(item, types=types)
                changed = {t: r for t, r in new.items() if r != item.get(t)}
                if changed:
                    with db() as con:
                        metadata._update(con, item["id"], changed)
                    state["changed"] += 1
            state["done"] = n
            time.sleep(0.15)
        log.info("Artwork order applied: %d of %d titles got new pictures", state["changed"], len(rows))
    finally:
        state.update(running=False, finished=time.time())


def request_apply(kinds=("movie", "show")) -> dict:
    with _lock:
        if state["running"]:
            return state
        state["running"] = True
    threading.Thread(target=_run, args=(tuple(kinds),), name="artwork-apply", daemon=True).start()
    return state
