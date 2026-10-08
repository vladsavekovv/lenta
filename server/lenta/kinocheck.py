"""KinoCheck (https://api.kinocheck.com): official trailers picked by an editorial team, 1080p or better,
found by TMDB or IMDb id, in English and German. The videos are hosted on YouTube, so a KinoCheck trailer
is a YouTube key and plays in the same player.

Without a key KinoCheck allows 1,000 requests a day; with a free key (Admin › Settings › Trailers) more.
LENTA counts its own requests per day and stops before the limit; the background trailer index waits
its turn so pages you open always have requests left.
"""
import threading
import time

import httpx

from . import logs
from .config import VERSION
from .db import get_setting, jloads, set_setting

log = logs.get("kinocheck")
BASE = "https://api.kinocheck.com"
DAILY_NO_KEY = 950          # KinoCheck allows 1,000 a day without a key; keep a little spare
DAILY_WITH_KEY = 20000      # KinoCheck doesn't publish the keyed limit; a 429 pauses lookups anyway
GAP = 1.0                   # seconds between background requests

_lock = threading.Lock()
_next = 0.0
state = {"error": None, "error_at": None, "paused_until": 0.0, "last_raw": ""}


class Later(Exception):
    """KinoCheck wasn't asked this time (daily limit, rate limit or no connection)."""


def enabled() -> bool:
    return (get_setting("kinocheck_trailers") or "1") != "0"


def api_key() -> str:
    return (get_setting("kinocheck_api_key") or "").strip()


def _usage() -> tuple[str, int]:
    day, _, n = (get_setting("kinocheck_usage") or "").partition(":")
    today = time.strftime("%Y-%m-%d")
    return today, (int(n) if day == today and n.isdigit() else 0)


def usage() -> dict:
    _, n = _usage()
    return {"today": n, "limit": DAILY_WITH_KEY if api_key() else DAILY_NO_KEY}


def _take(wait: bool, force: bool = False) -> None:
    """Reserve one request: counts the day's usage and paces background lookups."""
    global _next
    with _lock:
        now = time.time()
        if now < state["paused_until"] and not force:
            raise Later()
        today, n = _usage()
        if n >= (DAILY_WITH_KEY if api_key() else DAILY_NO_KEY):
            raise Later()
        delay = max(0.0, _next - now) if wait else 0.0
        _next = max(now, _next) + (GAP if wait else 0)
        set_setting("kinocheck_usage", f"{today}:{n + 1}")
    if delay:
        time.sleep(delay)


def _fail(msg: str, pause: float = 120) -> None:
    state.update(error=msg, error_at=time.time(), paused_until=time.time() + pause)
    log.info("KinoCheck: %s", msg)
    raise Later()


def request(kind: str, params: dict, wait: bool = False, key: str | None = None, force: bool = False) -> dict | None:
    """One KinoCheck lookup (/movies or /shows). None when KinoCheck doesn't know the title."""
    _take(wait, force)
    key = api_key() if key is None else key
    headers = {"Accept": "application/json", "User-Agent": f"LENTA/{VERSION} (personal media server)"}
    if key:
        headers.update({"X-Api-Key": key, "X-Api-Host": "api.kinocheck.com"})
    try:
        with httpx.Client(timeout=10, headers=headers, follow_redirects=True) as c:
            r = c.get(f"{BASE}/{'shows' if kind == 'show' else 'movies'}", params=params)
    except Exception as exc:
        _fail(f"KinoCheck could not be reached ({exc.__class__.__name__})")
    if r.status_code == 404:
        state.update(error=None, error_at=None)
        return None
    if r.status_code == 429:
        _fail("KinoCheck's request limit was reached; trying again later", pause=3600)
    if r.status_code in (401, 403):
        _fail("KinoCheck rejected the API key. Check it in Admin › Settings › Trailers", pause=3600)
    if r.status_code != 200:
        _fail(f"KinoCheck answered HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        _fail("KinoCheck sent an answer LENTA couldn't read")
    state.update(error=None, error_at=None)
    state["last_raw"] = r.text[:600]
    return data if isinstance(data, dict) and data else None


def _languages() -> list[str]:
    lang = (get_setting("metadata_language") or "en-US")[:2].lower()
    return ["de", "en"] if lang == "de" else ["en"]


def _yt(v: dict) -> str | None:
    for k in ("youtube_video_id", "youtube_id", "video_id"):
        if isinstance(v.get(k), str) and v[k]:
            return v[k]
    yt = v.get("youtube")
    return _yt(yt) if isinstance(yt, dict) else None


def _as_list(v) -> list:
    """Every video in a value, however KinoCheck nests it (an object, a list, or lists of lists)."""
    out = []
    if isinstance(v, dict):
        if _yt(v):
            out.append(v)
        else:
            for x in v.values():
                if isinstance(x, (dict, list)):
                    out += _as_list(x)
    elif isinstance(v, list):
        for x in v:
            out += _as_list(x)
    return out


def _videos(data: dict) -> list:
    """The title's videos, main trailer first."""
    vids = _as_list(data.get("trailer")) + _as_list(data.get("videos"))
    if not vids:                                   # unknown layout: look everywhere except recommendations
        vids = _as_list({k: v for k, v in data.items() if k not in ("recommendations", "_metadata")})
    return vids


def choices(item: dict, wait: bool = False) -> list[dict]:
    """The title's trailers on KinoCheck, best first, as YouTube choices."""
    if item.get("kind") not in ("movie", "show") or not enabled():
        return []
    extra = jloads(item.get("extra"), {})
    ident = ({"tmdb_id": item["tmdb_id"]} if item.get("tmdb_id")
             else {"imdb_id": extra["imdb_id"]} if extra.get("imdb_id") else None)
    if not ident:
        return []
    from . import metadata
    kind = "movie" if metadata.tmdb_kind_of(item) == "movie" else "show"
    from .trailer_sources import unavailable
    out, seen = [], set(unavailable())
    for lang in _languages():
        data = request(kind, {**ident, "language": lang, "limit": 25}, wait)
        if not data:
            continue
        for v in _videos(data):
            yt = _yt(v)
            cats = v.get("categories") or []
            if isinstance(cats, str):
                cats = [cats]
            if not yt or yt in seen or (cats and not set(cats) & {"Trailer", "Teaser"}):
                continue
            seen.add(yt)
            out.append({"key": yt, "name": v.get("title") or "Trailer", "type": (cats or ["Trailer"])[0],
                        "source": "kinocheck", "language": v.get("language") or lang,
                        "published": (v.get("published") or "")[:10]})
        if out:
            break
    trailers = [c for c in out if c["type"] == "Trailer"]
    return (trailers + [c for c in out if c["type"] != "Trailer"])[:4]


def test(key: str | None = None) -> dict:
    """Admin › Settings › Trailers › Test KinoCheck: Inception's trailers."""
    before = dict(state)
    try:
        data = request("movie", {"tmdb_id": 27205, "categories": "Trailer,Teaser", "language": "en"}, key=key, force=True)
    except Later:
        msg = state["error"] if state["error_at"] and state["error_at"] != before["error_at"] else None
        if key and key != api_key():
            state.update(before)                # a key typed but not saved yet mustn't pause the saved one
        return {"ok": False, "message": msg or "KinoCheck's daily limit is used up; try again tomorrow."}
    tried = [("en, trailers and teasers", data)]
    n = len({_yt(v) for v in _videos(data or {})})
    if not n:                                      # ask once more without filters, to tell "no videos" from "filter"
        try:
            data2 = request("movie", {"tmdb_id": 27205}, key=key, force=True)
        except Later:
            data2 = None
        tried.append(("no filters", data2))
        n = len({_yt(v) for v in _videos(data2 or {})})
    u = usage()
    used = (f" Used today: {u['today']} of {u['limit']} lookups"
            + (" (with your API key)." if (key or api_key()) else " (no key: 1,000 a day)."))
    if not n:
        raw = state.get("last_raw") or ""
        return {"ok": False, "message": "KinoCheck answered but LENTA found no trailers for Inception in the answer." + used,
                "report": [f"{label}: " + (", ".join(f"{k}={type(v).__name__}{'[' + str(len(v)) + ']' if isinstance(v, list) else ''}"
                                                       for k, v in d.items()) if isinstance(d, dict) else f"{label}: no answer")
                           for label, d in tried] + ["Raw answer (start): " + raw]}
    return {"ok": True, "message": f"KinoCheck works: {n} trailer{'s' if n != 1 else ''} for Inception." + used}
