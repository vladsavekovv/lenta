"""What the streaming services you pay for are showing — on Home, next to your own films.

LENTA holds your own media. This holds nothing: it is a shop window onto Netflix, Max, Disney+ and the
rest, built from TMDB's watch-provider data, which TMDB sources from JustWatch. Clicking a title opens
that service in a new tab. Nothing is played here, nothing is fetched from the service, nothing of
theirs is stored beyond the title, the picture TMDB publishes and a link.

**TMDB requires that this data be credited to JustWatch**, and says plainly that access is revoked where
it is not. The credit appears on the Streaming services settings page and under the rows themselves;
if you change how this data is displayed, keep it.

Three things shape the design:

  Home must never wait on a network.  A row is drawn from SQLite. A thread refreshes the cache once a
  day, the way imdb.py does, so a slow or unreachable TMDB makes Home no slower — the rows simply show
  what they showed yesterday, or nothing at all on a first run.

  The quota is small and the costs are lopsided.  A TMDB project gets a hundred search calls a day, and
  ten thousand units for everything else at about one unit a call. Discover and watch-provider lookups
  come out of the large pool, so a daily refresh of a few services costs a few hundred units of ten
  thousand. Nothing here searches.

  You should not be sent to Netflix for a film on your own disk.  Every title we already hold is left
  out, matched on TMDB id. Silently: a row of titles you own with "you have this" written across them
  would be worse than not showing them.
"""
import json
import threading
import time

from . import images, logs
from .config import VERSION
from .db import db, get_setting, set_setting
from .metadata import TMDB, TMDBError

log = logs.get("streaming")

MOVIE, SHOW = "movie", "show"
KINDS = (MOVIE, SHOW)
PER_ROW = 20                      # how many titles a row holds before anything is dropped
MAX_AGE = 20 * 3600               # refresh a day's worth, with some slack so it does not drift later daily
PROVIDERS_MAX_AGE = 7 * 86400     # the list of services in a country changes rarely

# TMDB's own name for each kind, in paths and in fields
_PATH = {MOVIE: "movie", SHOW: "tv"}
_TITLE = {MOVIE: "title", SHOW: "name"}
_DATE = {MOVIE: "release_date", SHOW: "first_air_date"}

state = {"running": False, "updated": None, "rows": 0, "error": None}
_lock = threading.Lock()


def region() -> str:
    """The country whose line-up to show. A service carries different things in different places, and
    getting this wrong is not a small error: it offers titles that cannot be watched from here."""
    return (get_setting("metadata_region") or "US").strip().upper()[:2] or "US"


def chosen() -> list[int]:
    """The services the household subscribes to, as TMDB provider ids."""
    try:
        got = json.loads(get_setting("streaming_services") or "[]")
    except ValueError:
        return []
    return [int(x) for x in got if str(x).isdigit()][:12] if isinstance(got, list) else []


def set_chosen(ids: list[int]) -> list[int]:
    keep = [int(x) for x in ids if str(x).isdigit()][:12]
    set_setting("streaming_services", json.dumps(keep))
    return keep


def hide_owned() -> bool:
    return (get_setting("streaming_hide_owned") or "1") != "0"


def languages_off() -> set[str]:
    """Languages whose films you would rather not be offered.

    A service's catalogue in any country carries a great deal that is simply not for you — not bad,
    not wrong, just not what you watch. Rather than a hardcoded idea of what people want, this is a
    list you make: the settings page shows which languages actually turn up in your rows, with how
    many of each, and you tick the ones to leave out.
    """
    try:
        got = json.loads(get_setting("streaming_languages_off") or "[]")
    except ValueError:
        return set()
    return {str(x).lower()[:8] for x in got if str(x).strip()} if isinstance(got, list) else set()


def set_languages_off(codes: list[str]) -> list[str]:
    keep = sorted({str(c).lower()[:8] for c in codes if str(c).strip()})[:40]
    set_setting("streaming_languages_off", json.dumps(keep))
    return keep


def enabled() -> bool:
    return bool(chosen()) and TMDB().enabled


# ---- the services on offer here ------------------------------------------------------------------
def providers(refresh: bool = False) -> list[dict]:
    """Every service available in this country, most used first.

    JustWatch orders them by how much they matter locally (`display_priority`), which is a better list
    than anything hardcoded would be: it already knows that Bulgaria has Nova Play and the US does not.
    """
    reg = region()
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM stream_providers WHERE region = ? ORDER BY priority, name", (reg,))]
    fresh = rows and time.time() - max(r["fetched_at"] for r in rows) < PROVIDERS_MAX_AGE
    if rows and fresh and not refresh:
        return rows

    tmdb = TMDB()
    if not tmdb.enabled:
        return rows
    try:
        got = tmdb._get("/watch/providers/movie", watch_region=reg).get("results", [])
    except TMDBError as exc:
        log.warning("The list of streaming services could not be fetched: %s", exc)
        return rows
    now = time.time()
    with db() as con:
        con.execute("DELETE FROM stream_providers WHERE region = ?", (reg,))
        con.executemany(
            "INSERT OR REPLACE INTO stream_providers (provider_id, name, logo, priority, region, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(int(p["provider_id"]), p.get("provider_name") or "", p.get("logo_path") or "",
              int((p.get("display_priorities") or {}).get(reg, p.get("display_priority") or 999)), reg, now)
             for p in got if p.get("provider_id")])
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM stream_providers WHERE region = ? ORDER BY priority, name", (reg,))]


def provider_names() -> dict[int, dict]:
    with db() as con:
        return {r["provider_id"]: dict(r) for r in con.execute("SELECT * FROM stream_providers")}


# ---- what each service is showing ----------------------------------------------------------------
def _discover(tmdb: TMDB, provider_id: int, kind: str) -> list[dict]:
    """One page of what this service carries here, most popular first.

    `flatrate` is the point of the filter: without it the results include everything the service will
    *rent* you, which is most of cinema and none of what a subscription actually covers.
    """
    out = []
    # One page is twenty titles, which is a full row — until a language filter removes some of them.
    # With one in use, take a second page so the row still fills rather than quietly shrinking.
    pages = 2 if languages_off() else 1
    for page in range(1, pages + 1):
        # A pipe between the numbers means "any of these", which is what picking several genres means.
        # A comma would mean "all of these at once" and would empty the row.
        ids = genre_ids(kind, tmdb)
        d = tmdb._get(f"/discover/{_PATH[kind]}",
                      watch_region=region(), with_watch_providers=str(provider_id),
                      with_watch_monetization_types="flatrate",
                      with_genres="|".join(str(i) for i in ids) if ids else None,
                      sort_by="popularity.desc", include_adult="false", page=page)
        out += d.get("results", [])
        if len(d.get("results", [])) < 20:
            break
    return out[:PER_ROW * pages]


def _watch_link(tmdb: TMDB, kind: str, tmdb_id: int) -> str:
    """Where to go to watch it. One cheap call per title, and the answer is cached for a day.

    TMDB returns a link per country that lands on the service's own page for the title. When it has
    none, the caller falls back to searching the service by name, which always lands somewhere useful
    rather than nowhere.
    """
    try:
        d = tmdb._get(f"/{_PATH[kind]}/{tmdb_id}/watch/providers", language=None)
    except TMDBError:
        return ""
    return ((d.get("results") or {}).get(region()) or {}).get("link") or ""


# ---- genres --------------------------------------------------------------------------------------
# Fifteen, the ones people actually look for. Not a copy of TMDB's list, which is longer and includes
# things nobody browses by, and not a list of numbers either: the numbers are fetched from TMDB, because
# films and series are numbered differently there and a number remembered wrongly filters the wrong
# thing silently.
#
# The two sides of that difference are worth knowing. TMDB keeps no "Science Fiction" or "Fantasy" for
# series — it has one "Sci-Fi & Fantasy" — and no Action or Adventure either, only "Action & Adventure".
# So a choice here is a name, and the name is turned into whichever numbers that kind really uses.
# Horror, Romance and Thriller exist for films and not for series at all; choosing one of those narrows
# your films and leaves your series alone, which the settings page says rather than silently doing.
GENRES: list[tuple[str, dict[str, list[str]]]] = [
    ("Action",          {MOVIE: ["Action"],          SHOW: ["Action & Adventure"]}),
    ("Adventure",       {MOVIE: ["Adventure"],       SHOW: ["Action & Adventure"]}),
    ("Animation",       {MOVIE: ["Animation"],       SHOW: ["Animation"]}),
    ("Comedy",          {MOVIE: ["Comedy"],          SHOW: ["Comedy"]}),
    ("Crime",           {MOVIE: ["Crime"],           SHOW: ["Crime"]}),
    ("Documentary",     {MOVIE: ["Documentary"],     SHOW: ["Documentary"]}),
    ("Drama",           {MOVIE: ["Drama"],           SHOW: ["Drama"]}),
    ("Family",          {MOVIE: ["Family"],          SHOW: ["Family"]}),
    ("Fantasy",         {MOVIE: ["Fantasy"],         SHOW: ["Sci-Fi & Fantasy"]}),
    ("Horror",          {MOVIE: ["Horror"],          SHOW: []}),
    ("Mystery",         {MOVIE: ["Mystery"],         SHOW: ["Mystery"]}),
    ("Romance",         {MOVIE: ["Romance"],         SHOW: []}),
    ("Science Fiction", {MOVIE: ["Science Fiction"], SHOW: ["Sci-Fi & Fantasy"]}),
    ("Thriller",        {MOVIE: ["Thriller"],        SHOW: []}),
    ("Western",         {MOVIE: ["Western"],         SHOW: ["Western"]}),
]
GENRE_NAMES = [g[0] for g in GENRES]
GENRES_MAX_AGE = 30 * 86400


def chosen_genres() -> list[str]:
    try:
        got = json.loads(get_setting("streaming_genres") or "[]")
    except ValueError:
        return []
    return [g for g in GENRE_NAMES if g in set(got)] if isinstance(got, list) else []


def set_genres(names: list[str]) -> list[str]:
    keep = [g for g in GENRE_NAMES if g in set(names or [])]
    set_setting("streaming_genres", json.dumps(keep))
    return keep


def _tmdb_genres(tmdb: TMDB | None = None) -> dict[str, dict[str, int]]:
    """TMDB's genre numbers, by kind and name. Fetched once a month; they change about never."""
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM stream_genres")]
    fresh = rows and time.time() - max(r["fetched_at"] for r in rows) < GENRES_MAX_AGE
    if not fresh:
        tmdb = tmdb or TMDB()
        if tmdb.enabled:
            got = []
            for kind in KINDS:
                try:
                    d = tmdb._get(f"/genre/{_PATH[kind]}/list")
                except TMDBError as exc:
                    log.info("Genre list for %s could not be fetched: %s", kind, exc)
                    continue
                got += [(kind, int(g["id"]), g.get("name") or "", time.time())
                        for g in d.get("genres", []) if g.get("id")]
            if got:
                with db() as con:
                    con.executemany("INSERT OR REPLACE INTO stream_genres (kind, genre_id, name, fetched_at)"
                                    " VALUES (?, ?, ?, ?)", got)
                rows = [{"kind": k, "genre_id": i, "name": n, "fetched_at": t} for k, i, n, t in got]
    out: dict[str, dict[str, int]] = {k: {} for k in KINDS}
    for r in rows:
        out.setdefault(r["kind"], {})[r["name"]] = r["genre_id"]
    return out


def genre_ids(kind: str, tmdb: TMDB | None = None) -> list[int]:
    """The TMDB numbers for the chosen genres, for this kind. Empty means do not narrow at all."""
    names = chosen_genres()
    if not names:
        return []
    known = _tmdb_genres(tmdb).get(kind, {})
    wanted = {n for name, mapping in GENRES if name in names for n in mapping.get(kind, [])}
    return sorted({known[n] for n in wanted if n in known})


def genre_state() -> dict:
    """What the settings page shows: every genre, whether it is on, and whether series have it at all."""
    picked = set(chosen_genres())
    return {"all": [{"name": name, "on": name in picked, "films_only": not mapping.get(SHOW)}
                    for name, mapping in GENRES],
            "chosen": sorted(picked)}


# ---- the title's own number on Netflix ------------------------------------------------------------
# Netflix addresses a film by a number — netflix.com/watch/81757047 — and that number is what turns a
# card into "open this film" rather than "search Netflix for this name". Netflix does not publish the
# numbers, and TMDB does not carry them. Wikidata does: property P1874, Netflix ID, filled in by people
# for the titles they care about. So the route is TMDB for the Wikidata Q-number it already knows, then
# Wikidata for the Netflix one.
#
# Coverage is partial and always will be — P1874 is volunteer work, thorough for well-known films and
# thin elsewhere. So this is an improvement on the search link, never a replacement: a title without a
# number still opens the service's search, exactly as before.
#
# A number, once found, is kept for good: Netflix does not renumber its catalogue. A title looked up and
# not found is remembered too, with the date, so the same fruitless question is not asked every night —
# but it is asked again after a month, because someone may have added it since.
ID_RETRY = 30 * 86400
WIKIDATA = "https://www.wikidata.org/w/api.php"


def _known_ids(pairs: list[tuple[int, str]]) -> dict[tuple[int, str], str]:
    with db() as con:
        return {(r["tmdb_id"], r["kind"]): r["netflix_id"] for r in con.execute("SELECT * FROM stream_ids")
                if (r["netflix_id"] or time.time() - r["checked_at"] < ID_RETRY)}


def _netflix_ids(tmdb: TMDB, pairs: list[tuple[int, str]]) -> None:
    """Find and remember the Netflix number for titles that do not have one yet."""
    import httpx

    known = _known_ids(pairs)
    todo = [p for p in pairs if p not in known]
    if not todo:
        return
    qids: dict[str, tuple[int, str]] = {}
    for tid, kind in todo[:60]:                 # a cap, so one refresh cannot run away with the quota
        try:
            ext = tmdb._get(f"/{_PATH[kind]}/{tid}/external_ids", language=None)
        except TMDBError:
            continue
        q = ext.get("wikidata_id")
        if q:
            qids[q] = (tid, kind)
    found: dict[tuple[int, str], str] = {}
    ids = list(qids)
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": f"LENTA/{VERSION} (personal media server)"}) as c:
            for batch in [ids[i:i + 50] for i in range(0, len(ids), 50)]:     # the API takes 50 at a time
                r = c.get(WIKIDATA, params={"action": "wbgetentities", "ids": "|".join(batch),
                                            "props": "claims", "format": "json"})
                r.raise_for_status()
                for q, ent in (r.json().get("entities") or {}).items():
                    claims = ((ent.get("claims") or {}).get("P1874") or [])
                    value = next((cl["mainsnak"]["datavalue"]["value"] for cl in claims
                                  if cl.get("mainsnak", {}).get("datavalue")), None)
                    if value and q in qids:
                        found[qids[q]] = str(value)
    except Exception as exc:                                  # noqa: BLE001 - Wikidata is a nicety
        log.info("Netflix numbers could not be looked up this time: %s", exc)
    now = time.time()
    with db() as con:
        con.executemany("INSERT OR REPLACE INTO stream_ids (tmdb_id, kind, netflix_id, checked_at)"
                        " VALUES (?, ?, ?, ?)",
                        [(tid, kind, found.get((tid, kind), ""), now) for tid, kind in todo[:60]])
    if found:
        log.info("Netflix numbers found for %d of %d titles", len(found), len(todo[:60]))


def netflix_ids() -> dict[tuple[int, str], str]:
    with db() as con:
        return {(r["tmdb_id"], r["kind"]): r["netflix_id"]
                for r in con.execute("SELECT * FROM stream_ids WHERE netflix_id <> ''")}


def _store(provider_id: int, kind: str, results: list[dict], links: dict[int, str]) -> int:
    now = time.time()
    rows = []
    for rank, r in enumerate(results):
        tid = r.get("id")
        if not tid:
            continue
        date = r.get(_DATE[kind]) or ""
        rows.append((provider_id, kind, int(tid), rank, r.get(_TITLE[kind]) or "",
                     int(date[:4]) if date[:4].isdigit() else None,
                     r.get("poster_path") or "", r.get("overview") or "",
                     float(r.get("vote_average") or 0) or None, links.get(int(tid), ""),
                     (r.get("original_language") or "").lower(), r.get("backdrop_path") or "", now))
    with db() as con:
        con.execute("DELETE FROM stream_titles WHERE provider_id = ? AND kind = ?", (provider_id, kind))
        con.executemany(
            "INSERT OR REPLACE INTO stream_titles (provider_id, kind, tmdb_id, rank, title, year, poster,"
            " overview, rating, link, language, backdrop, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def refresh(force: bool = False) -> int:
    """Fetch what every chosen service is showing. Returns how many titles were stored."""
    tmdb = TMDB()
    if not tmdb.enabled:
        raise TMDBError("No TMDB key: add one in Settings › Metadata.")
    ids = chosen()
    if not ids:
        return 0
    providers()                                    # keep the names and logos current too
    total = 0
    for pid in ids:
        for kind in KINDS:
            if not force and not _stale(pid, kind):
                continue
            try:
                results = _discover(tmdb, pid, kind)
            except TMDBError as exc:
                log.warning("Could not read what service %s is showing: %s", pid, exc)
                continue
            # A link is worked out from the service and the title when the row is drawn, so there is no
            # longer a call per title here. For a service we have no search address for, TMDB's own
            # "where to watch" page is fetched as a fallback — only for those.
            links = {}
            if not _SEARCH.get(((provider_names().get(pid) or {}).get("name") or "").strip().lower()):
                for r in results:
                    if r.get("id"):
                        link = _watch_link(tmdb, kind, int(r["id"]))
                        if link:
                            links[int(r["id"])] = link
            total += _store(pid, kind, results, links)
            # Only worth the calls for a service that addresses titles by number and whose numbers
            # Wikidata actually carries.
            if "netflix" in ((provider_names().get(pid) or {}).get("name") or "").lower():
                _netflix_ids(tmdb, [(int(r["id"]), kind) for r in results if r.get("id")])
    return total


def _stale(provider_id: int, kind: str) -> bool:
    with db() as con:
        row = con.execute("SELECT MAX(fetched_at) AS at FROM stream_titles WHERE provider_id = ? AND kind = ?",
                          (provider_id, kind)).fetchone()
    return not row or not row["at"] or time.time() - row["at"] > MAX_AGE


def _run(force: bool) -> None:
    try:
        state["rows"] = refresh(force)
        state["updated"] = time.time()
        state["error"] = None
        log.info("Streaming rows refreshed: %d titles", state["rows"])
    except Exception as exc:                                  # noqa: BLE001 - a background thread
        state["error"] = str(exc)
        log.warning("Streaming rows could not be refreshed: %s", exc)
    finally:
        state["running"] = False


def request_refresh(force: bool = False) -> None:
    """Refresh in the background. Does nothing when one is already running."""
    if not enabled():
        return
    with _lock:
        if state["running"]:
            return
        state["running"] = True
    threading.Thread(target=_run, args=(force,), name="streaming-refresh", daemon=True).start()


# ---- the rows ------------------------------------------------------------------------------------
def _owned() -> set[int]:
    """The TMDB ids of everything on this server, so none of it is advertised back at you."""
    if not hide_owned():
        return set()
    with db() as con:
        return {r[0] for r in con.execute(
            "SELECT DISTINCT tmdb_id FROM items WHERE tmdb_id IS NOT NULL AND kind IN ('movie', 'show')")}


ART_SHAPES = ("poster", "backdrop")


def art_overrides() -> dict[tuple[int, str], dict]:
    """Pictures chosen by hand, by title and by shape.

    One per shape, because a row can be drawn either way: a wide picture put in a poster's place would
    be cropped to a strip, and the point of choosing it was that it looked right.
    """
    with db() as con:
        return {(r["tmdb_id"], r["kind"]): {"poster": r["ref"], "backdrop": r["backdrop_ref"]}
                for r in con.execute("SELECT * FROM stream_art")}


def set_art(tmdb_id: int, kind: str, ref: str, shape: str = "poster") -> None:
    """Remember a picture for one shape of one title.

    Every column is named in the insert, including the ones being left empty. A server that made this
    table before there were two shapes has `ref NOT NULL` with no default, so an insert that skipped it
    broke the constraint — and INSERT OR IGNORE then swallowed the breakage, leaving no row for the
    update to find. Nothing failed, nothing was saved, and the answer came back "ok".
    """
    column = "backdrop_ref" if shape == "backdrop" else "ref"
    with db() as con:
        con.execute("INSERT OR IGNORE INTO stream_art (tmdb_id, kind, ref, backdrop_ref, set_at)"
                    " VALUES (?, ?, '', '', ?)", (int(tmdb_id), kind, time.time()))
        cur = con.execute(f"UPDATE stream_art SET {column} = ?, set_at = ? WHERE tmdb_id = ? AND kind = ?",
                          (ref, time.time(), int(tmdb_id), kind))
        if not cur.rowcount:            # never report success for something that did not happen
            raise RuntimeError("The picture could not be saved.")


def clear_art(tmdb_id: int, kind: str, shape: str | None = None) -> None:
    """Back to what TMDB publishes — for one shape, or for both when none is named."""
    with db() as con:
        if shape in ART_SHAPES:
            column = "backdrop_ref" if shape == "backdrop" else "ref"
            con.execute(f"UPDATE stream_art SET {column} = '' WHERE tmdb_id = ? AND kind = ?",
                        (int(tmdb_id), kind))
            con.execute("DELETE FROM stream_art WHERE tmdb_id = ? AND kind = ? AND ref = '' AND backdrop_ref = ''",
                        (int(tmdb_id), kind))
            return
        con.execute("DELETE FROM stream_art WHERE tmdb_id = ? AND kind = ?", (int(tmdb_id), kind))


def art_choices(tmdb_id: int, kind: str) -> dict:
    """Every picture TMDB holds for this title, upright and wide, best first.

    The same list the editor offers for a title of your own, from the same place the rows themselves
    came from — so what you pick here is artwork TMDB already serves, not a link to go and find.
    """
    from . import images as _images
    tmdb = TMDB()
    if not tmdb.enabled:
        raise TMDBError("No TMDB key: add one in Settings › Metadata.")
    got = tmdb.images(kind, int(tmdb_id))
    return {shape: [{"ref": c["ref"], "url": _images.url(c["ref"]), "width": c.get("width"),
                     "height": c.get("height"), "lang": c.get("lang")}
                    for c in got.get(shape, []) if c.get("ref")][:24]
            for shape in ART_SHAPES}


def languages(provider_id: int | None = None) -> list[dict]:
    """Which languages actually turn up in the rows, and how many of each.

    This is what the settings page offers to filter on, rather than a list of every language there is:
    ticking boxes is only useful when the boxes are things you have actually been shown.
    """
    where, params = ("WHERE provider_id = ?", [provider_id]) if provider_id else ("", [])
    with db() as con:
        rows = con.execute(f"SELECT language AS code, COUNT(*) AS n FROM stream_titles {where}"
                           f" GROUP BY language ORDER BY n DESC", params).fetchall()
    off = languages_off()
    return [{"code": r["code"] or "?", "count": r["n"], "off": (r["code"] or "") in off}
            for r in rows if r["code"]]


def cards(provider_id: int, kind: str, limit: int = 20) -> list[dict]:
    """A row of what one service is showing, ready to draw. Empty when nothing has been fetched yet."""
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM stream_titles WHERE provider_id = ? AND kind = ? ORDER BY rank", (provider_id, kind))]
    if not rows:
        return []
    service = provider_names().get(provider_id) or {}
    name = service.get("name") or "the service"
    skip = _owned()
    off = languages_off()
    mine = art_overrides()
    numbers = netflix_ids() if "netflix" in name.lower() else {}
    out = []
    for r in rows:
        if r["tmdb_id"] in skip:
            continue
        if (r.get("language") or "") in off:
            continue
        own = mine.get((r["tmdb_id"], kind)) or {}
        out.append({
            "kind": "streaming", "id": f"{provider_id}-{kind}-{r['tmdb_id']}", "tmdb_id": r["tmdb_id"],
            "media": kind, "title": r["title"], "year": r["year"],
            # TMDB artwork, fetched and cached by LENTA's own image store rather than hotlinked, so a
            # browser never has to talk to TMDB and the pictures keep working offline once seen. A
            # picture you chose yourself wins, and survives the nightly refresh that replaces the rest.
            "poster": images.url(own.get("poster")) if own.get("poster") else (
                images.url(f"tmdb:{r['poster']}") if r["poster"] else None),
            # The wide picture, so these rows can be drawn in whichever style the rest of Home uses.
            "backdrop": images.url(own.get("backdrop")) if own.get("backdrop") else (
                images.url(f"tmdb:{r['backdrop']}") if r.get("backdrop") else None),
            "own_art": bool(own.get("poster") or own.get("backdrop")),
            "language": r.get("language") or "",
            "overview": r["overview"], "rating": r["rating"],
            "service": name, "service_id": provider_id,
            "service_logo": images.url(f"tmdb:{service['logo']}") if service.get("logo") else None,
            # Best first: the title's own address on the service, which opens the film itself. Then the
            # service's search for its exact name, which lands you inside the service on the right
            # result. The link TMDB supplies — a page on TMDB about where to watch — is last, and only
            # for a service whose own address we have no pattern for.
            "link": (_netflix_link(numbers.get((r["tmdb_id"], kind)), kind)
                     or _search_link(name, r["title"]) or r["link"]),
        })
        if len(out) >= limit:
            break
    return out


_SEARCH = {
    "netflix": "https://www.netflix.com/search?q={q}",
    "disney plus": "https://www.disneyplus.com/search?q={q}",
    "max": "https://play.max.com/search?q={q}",
    "hbo max": "https://play.max.com/search?q={q}",
    "amazon prime video": "https://www.amazon.com/s?k={q}&i=instant-video",
    "apple tv plus": "https://tv.apple.com/search?term={q}",
}


def _netflix_link(number: str | None, kind: str) -> str:
    """A film opens on its playback address, which starts it. A series opens on its own page instead,
    because /watch on a series starts an episode Netflix chooses rather than one you did."""
    if not number:
        return ""
    return f"https://www.netflix.com/{'watch' if kind == MOVIE else 'title'}/{number}"


def _search_link(service: str, title: str) -> str:
    from urllib.parse import quote_plus
    pattern = _SEARCH.get(service.strip().lower())
    return pattern.format(q=quote_plus(title)) if pattern else ""


def rows_for_home() -> list[dict]:
    """Every streaming row Home could show, in the order the services were chosen.

    Named for what the data is. TMDB knows its own popularity across its whole catalogue, filtered to
    this service and this country; it does not know the service's published chart, so these rows do not
    claim to be one.
    """
    names = provider_names()
    out = []
    for pid in chosen():
        name = (names.get(pid) or {}).get("name") or "this service"
        out.append({"id": f"stream-{pid}-movie", "provider_id": pid, "media": MOVIE,
                    "title": f"Popular on {name}"})
        out.append({"id": f"stream-{pid}-show", "provider_id": pid, "media": SHOW,
                    "title": f"Popular series on {name}"})
    return out


def status() -> dict:
    with db() as con:
        n = con.execute("SELECT COUNT(*) FROM stream_titles").fetchone()[0]
        at = con.execute("SELECT MAX(fetched_at) AS at FROM stream_titles").fetchone()["at"]
    return {"enabled": enabled(), "region": region(), "services": chosen(), "hide_owned": hide_owned(),
            "languages": languages(), "languages_off": sorted(languages_off()),
            "genres": genre_state(),
            "titles": n, "updated": at, "running": state["running"], "error": state["error"],
            "attribution": "Streaming availability data from JustWatch"}
