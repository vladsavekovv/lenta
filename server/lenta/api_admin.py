"""Administrative dashboard API: overview, libraries, users, settings, metadata fixes, sessions, logs."""
import json
import os
import shutil
import subprocess
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import about, auth, imdb, logs, metadata, scanner, trailers, transcode, trickplay, intros, watcher
from .api_auth import validate_new_user
from .api_playback import current_now_playing
from .config import DATA_DIR, FFMPEG, LIBRARY_KINDS, TRANSCODE_DIR, VERSION
from .db import SETTING_DEFAULTS, all_settings, db, get_setting, jloads, now, set_setting
from .serialize import TOP_KIND, card

def _run_user() -> str:
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.admin_user)])
log = logs.get("admin")
STARTED = time.time()
_ffmpeg_version = None


def ffmpeg_version() -> str:
    global _ffmpeg_version
    if _ffmpeg_version is None:
        try:
            out = subprocess.run([FFMPEG, "-version"], capture_output=True, timeout=10).stdout.decode()
            _ffmpeg_version = out.split("\n")[0].replace("ffmpeg version ", "").split(" Copyright")[0]
        except (OSError, subprocess.SubprocessError):
            _ffmpeg_version = "not found"
    return _ffmpeg_version


@router.get("/overview")
def overview():
    with db() as con:
        counts = {r["kind"]: r["n"] for r in con.execute("SELECT kind, COUNT(*) AS n FROM items GROUP BY kind")}
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        libs = [dict(r) for r in con.execute("SELECT * FROM libraries ORDER BY position, id")]
        total_size = con.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
    storage, seen = [], set()
    for lib in libs:
        for path in jloads(lib["paths"]):
            try:
                usage = shutil.disk_usage(path)
                dev = os.stat(path).st_dev
            except OSError:
                storage.append({"path": path, "library": lib["name"], "missing": True})
                continue
            if dev in seen:
                continue
            seen.add(dev)
            storage.append({"path": path, "library": lib["name"], "total": usage.total, "used": usage.used,
                            "free": usage.free})
    try:
        data_free = shutil.disk_usage(DATA_DIR).free
    except OSError:
        data_free = None
    return {
        "version": VERSION, "uptime": time.time() - STARTED, "ffmpeg": ffmpeg_version(), "data_dir": str(DATA_DIR),
        "data_free": data_free, "transcode_dir": str(TRANSCODE_DIR), "trailers": trailers.counts(), "trickplay": trickplay.counts(), "intros": intros.counts(), "watcher": watcher.status(), "about": about.counts(),
        "imdb": imdb.status(),
        "hardware": transcode.hw, "counts": counts, "users": users, "media_size": total_size,
        "libraries": len(libs), "storage": storage,
        "scan": {**scanner.overview(), "scans": scanner.running(), "queued": scanner.queued()},
        "sessions": [s.info() for s in transcode.sessions.values()],
        "now_playing": current_now_playing(),
    }


@router.get("/logs")
def get_logs(limit: int = 200):
    return logs.recent(limit)


# ---- libraries ----------------------------------------------------------------

class LibraryBody(BaseModel):
    name: str
    kind: str
    paths: list[str]


def _validate_library(body: LibraryBody) -> list[str]:
    if body.kind not in LIBRARY_KINDS:
        raise HTTPException(400, "Library type must be movies, shows, music or photos")
    if not body.name.strip():
        raise HTTPException(400, "Give the library a name")
    paths = []
    for p in body.paths:
        p = os.path.abspath(os.path.expanduser(p.strip()))
        if not p or p in paths:
            continue
        if not os.path.isdir(p):
            raise HTTPException(400, f"Folder not found: {p}")
        if not os.access(p, os.R_OK | os.X_OK):
            raise HTTPException(400, f"The server can't read {p}. Give the '{_run_user()}' account read access.")
        paths.append(p)
    if not paths:
        raise HTTPException(400, "Add at least one folder")
    return paths


@router.get("/libraries")
def list_libraries():
    with db() as con:
        rows = con.execute("SELECT * FROM libraries ORDER BY position, id").fetchall()
        out = []
        for r in rows:
            n = con.execute("SELECT COUNT(*) FROM items WHERE library_id = ? AND kind = ?",
                            (r["id"], TOP_KIND[r["kind"]])).fetchone()[0]
            files = con.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE library_id = ?",
                                (r["id"],)).fetchone()
            unmatched = con.execute("SELECT COUNT(*) FROM items WHERE library_id = ? AND kind IN ('movie','show') "
                                    "AND matched = 0", (r["id"],)).fetchone()[0]
            cur = scanner.library_status(r["id"])
            if cur and cur.get("running"):
                status = {"state": "scanning", "phase": cur["phase"], "done": cur["done"], "total": cur["total"]}
            elif r["id"] in scanner.queued():
                status = {"state": "queued"}
            else:
                status = {"state": "idle"}
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "paths": jloads(r["paths"]),
                        "last_scan": r["last_scan"], "count": n, "files": files[0], "size": files[1],
                        "unmatched": unmatched, "status": status,
                        "report": jloads(r["scan_report"]) if r["scan_report"] else None})
    return out


@router.post("/libraries")
def create_library(body: LibraryBody):
    paths = _validate_library(body)
    with db() as con:
        cur = con.execute("INSERT INTO libraries(name, kind, paths, created_at, position) VALUES(?, ?, ?, ?, "
                          "(SELECT COALESCE(MAX(position), 0) + 1 FROM libraries))",
                          (body.name.strip(), body.kind, json.dumps(paths), now()))
    scanner.request_scan(cur.lastrowid)
    return {"id": cur.lastrowid}


@router.put("/libraries/order")
def order_libraries(body: dict):
    """New order of the libraries in the menu and on Home: {"ids": [3, 1, 2]}. Applies to everyone."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "ids must be a list of library ids")
    with db() as con:
        known = [r["id"] for r in con.execute("SELECT id FROM libraries ORDER BY position, id")]
        order = [i for i in ids if i in known] + [i for i in known if i not in ids]
        for pos, lid in enumerate(order, 1):
            con.execute("UPDATE libraries SET position = ? WHERE id = ?", (pos, lid))
    return {"ids": order}


@router.put("/libraries/{library_id}")
def update_library(library_id: int, body: LibraryBody):
    paths = _validate_library(body)
    with db() as con:
        row = con.execute("SELECT kind FROM libraries WHERE id = ?", (library_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Library not found")
        if row["kind"] != body.kind:
            raise HTTPException(400, "A library's type can't change. Create a new library instead.")
        con.execute("UPDATE libraries SET name = ?, paths = ? WHERE id = ?",
                    (body.name.strip(), json.dumps(paths), library_id))
    scanner.request_scan(library_id)
    return {"ok": True}


@router.delete("/libraries/{library_id}")
def delete_library(library_id: int):
    with db() as con:
        con.execute("DELETE FROM libraries WHERE id = ?", (library_id,))
    return {"ok": True}


@router.post("/libraries/{library_id}/scan")
def scan_library(library_id: int, refresh: int = 0):
    scanner.request_scan(library_id, bool(refresh))
    return {"ok": True}


@router.post("/scan")
def scan_all():
    scanner.request_scan(None)
    return {"ok": True}


@router.post("/scan-path")
def scan_path(body: dict):
    """Scan one folder (or file) now, e.g. from a script or Radarr/Sonarr after a download:
    POST /api/admin/scan-path {"path": "/mnt/media/Movies/Inception (2010)"}  (admin session or Bearer token)."""
    import os
    path = os.path.normpath(str(body.get("path") or ""))
    if not os.path.isabs(path):
        raise HTTPException(400, "Give the full path of a folder or file inside a library")
    folder = path if os.path.isdir(path) or not os.path.splitext(path)[1] else os.path.dirname(path)
    with db() as con:
        libs = con.execute("SELECT id, name, paths FROM libraries").fetchall()
    for lib in libs:
        for root in jloads(lib["paths"], []):
            root = os.path.normpath(root)
            if folder == root:
                scanner.request_scan(lib["id"])
                return {"ok": True, "library": lib["name"], "scan": "library"}
            if folder.startswith(os.path.join(root, "")):
                top = os.path.join(root, os.path.relpath(folder, root).split(os.sep)[0])
                scanner.request_scan_folders(lib["id"], [top])
                return {"ok": True, "library": lib["name"], "scan": top}
    raise HTTPException(404, "That path isn't inside any library folder")


@router.get("/browse")
def browse(path: str = "/"):
    path = os.path.abspath(os.path.expanduser(path or "/"))
    try:
        entries = sorted((e for e in os.scandir(path) if e.is_dir(follow_symlinks=True)
                          and not e.name.startswith(".")), key=lambda e: e.name.lower())
    except OSError as exc:
        raise HTTPException(400, f"Can't open {path}: {exc.strerror}")
    parent = os.path.dirname(path) if path != "/" else None
    return {"path": path, "parent": parent, "folders": [{"name": e.name, "path": e.path} for e in entries[:500]]}


# ---- users ------------------------------------------------------------------------

class UserBody(BaseModel):
    username: str
    password: str | None = None
    is_admin: bool = False
    all_libraries: bool = True
    library_ids: list[int] = []


@router.get("/users")
def list_users():
    with db() as con:
        rows = con.execute("SELECT id, username, is_admin, all_libraries, color, created_at FROM users "
                           "ORDER BY id").fetchall()
        out = []
        for r in rows:
            libs = [x["library_id"] for x in con.execute("SELECT library_id FROM user_libraries WHERE user_id = ?",
                                                         (r["id"],))]
            last = con.execute("SELECT MAX(updated_at) FROM watch_state WHERE user_id = ?", (r["id"],)).fetchone()[0]
            out.append({**dict(r), "is_admin": bool(r["is_admin"]), "all_libraries": bool(r["all_libraries"]),
                        "library_ids": libs, "last_watched": last})
    return out


def _set_user_libraries(con, user_id: int, ids: list[int]) -> None:
    con.execute("DELETE FROM user_libraries WHERE user_id = ?", (user_id,))
    for lid in ids:
        con.execute("INSERT OR IGNORE INTO user_libraries(user_id, library_id) VALUES(?, ?)", (user_id, lid))


@router.post("/users")
def create_user(body: UserBody):
    username = validate_new_user(body.username, body.password or "")
    with db() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, f"There is already a user called {username}")
        cur = con.execute("INSERT INTO users(username, password_hash, is_admin, all_libraries, color, created_at) "
                          "VALUES(?, ?, ?, ?, ?, ?)", (username, auth.hash_password(body.password),
                                                       int(body.is_admin), int(body.all_libraries),
                                                       auth.pick_color(), now()))
        _set_user_libraries(con, cur.lastrowid, body.library_ids)
    return {"id": cur.lastrowid}


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserBody, admin: dict = Depends(auth.admin_user)):
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "User not found")
        if row["is_admin"] and not body.is_admin:
            admins = con.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "Keep at least one administrator")
        username = validate_new_user(body.username, body.password if body.password else "placeholder")
        clash = con.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user_id)).fetchone()
        if clash:
            raise HTTPException(409, f"There is already a user called {username}")
        con.execute("UPDATE users SET username = ?, is_admin = ?, all_libraries = ? WHERE id = ?",
                    (username, int(body.is_admin), int(body.all_libraries), user_id))
        if body.password:
            con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(body.password), user_id))
        _set_user_libraries(con, user_id, body.library_ids)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(auth.admin_user)):
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't remove your own account while signed in to it")
    with db() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"ok": True}


# ---- settings -------------------------------------------------------------------

EDITABLE = [k for k in SETTING_DEFAULTS if k != "jwt_secret"]


@router.get("/settings")
def get_settings():
    s = all_settings()
    out = {k: s.get(k, "") for k in EDITABLE}
    for secret in ("tmdb_api_key", "opensubtitles_api_key", "opensubtitles_password", "omdb_api_key", "fanart_api_key", "fanart_client_key", "kinocheck_api_key"):
        value = out.pop(secret)
        out[f"{secret}_set"] = bool(value)
        out[f"{secret}_hint"] = f"…{value[-4:]}" if value and secret != "opensubtitles_password" else ""
    return out


@router.put("/settings")
def put_settings(body: dict):
    redetect = refresh_about = False
    for k, v in body.items():
        if k not in EDITABLE:
            continue
        if k in ("tmdb_api_key", "opensubtitles_api_key", "opensubtitles_password", "omdb_api_key", "fanart_api_key", "fanart_client_key", "kinocheck_api_key") and v is None:
            continue
        if k == "default_theme" and v not in ("projector", "noir", "abyss", "velvet", "aurora", "redline"):
            raise HTTPException(400, "Unknown theme")
        if k == "hw_accel" and v not in ("auto", "nvenc", "qsv", "vaapi", "none"):
            raise HTTPException(400, "Unknown hardware acceleration option")
        if k == "scan_interval_minutes":
            try:
                v = max(0, int(v))
            except ValueError:
                raise HTTPException(400, "Scan interval must be a number of minutes")
        if k.startswith("artwork_sources_"):
            from . import artwork
            try:
                v = artwork.validate_order(v)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        if k in ("hw_accel", "vaapi_device"):
            redetect = True
        value = "" if v is None else str(v).strip()
        if k == "metadata_language" and value != (get_setting(k) or ""):
            with db() as con:        # Wikipedia summaries are fetched again in the new language
                con.execute("UPDATE items SET extra = json_remove(extra, '$.wiki') WHERE json_extract(extra, '$.wiki') IS NOT NULL")
        if k == "trickplay_interval":
            try:
                value = str(max(2, min(60, int(value or 10))))
            except ValueError:
                raise HTTPException(400, "Thumbnail interval must be a number of seconds")
        if k == "watch_interval":
            try:
                value = str(max(30, min(1800, int(value or 60))))
            except ValueError:
                raise HTTPException(400, "Watch interval must be a number of seconds")
        if k == "intro_detection" and value != (get_setting(k) or ""):
            set_setting(k, value)
            intros.start()
        if k in ("trickplay", "trickplay_interval") and value != (get_setting(k) or ""):
            set_setting(k, value)
            trickplay.start()
        if k == "imdb_ratings" and value == "1" and (get_setting(k) or "1") != "1":
            set_setting(k, value)
            imdb.request_refresh()
        if k == "omdb_api_key" and value != (get_setting(k) or ""):
            refresh_about = True
        set_setting(k, value)
    if refresh_about:
        from . import about
        with db() as con:            # entries that failed with the old key are tried again
            con.execute("UPDATE items SET extra = json_remove(extra, '$.omdb') WHERE json_extract(extra, '$.omdb.error') IS NOT NULL")
        about.request_refresh()
    if redetect:
        threading.Thread(target=transcode.detect_hardware, daemon=True).start()
    return get_settings()


@router.post("/settings/test-tmdb")
def test_tmdb(body: dict):
    key = body.get("tmdb_api_key")
    try:
        metadata.TMDB(key if key else None).test()
    except metadata.TMDBError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.post("/settings/test-omdb")
def test_omdb(body: dict):
    from . import about
    key = (body.get("omdb_api_key") or "").strip() or about.omdb_key()
    if not key:
        raise HTTPException(400, "Paste an OMDb key first")
    try:
        d = about.omdb_request({"i": "tt0087332"}, key)          # Ghostbusters (1984)
    except about.LookupError_ as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(400, "OMDb could not be reached. Check the server's internet connection.")
    return {"ok": True, "sample": f"{d.get('Title')} ({d.get('Year')}) — IMDb {d.get('imdbRating')}"}


@router.get("/artwork-sources")
def artwork_sources():
    from . import artwork
    return {"movie": artwork.order("movie"), "show": artwork.order("show"), "apply": artwork.state}


@router.post("/artwork/apply")
def artwork_apply():
    """Pick every title's pictures again with the current source order (locked pictures stay)."""
    from . import artwork
    return artwork.request_apply()


@router.post("/settings/test-fanart")
def test_fanart(body: dict):
    from . import artwork
    key = (body.get("fanart_api_key") or "").strip() or artwork.fanart_key()
    client = (body.get("fanart_client_key") or "").strip() or artwork.fanart_client_key()
    if not key and not client:
        raise HTTPException(400, "Paste your Fanart.tv project API key first")
    try:
        d = artwork.fanart_request("movie", "603", key or client, client)      # The Matrix
    except PermissionError:
        raise HTTPException(400, "Fanart.tv rejected the key. Fanart.tv needs a project API key (free, fanart.tv › Get an API key › "
                                 "project key); the personal key alone isn't enough.")
    except Exception:
        raise HTTPException(400, "Fanart.tv could not be reached. Check the server's internet connection.")
    n = sum(len((d or {}).get(k, [])) for k in ("movieposter", "moviebackground", "hdmovielogo"))
    return {"ok": True, "sample": f"The Matrix: {n} posters, backgrounds and logos"}


@router.post("/settings/test-kinocheck")
def test_kinocheck(body: dict):
    from . import kinocheck
    return kinocheck.test((body.get("kinocheck_api_key") or "").strip() or None)


@router.post("/settings/test-apple")
def test_apple():
    from . import trailer_sources
    return trailer_sources.apple_test()        # {ok, message, report: what each way of asking returned, sample}


@router.post("/settings/test-opensubtitles")
def test_opensubtitles():
    from . import opensubtitles
    client = opensubtitles.OpenSubtitles()
    if not client.enabled:
        raise HTTPException(400, "Save an OpenSubtitles API key first")
    try:
        return client.test()
    except opensubtitles.SubtitleError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(400, "OpenSubtitles could not be reached. Check the server's internet connection.")


@router.post("/hardware/detect")
def redetect_hardware():
    return transcode.detect_hardware()


# ---- metadata fixes ------------------------------------------------------------------

@router.get("/unmatched")
def unmatched(library_id: int | None = None):
    with db() as con:
        sql = "SELECT * FROM items WHERE kind IN ('movie','show') AND matched = 0"
        params = []
        if library_id:
            sql += " AND library_id = ?"
            params.append(library_id)
        rows = con.execute(sql + " ORDER BY sort_title LIMIT 500", params).fetchall()
        out = []
        for r in rows:
            f = con.execute("SELECT path FROM files f JOIN items e ON e.id = f.item_id LEFT JOIN items se ON "
                            "se.id = e.parent_id WHERE e.id = ? OR se.parent_id = ? LIMIT 1", (r["id"], r["id"])).fetchone()
            out.append({**card(r), "path": f["path"] if f else None,
                        "attempts": jloads(r["extra"], {}).get("match_attempts", []) if r["extra"] else []})
    return out


_retry = {"running": False, "done": 0, "total": 0, "matched": 0}


@router.post("/unmatched/retry")
def retry_unmatched(library_id: int | None = None):
    """Search again for every title without metadata (not ones fixed by hand), in the background."""
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        raise HTTPException(400, "Add a TMDB API key in Settings first")
    if _retry["running"]:
        return _retry

    def work():
        try:
            _work()
        finally:
            _retry["running"] = False

    def _work():
        # Read movie names again with the current rules first (splits films that were filed under a
        # collection folder's name), so this works without waiting for a library scan.
        with db() as con:
            libs = [dict(r) for r in con.execute("SELECT * FROM libraries WHERE kind = 'movies'")
                    if library_id is None or r["id"] == library_id]
        for lib in libs:
            try:
                scanner._reparse_movies(lib, jloads(lib["paths"]))
                set_setting(f"parser_version_{lib['id']}", scanner.PARSER_VERSION)
            except Exception as exc:
                log.warning("Re-reading names in '%s' failed: %s", lib["name"], exc)
        with db() as con:
            for lib in libs:
                scanner._cleanup(con, lib["id"])
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM items WHERE kind IN ('movie','show') AND matched = 0 AND match_locked = 0 "
                "AND (? IS NULL OR library_id = ?)", (library_id, library_id))]
        _retry["total"] = len(ids)
        for n, item_id in enumerate(ids, 1):
            try:
                if metadata.match_item(item_id, tmdb):
                    _retry["matched"] += 1
            except Exception as exc:
                log.warning("Retry for item %s failed: %s", item_id, exc)
            _retry["done"] = n
        trailers.request_index()

    _retry.update(running=True, done=0, total=0, matched=0)
    threading.Thread(target=work, name="metadata-retry", daemon=True).start()
    return _retry


@router.get("/unmatched/retry")
def retry_status():
    return _retry


@router.get("/match")
def match_search(item_id: int, query: str, year: int | None = None):
    with db() as con:
        item = con.execute("SELECT kind FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item or item["kind"] not in ("movie", "show"):
        raise HTTPException(400, "Only movies and shows can be matched")
    tmdb = metadata.TMDB()
    if not tmdb.enabled:
        raise HTTPException(400, "Add a TMDB API key in Settings first")
    try:
        results = tmdb.search(item["kind"], query, year)
    except metadata.TMDBError as exc:
        raise HTTPException(400, str(exc))
    from . import images
    return [{**r, "poster": images.url(r["poster"])} for r in results]


class MatchBody(BaseModel):
    tmdb_id: int


@router.post("/items/{item_id}/match")
def apply_match(item_id: int, body: MatchBody):
    if not metadata.match_item(item_id, tmdb_id=body.tmdb_id):
        raise HTTPException(400, "Could not apply that match. See the server log for details.")
    with db() as con:
        con.execute("UPDATE items SET match_locked = 1 WHERE id = ?", (item_id,))
    return {"ok": True}


@router.post("/items/{item_id}/refresh")
def refresh_item(item_id: int):
    with db() as con:
        item = con.execute("SELECT tmdb_id, kind, extra FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404, "Item not found")
    if not metadata.match_item(item_id, tmdb_id=item["tmdb_id"],
                               tmdb_kind=metadata.tmdb_kind_of(item) if item["tmdb_id"] else None):
        raise HTTPException(400, "Metadata refresh failed. Check the TMDB key and the server log.")
    return {"ok": True}


class EditBody(BaseModel):
    title: str
    year: int | None = None
    overview: str | None = None


@router.put("/items/{item_id}")
def edit_item(item_id: int, body: EditBody):
    if not body.title.strip():
        raise HTTPException(400, "Title can't be empty")
    with db() as con:
        con.execute("UPDATE items SET title = ?, sort_title = ?, year = ?, overview = ?, match_locked = 1, "
                    "updated_at = ? WHERE id = ?", (body.title.strip(), metadata.sort_title(body.title), body.year,
                                                    body.overview, now(), item_id))
    return {"ok": True}


@router.delete("/sessions/{sid}")
def kill_session(sid: str):
    transcode.stop_session(sid)
    return {"ok": True}
