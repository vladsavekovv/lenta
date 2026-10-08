"""Where trailers come from, best first:

  1. Local trailer files next to the media (like Plex and Jellyfin):
       Movie Name-trailer.mp4, trailer.mp4, anything ending in "-trailer" / "_trailer2" …, or a "trailers" folder.
     Played straight from your server: best quality, starts at once, no ads.
  2. Apple TV (iTunes) trailers for movies: HD video files from Apple, found by title and year (no key).
  3. YouTube trailers listed on TMDB (the fallback).

A trailer "key" is either a YouTube video id, or "v:<address>" for a video file the browser plays itself
("v:/api/items/12/trailer-file" for a local file, "v:https://…apple.com/…m4v" for Apple).
"""
import os
import re
import threading
import time

import httpx

from . import logs
from .config import VERSION
from .db import db, get_setting, jloads

log = logs.get("trailers")
KEY_VERSION = 4            # raised when the search improves: titles not chosen by hand are looked at again once
PLAYABLE = (".mp4", ".m4v", ".webm", ".mov")       # what browsers play without converting
TRAILER_NAME = re.compile(r"(^|[\W_])trailer\d*$", re.I)
_YT = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_APPLE_HOST = re.compile(r"^https://([a-z0-9-]+\.)*(apple\.com|mzstatic\.com)/", re.I)


def is_trailer_file(path: str) -> bool:
    stem = os.path.splitext(os.path.basename(path))[0]
    return bool(TRAILER_NAME.search(stem)) or os.path.basename(os.path.dirname(path)).lower() == "trailers"


def valid_key(key: str) -> bool:
    if not isinstance(key, str) or len(key) > 1000:
        return False
    if key.startswith("v:"):
        addr = key[2:]
        return bool(re.fullmatch(r"/api/items/\d+/trailer-file(\?n=\d+)?", addr) or _APPLE_HOST.match(addr))
    return bool(_YT.match(key))


# ---- YouTube videos that don't play here ------------------------------------------------------
# Uploaders can block a video in some countries ("not available in your country") or switch off
# embedding. The player reports such a video (YouTube error 100/101/150); LENTA remembers it for 60 days
# and skips it everywhere, so each title falls back to the next trailer that does play.
UNAVAILABLE_DAYS = 60
_unavail_lock = threading.Lock()


def _unavailable_map() -> dict:
    d = jloads(get_setting("yt_unavailable") or "{}", {})
    return d if isinstance(d, dict) else {}


def unavailable() -> set[str]:
    cutoff = time.time() - UNAVAILABLE_DAYS * 86400
    return {k for k, t in _unavailable_map().items() if isinstance(t, (int, float)) and t > cutoff}


def mark_unavailable(key: str) -> bool:
    if not _YT.match(key or ""):
        return False
    from .db import set_setting
    import json
    with _unavail_lock:
        cutoff = time.time() - UNAVAILABLE_DAYS * 86400
        d = {k: t for k, t in _unavailable_map().items() if isinstance(t, (int, float)) and t > cutoff}
        d[key] = time.time()
        if len(d) > 5000:
            d = dict(sorted(d.items(), key=lambda kv: kv[1])[-5000:])
        set_setting("yt_unavailable", json.dumps(d))
    log.info("YouTube video %s doesn't play here; skipping it from now on", key)
    return True


# ---- trailer links added by hand (Edit metadata › Trailers › Add a YouTube link) ------------------
_YT_URL = [re.compile(p) for p in (
    r"[?&]v=([A-Za-z0-9_-]{11})", r"youtu\.be/([A-Za-z0-9_-]{11})", r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})")]


def youtube_id(text: str) -> str | None:
    """A YouTube video id from a link (watch?v=, youtu.be/, /embed/, /shorts/, /live/) or a bare id."""
    text = (text or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    if not re.match(r"^(https?://)?([a-z0-9-]+\.)*(youtube\.com|youtube-nocookie\.com|youtu\.be)/", text, re.I):
        return None
    for rx in _YT_URL:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def youtube_check(key: str) -> dict:
    """YouTube's public oEmbed: the video's title, or why it can't be used (removed, private, not embeddable).
    A country block can't be seen from here; the player notices that when it plays."""
    try:
        with httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": f"LENTA/{VERSION}"}) as c:
            r = c.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={key}", "format": "json"})
    except Exception:
        return {"ok": True, "name": None}            # can't check now: accept it, the player will tell
    if r.status_code in (401, 403):
        return {"ok": False, "error": "The uploader doesn't allow this video to be played on other sites."}
    if r.status_code in (400, 404):
        return {"ok": False, "error": "YouTube says this video doesn't exist or is private."}
    try:
        return {"ok": True, "name": r.json().get("title")}
    except ValueError:
        return {"ok": True, "name": None}


def link_choices(item: dict) -> list[dict]:
    links = jloads(item.get("extra"), {}).get("trailer_links") or []
    return [{"key": x["key"], "name": x.get("name") or "Your YouTube link", "type": "Trailer", "source": "link"}
            for x in links if isinstance(x, dict) and _YT.match(x.get("key") or "")]


# ---- 1. local files ---------------------------------------------------------------------------

def _folders(item: dict) -> tuple[list[tuple[str, str | None]], list[str]]:
    """[(folder, file stem or None)] to look in, and the library roots."""
    with db() as con:
        lib = con.execute("SELECT paths FROM libraries WHERE id = ?", (item["library_id"],)).fetchone()
        if item["kind"] == "movie":
            rows = con.execute("SELECT path FROM files WHERE item_id = ? ORDER BY size DESC", (item["id"],)).fetchall()
        else:
            rows = con.execute("SELECT f.path FROM files f JOIN items e ON e.id = f.item_id JOIN items se ON se.id = e.parent_id "
                               "WHERE se.parent_id = ? LIMIT 1", (item["id"],)).fetchall()
    roots = [os.path.normpath(p) for p in jloads(lib["paths"] if lib else "[]")]
    out = []
    for r in rows[:1]:
        d = os.path.dirname(r["path"])
        stem = os.path.splitext(os.path.basename(r["path"]))[0]
        if item["kind"] == "show":
            root = next((rt for rt in roots if d.startswith(rt + os.sep)), None)
            if root:
                d = os.path.join(root, os.path.relpath(d, root).split(os.sep)[0])
            stem = None
        out.append((d, stem))
    return out, roots


def local_files(item: dict) -> list[str]:
    folders, roots = _folders(item)
    found = []
    for folder, stem in folders:
        own = os.path.normpath(folder) not in roots
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            base, ext = os.path.splitext(n)
            if ext.lower() not in PLAYABLE:
                continue
            if stem and base.lower().startswith(stem.lower()) and TRAILER_NAME.search(base):
                found.append(os.path.join(folder, n))           # Movie Name-trailer.mp4 (also in a shared folder)
            elif own and TRAILER_NAME.search(base):
                found.append(os.path.join(folder, n))           # trailer.mp4 in the title's own folder
        if own:
            for sub in names:
                if sub.lower() == "trailers" and os.path.isdir(os.path.join(folder, sub)):
                    try:
                        found += [os.path.join(folder, sub, n) for n in sorted(os.listdir(os.path.join(folder, sub)))
                                  if os.path.splitext(n)[1].lower() in PLAYABLE]
                    except OSError:
                        pass
    return found


def local_choices(item: dict) -> list[dict]:
    return [{"key": f"v:/api/items/{item['id']}/trailer-file" + (f"?n={i}" if i else ""),
             "name": os.path.splitext(os.path.basename(p))[0], "type": "Local file", "source": "local",
             "file": os.path.basename(p)} for i, p in enumerate(local_files(item))]


# ---- 2. Apple TV (iTunes) ---------------------------------------------------------------------

def apple_enabled() -> bool:
    return (get_setting("apple_trailers") or "1") != "0"


def _norm(t: str) -> str:
    return re.sub(r"[^0-9a-z\u0400-\u04ff]+", "", (t or "").casefold())


def _latin(t: str) -> bool:
    return bool(re.search(r"[A-Za-z]", t or ""))


def _clean_track(name: str) -> str:
    """Apple's names carry extras: "Inception (2010)", "Heat (Director's Definitive Edition)", "Up [Dubbed]"."""
    prev = None
    while prev != name:
        prev, name = name, re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", name or "")
    return name


# Apple's search allows about 20 requests a minute: the background index waits its turn; a page you open
# doesn't wait (it just skips Apple for now) and Apple saying "slow down" pauses all lookups for a minute.
_apple_lock = threading.Lock()
_apple_next = 0.0
APPLE_GAP = 3.2
apple_state = {"last_ok": None, "error": None, "error_at": None, "searches": 0}
_country_empty: dict[str, int] = {}     # stores that never return films (Apple sells none there) are skipped
_country_seen: set[str] = set()
_bad_country: set[str] = set()


def _apple_slot(wait: bool) -> bool:
    global _apple_next
    with _apple_lock:
        now = time.time()
        if now < _apple_next and not wait:
            return False
        delay = max(0.0, _apple_next - now)
        _apple_next = max(now, _apple_next) + APPLE_GAP
    if delay:
        time.sleep(delay)
    return True


class Later(Exception):
    """Apple wasn't asked this time (rate limit or no connection): look again on the next index run."""


def _apple_backoff():
    global _apple_next
    with _apple_lock:
        _apple_next = time.time() + 60


def _fail(msg: str):
    apple_state.update(error=msg, error_at=time.time())
    log.info("Apple trailers: %s", msg)
    raise Later()


# Ways of asking Apple's search for films. Apple has changed what the search returns before, so LENTA checks
# once (with a well-known film) which of these still finds films from this server, and uses that one.
VARIANTS = [
    ({"media": "movie", "entity": "movie"}, "lenta"),
    ({"media": "movie"}, "lenta"),
    ({"media": "movie", "entity": "movie"}, "browser"),
    ({"media": "movie"}, "browser"),
    ({"entity": "movie"}, "browser"),
    ({"media": "all"}, "browser"),
]
_UA = {"lenta": f"LENTA/{VERSION}",
       "browser": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"}
_variant = {"index": None, "checked": 0.0, "report": []}


def _get(term: str, country: str, params: dict, ua: str):
    with httpx.Client(timeout=10, headers={"User-Agent": _UA[ua]}, follow_redirects=True) as cl:
        return cl.get("https://itunes.apple.com/search",
                      params={"term": term, **params, "limit": 25, "country": country})


def _films(results: list) -> list:
    return [m for m in results if m.get("kind") == "feature-movie" or m.get("wrapperType") == "track" and m.get("previewUrl")
            and (m.get("trackViewUrl") or "").find("/movie/") >= 0]


def _probe(force: bool = False) -> int | None:
    """Which VARIANTS entry finds films; None when none does. Checked again every 6 hours."""
    if not force and _variant["checked"] and time.time() - _variant["checked"] < 6 * 3600:
        return _variant["index"]
    report, found = [], None
    for i, (params, ua) in enumerate(VARIANTS):
        _apple_slot(True)
        try:
            r = _get("Inception", "US", params, ua)
            res = r.json().get("results", []) if r.status_code == 200 else []
            films = _films(res)
            report.append(f"{'&'.join(f'{k}={v}' for k, v in params.items())} ({ua}): HTTP {r.status_code}, "
                          f"{len(res)} results, {len(films)} films, {sum(1 for m in films if m.get('previewUrl'))} with trailer")
            if r.status_code in (403, 429):
                _apple_backoff()
                break
            if any(m.get("previewUrl") for m in films):
                found = i
                break
        except Exception as exc:
            report.append(f"{params} ({ua}): {exc.__class__.__name__}: {exc}")
            break
    _variant.update(index=found, checked=time.time(), report=report)
    log.info("Apple trailer search check: %s", " | ".join(report))
    return found


def _search(term: str, country: str, wait: bool, force: bool = False) -> list[dict] | None:
    """Apple's films for a search, or None when that country's store doesn't answer for films."""
    if not force and apple_state["error_at"] and time.time() - apple_state["error_at"] < 120:
        raise Later()                             # Apple just failed: don't hammer it title after title
    if _variant["checked"] == 0 or time.time() - _variant["checked"] > 6 * 3600:
        if not wait:
            raise Later()                         # the check takes a few seconds: leave it to the background job
        try:
            _probe()
        except Exception as exc:
            _fail(f"Apple could not be reached ({exc.__class__.__name__}: {exc})")
    if _variant["index"] is None:                 # Apple's search returns no films at all: step aside (checked again in 6 h)
        apple_state.update(error="Apple's search sends back no films, so Apple trailers can't be found for now")
        return []
    params, ua = VARIANTS[_variant["index"]]
    if not _apple_slot(wait):
        raise Later()
    try:
        r = _get(term, country, params, ua)
    except Exception as exc:
        _fail(f"Apple could not be reached ({exc.__class__.__name__}: {exc})")
    apple_state["searches"] += 1
    if r.status_code in (403, 429):
        _apple_backoff()
        _fail(f"Apple asked LENTA to slow down (HTTP {r.status_code}); trying again later")
    if r.status_code in (400, 404) and country != "US":
        _bad_country.add(country)                 # no such store
        return None
    if r.status_code != 200:
        _fail(f"Apple answered HTTP {r.status_code}")
    try:
        results = _films(r.json().get("results", []))
    except ValueError:
        _fail("Apple sent an answer LENTA couldn't read")
    apple_state.update(last_ok=time.time(), error=None, error_at=None)
    if country != "US":
        if results:
            _country_seen.add(country)
        else:
            _country_empty[country] = _country_empty.get(country, 0) + 1
    return results


def _country_useless(c: str) -> bool:
    return c in _bad_country or (c not in _country_seen and _country_empty.get(c, 0) >= 8)


def apple_choices(item: dict, wait: bool = False) -> list[dict]:
    if item["kind"] != "movie" or not apple_enabled() or not item.get("title"):
        return []
    extra = jloads(item.get("extra"), {})
    names = [item["title"], item.get("original_title") or "", extra.get("parsed_title") or ""]
    titles = {_norm(n) for n in names} - {""}
    year = item.get("year")
    region = (get_setting("metadata_region") or "US").upper()
    # What to ask where: the local store knows the local title; the US store knows the English one. A title
    # shown in another language (metadata language bg-BG, say) is searched by its original title too.
    plan = []
    if region != "US" and not _country_useless(region):
        plan.append((item["title"], region))
    for n in dict.fromkeys([item.get("original_title") or "", item["title"], extra.get("parsed_title") or ""]):
        if n and _latin(n) and (n, "US") not in plan:
            plan.append((n, "US"))
    plan = plan[:3]
    out = []
    for term, country in plan:
        try:
            results = _search(term, country, wait)
        except Later:
            if out:
                return out
            raise
        for m in results or []:
            url = m.get("previewUrl")
            y = int((m.get("releaseDate") or "0")[:4] or 0)
            name = m.get("trackName") or ""
            if url and (_norm(_clean_track(name)) in titles or _norm(name) in titles) \
                    and (not year or not y or abs(y - year) <= 1) \
                    and _APPLE_HOST.match(url) and not any(o["key"] == "v:" + url for o in out):
                out.append({"key": "v:" + url, "name": f"{_clean_track(name)} — Apple TV trailer", "type": "Trailer",
                            "source": "apple", "published": (m.get("releaseDate") or "")[:10]})
        if out:
            break
    return out[:2]


def apple_test() -> dict:
    """Admin › Settings › Trailers › Test: can this server get trailers from Apple?"""
    region = (get_setting("metadata_region") or "US").upper()
    try:
        idx = _probe(force=True)
    except Exception as exc:
        return {"ok": False, "message": f"Apple could not be reached ({exc.__class__.__name__}: {exc})",
                "report": _variant["report"]}
    if idx is None:
        return {"ok": False, "report": _variant["report"],
                "message": "Apple answers, but its search sends back no films to this server (tried "
                           f"{len(_variant['report'])} ways). Apple trailers can't be used for now; LENTA keeps "
                           "YouTube trailers and checks again every 6 hours."}
    apple_state.update(error=None, error_at=None)
    probe = {"kind": "movie", "title": "Inception", "original_title": "Inception", "year": 2010, "extra": "{}"}
    found = apple_choices(probe, wait=True)
    msg = "Apple works: Inception has an Apple TV trailer."
    if idx:
        msg += " (Apple only answered to a different kind of search, which LENTA now uses.)"
    if region != "US":
        try:
            local = _search("Inception", region, True, force=True)
        except Later:
            local = None
        msg += (f" Apple's {region} store has films too, so LENTA asks it first, then the US store." if local
                else f" Apple's {region} store has no films, so LENTA uses the US store (by the original English title).")
    return {"ok": True, "message": msg, "report": _variant["report"], "sample": found[0]["key"][2:] if found else None}


def best(item: dict, wait: bool = False):
    """A local, Apple or KinoCheck trailer; None (the caller then uses TMDB's YouTube list) or "later"
    (a source couldn't be asked now: use YouTube for the moment and look again on the next index run)."""
    local = local_choices(item)
    if local:
        return local[0]
    blocked = unavailable()
    links = [c for c in link_choices(item) if c["key"] not in blocked]
    if links:                                     # your own links come before every online source
        return links[0]
    try:
        apple = apple_choices(item, wait)
    except Later:
        apple = None
    if apple:
        return apple[0]
    from . import kinocheck
    try:
        kc = kinocheck.choices(item, wait)
    except kinocheck.Later:
        return "later"
    if kc:
        return kc[0]
    return "later" if apple is None else None
