"""Answers for a program that starts films by voice: words in, a LENTA item id out.

Built for one caller — Vlad's voice assistant on skynet — and shaped by one rule. TMDB's terms forbid
using their content "with" an AI application, and everything LENTA knows about a title beyond its file
name came from TMDB: the title as it is shown, the overview, the genres, the artwork, the names in
other languages. So the matching happens **here**, on the server, and what goes back is only what
LENTA made itself: item ids, counts and yes/no flags. **No endpoint in this file may answer with a
title, a name, a genre or anything else read out of an item row** — `tests/test_find.py` asks every
one of them and fails on any string it did not expect.

The words that come *in* are Vlad's own, spoken; comparing them against the library on this machine
does not hand TMDB's data to anything.

  GET /api/find                — words or a random pick → an item id to play (a show → its next episode)
  GET /api/now-playing         — the id the caller's own account is playing, if anything
  GET /api/items/{id}/playable — is this id still there, still permitted and still on disk?
  GET /api/pick?ids=           — the cards for a short list, in that order, for the page `#/pick` draws
                                 (this one *does* carry titles: it is for the browser, not the assistant)
"""
import os
import random
import re
import time
import unicodedata
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException, Query

from . import auth
from .db import db, jloads
from .serialize import UNWATCHED_SHOW, cards

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- words

# Spoken numbers, as the assistant's speech-to-text is likely to write them, in English and Bulgarian.
# "Rocky two", "Rocky II", "Rocky 2" and "Роки втора част" all mean the same film.
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
    "ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
    "едно": 1, "един": 1, "една": 1, "две": 2, "два": 2, "три": 3, "четири": 4, "пет": 5, "шест": 6,
    "седем": 7, "осем": 8, "девет": 9, "десет": 10,
    "първа": 1, "първи": 1, "първо": 1, "втора": 2, "втори": 2, "второ": 2, "трета": 3, "трети": 3,
    "трето": 3, "четвърта": 4, "четвърти": 4, "пета": 5, "пети": 5, "шеста": 6, "шести": 6,
}
# "v" and "x" are left out on purpose: they are letters in too many titles ("Malcolm X", "V for Vendetta").

# Words that carry no title: articles, and what a person says around a title rather than in it.
FILLER = {
    "the", "a", "an", "part", "chapter", "movie", "film", "episode",
    "част", "филм", "филма", "филмът", "епизод", "глава",
}
# "Play the original Rocky" — said when a title has sequels; picks the earliest of the matches.
ORIGINAL = {"original", "оригинал", "оригинала", "оригиналния", "оригиналният", "първия", "първият"}

# Genres as Vlad might say them in Bulgarian, onto the English names TMDB gives LENTA by default.
# A genre already in English (or in whatever language the library uses) is matched as it is.
GENRE_WORDS = {
    "екшън": "Action", "екшен": "Action", "приключенски": "Adventure", "приключение": "Adventure",
    "анимация": "Animation", "анимационен": "Animation", "комедия": "Comedy", "криминален": "Crime",
    "криминале": "Crime", "документален": "Documentary", "драма": "Drama", "семеен": "Family",
    "фентъзи": "Fantasy", "исторически": "History", "ужаси": "Horror", "хорър": "Horror",
    "музикален": "Music", "мистерия": "Mystery", "романтичен": "Romance", "романтика": "Romance",
    "фантастика": "Science Fiction", "научна фантастика": "Science Fiction", "трилър": "Thriller",
    "военен": "War", "уестърн": "Western",
}

YEAR = re.compile(r"^(19[0-9]{2}|20[0-9]{2})$")


def _fold(text: str) -> str:
    """Lower case, accents off, '&' as a word, every other mark a space."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    return re.sub(r"[^\w]+", " ", text).replace("_", " ").strip()


def tokens(text: str) -> list[str]:
    """A name as comparable words: numbers as digits, filler dropped."""
    out = []
    for word in _fold(text).split():
        if word in FILLER:
            continue
        out.append(str(NUMBER_WORDS[word]) if word in NUMBER_WORDS else word)
    return out


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 5:
        return False                       # short words and numbers must be exact: "Up" is not "Ups"
    return SequenceMatcher(None, a, b).ratio() >= 0.8


def score_name(query: list[str], name: list[str]) -> float:
    """How well spoken words fit one name of a title, 0 to 100. 0 means they do not fit at all."""
    if not query or not name:
        return 0
    if query == name:
        return 100
    unused = list(name)
    for word in query:
        hit = next((n for n in unused if _same_word(word, n)), None)
        if hit is None:
            return 0                        # every spoken word has to be in the name
        unused.remove(hit)
    # A number in the title that was not said is a different film: "Rocky" is not "Rocky 2".
    if any(n.isdigit() for n in unused):
        return 0 if len(name) - len(unused) <= 1 else 40
    return 50 + 45 * (len(query) / len(name))


# ---------------------------------------------------------------- the library

KINDS = {"film": ("movie",), "movie": ("movie",), "show": ("show",), "any": ("movie", "show"),
         "photos": ("photoalbum",)}
# "channel" is answered from the live TV channels, not from items (see _find_channel).


def _rows(user: dict, kinds: tuple, genre: str | None, unwatched: bool, exclude: set[int],
          flt: dict | None = None) -> list[dict]:
    flt = flt or {}
    where, params = auth.library_filter(user, "i.library_id")
    params = {**params, "uid": user["id"]}
    clause = [where, f"i.kind IN ({','.join(repr(k) for k in kinds)})"]
    # 1.26.3 — each only when asked for, so a request without them runs exactly the query it always did.
    if "year_from" in flt:
        clause.append("i.year IS NOT NULL AND i.year >= :year_from")
        params["year_from"] = flt["year_from"]
    if "year_to" in flt:
        clause.append("i.year IS NOT NULL AND i.year <= :year_to")
        params["year_to"] = flt["year_to"]
    if flt.get("no_genre"):
        marks = ",".join(f":ng{n}" for n in range(len(flt["no_genre"])))
        clause.append(f"NOT EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(i.genres) THEN i.genres ELSE '[]' END) g "
                      f"WHERE lower(g.value) IN ({marks}))")
        params.update({f"ng{n}": g for n, g in enumerate(flt["no_genre"])})
    if flt.get("library"):
        clause.append(f"i.library_id IN ({','.join(str(int(i)) for i in flt['library'])})")
    if flt.get("no_library"):
        clause.append(f"i.library_id NOT IN ({','.join(str(int(i)) for i in flt['no_library'])})")
    if genre:
        clause.append("EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE lower(g.value) = lower(:genre))")
        params["genre"] = genre
    if unwatched:
        done = "i.id IN (SELECT item_id FROM watch_state WHERE user_id = :uid AND completed = 1)"
        remaining = UNWATCHED_SHOW.replace(" AS unwatched", "")
        clause.append(f"((i.kind = 'movie' AND NOT {done}) OR (i.kind = 'show' AND {remaining} > 0))")
    # Only titles that have something to play: a film with a file, a show with an episode that has one,
    # an album with a photo in it.
    clause.append("(EXISTS (SELECT 1 FROM files f WHERE f.item_id = i.id) OR EXISTS (SELECT 1 FROM items se "
                  "JOIN items e ON e.parent_id = se.id JOIN files f ON f.item_id = e.id WHERE se.parent_id = i.id)"
                  " OR (i.kind = 'photoalbum' AND EXISTS (SELECT 1 FROM items p WHERE p.parent_id = i.id "
                  "AND p.kind = 'photo')))")
    with db() as con:
        rows = [dict(r) for r in con.execute(
            f"SELECT i.id, i.kind, i.title, i.original_title, i.year, i.tmdb_id, i.extra, "
            f"(SELECT MAX(w.updated_at) FROM watch_state w WHERE w.user_id = :uid AND w.item_id = i.id) AS touched "
            f"FROM items i WHERE {' AND '.join(clause)}", params)]
    rows = [r for r in rows if r["id"] not in exclude]
    if flt.get("no_library"):
        # The same film kept in another library too is still that film: "not boxing" means no boxing film,
        # wherever else a copy sits. Same test as `_same_title`: the TMDB id, or the title words and year.
        with db() as con:
            left = con.execute(
                f"SELECT kind, tmdb_id, title, year FROM items WHERE library_id IN "
                f"({','.join(str(int(i)) for i in flt['no_library'])}) AND kind IN ({','.join(repr(k) for k in kinds)})"
            ).fetchall()
        by_tmdb = {(r["kind"], r["tmdb_id"]) for r in left if r["tmdb_id"]}
        by_name = {(r["kind"], tuple(tokens(r["title"])), r["year"]) for r in left}
        rows = [r for r in rows if not ((r.get("tmdb_id") and (r["kind"], r["tmdb_id"]) in by_tmdb)
                                        or (r["kind"], tuple(tokens(r["title"])), r.get("year")) in by_name)]
    return rows


def _names(row: dict) -> list[list[str]]:
    extra = jloads(row.get("extra"), {})
    names = [row.get("title"), row.get("original_title"), extra.get("parsed_title")]
    names += [n[0] for n in extra.get("parsed_alt", []) if n and n[0]]
    names += list(extra.get("aka") or [])
    seen, out = set(), []
    for n in names:
        t = tokens(n or "")
        if t and tuple(t) not in seen:
            seen.add(tuple(t))
            out.append(t)
    return out


def _same_title(a: dict, b: dict) -> bool:
    """Two rows that are one film kept twice (two libraries, two copies): not a real choice."""
    if a["kind"] != b["kind"]:
        return False
    if a.get("tmdb_id") and a.get("tmdb_id") == b.get("tmdb_id"):
        return True
    return tokens(a["title"]) == tokens(b["title"]) and a.get("year") == b.get("year")


def _collapse(rows: list[dict]) -> list[dict]:
    """One row per title, keeping the copy last watched (else the oldest id), in the order given.

    Each row joins the first group whose first row is the same title (`_same_title`). Until 1.26.0 that
    was found by comparing the row with every group so far, which made a random pick over Vlad's 1,900
    films take about 35 s on svkplex (measured by Jarvis, 5 Nov). The two ways a head can match — the same
    TMDB id, or the same words and year — are now looked up by key instead. Same groups, same order.
    """
    groups: list[list[dict]] = []
    by_tmdb: dict[tuple, int] = {}
    by_name: dict[tuple, int] = {}
    for r in rows:
        tk = (r["kind"], r["tmdb_id"]) if r.get("tmdb_id") else None
        nk = (r["kind"], tuple(tokens(r["title"])), r.get("year"))
        hits = [i for i in (by_tmdb.get(tk) if tk else None, by_name.get(nk)) if i is not None]
        if hits:
            groups[min(hits)].append(r)
            continue
        groups.append([r])
        if tk:
            by_tmdb.setdefault(tk, len(groups) - 1)
        by_name.setdefault(nk, len(groups) - 1)
    return [max(g, key=lambda r: (r["touched"] or 0, -r["id"])) for g in groups]


def _episode(show_id: int, season: int, episode: int) -> int | None:
    with db() as con:
        row = con.execute(
            "SELECT e.id FROM items e JOIN items se ON se.id = e.parent_id WHERE se.parent_id = ? "
            "AND se.parent_index = ? AND e.kind = 'episode' AND e.index_number = ? "
            "AND EXISTS (SELECT 1 FROM files f WHERE f.item_id = e.id) ORDER BY e.id LIMIT 1",
            (show_id, season, episode)).fetchone()
    return row["id"] if row else None


def _target(row: dict, user: dict, season: int | None, episode: int | None) -> dict:
    """What `#/play/<id>` should be given for this title, and how it would start."""
    from .api_library import next_up, states_for
    if row["kind"] == "photoalbum":
        return {"item_id": row["id"], "kind": "photoalbum", "show_id": None, "action": "open"}
    if row["kind"] == "show":
        if season is not None:
            ep_id = _episode(row["id"], season, episode or 1)
            return {"item_id": ep_id, "kind": "episode", "show_id": row["id"],
                    "action": "start" if ep_id else "missing"}
        ep, action, _ = next_up(user["id"], row["id"])
        return {"item_id": ep["id"] if ep else None, "kind": "episode", "show_id": row["id"],
                "action": action if ep else "missing"}
    st = states_for(user["id"], [row["id"]]).get(row["id"], {})
    pos = 0 if st.get("completed") else (st.get("position") or 0)
    return {"item_id": row["id"], "kind": "movie", "show_id": None, "action": "resume" if pos > 15 else "start"}


def _ids(text: str | None, limit: int = 50) -> list[int]:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part.isdigit() and len(part) <= 12 and int(part) not in out:
            out.append(int(part))
    return out[:limit]


# ---------------------------------------------------------------- endpoints

@router.get("/find")
def find(q: str = Query("", max_length=200), kind: str = "any", season: int | None = Query(None, ge=0, le=999),
         episode: int | None = Query(None, ge=1, le=9999), genre: str | None = Query(None, max_length=60),
         unwatched: bool = False, random_pick: bool = Query(False, alias="random"), exclude: str = "",
         year_from: int | None = Query(None, ge=1870, le=2100), year_to: int | None = Query(None, ge=1870, le=2100),
         no_genre: str = Query("", max_length=600), library: str = Query("", max_length=600),
         no_library: str = Query("", max_length=600), user: dict = Depends(auth.current_user)):
    """Words → the one thing to play, or the few it could be. Ids, counts and flags only — unless the
    caller is an assistant key with the `titles` scope (1.26.0), which gets each id's title and year too.

    1.26.3 narrows it further, for "any film from the 1980s, not boxing or animation" (Jarvis, 11 Nov):
    `year_from`/`year_to`, `no_genre`, `library` and `no_library`. With none of them, nothing changes."""
    if kind == "channel":
        out = _find_channel(q, user)
    else:
        if kind not in KINDS:
            raise HTTPException(400, "kind is film, show, any, photos or channel")
        flt = _filters(user, year_from, year_to, no_genre, library, no_library)
        out = {**_find(q, KINDS[kind], season, episode, genre, unwatched, random_pick, exclude, user, flt),
               "channel_id": None}
    return _with_titles(out, user)


def _filters(user, year_from, year_to, no_genre, library, no_library) -> dict:
    """The 1.26.3 filters, checked. Anything wrong is refused with what was wrong — never dropped
    quietly, because an assistant would then say it had left something out that it had not."""
    flt: dict = {}
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "year_from is after year_to")
    if year_from is not None:
        flt["year_from"] = year_from
    if year_to is not None:
        flt["year_to"] = year_to
    names = [n.strip() for n in no_genre.split(",") if n.strip()]
    if len(names) > 10:
        raise HTTPException(400, "no_genre takes at most 10 genres")
    if names:
        known = _genres_known(user)
        out = []
        for n in names:
            g = GENRE_WORDS.get(_fold(n), n)
            if g.lower() not in known:
                raise HTTPException(400, f"No title in LENTA has the genre {n!r}")
            out.append(g.lower())
        flt["no_genre"] = out
    allowed = auth.allowed_libraries(user)
    with db() as con:
        existing = {r["id"] for r in con.execute("SELECT id FROM libraries")}
    for key, text in (("library", library), ("no_library", no_library)):
        if not text.strip():
            continue
        ids = _ids(text, 50)
        if not ids or len(ids) != len([p for p in text.split(",") if p.strip()]):
            raise HTTPException(400, f"{key} is library ids, comma-separated")
        for i in ids:
            if i not in existing or (allowed is not None and i not in allowed):
                raise HTTPException(400, f"No library {i} for this account")
        flt[key] = ids
    return flt


def _genres_known(user: dict) -> set[str]:
    """Every genre a title in this account's libraries carries, in lower case."""
    where, params = auth.library_filter(user, "i.library_id")
    with db() as con:
        return {r["g"] for r in con.execute(
            f"SELECT DISTINCT lower(g.value) AS g FROM items i, "
            f"json_each(CASE WHEN json_valid(i.genres) THEN i.genres ELSE '[]' END) g WHERE {where}", params)}


def _with_titles(out: dict, user: dict) -> dict:
    from .assistant import channel_names, describe, has
    if not has(user, "titles"):
        return out
    if out.get("kind") == "channel":
        names = channel_names([out.get("channel_id")] + list(out.get("candidate_ids") or []))
        if out.get("channel_id") in names:
            out["channel_name"] = names[out["channel_id"]]
        out["candidates"] = [{"channel_id": c, "channel_name": names.get(c)} for c in out.get("candidate_ids") or []]
        return out
    ids = [out.get("item_id"), out.get("show_id")] + list(out.get("candidate_ids") or [])
    names = describe(ids, user)
    if out.get("item_id") in names:
        out.update(names[out["item_id"]])
    with db() as con:
        kinds = {r["id"]: r["kind"] for r in con.execute(
            f"SELECT id, kind FROM items WHERE id IN ({','.join('?' * len(out.get('candidate_ids') or [0]))})",
            list(out.get("candidate_ids") or [0]))}
    out["candidates"] = [{"item_id": c, "kind": kinds.get(c), **names.get(c, {})}
                         for c in out.get("candidate_ids") or [] if c in names]
    return out


def _find_channel(q: str, user: dict) -> dict:
    """"Put Nova on": a live channel by its name (or its number), for an account that may watch TV."""
    from . import livetv
    none = {"item_id": None, "kind": "channel", "show_id": None, "action": None, "channel_id": None,
            "matches": 0, "ambiguous": False, "candidate_ids": []}
    try:
        auth.live_tv_viewer(user)
    except HTTPException:
        return none
    query = tokens(q)
    if not query:
        return none
    scored = []
    for c in livetv.channels(include_hidden=False):
        name = tokens(c.get("name") or "")
        s = score_name(query, name)
        if s and name[:len(query)] == query:
            s += 6            # "Nova" is Nova TV before Kino Nova: a channel is named by its first word
        if not s and len(query) == 1 and query[0].isdigit() and c.get("number") == int(query[0]):
            s = 90
        if s:
            scored.append((s, c.get("number") or 99999, c["id"]))
    if not scored:
        return none
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))       # best first, then by channel number
    top = scored[0][0]
    tier = [c for s, _, c in scored if s >= top - 5]
    exact = [c for s, _, c in scored if s == 100]
    if len(tier) == 1 or len(exact) == 1:                 # "Nova" is Nova, even with "Nova Sport" there
        return {**none, "action": "open", "channel_id": (exact or tier)[0], "matches": len(scored)}
    return {**none, "matches": len(scored), "ambiguous": True, "candidate_ids": tier[:20]}


def _find(q, kinds, season, episode, genre, unwatched, random_pick, exclude, user, flt=None) -> dict:
    genre_name = GENRE_WORDS.get(_fold(genre or ""), genre.strip() if genre else None)
    rows = _rows(user, kinds, genre_name, unwatched, set(_ids(exclude, 500)), flt or {})
    words = _fold(q).split()
    original = any(w in ORIGINAL for w in words)
    query = [t for t in tokens(" ".join(w for w in words if w not in ORIGINAL))]

    def answer(row, matches, ambiguous, candidates):
        target = _target(row, user, season, episode) if row else {"item_id": None, "kind": None,
                                                                    "show_id": None, "action": None}
        return {**target, "matches": matches, "ambiguous": ambiguous, "candidate_ids": candidates}

    if not query:
        asked = random_pick or genre or unwatched or bool(flt)
        if not (random_pick or genre or unwatched) or not rows:
            return answer(None, len(_collapse(rows)) if asked else 0, False, [])
        rows = _collapse(rows)
        return answer(random.choice(rows), len(rows), False, [])

    # A spoken year narrows, but "1917" and "2012" are titles as well as years, so it is only taken
    # as a year when the title words still match something without it.
    year = None
    if len(query) > 1 and YEAR.match(query[-1]):
        year, query = int(query[-1]), query[:-1]

    scored = []
    for r in rows:
        s = max((score_name(query, n) for n in _names(r)), default=0)
        if s and year is not None:
            if r.get("year") and abs(r["year"] - year) <= 1:
                s += 10
            else:
                continue
        if s:
            scored.append((s, r))
    scored.sort(key=lambda x: (-x[0], x[1].get("year") or 9999, x[1]["id"]))
    best = _collapse([r for _, r in scored])
    if not best:
        return answer(None, 0, False, [])
    by_id = {r["id"]: s for s, r in scored}
    top = max(by_id[r["id"]] for r in best)
    # Only titles as good as the best are a real choice; "Rocky" said alone does not make "Rocky Balboa"
    # a rival to "Rocky" itself.
    tier = [r for r in best if by_id[r["id"]] >= top - 5]
    if len(tier) > 1 and original:
        tier = sorted(tier, key=lambda r: (r.get("year") or 9999, r["id"]))[:1]
    if random_pick and len(tier) > 1:
        tier = [random.choice(tier)]
    if len(tier) == 1:
        return answer(tier[0], len(best), False, [])
    return answer(None, len(best), True, [r["id"] for r in tier][:20])


@router.get("/now-playing")
def now_playing_id(user: dict = Depends(auth.current_user)):
    """The item the caller's own account is playing anywhere, by id. Nobody else's, and never a title."""
    from .api_playback import _np_lock, now_playing
    prefix = (f"{user['id']}:", f"{user['id']}-")
    with _np_lock:
        mine = [v for k, v in now_playing.items() if k.startswith(prefix) and time.time() - v["updated"] <= 60]
    # Every LENTA page of this account, with what it is doing (players.py, 1.25.0). Ids, states,
    # seconds and the names Vlad gave his devices — the same rule as everything else in this file.
    # 1.26.0: with a `titles` key, each device and the top-level item carry their titles too.
    from .players import players_for
    devices = players_for(user["id"], user)
    if not mine:
        return {"item_id": None, "state": None, "players": devices}
    last = max(mine, key=lambda v: v["updated"])
    out = {"item_id": last["item_id"], "state": last["state"], "players": devices}
    from .assistant import describe, has
    if has(user, "titles"):
        out.update(describe([last["item_id"]], user).get(last["item_id"], {}))
    return out


@router.get("/items/{item_id}/playable")
def playable_endpoint(item_id: int, user: dict = Depends(auth.current_user)):
    out = playable(item_id, user)
    from .assistant import describe, has
    if out["playable"] and has(user, "titles"):
        out.update(describe([out["item_id"]], user).get(out["item_id"], {}))
    return out


def playable(item_id: int, user: dict) -> dict:
    """Can `#/play/<id>` start this, today, for this account? Missing and forbidden look the same."""
    no = {"playable": False, "item_id": None, "kind": None}
    with db() as con:
        row = con.execute("SELECT id, kind, library_id FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row or row["kind"] not in ("movie", "episode", "show"):
        return no
    try:
        auth.check_item_access(user, row["library_id"])
    except HTTPException:
        return no
    target = row["id"]
    if row["kind"] == "show":
        target = _target(dict(row), user, None, None)["item_id"]
        if not target:
            return no
    with db() as con:
        paths = [r["path"] for r in con.execute("SELECT path FROM files WHERE item_id = ?", (target,))]
    if not any(os.path.exists(p) for p in paths):
        return no
    return {"playable": True, "item_id": target, "kind": "episode" if row["kind"] != "movie" else "movie"}


@router.get("/pick")
def pick(ids: str = "", user: dict = Depends(auth.current_user)):
    """Cards for `#/pick?ids=…`, in the order asked, leaving out anything this account may not see."""
    wanted = _ids(ids, 24)
    if not wanted:
        return {"titles": []}
    from .api_library import _top_columns
    where, params = auth.library_filter(user, "i.library_id")
    params = {**params, "uid": user["id"]}
    marks = ",".join(f":id{n}" for n in range(len(wanted)))
    params.update({f"id{n}": v for n, v in enumerate(wanted)})
    with db() as con:
        rows = con.execute(f"SELECT {_top_columns('show')} FROM items i WHERE i.id IN ({marks}) AND {where} "
                           f"AND i.kind IN ('movie','show')", params).fetchall()
    found = {c["id"]: c for c in cards(rows, user["id"])}
    return {"titles": [found[i] for i in wanted if i in found]}
