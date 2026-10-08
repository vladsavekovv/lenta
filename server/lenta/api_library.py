"""Browsing: libraries, home rows, item details, search, watch state, artwork."""
import json
import random
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import auth, images, metadata
from .db import db, jloads, now
from .serialize import (CHILD_COUNT, EPISODE_JOIN, PHOTO_COVER, TOP_KIND, UNWATCHED_SHOW, card, cards,
                        file_info, states_for)

router = APIRouter(prefix="/api")

SEASON_UNWATCHED = """(SELECT COUNT(*) FROM items e WHERE e.parent_id = i.id AND e.id NOT IN
    (SELECT item_id FROM watch_state WHERE user_id = :uid AND completed = 1)) AS unwatched"""
SHOW_LATEST = """COALESCE((SELECT MAX(e.added_at) FROM items se JOIN items e ON e.parent_id = se.id
    WHERE se.parent_id = i.id), i.added_at)"""
SORTS = {
    "added": "{added} DESC",
    "title": "i.sort_title COLLATE NOCASE ASC",
    "year": "i.year DESC NULLS LAST, i.sort_title",
    "rating": "i.rating DESC NULLS LAST, i.sort_title",
    "release": "i.release_date DESC NULLS LAST",
    "random": "RANDOM()",
}


def get_item(item_id: int, user: dict) -> dict:
    with db() as con:
        row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "This title is no longer in the library")
    auth.check_item_access(user, row["library_id"])
    return dict(row)


def _top_columns(kind: str) -> str:
    cols = "i.*"
    if kind == "show":
        cols += f", {UNWATCHED_SHOW}, {CHILD_COUNT}"
    elif kind == "photoalbum":
        cols += f", {PHOTO_COVER}, {CHILD_COUNT}"
    elif kind == "album":
        cols += f", {CHILD_COUNT}"
    return cols


# ---- libraries ------------------------------------------------------------------

@router.get("/libraries")
def libraries(user: dict = Depends(auth.current_user)):
    where, params = auth.library_filter(user, "l.id")
    with db() as con:
        rows = con.execute(f"SELECT l.* FROM libraries l WHERE {where} ORDER BY l.position, l.id", params).fetchall()
        out = []
        for lib in rows:
            count = con.execute("SELECT COUNT(*) FROM items WHERE library_id = ? AND kind = ?",
                                (lib["id"], TOP_KIND[lib["kind"]])).fetchone()[0]
            out.append({"id": lib["id"], "name": lib["name"], "kind": lib["kind"], "count": count})
    return out


@router.get("/libraries/{library_id}/items")
def library_items(library_id: int, sort: str = "added", genre: str | None = None, filter: str | None = None,
                  q: str | None = None, offset: int = 0, limit: int = Query(60, le=500),
                  user: dict = Depends(auth.current_user)):
    lib = _library(library_id, user)
    kind = TOP_KIND[lib["kind"]]
    where = ["i.library_id = :lid", "i.kind = :kind"]
    params = {"lid": library_id, "kind": kind, "uid": user["id"], "limit": limit, "offset": offset}
    if genre:
        where.append("EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value = :genre)")
        params["genre"] = genre
    if q:
        where.append("(i.title LIKE :q OR i.artist LIKE :q)")
        params["q"] = f"%{q.strip()}%"
    if filter in ("unwatched", "watched", "in_progress"):
        if kind == "movie":
            done = "i.id IN (SELECT item_id FROM watch_state WHERE user_id = :uid AND completed = 1)"
            progress = ("i.id IN (SELECT item_id FROM watch_state WHERE user_id = :uid AND completed = 0 "
                        "AND position > 0)")
            where.append({"unwatched": f"NOT {done}", "watched": done, "in_progress": progress}[filter])
        elif kind == "show":
            remaining = UNWATCHED_SHOW.replace(" AS unwatched", "")
            where.append({"unwatched": f"{remaining} > 0", "watched": f"{remaining} = 0",
                          "in_progress": f"{remaining} > 0 AND i.id IN (SELECT se.parent_id FROM items se JOIN "
                                         f"items e ON e.parent_id = se.id JOIN watch_state w ON w.item_id = e.id "
                                         f"WHERE w.user_id = :uid)"}[filter])
    added = SHOW_LATEST if kind == "show" else "i.added_at"
    order = SORTS.get(sort, SORTS["added"]).format(added=added)
    clause = " AND ".join(where)
    with db() as con:
        total = con.execute(f"SELECT COUNT(*) FROM items i WHERE {clause}", params).fetchone()[0]
        rows = con.execute(f"SELECT {_top_columns(kind)} FROM items i WHERE {clause} ORDER BY {order} "
                           f"LIMIT :limit OFFSET :offset", params).fetchall()
    return {"library": {"id": lib["id"], "name": lib["name"], "kind": lib["kind"]}, "total": total,
            "items": cards(rows, user["id"])}


@router.get("/libraries/{library_id}/genres")
def library_genres(library_id: int, user: dict = Depends(auth.current_user)):
    lib = _library(library_id, user)
    with db() as con:
        rows = con.execute("SELECT g.value AS genre, COUNT(*) AS n FROM items i, json_each(i.genres) g "
                           "WHERE i.library_id = ? AND i.kind = ? GROUP BY g.value ORDER BY n DESC",
                           (library_id, TOP_KIND[lib["kind"]])).fetchall()
    return [{"name": r["genre"], "count": r["n"]} for r in rows]


def _library(library_id: int, user: dict) -> dict:
    with db() as con:
        lib = con.execute("SELECT * FROM libraries WHERE id = ?", (library_id,)).fetchone()
    if not lib:
        raise HTTPException(404, "Library not found")
    auth.check_item_access(user, lib["id"])
    return dict(lib)


# ---- continue watching / next up -------------------------------------------------

def show_episodes(show_id: int) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT e.* FROM items e JOIN items se ON se.id = e.parent_id WHERE se.parent_id = ? "
            "AND e.kind = 'episode' ORDER BY (se.parent_index = 0), se.parent_index, e.index_number, e.sort_title",
            (show_id,))]


def next_up(user_id: int, show_id: int) -> tuple[dict | None, str, float]:
    """The episode the Play button on a show should start: (episode, action, last activity time)."""
    episodes = show_episodes(show_id)
    regular = [e for e in episodes if e.get("parent_index") != 0] or episodes
    if not regular:
        return None, "none", 0
    states = states_for(user_id, [e["id"] for e in regular])
    touched = [(states[e["id"]]["updated_at"] or 0, n) for n, e in enumerate(regular) if e["id"] in states]
    if not touched:
        return regular[0], "start", 0
    last_time, last_idx = max(touched)
    last = regular[last_idx]
    st = states[last["id"]]
    if not st["completed"] and (st["position"] or 0) > 0:
        return last, "resume", last_time
    for e in regular[last_idx + 1:] + regular[:last_idx]:
        if not states.get(e["id"], {}).get("completed"):
            return e, "next", last_time
    return regular[0], "rewatch", last_time


def _episode_cards(ids: list[int], user_id: int) -> dict[int, dict]:
    if not ids:
        return {}
    with db() as con:
        rows = con.execute(f"{EPISODE_JOIN} WHERE e.id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {c["id"]: c for c in cards(rows, user_id)}


def continue_watching(user: dict, limit: int = 20) -> list[dict]:
    where, params = auth.library_filter(user, "i.library_id")
    params = {**params, "uid": user["id"]}
    with db() as con:
        progress = con.execute(
            f"SELECT i.id, i.kind, w.updated_at FROM watch_state w JOIN items i ON i.id = w.item_id "
            f"WHERE w.user_id = :uid AND w.completed = 0 AND w.position > 15 AND i.kind IN ('movie','episode') "
            f"AND {where} ORDER BY w.updated_at DESC LIMIT 40", params).fetchall()
        shows = con.execute(
            f"SELECT se.parent_id AS show_id, MAX(w.updated_at) AS last FROM watch_state w "
            f"JOIN items i ON i.id = w.item_id JOIN items se ON se.id = i.parent_id "
            f"WHERE w.user_id = :uid AND i.kind = 'episode' AND {where} AND w.updated_at > :since "
            f"GROUP BY se.parent_id ORDER BY last DESC LIMIT 20",
            {**params, "since": time.time() - 120 * 86400}).fetchall()
    entries = []  # (time, item_id, kind, label)
    seen_shows = set()
    episode_ids = [r["id"] for r in progress if r["kind"] == "episode"]
    eps = _episode_cards(episode_ids, user["id"])
    for r in progress:
        if r["kind"] == "episode":
            show_id = eps.get(r["id"], {}).get("show_id")
            if show_id in seen_shows:
                continue
            seen_shows.add(show_id)
        entries.append((r["updated_at"], r["id"], r["kind"], "resume"))
    for s in shows:
        if s["show_id"] in seen_shows:
            continue
        episode, action, last = next_up(user["id"], s["show_id"])
        if episode and action == "next":
            entries.append((last, episode["id"], "episode", "next"))
            seen_shows.add(s["show_id"])
    entries.sort(reverse=True)
    entries = entries[:limit]
    eps = _episode_cards([e[1] for e in entries if e[2] == "episode"], user["id"])
    movie_ids = [e[1] for e in entries if e[2] == "movie"]
    with db() as con:
        movies = {r["id"]: r for r in con.execute(
            f"SELECT * FROM items WHERE id IN ({','.join('?' * len(movie_ids))})", movie_ids)} if movie_ids else {}
    movie_cards = {c["id"]: c for c in cards(list(movies.values()), user["id"])}
    out = []
    for _, item_id, kind, label in entries:
        c = eps.get(item_id) if kind == "episode" else movie_cards.get(item_id)
        if c:
            out.append({**c, "continue": label})
    return out


def _watchlist(user: dict, limit: int = 40) -> list[dict]:
    where, params = auth.library_filter(user, "i.library_id")
    with db() as con:
        rows = con.execute(
            f"SELECT {_top_columns('show')} FROM watchlist w JOIN items i ON i.id = w.item_id "
            f"WHERE w.user_id = :uid AND {where} ORDER BY w.added_at DESC LIMIT {int(limit)}",
            {**params, "uid": user["id"]}).fetchall()
    return cards(rows, user["id"])


def order_rows(rows: list[dict], order: list[str]) -> list[dict]:
    """Home sections in the user's order (Settings › Home screen). Sections the saved order doesn't know
    yet (a new library, a new genre) keep their place after the section that comes before them by default."""
    if not order:
        return rows
    rank = {rid: i for i, rid in enumerate(order)}
    known = sorted((r for r in rows if r["id"] in rank), key=lambda r: rank[r["id"]])
    out = list(known)
    prev = None
    for r in rows:                                  # default order
        if r["id"] not in rank:
            at = out.index(prev) + 1 if prev is not None else 0
            out.insert(at, r)
        prev = r
    return out


def _row_order(user: dict) -> list[str]:
    from .api_prefs import get_prefs
    try:
        ids = json.loads(get_prefs(user["id"]).get("home_row_order") or "[]")
        return [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []
    except ValueError:
        return []


RULES = ("recent", "top", "shuffle", "genre")


def _sections_config(user: dict) -> dict:
    """Settings › Home screen: { custom: [{id, title, libs, rule, genre}], titles: {id: name},
    libs: {id: [library ids]}, hidden: [ids] }."""
    from .api_prefs import get_prefs
    try:
        cfg = json.loads(get_prefs(user["id"]).get("home_sections") or "{}")
    except ValueError:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    out = {"custom": [], "titles": {}, "libs": {}, "hidden": []}
    for c in cfg.get("custom") or []:
        if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"].startswith("c-"):
            out["custom"].append({"id": c["id"], "title": str(c.get("title") or "New section")[:80],
                                  "libs": [int(x) for x in c.get("libs") or [] if str(x).isdigit()],
                                  "rule": c.get("rule") if c.get("rule") in RULES else "recent",
                                  "genre": str(c.get("genre") or "")[:80]})
    if isinstance(cfg.get("titles"), dict):
        out["titles"] = {str(k): str(v)[:80] for k, v in cfg["titles"].items() if str(v).strip()}
    if isinstance(cfg.get("libs"), dict):
        out["libs"] = {str(k): [int(x) for x in v if str(x).isdigit()] for k, v in cfg["libs"].items() if isinstance(v, list)}
    if isinstance(cfg.get("hidden"), list):
        out["hidden"] = [str(x) for x in cfg["hidden"]]
    return out


def _section_specs(user: dict, libs: list[dict], con) -> list[dict]:
    """Every section Home can show, in the default order, before the user's changes."""
    video_libs = [l for l in libs if l["kind"] in ("movies", "shows")]
    all_ids = [l["id"] for l in libs]
    specs = [{"id": "continue", "type": "continue", "title": "Continue watching", "libs": all_ids},
             {"id": "mylist", "type": "mylist", "title": "My list", "libs": all_ids}]
    specs += [{"id": f"recent-{l['id']}", "type": "recent", "title": f"Recently added to {l['name']}", "libs": [l["id"]]}
              for l in video_libs]
    ids = row_libraries(user, video_libs)       # the libraries Highest rated and the genre rows use by default
    specs.append({"id": "top", "type": "top", "title": "Highest rated on this server", "libs": ids})
    if ids:
        marks = ",".join("?" * len(ids))
        genres = con.execute(f"SELECT g.value AS genre, COUNT(*) AS n FROM items i, json_each(i.genres) g "
                             f"WHERE i.library_id IN ({marks}) AND i.kind IN ('movie','show') "
                             f"GROUP BY g.value HAVING n >= 4 ORDER BY n DESC LIMIT 6", ids).fetchall()
        specs += [{"id": f"genre-{g['genre']}", "type": "genre", "genre": g["genre"], "title": g["genre"], "libs": ids}
                  for g in genres]
    for l in libs:
        if l["kind"] not in ("movies", "shows"):
            title = "Recently added music" if l["kind"] == "music" else f"Latest in {l['name']}"
            specs.append({"id": f"recent-{l['id']}", "type": "recent", "title": title, "libs": [l["id"]]})
    return specs


def _section_items(con, uid: int, libs: list[dict], lib_ids: list[int], rule: str, genre: str = "",
                   seed: str = "") -> list:
    """The titles of a section: rule = recent | top (ten best rated) | shuffle (new order every day) | genre."""
    chosen = [l for l in libs if l["id"] in set(lib_ids)]
    if not chosen:
        return []
    kinds = sorted({TOP_KIND[l["kind"]] for l in chosen})
    ids = [l["id"] for l in chosen]
    where = (f"i.library_id IN ({','.join('?' * len(ids))}) AND i.kind IN ({','.join('?' * len(kinds))})")
    params = [*ids, *kinds]
    cols = f"i.*, {UNWATCHED_SHOW.replace(':uid', str(int(uid)))}, {CHILD_COUNT}" + (
        f", {PHOTO_COVER}" if "photoalbum" in kinds else "")
    if rule == "top":
        return con.execute(f"SELECT {cols} FROM items i WHERE {where} AND i.rating >= 7 "
                           f"ORDER BY i.rating DESC LIMIT 10", params).fetchall()
    if rule in ("genre", "shuffle"):
        if rule == "genre":
            where += " AND EXISTS (SELECT 1 FROM json_each(i.genres) x WHERE x.value = ?)"
            params.append(genre)
        pick = [r[0] for r in con.execute(f"SELECT i.id FROM items i WHERE {where}", params)]
        random.Random(time.strftime("%Y-%m-%d") + seed).shuffle(pick)
        pick = pick[:24]
        if not pick:
            return []
        got = {r["id"]: r for r in con.execute(f"SELECT {cols} FROM items i WHERE i.id IN ({','.join('?' * len(pick))})", pick)}
        return [got[i] for i in pick if i in got]
    order = "i.release_date DESC" if kinds == ["photoalbum"] else \
        f"CASE WHEN i.kind = 'show' THEN {SHOW_LATEST} ELSE i.added_at END DESC"
    return con.execute(f"SELECT {cols} FROM items i WHERE {where} ORDER BY {order} LIMIT 24", params).fetchall()


@router.get("/home/sections")
def home_sections(default: bool = False, user: dict = Depends(auth.current_user)):
    """Every section Home can show for this user (Settings › Home screen): names, libraries, what they show,
    in the user's order (or the default order); deleted ones are listed too so they can come back."""
    rows, libs = _home_rows(user, sections=True)
    if not default:
        rows = order_rows(rows, _row_order(user))
    with db() as con:
        ids = [l["id"] for l in libs if l["kind"] in ("movies", "shows")]
        genres = [r["genre"] for r in con.execute(
            f"SELECT g.value AS genre, COUNT(*) AS n FROM items i, json_each(i.genres) g "
            f"WHERE i.library_id IN ({','.join('?' * len(ids)) or 'NULL'}) AND i.kind IN ('movie','show') "
            f"GROUP BY g.value ORDER BY n DESC", ids)] if ids else []
    keep = ("id", "title", "default_title", "default_libs", "type", "rule", "genre", "libs", "custom", "hidden", "renamed", "libs_changed")
    return {"sections": [{k: r.get(k) for k in keep} for r in rows],
            "libraries": [{"id": l["id"], "name": l["name"], "kind": l["kind"]} for l in libs],
            "genres": genres}


@router.get("/home")
def home(user: dict = Depends(auth.current_user)):
    uid = user["id"]
    rows, libs, hero_pool = _home_rows(user)
    hero = banner_pick(user, libs) or (_hero_card(random.choice(hero_pool), uid) if hero_pool else None)
    return {"hero": hero, "rows": order_rows(rows, _row_order(user)),
            "libraries": [{"id": l["id"], "name": l["name"], "kind": l["kind"]} for l in libs]}


def _home_rows(user: dict, sections: bool = False):
    """Home's sections in the default order, with the user's own sections and changes (Settings › Home
    screen). sections=True: for the list in Settings: every section, also empty and deleted ones."""
    uid = user["id"]
    where, lparams = auth.library_filter(user, "l.id")
    with db() as con:
        libs = [dict(r) for r in con.execute(f"SELECT l.* FROM libraries l WHERE {where} ORDER BY l.position, l.id", lparams)]
    cfg = _sections_config(user)
    allowed = {l["id"] for l in libs}
    rows, hero_pool = [], []
    cw = mylist = None
    with db() as con:
        specs = _section_specs(user, libs, con)
        specs += [{"id": c["id"], "type": "custom", "rule": c["rule"], "genre": c["genre"], "title": c["title"],
                   "libs": c["libs"], "custom": True} for c in cfg["custom"]]
        for sp in specs:
            sp["default_title"], sp["default_libs"] = sp["title"], [i for i in sp["libs"] if i in allowed]
            if not sp.get("custom") and sp["id"] in cfg["titles"]:
                sp["title"], sp["renamed"] = cfg["titles"][sp["id"]], True
            if not sp.get("custom") and sp["id"] in cfg["libs"]:
                sp["libs"], sp["libs_changed"] = cfg["libs"][sp["id"]], True
            sp["libs"] = [i for i in sp["libs"] if i in allowed]
            sp["hidden"] = sp["id"] in cfg["hidden"] and not sp.get("custom")
            if sp["hidden"] and not sections:
                continue
            row = {**sp, "items": []}
            t = sp["type"]
            if t == "top" or sp.get("rule") == "top":
                row["style"] = "top10"
            if t == "continue":
                row["style"] = "continue"
            if not sections:
                lib_set = set(sp["libs"])
                if t == "continue":
                    if cw is None:
                        cw = continue_watching(user)
                    row["items"] = [c for c in cw if c.get("library_id") in lib_set]
                elif t == "mylist":
                    if mylist is None:
                        mylist = _watchlist(user)
                    row["items"] = [c for c in mylist if c.get("library_id") in lib_set]
                else:
                    rule = sp.get("rule") or {"recent": "recent", "top": "top", "genre": "genre"}[t]
                    items = _section_items(con, uid, libs, sp["libs"], rule, sp.get("genre") or "", seed=sp["id"])
                    if t == "top" and len(items) < 4:
                        items = []
                    row["items"] = cards(items, uid)
                    if t == "recent":
                        hero_pool += [dict(r) for r in items[:8] if r["kind"] in ("movie", "show") and (r["backdrop"] or r["thumb"])]
                if not row["items"]:
                    continue
                # the name links to the library when the section shows exactly that one library
                if t == "recent" and len(sp["libs"]) == 1:
                    row["library_id"] = sp["libs"][0]
            rows.append(row)
    if sections:
        return rows, libs
    return rows, libs, hero_pool


def _hero_card(item: dict, uid: int) -> dict:
    hero = card(item)
    hero.update(_play_target(item, uid))
    return hero


def banner_libraries(user: dict, libs: list[dict]) -> list[int]:
    """The movie and TV libraries the Home banner draws from (Settings › Home screen)."""
    from .api_prefs import get_prefs
    video = [l["id"] for l in libs if l["kind"] in ("movies", "shows")]
    chosen = [int(x) for x in (get_prefs(user["id"]).get("home_banner_libraries") or "").split(",") if x.isdigit()]
    picked = [i for i in video if i in chosen]
    return picked or video          # nothing chosen (or only libraries since removed): all of them


def row_libraries(user: dict, video_libs: list[dict]) -> list[int]:
    """The libraries the Highest rated and genre rows on Home draw from (Settings › Home screen)."""
    from .api_prefs import get_prefs
    video = [l["id"] for l in video_libs]
    chosen = [int(x) for x in (get_prefs(user["id"]).get("home_row_libraries") or "").split(",") if x.isdigit()]
    return [i for i in video if i in chosen] or video   # nothing chosen (or only removed libraries): all


def banner_pick(user: dict, libs: list[dict], exclude: list[int] | None = None) -> dict | None:
    """A random movie or show with artwork from the banner libraries, avoiding the ones just shown.
    Titles with an approved banner trailer go first; the others follow once those have all been shown."""
    ids = banner_libraries(user, libs)
    if not ids:
        return None
    uid = user["id"]
    marks = ",".join("?" * len(ids))
    cols = _top_columns("show").replace(":uid", str(int(uid)))
    sql = (f"SELECT {cols} FROM items i WHERE i.library_id IN ({marks}) AND i.kind IN ('movie','show') "
           f"AND (i.backdrop IS NOT NULL OR i.thumb IS NOT NULL)")
    with db() as con:
        for skip in (exclude or [])[:200], []:          # everything shown already: start over
            # titles whose trailer is approved for the banner (Edit › General) come first, then the rest
            q = sql + (f" AND i.id NOT IN ({','.join('?' * len(skip))})" if skip else "") + \
                " ORDER BY COALESCE(json_extract(i.extra, '$.home_trailer'), 0) DESC, RANDOM() LIMIT 1"
            row = con.execute(q, (*ids, *skip)).fetchone()
            if row:
                return _hero_card(dict(row), uid)
    return None


@router.get("/home/banner")
def home_banner(exclude: str = "", user: dict = Depends(auth.current_user)):
    """The next title for the Home banner (it changes every few minutes, Settings › Home screen)."""
    where, lparams = auth.library_filter(user, "l.id")
    with db() as con:
        libs = [dict(r) for r in con.execute(f"SELECT l.* FROM libraries l WHERE {where} ORDER BY l.position, l.id", lparams)]
    skip = [int(x) for x in exclude.split(",") if x.isdigit()]
    return {"hero": banner_pick(user, libs, skip)}


def _play_target(item: dict, user_id: int) -> dict:
    """What a Play button for this item starts, and from where."""
    if item["kind"] == "show":
        ep, action, _ = next_up(user_id, item["id"])
        if not ep:
            return {"play": None}
        st = states_for(user_id, [ep["id"]]).get(ep["id"], {})
        return {"play": {"item_id": ep["id"], "action": action, "season": ep.get("parent_index"),
                         "episode": ep.get("index_number"), "title": ep["title"],
                         "position": 0 if st.get("completed") else st.get("position", 0)}}
    if item["kind"] in ("movie", "episode"):
        st = states_for(user_id, [item["id"]]).get(item["id"], {})
        pos = 0 if st.get("completed") else (st.get("position") or 0)
        return {"play": {"item_id": item["id"], "action": "resume" if pos > 15 else "start", "position": pos}}
    return {"play": None}


# ---- item details --------------------------------------------------------------

def _extra(item: dict) -> dict:
    extra = jloads(item.get("extra"), {})
    for person in extra.get("cast", []):
        person["profile"] = images.url(person.get("profile"))
    return {k: v for k, v in extra.items() if not k.startswith("parsed_")}


def _files(item_id: int) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM files WHERE item_id = ? ORDER BY height DESC, size DESC",
                           (item_id,)).fetchall()
    return [file_info(r) for r in rows]


@router.get("/items/{item_id}")
def item_detail(item_id: int, user: dict = Depends(auth.current_user)):
    item = get_item(item_id, user)
    uid = user["id"]
    if item["kind"] == "season":
        return item_detail(item["parent_id"], user) | {"selected_season": item["id"]}
    kind = item["kind"]
    state = states_for(uid, [item_id]).get(item_id)
    out = card(item, state)
    out["extra"] = _extra(item)
    out["match_locked"] = bool(item["match_locked"])
    out["tmdb_id"] = item["tmdb_id"]
    out["tmdb_kind"] = metadata.tmdb_kind_of(item) if item["tmdb_id"] else None
    with db() as con:
        out["in_watchlist"] = con.execute("SELECT 1 FROM watchlist WHERE user_id = ? AND item_id = ?",
                                          (uid, item_id)).fetchone() is not None
        if kind in ("movie", "episode", "track"):
            out["files"] = _files(item_id)
        if kind == "movie":
            out.update(_play_target(item, uid))
            genres = jloads(item["genres"])
            if genres:
                rows = con.execute("SELECT i.* FROM items i WHERE i.library_id = ? AND i.kind = 'movie' AND i.id != ? "
                                   "AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value = ?) "
                                   "ORDER BY RANDOM() LIMIT 12", (item["library_id"], item_id, genres[0])).fetchall()
                out["similar"] = cards(rows, uid)
        elif kind == "show":
            seasons = con.execute(f"SELECT i.*, {CHILD_COUNT}, {SEASON_UNWATCHED} FROM items i WHERE "
                                  f"i.parent_id = :sid AND i.kind = 'season' ORDER BY (i.parent_index = 0), "
                                  f"i.parent_index", {"sid": item_id, "uid": uid}).fetchall()
            out["seasons"] = []
            for s in seasons:
                eps = con.execute("SELECT * FROM items WHERE parent_id = ? ORDER BY index_number, sort_title",
                                  (s["id"],)).fetchall()
                sc = card(s)
                sc["episodes"] = cards(eps, uid)
                out["seasons"].append(sc)
            out.update(_play_target(item, uid))
            out["unwatched"] = sum(s["unwatched"] for s in seasons if s["parent_index"])
            genres = jloads(item["genres"])
            if genres:
                rows = con.execute("SELECT i.* FROM items i WHERE i.library_id = ? AND i.kind = 'show' AND i.id != ? "
                                   "AND EXISTS (SELECT 1 FROM json_each(i.genres) g WHERE g.value = ?) "
                                   "ORDER BY RANDOM() LIMIT 12", (item["library_id"], item_id, genres[0])).fetchall()
                out["similar"] = cards(rows, uid)
        elif kind == "episode":
            ep = con.execute(f"{EPISODE_JOIN} WHERE e.id = ?", (item_id,)).fetchone()
            out.update(card(ep, state))
            out["extra"] = _extra(item)
            out["files"] = _files(item_id)
            out.update(_play_target(item, uid))
            out["next_id"] = next_episode_id(item_id)
        elif kind == "album":
            tracks = con.execute("SELECT i.*, f.duration AS duration, f.id AS file_id FROM items i "
                                 "LEFT JOIN files f ON f.item_id = i.id WHERE i.parent_id = ? "
                                 "GROUP BY i.id ORDER BY i.parent_index, i.index_number, i.sort_title",
                                 (item_id,)).fetchall()
            out["tracks"] = [{**card(t), "duration": t["duration"], "disc": t["parent_index"]} for t in tracks]
            more = con.execute(f"SELECT {_top_columns('album')} FROM items i WHERE i.library_id = ? "
                               f"AND i.kind = 'album' AND i.artist = ? AND i.id != ? ORDER BY i.year DESC",
                               (item["library_id"], item["artist"], item_id)).fetchall()
            out["more_by_artist"] = cards(more, uid)
        elif kind == "photoalbum":
            photos = con.execute("SELECT * FROM items WHERE parent_id = ? ORDER BY sort_title", (item_id,)).fetchall()
            out["photos"] = cards(photos, uid)
        elif kind in ("track", "photo") and item["parent_id"]:
            out["parent"] = card(con.execute("SELECT * FROM items WHERE id = ?", (item["parent_id"],)).fetchone())
    return out


def next_episode_id(episode_id: int) -> int | None:
    with db() as con:
        row = con.execute("SELECT se.parent_id AS show_id FROM items e JOIN items se ON se.id = e.parent_id "
                          "WHERE e.id = ?", (episode_id,)).fetchone()
    if not row:
        return None
    eps = show_episodes(row["show_id"])
    ids = [e["id"] for e in eps]
    try:
        n = ids.index(episode_id)
    except ValueError:
        return None
    return ids[n + 1] if n + 1 < len(ids) else None


@router.get("/items/{item_id}/preview")
def item_preview(item_id: int, user: dict = Depends(auth.current_user)):
    """The little that a hover preview card needs: artwork, facts, what Play starts, list and watched state."""
    item = get_item(item_id, user)
    uid = user["id"]
    kind = item["kind"]
    out = card(item, states_for(uid, [item_id]).get(item_id))
    with db() as con:
        out["in_watchlist"] = con.execute("SELECT 1 FROM watchlist WHERE user_id = ? AND item_id = ?",
                                          (uid, item_id)).fetchone() is not None
        if kind == "show":
            row = con.execute(f"SELECT {UNWATCHED_SHOW}, {CHILD_COUNT} FROM items i WHERE i.id = :id",
                              {"id": item_id, "uid": uid}).fetchone()
            out["unwatched"], out["child_count"] = row[0], row[1]
            out["watched"] = out["unwatched"] == 0 and bool(out["child_count"])
        else:
            out["watched"] = bool((out.get("progress") or {}).get("completed"))
        f = con.execute("SELECT height, hdr FROM files WHERE item_id = ? ORDER BY height DESC LIMIT 1", (item_id,)).fetchone()
        if f and f["height"]:
            h = f["height"]
            out["quality"] = "4K" if h >= 1600 else "HD" if h >= 700 else "SD"
            out["hdr"] = bool(f["hdr"])
    out.update(_play_target(item, uid))
    return out


class WatchedBody(BaseModel):
    watched: bool


@router.post("/items/{item_id}/watched")
def set_watched(item_id: int, body: WatchedBody, user: dict = Depends(auth.current_user)):
    item = get_item(item_id, user)
    with db() as con:
        if item["kind"] in ("movie", "episode"):
            ids = [item_id]
        elif item["kind"] == "season":
            ids = [r["id"] for r in con.execute("SELECT id FROM items WHERE parent_id = ?", (item_id,))]
        elif item["kind"] == "show":
            ids = [r["id"] for r in con.execute("SELECT e.id FROM items e JOIN items se ON se.id = e.parent_id "
                                                "WHERE se.parent_id = ?", (item_id,))]
        else:
            raise HTTPException(400, "Only movies and episodes can be marked watched")
        for i in ids:
            if body.watched:
                con.execute("INSERT INTO watch_state(user_id, item_id, position, completed, play_count, updated_at) "
                            "VALUES(?, ?, 0, 1, 1, ?) ON CONFLICT(user_id, item_id) DO UPDATE SET position = 0, "
                            "completed = 1, play_count = play_count + (completed = 0), updated_at = excluded.updated_at",
                            (user["id"], i, now()))
            else:
                con.execute("DELETE FROM watch_state WHERE user_id = ? AND item_id = ?", (user["id"], i))
    return {"ok": True, "count": len(ids)}


class ListBody(BaseModel):
    on: bool


@router.post("/items/{item_id}/watchlist")
def set_watchlist(item_id: int, body: ListBody, user: dict = Depends(auth.current_user)):
    get_item(item_id, user)
    with db() as con:
        if body.on:
            con.execute("INSERT OR IGNORE INTO watchlist(user_id, item_id, added_at) VALUES(?, ?, ?)",
                        (user["id"], item_id, now()))
        else:
            con.execute("DELETE FROM watchlist WHERE user_id = ? AND item_id = ?", (user["id"], item_id))
    return {"ok": True}


@router.get("/watchlist")
def watchlist(user: dict = Depends(auth.current_user)):
    return _watchlist(user, 500)


# ---- search & people ---------------------------------------------------------------

@router.get("/search")
def search(q: str, user: dict = Depends(auth.current_user)):
    q = q.strip()
    if len(q) < 2:
        return {"query": q, "titles": [], "episodes": [], "tracks": [], "people": []}
    where, params = auth.library_filter(user, "i.library_id")
    params = {**params, "q": f"%{q}%", "start": f"{q}%", "uid": user["id"]}
    with db() as con:
        titles = con.execute(
            f"SELECT {_top_columns('show')} FROM items i WHERE {where} AND i.kind IN ('movie','show','album','photoalbum') "
            f"AND (i.title LIKE :q OR i.original_title LIKE :q OR i.artist LIKE :q) "
            f"ORDER BY (i.title LIKE :start) DESC, i.rating DESC NULLS LAST LIMIT 40", params).fetchall()
        episodes = con.execute(f"{EPISODE_JOIN} WHERE e.kind = 'episode' AND e.title LIKE :q AND "
                               f"{where.replace('i.library_id', 'e.library_id')} LIMIT 12", params).fetchall()
        tracks = con.execute(f"SELECT i.*, a.title AS album_title FROM items i JOIN items a ON a.id = i.parent_id "
                             f"WHERE i.kind = 'track' AND i.title LIKE :q AND {where} LIMIT 12", params).fetchall()
        people = con.execute(
            f"SELECT json_extract(c.value, '$.name') AS name, MAX(json_extract(c.value, '$.profile')) AS profile, "
            f"COUNT(*) AS n FROM items i, json_each(i.extra, '$.cast') c WHERE {where} "
            f"AND json_extract(c.value, '$.name') LIKE :q GROUP BY name ORDER BY n DESC LIMIT 10", params).fetchall()
    return {
        "query": q,
        "titles": cards(titles, user["id"]),
        "episodes": cards(episodes, user["id"]),
        "tracks": [{**card(t), "album_title": t["album_title"]} for t in tracks],
        "people": [{"name": p["name"], "profile": images.url(p["profile"]), "count": p["n"]} for p in people],
    }


CREW_ROLES = (("directors", "Director"), ("creators", "Creator"), ("writers", "Writer"), ("composer", "Music"),
              ("cinematography", "Cinematography"), ("producers", "Producer"), ("editors", "Editor"))


@router.get("/people")
def person(name: str, user: dict = Depends(auth.current_user)):
    where, params = auth.library_filter(user, "i.library_id")
    with db() as con:
        rows = con.execute(
            f"SELECT DISTINCT i.*, json_extract(c.value, '$.character') AS character "
            f"FROM items i, json_each(i.extra, '$.cast') c WHERE {where} AND i.kind IN ('movie','show') "
            f"AND json_extract(c.value, '$.name') = :name ORDER BY i.year DESC",
            {**params, "name": name}).fetchall()
        profile = con.execute(
            f"SELECT json_extract(c.value, '$.profile') AS p FROM items i, json_each(i.extra, '$.cast') c "
            f"WHERE json_extract(c.value, '$.name') = :name AND p IS NOT NULL LIMIT 1", {"name": name}).fetchone()
        crew = {}
        for key, role in CREW_ROLES:      # directors, writers, composers … link here from the About panel
            for r in con.execute(f"SELECT i.* FROM items i, json_each(i.extra, '$.{key}') c WHERE {where} "
                                 f"AND i.kind IN ('movie','show') AND c.value = :name", {**params, "name": name}):
                entry = crew.setdefault(r["id"], [r, []])
                if role not in entry[1]:
                    entry[1].append(role)
    seen = {r["id"] for r in rows}
    roles = {r["id"]: [] for r in rows}
    for item_id, (r, rs) in crew.items():
        if item_id not in seen:
            rows.append(r)
            seen.add(item_id)
        roles[item_id] = rs
    rows.sort(key=lambda r: -(r["year"] or 0))
    items = cards(rows, user["id"])
    for item, row in zip(items, rows):
        item["character"] = row["character"] if "character" in row.keys() else None
        item["roles"] = roles.get(row["id"], [])
    return {"name": name, "profile": images.url(profile["p"]) if profile else None, "items": items}


# ---- artwork --------------------------------------------------------------------

_IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable"}


@router.get("/img/{kind}/{name}")
def image(kind: str, name: str, w: int | None = None, user: dict = Depends(auth.current_user)):
    path = images.resolve(kind, name, w)
    if not path:
        raise HTTPException(404, "Image not available")
    return FileResponse(path, headers=_IMMUTABLE)


@router.get("/photos/{item_id}/thumb")
def photo_thumb(item_id: int, w: int = 500, user: dict = Depends(auth.current_user)):
    path = _photo_path(item_id, user)
    thumb = images.photo_thumbnail(path, w, item_id)
    if not thumb:
        raise HTTPException(404, "This photo can't be read")
    return FileResponse(thumb, headers=_IMMUTABLE)


@router.get("/photos/{item_id}/full")
def photo_full(item_id: int, user: dict = Depends(auth.current_user)):
    path = _photo_path(item_id, user)
    # Large originals are downsized for the browser; the download link serves the original.
    thumb = images.photo_thumbnail(path, 1920, item_id)
    return FileResponse(thumb or path, headers=_IMMUTABLE)


def _photo_path(item_id: int, user: dict) -> str:
    item = get_item(item_id, user)
    if item["kind"] != "photo":
        raise HTTPException(404, "Not a photo")
    with db() as con:
        row = con.execute("SELECT path FROM files WHERE item_id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Photo file missing")
    return row["path"]
