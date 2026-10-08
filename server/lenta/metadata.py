"""Online metadata from The Movie Database (TMDB).

Get a free key at https://www.themoviedb.org/settings/api and paste either the
v3 API key or the v4 read access token into Admin > Settings.
"""
import json
import re
import time
import unicodedata

import httpx

from .db import db, get_setting, now
from . import logs

log = logs.get("metadata")
API = "https://api.themoviedb.org/3"


class TMDBError(Exception):
    pass


class TMDB:
    def __init__(self, key: str | None = None):
        self.key = (key if key is not None else get_setting("tmdb_api_key")).strip()
        self.language = get_setting("metadata_language") or "en-US"
        self.region = get_setting("metadata_region") or "US"
        self._client = httpx.Client(timeout=20)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, **params) -> dict:
        headers = {"Accept": "application/json"}
        if len(self.key) > 40:          # v4 read access token (a JWT)
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        params.setdefault("language", self.language)
        params = {k: v for k, v in params.items() if v is not None}   # language=None: images in any language
        for attempt in range(3):
            try:
                r = self._client.get(f"{API}{path}", params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise TMDBError(f"TMDB unreachable: {exc}") from exc
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "2")))
                continue
            if r.status_code == 401:
                raise TMDBError("TMDB rejected the API key. Check it in Admin > Settings.")
            if r.status_code == 404:
                raise TMDBError("Not found on TMDB")
            r.raise_for_status()
            return r.json()
        raise TMDBError("TMDB rate limit")

    def test(self) -> bool:
        self._get("/configuration")
        return True

    # ---- search -------------------------------------------------------
    def search(self, kind: str, query: str, year: int | None = None) -> list[dict]:
        if kind == "movie":
            params = {"query": query, "include_adult": "false"}
            if year:
                params["year"] = year
            results = self._get("/search/movie", **params).get("results", [])
            if not results and year:  # filenames often carry the wrong year
                results = self._get("/search/movie", query=query, include_adult="false").get("results", [])
            return [{"tmdb_id": r["id"], "title": r.get("title"), "original_title": r.get("original_title"),
                     "year": _year(r.get("release_date")),
                     "overview": r.get("overview"), "poster": _ref(r.get("poster_path"))} for r in results[:12]]
        params = {"query": query, "include_adult": "false"}
        if year:
            params["first_air_date_year"] = year
        results = self._get("/search/tv", **params).get("results", [])
        if not results and year:
            results = self._get("/search/tv", query=query, include_adult="false").get("results", [])
        return [{"tmdb_id": r["id"], "title": r.get("name"), "original_title": r.get("original_name"),
                 "year": _year(r.get("first_air_date")),
                 "overview": r.get("overview"), "poster": _ref(r.get("poster_path"))} for r in results[:12]]

    # ---- details ------------------------------------------------------
    def _image_language(self) -> str:
        return f"{self.language.split('-')[0]},en,null"

    def movie(self, tmdb_id: int) -> dict:
        d = self._get(f"/movie/{tmdb_id}", append_to_response="credits,release_dates,images,videos,external_ids,keywords",
                      include_image_language=self._image_language())
        cert = ""
        for rel in d.get("release_dates", {}).get("results", []):
            if rel.get("iso_3166_1") == self.region:
                cert = next((x["certification"] for x in rel.get("release_dates", []) if x.get("certification")), "")
        crew = d.get("credits", {}).get("crew", [])
        return {
            "tmdb_id": d["id"],
            "title": d.get("title") or d.get("original_title"),
            "original_title": d.get("original_title"),
            "year": _year(d.get("release_date")),
            "release_date": d.get("release_date"),
            "overview": d.get("overview"),
            "tagline": d.get("tagline"),
            "genres": [g["name"] for g in d.get("genres", [])],
            "rating": d.get("vote_average"),
            "certification": cert,
            "runtime": d.get("runtime"),
            "poster": _ref(d.get("poster_path")),
            "backdrop": _pick_backdrop(d.get("images", {}).get("backdrops", []), d.get("backdrop_path")),
            "logo": _pick_logo(d.get("images", {}).get("logos", []), self.language),
            "extra": {
                "cast": _cast(d.get("credits", {}).get("cast", [])),
                "directors": [c["name"] for c in crew if c.get("job") == "Director"],
                "writers": [c["name"] for c in crew if c.get("department") == "Writing"][:4],
                "studios": [c["name"] for c in d.get("production_companies", [])][:3],
                "trailer": _trailer(d.get("videos", {}).get("results", [])),
                **_movie_details(d),
            },
        }

    def show(self, tmdb_id: int) -> dict:
        d = self._get(f"/tv/{tmdb_id}", append_to_response="credits,content_ratings,images,videos,external_ids,keywords",
                      include_image_language=self._image_language())
        cert = next((r.get("rating") for r in d.get("content_ratings", {}).get("results", [])
                     if r.get("iso_3166_1") == self.region), "")
        runtimes = d.get("episode_run_time") or []
        return {
            "tmdb_id": d["id"],
            "title": d.get("name") or d.get("original_name"),
            "original_title": d.get("original_name"),
            "year": _year(d.get("first_air_date")),
            "release_date": d.get("first_air_date"),
            "overview": d.get("overview"),
            "tagline": d.get("tagline"),
            "genres": [g["name"] for g in d.get("genres", [])],
            "rating": d.get("vote_average"),
            "certification": cert or "",
            "runtime": runtimes[0] if runtimes else None,
            "poster": _ref(d.get("poster_path")),
            "backdrop": _pick_backdrop(d.get("images", {}).get("backdrops", []), d.get("backdrop_path")),
            "logo": _pick_logo(d.get("images", {}).get("logos", []), self.language),
            "extra": {
                "cast": _cast(d.get("credits", {}).get("cast", [])),
                "creators": [c["name"] for c in d.get("created_by", [])],
                "studios": [n["name"] for n in d.get("networks", [])][:3],
                "status": d.get("status"),
                "trailer": _trailer(d.get("videos", {}).get("results", [])),
                **_show_details(d),
            },
            "seasons": {s["season_number"]: {"title": s.get("name"), "overview": s.get("overview"),
                                             "poster": _ref(s.get("poster_path")),
                                             "year": _year(s.get("air_date"))}
                        for s in d.get("seasons", [])},
        }

    def images(self, kind: str, tmdb_id: int) -> dict:
        """Posters, backdrops and logos on TMDB, as image refs, best first."""
        path = f"/movie/{tmdb_id}/images" if kind == "movie" else f"/tv/{tmdb_id}/images"
        d = self._get(path, include_image_language=self._image_language(), language=None)
        def refs(items):
            items = sorted(items, key=lambda i: (-(i.get("vote_count") or 0), -(i.get("width") or 0)))
            return [{"ref": _ref(i.get("file_path")), "width": i.get("width"), "height": i.get("height"),
                     "lang": i.get("iso_639_1")} for i in items[:40] if i.get("file_path")]
        mark_clean(_ref(b.get("file_path")) for b in d.get("backdrops", []) if b.get("iso_639_1") in (None, "", "xx"))
        return {"poster": refs(d.get("posters", [])), "backdrop": refs(d.get("backdrops", [])),
                "logo": refs(d.get("logos", []))}

    def videos(self, kind: str, tmdb_id: int) -> list[dict]:
        """YouTube trailers and teasers, best first: metadata language, then English, official, HD, newest."""
        lang = self.language.split("-")[0]
        path = f"/movie/{tmdb_id}/videos" if kind == "movie" else f"/tv/{tmdb_id}/videos"
        results = self._get(path, include_video_language=f"{lang},en,null").get("results", [])
        from .trailer_sources import unavailable
        blocked = unavailable()
        vids = [v for v in results if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser") and v.get("key")
                and v["key"] not in blocked]

        def score(v):
            return (v.get("type") == "Trailer", v.get("iso_639_1") == lang, v.get("iso_639_1") == "en",
                    bool(v.get("official")), (v.get("size") or 0) >= 1080, v.get("published_at") or "")
        vids.sort(key=score, reverse=True)
        return [{"key": v["key"], "name": v.get("name"), "language": v.get("iso_639_1"), "type": v.get("type"),
                 "official": bool(v.get("official")), "size": v.get("size"),
                 "published": (v.get("published_at") or "")[:10]} for v in vids]

    def season(self, tmdb_id: int, number: int) -> dict:
        d = self._get(f"/tv/{tmdb_id}/season/{number}")
        return {e["episode_number"]: {
            "title": e.get("name"), "overview": e.get("overview"), "release_date": e.get("air_date"),
            "rating": e.get("vote_average"), "runtime": e.get("runtime"), "thumb": _ref(e.get("still_path")),
        } for e in d.get("episodes", [])}


def _year(date: str | None) -> int | None:
    try:
        return int(date[:4]) if date else None
    except ValueError:
        return None


def _ref(path: str | None) -> str | None:
    return f"tmdb:{path}" if path else None


DETAILS_VERSION = 3      # bump when _movie_details/_show_details learn new fields: titles are refreshed in the background
DETAIL_KEYS = ("composer", "cinematography", "editors", "producers", "countries", "original_language", "languages",
               "budget", "revenue", "collection", "keywords", "imdb_id", "wikidata_id", "homepage", "companies",
               "seasons_count", "episodes_count", "last_air", "next_air", "show_type", "details_v", "tvdb_id")


def _crew(crew: list, *jobs: str, limit: int = 4) -> list[str]:
    out = []
    for c in crew:
        if c.get("job") in jobs and c.get("name") and c["name"] not in out:
            out.append(c["name"])
    return out[:limit]


def _languages(d: dict) -> tuple[str | None, list[str]]:
    spoken = d.get("spoken_languages") or []
    names = {l.get("iso_639_1"): (l.get("english_name") or l.get("name")) for l in spoken}
    code = d.get("original_language")
    original = names.get(code) or (code.upper() if code else None)
    return original, [n for n in names.values() if n][:6]


def _common_details(d: dict) -> dict:
    ext = d.get("external_ids") or {}
    kw = d.get("keywords") or {}
    original, languages = _languages(d)
    return {
        "countries": [c["name"] for c in d.get("production_countries", []) if c.get("name")][:4],
        "original_language": original,
        "languages": languages,
        "keywords": [k["name"] for k in (kw.get("keywords") or kw.get("results") or []) if k.get("name")][:14],
        "imdb_id": d.get("imdb_id") or ext.get("imdb_id") or None,
        "wikidata_id": ext.get("wikidata_id") or None,
        "tvdb_id": ext.get("tvdb_id") or None,
        "homepage": d.get("homepage") or None,
        "details_v": DETAILS_VERSION,
    }


def _movie_details(d: dict) -> dict:
    crew = (d.get("credits") or {}).get("crew", [])
    coll = d.get("belongs_to_collection")
    return {
        **_common_details(d),
        "composer": _crew(crew, "Original Music Composer", "Music", limit=2),
        "cinematography": _crew(crew, "Director of Photography", limit=2),
        "editors": _crew(crew, "Editor", limit=2),
        "producers": _crew(crew, "Producer", limit=4),
        "budget": d.get("budget") or None,
        "revenue": d.get("revenue") or None,
        "collection": {"id": coll["id"], "name": coll.get("name")} if coll and coll.get("id") else None,
    }


def _show_details(d: dict) -> dict:
    crew = (d.get("credits") or {}).get("crew", [])
    out = _common_details(d)
    if not out["countries"]:
        out["countries"] = list(d.get("origin_country") or [])[:4]
    nxt = d.get("next_episode_to_air") or {}
    out.update({
        "composer": _crew(crew, "Original Music Composer", "Music", limit=2),
        "producers": _crew(crew, "Executive Producer", "Producer", limit=4),
        "companies": [c["name"] for c in d.get("production_companies", []) if c.get("name")][:3],
        "seasons_count": d.get("number_of_seasons"),
        "episodes_count": d.get("number_of_episodes"),
        "last_air": d.get("last_air_date"),
        "next_air": nxt.get("air_date"),
        "show_type": d.get("type"),
    })
    return out


def _cast(cast: list) -> list:
    return [{"name": c.get("name"), "character": c.get("character"), "profile": _ref(c.get("profile_path"))}
            for c in cast[:15]]


def _trailer(videos: list) -> str | None:
    for v in videos:
        if v.get("site") == "YouTube" and v.get("type") == "Trailer":
            return v.get("key")
    return None


_clean_cache: dict = {"at": 0.0, "set": set()}


def clean_set() -> set:
    """Every picture known to have no words on it (read again at most once a minute)."""
    import time as _t
    if _t.time() - _clean_cache["at"] > 60:
        try:
            with db() as con:
                _clean_cache["set"] = {r[0] for r in con.execute("SELECT ref FROM clean_art")}
        except Exception:
            pass
        _clean_cache["at"] = _t.time()
    return _clean_cache["set"]


def mark_clean(refs) -> None:
    """Remember pictures without words on them (clean_art), so logos are only laid over those."""
    refs = [(r,) for r in refs if r]
    if not refs:
        return
    try:
        with db() as con:
            con.executemany("INSERT OR IGNORE INTO clean_art(ref) VALUES(?)", refs)
        _clean_cache["set"].update(r[0] for r in refs)
    except Exception:
        pass


def _pick_backdrop(backdrops: list, default: str | None) -> str | None:
    """A background picture without the title written on it (TMDB marks those with no language), so the
    title's logo can go on top without showing the name twice. TMDB lists the best-voted first."""
    mark_clean(_ref(b.get("file_path")) for b in backdrops if b.get("iso_639_1") in (None, "", "xx"))
    for b in backdrops:
        if b.get("iso_639_1") in (None, "", "xx") and b.get("file_path"):
            return _ref(b["file_path"])
    return _ref(default)


def _pick_logo(logos: list, language: str) -> str | None:
    lang = language.split("-")[0]
    for wanted in (lang, "en", None):
        for logo in logos:
            if logo.get("iso_639_1") == wanted:
                return _ref(logo.get("file_path"))
    return None


# ---- applying metadata to library items --------------------------------

_FIELDS = ("title", "original_title", "year", "release_date", "overview", "tagline", "rating",
           "certification", "runtime", "poster", "backdrop", "logo")


# Fields an admin edited by hand are locked (extra.locked_fields) and never overwritten by a refresh.
# Columns lock by their own name; "genres" and these extra keys can be locked too.
EXTRA_LOCKABLE = ("edition", "studios", "directors", "writers", "creators")


def locked_fields(extra) -> set[str]:
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except ValueError:
            extra = {}
    return set((extra or {}).get("locked_fields") or [])


def _update(con, item_id: int, values: dict, new_extra: dict | None = None) -> None:
    """UPDATE an item with values, keeping every locked field (and hand-entered extras) as they are."""
    row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return
    old_extra = json.loads(row["extra"] or "{}")
    locks = locked_fields(old_extra)
    values = {k: (row[k] if k in locks else v) for k, v in values.items()}
    if new_extra is not None:
        for key in ("locked_fields", "edition"):
            if key in old_extra:
                new_extra[key] = old_extra[key]
        for key in EXTRA_LOCKABLE:
            if key in locks and key in old_extra:
                new_extra[key] = old_extra[key]
        if "trailer" in locks:                       # a trailer chosen by hand (editor › Trailers)
            for key in ("trailer", "trailer_name"):
                if key in old_extra:
                    new_extra[key] = old_extra[key]
        new_extra.pop("match_attempts", None)
        if new_extra.get("imdb_id") == old_extra.get("imdb_id"):     # same title: keep the Wikipedia/OMDb cache
            for key in ("wiki", "omdb"):
                if key in old_extra and key not in new_extra:
                    new_extra[key] = old_extra[key]
        if not new_extra.get("trailer") and old_extra.get("trailer"):     # keep the indexed trailer
            for key in ("trailer", "trailer_name", "trailer_checked"):
                if key in old_extra:
                    new_extra[key] = old_extra[key]
        values["extra"] = json.dumps(new_extra)
    if "title" in locks:
        values.pop("sort_title", None)
    sets = ", ".join(f"{k} = ?" for k in values)
    con.execute(f"UPDATE items SET {sets}, updated_at = ? WHERE id = ?", (*values.values(), now(), item_id))


def apply_movie_or_show(item_id: int, data: dict, keep_local_art: bool = True) -> None:
    with db() as con:
        row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    values = {k: data.get(k) for k in _FIELDS}
    # Pictures come from the artwork sources in their order (Admin › Settings › Artwork sources); TMDB's
    # own pictures for this title are one of them. Locked pictures are kept by _update.
    if row:
        from . import artwork
        item = dict(row) | {"tmdb_id": data["tmdb_id"], "title": data.get("title") or row["title"],
                            "year": data.get("year") or row["year"],
                            "extra": json.dumps({**json.loads(row["extra"] or "{}"), **data.get("extra", {})})}
        chosen = artwork.pick(item, tmdb_primary={t: data.get(t) for t in artwork.TYPES})
        for t in artwork.TYPES:
            values[t] = chosen.get(t)
    with db() as con:
        values.update(genres=json.dumps(data.get("genres", [])), tmdb_id=data["tmdb_id"], matched=1,
                      sort_title=sort_title(data.get("title") or ""))
        _update(con, item_id, values, dict(data.get("extra", {})))


def apply_show_children(tmdb: TMDB, show_id: int, show_data: dict) -> None:
    """Fill season and episode titles, overviews and stills."""
    with db() as con:
        seasons = con.execute("SELECT id, parent_index, title FROM items WHERE parent_id = ? AND kind = 'season'",
                              (show_id,)).fetchall()
    for season in seasons:
        number = season["parent_index"]
        info = show_data.get("seasons", {}).get(number) or {}
        with db() as con:
            _update(con, season["id"], {"title": info.get("title") or season["title"], "overview": info.get("overview"),
                                        "poster": info.get("poster"), "year": info.get("year"), "matched": 1})
        try:
            episodes = tmdb.season(show_data["tmdb_id"], number)
        except TMDBError as exc:
            log.info("No TMDB episode data for season %s: %s", number, exc)
            continue
        with db() as con:
            rows = con.execute("SELECT id, index_number, thumb, title, runtime FROM items "
                               "WHERE parent_id = ? AND kind = 'episode'", (season["id"],)).fetchall()
            for ep in rows:
                e = episodes.get(ep["index_number"])
                if not e:
                    continue
                _update(con, ep["id"], {
                    "title": e["title"] or ep["title"], "overview": e["overview"], "release_date": e["release_date"],
                    "year": _year(e["release_date"]), "rating": e["rating"], "runtime": e["runtime"] or ep["runtime"],
                    "thumb": e["thumb"] or ep["thumb"], "matched": 1})


# ---- finding the right title -------------------------------------------------------
#
# File and folder names are messy: "A.Nightmare.on.Elm.Street.IV.The.Dream.Master.1988" is
# "A Nightmare on Elm Street 4: The Dream Master" on TMDB, "Poltergeist III Were Back" is
# "Poltergeist III", "VII New Nightmare" is "Wes Craven's New Nightmare", a "KP Cut" fan edit of a
# series is the series. So a title is searched in many forms, from most to least literal, and a
# loose form is only accepted when the year agrees and the result really contains those words.

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
         "xi": "11", "xii": "12"}
STOP = {"the", "a", "an", "of", "and", "s", "in", "on", "at", "to", "de", "la", "le", "el", "der", "die", "das"}
_EDITION = re.compile(
    r"\b(?:(?:director'?s|final|theatrical|extended|ultimate|special|collector'?s|anniversary|unrated|"
    r"international|fan|[a-z]{1,3})\s+(?:cut|edition|edit|version)|extended|remastered|unrated|uncut|imax|"
    r"despecialized|criterion|restored|redux|open\s+matte|remux)\b", re.I)


def _tokens(text: str) -> list[str]:
    text = re.sub(r"(?<=\b\w)\.(?=\w\b)", "", text or "")          # "O.K." -> "OK", "S.W.A.T." -> "SWAT"
    text = unicodedata.normalize("NFKD", text.replace("&", " and ")).lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [ROMAN.get(t, t) for t in re.findall(r"\w+", text.replace("'", "")) if t != "_"]


def similarity(query: str, title: str) -> float:
    """How much of the query is in the title (mostly), and of the title in the query."""
    q = [t for t in _tokens(query) if t not in STOP] or _tokens(query)
    t = [x for x in _tokens(title) if x not in STOP] or _tokens(title)
    if not q or not t:
        return 0.0
    common = len(set(q) & set(t))
    return 0.7 * common / len(set(q)) + 0.3 * common / len(set(t))


def query_variants(title: str) -> list[tuple[str, bool]]:
    """(query, loose) pairs, most literal first. Loose ones are fragments of the title."""
    out: list[tuple[str, bool]] = []

    def add(q: str, loose: bool) -> None:
        # Dots are word separators only in release-style names; "U.S.A." and "Mr. Smith" keep theirs.
        q = re.sub(r"_", " ", q)
        if " " not in q.strip():
            q = q.replace(".", " ")
        q = re.sub(r"\s+", " ", q).strip(" -:,")
        if len(re.sub(r"\W", "", q)) >= 3 and all(q.lower() != x.lower() for x, _ in out):
            out.append((q, loose))

    def arabic(q: str) -> str:
        return " ".join(ROMAN.get(w.lower(), w) for w in q.split())

    base = re.sub(r"\s+", " ", title).strip()
    clean = _EDITION.sub(" ", base)
    for q in (base, arabic(base), clean, arabic(clean)):
        add(q, False)
    if re.search(r"\b[A-Za-z]{2}\b", clean):                       # "OK Corral" -> "O.K. Corral"
        add(re.sub(r"\b([A-Z])([A-Z])\b", r"\1.\2.", re.sub(r"\b(ok|Ok)\b", "OK", clean)), False)
    forms = [clean.split()] + ([arabic(clean).split()] if arabic(clean) != clean else [])
    # "A Nightmare on Elm Street IV The Dream Master": the series part and the subtitle part,
    # with the number written both ways (TMDB has "Poltergeist III" but "... Elm Street 4").
    for words in forms:
        for i, w in enumerate(words[1:], 1):
            if re.fullmatch(r"\d{1,2}", w) or w.lower() in ROMAN:
                add(" ".join(words[:i + 1]), True)
                if len(words) - i - 1 >= 2:
                    add(" ".join(words[i + 1:]), True)
                break
    for sep in (" - ", ": "):
        if sep in clean:
            head, tail = clean.split(sep, 1)
            add(tail, True)
            add(head, True)
    add(re.sub(r"^\d{1,2}\s+(?=[A-Za-z])", "", clean), True)        # "2 The Two Towers"
    for words in forms:                       # drop trailing words; one word left only counts with a year
        for n in range(len(words) - 1, 0, -1):
            add(" ".join(words[:n]), True)
    if re.search(r"\b(?:[A-Za-z]\.){2,}", clean):                    # "Invasion U.S.A." -> "Invasion USA"
        add(re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", ""), clean), False)
    return out[:18]


def _score(result: dict, query: str, year: int | None, rank: int) -> tuple[float, float, int | None]:
    sim = max(similarity(query, result.get("title") or ""), similarity(query, result.get("original_title") or ""))
    dy = abs(result["year"] - year) if year and result.get("year") else None
    bonus = 0.0 if dy is None else 0.3 if dy == 0 else 0.15 if dy == 1 else -0.4
    # TMDB lists the most popular match first; that counts for something when words tie.
    return sim + bonus + max(0.0, 0.2 - 0.07 * rank), sim, dy


def _pick(results: list[dict], query: str, year: int | None, loose: bool, strict_sim: float = 0.6) -> dict | None:
    best, best_score = None, -1.0
    for rank, r in enumerate(results[:8]):
        score, sim, dy = _score(r, query, year, rank)
        if loose:
            single = len([t for t in _tokens(query) if t not in STOP]) <= 1
            ok = (dy is not None and dy <= 1 and sim >= strict_sim and (not single or dy == 0)) or \
                 (year is None and rank == 0 and sim >= 0.9 and len(_tokens(query)) >= 2)
        else:
            ok = score >= 0.55 and sim >= 0.2
        if ok and score > best_score:
            best, best_score = r, score
    return best


def find_match(tmdb: "TMDB", kind: str, names: list[tuple[str, int | None]]) -> tuple[dict | None, str, list[str]]:
    """Try every name the file and its folders suggest, in many forms. Returns (result, tmdb_kind, attempts)."""
    attempts: list[str] = []
    seen: set[tuple[str, str, int | None]] = set()

    def run(search_kind: str, q: str, year: int | None, loose: bool, strict_sim: float = 0.6) -> dict | None:
        key = (search_kind, q.lower(), year)
        if key in seen or len(attempts) >= 40:
            return None
        seen.add(key)
        attempts.append(f"{'TV: ' if search_kind != kind else ''}{q}{f' ({year})' if year else ''}")
        return _pick(tmdb.search(search_kind, q, year), q, year, loose, strict_sim)

    names = [(t, y) for t, y in names if t]
    for title, year in names:
        for q, loose in query_variants(title):
            hit = run(kind, q, year, loose)
            if hit:
                return hit, kind, attempts
    # Last resort, the other catalogue: fan edits of a series filed as movies, and TV films or
    # miniseries that TMDB lists as a film but that sit in a TV library.
    other = "show" if kind == "movie" else "movie"
    for title, year in names:
        for q, loose in query_variants(title)[:4]:
            hit = run(other, q, year, True, strict_sim=0.75)
            if hit:
                return hit, "tv" if other == "show" else "movie", attempts
    return None, kind, attempts


def match_item(item_id: int, tmdb: TMDB | None = None, tmdb_id: int | None = None,
               tmdb_kind: str | None = None) -> bool:
    """Search (or use a given id) and apply metadata to a movie or show. Returns True if matched.

    tmdb_kind "tv" on a movie means it was matched to a series (fan edits, miniseries cuts)."""
    tmdb = tmdb or TMDB()
    if not tmdb.enabled:
        return False
    with db() as con:
        item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item or item["kind"] not in ("movie", "show"):
        return False
    kind = item["kind"]
    extra = json.loads(item["extra"] or "{}")
    try:
        if tmdb_id is None:
            names = [(extra.get("parsed_title") or item["title"], extra.get("parsed_year") or item["year"])]
            names += [tuple(n) for n in extra.get("parsed_alt", []) if n and n[0]]
            if kind == "show":
                names += _episode_names(item_id, names[0][1])
            hit, tmdb_kind, attempts = find_match(tmdb, kind, names)
            if not hit:
                log.info("No TMDB match for %s '%s' after %d searches: %s", kind, names[0][0], len(attempts),
                         "; ".join(attempts))
                extra["match_attempts"] = attempts
                with db() as con:
                    con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(extra), item_id))
                return False
            tmdb_id = hit["tmdb_id"]
        use_tv = tmdb_kind == "tv" or (kind == "show" and tmdb_kind != "movie")
        data = tmdb.show(tmdb_id) if use_tv else tmdb.movie(tmdb_id)
        # Keep what the scanner parsed so a later "refresh" can search again.
        data["extra"].update({k: v for k, v in extra.items() if k.startswith("parsed_")})
        if kind == "movie" and use_tv:
            data["extra"]["tmdb_kind"] = "tv"
            data.pop("seasons", None)
        if kind == "show" and not use_tv:
            data["extra"]["tmdb_kind"] = "movie"
        apply_movie_or_show(item_id, data)
        if kind == "show" and use_tv:
            apply_show_children(tmdb, item_id, data)
        log.info("Matched %s '%s' -> TMDB %s%s '%s'", kind, item["title"], "tv/" if use_tv else "", tmdb_id,
                 data["title"])
        return True
    except TMDBError as exc:
        log.warning("Metadata for '%s' failed: %s", item["title"], exc)
        return False


def tmdb_kind_of(item) -> str:
    """'movie' or 'tv': which TMDB catalogue an item's tmdb_id refers to."""
    try:
        stored = json.loads(item["extra"] or "{}").get("tmdb_kind")
    except (ValueError, TypeError, AttributeError):
        stored = None
    if item["kind"] == "show":
        return "movie" if stored == "movie" else "tv"
    return "tv" if stored == "tv" else "movie"


def _episode_names(show_id: int, year: int | None) -> list[tuple[str, int | None]]:
    """A show's folder can be misspelt ("Hallo 4") while its files are right ("Halo 4 - S01E03 -
    Forward Unto Dawn"): the show names read from a few episode files, with and without the episode title."""
    from guessit import guessit
    with db() as con:
        paths = [r["path"] for r in con.execute(
            "SELECT f.path FROM files f JOIN items e ON e.id = f.item_id JOIN items se ON se.id = e.parent_id "
            "WHERE se.parent_id = ? ORDER BY se.parent_index, e.index_number LIMIT 4", (show_id,))]
    out: list[tuple[str, int | None]] = []
    for path in paths:
        try:
            g = guessit(path.rsplit("/", 1)[-1], {"type": "episode"})
        except Exception:
            continue
        title = g.get("title")
        title = title[0] if isinstance(title, list) else title
        if not title:
            continue
        y = g.get("year") or year
        y = y[0] if isinstance(y, list) else y
        ep = g.get("episode_title")
        ep = ep[0] if isinstance(ep, list) else ep
        for name in (str(title), f"{title} {re.split(r' - |: ', str(ep))[0]}" if ep else None):
            if name and (name, y) not in out:
                out.append((name, y))
    return out[:4]


def sort_title(title: str) -> str:
    t = title.strip().lower()
    for article in ("the ", "a ", "an "):
        if t.startswith(article):
            return t[len(article):]
    return t
