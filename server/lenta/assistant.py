"""Working with an assistant program (1.26.0): its key, what it may do, and what it can ask about.

Asked for by Vlad on 9 Nov 2026 — "Jarvis and LENTA must be able to work with each other extremely well
and easy" — after the plan in his vault (`Jarvis - LENTA integration plan`) and the lead's answer to it
in `Homelab/LENTA media server`.

**The key.** Jarvis used to sign in like a person, with a session token. Since 1.19.0 signing out — on
any browser — and changing the password end every session of the account, so Jarvis went deaf the
first time Vlad signed out on the PC, and its token died after thirty days anyway. A key belongs to one
program and one account, does not expire unless told to, and is withdrawn on its own in Settings ›
Assistants. Only a SHA-256 of it is stored: a key is 43 random characters, so a slow hash would protect
nothing a fast one does not. `auth._key_user` reads it, **only** from the Authorization header.

**Scopes.** What a key may do is decided by the person who made it, and enforced here, before any
endpoint runs — not left to the program's own good behaviour. Everything not listed below is refused
with 403, so an endpoint added later is closed to keys until someone opens it on purpose.

  watch    find, play and control players, read libraries, channels and the guide — ids and numbers
  titles   titles, years, details, artwork and photos. **Off by default**: published LENTA keeps the
           ids-only answers of 1.24.0, and turning this on is the owner's choice (TMDB, 9 Nov 2026:
           personal, non-commercial use with an assistant is fine; no training).
  library  mark watched, My list
  admin    scans, metadata refreshes and /api/health — only if the account is an administrator.

Deleting anything, users, permissions and settings are never open to a key, whatever it holds.

  GET  /api/capabilities           this LENTA's version and what this key can use, by feature
  GET  /api/contract               the machine-readable contract (contract/assistant.json)
  GET  /api/health                 is LENTA well? (admin)
  GET/POST/PATCH/DELETE /api/admin/assistant-keys   make, list, change, withdraw (administrators, not keys)
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth
from .config import DATA_DIR, TRANSCODE_DIR, VERSION
from .db import db, get_setting, jloads

router = APIRouter(prefix="/api")
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.admin_user)])

CONTRACT_FILE = Path(__file__).parent / "contract" / "assistant.json"
CONTRACT = 1                     # raised only when something Jarvis reads is removed or renamed

SCOPES = ("watch", "titles", "library", "admin")
DEFAULT_SCOPES = ("watch", "library")
NAME_MAX = 40

_ID = r"\d{1,12}"
_DEV = r"[A-Za-z0-9_-]{8,64}"
# (method, path) a key may reach, per scope. Anchored regular expressions over the path only.
ALLOW = {
    None: [   # any key
        ("GET", r"/api/capabilities"), ("GET", r"/api/contract"), ("GET", r"/api/auth/me"),
        ("GET", r"/api/server"), ("GET", r"/api/system"), ("GET", r"/api/events"),
    ],
    "watch": [
        ("GET", r"/api/find"), ("GET", r"/api/now-playing"), ("GET", rf"/api/items/{_ID}/playable"),
        ("POST", rf"/api/players/{_DEV}/command"), ("GET", r"/api/libraries"),
        ("GET", r"/api/livetv/channels"), ("GET", r"/api/livetv/epg"), ("GET", r"/api/livetv/epg/now"),
        ("GET", rf"/api/livetv/channels/{_ID}/logo"),
    ],
    "titles": [
        ("GET", r"/api/search"), ("GET", rf"/api/items/{_ID}"),
        ("GET", rf"/api/items/{_ID}/(about|preview|extras)"),
        ("GET", rf"/api/libraries/{_ID}/(items|genres|genre-tiles)"),
        ("GET", r"/api/home"), ("GET", r"/api/home/banner"), ("GET", r"/api/people"),
        ("GET", r"/api/watchlist"), ("GET", r"/api/pick"), ("GET", r"/api/img/[A-Za-z0-9_-]+/[^/]+"),
        ("GET", rf"/api/photos/{_ID}/(thumb|full)"),
    ],
    "library": [
        ("POST", rf"/api/items/{_ID}/(watched|watchlist)"),
    ],
    "admin": [
        ("GET", r"/api/health"), ("POST", r"/api/admin/scan"), ("POST", rf"/api/admin/libraries/{_ID}/scan"),
        ("POST", rf"/api/admin/items/{_ID}/(refresh|refresh-metadata)"),
    ],
}
_COMPILED = {s: [(m, re.compile(p)) for m, p in rules] for s, rules in ALLOW.items()}


def allowed(method: str, path: str, scopes: list[str], is_admin: bool) -> bool:
    method = "GET" if method == "HEAD" else method
    for scope in (None, *scopes):
        if scope == "admin" and not is_admin:
            continue
        for m, rx in _COMPILED.get(scope, []):
            if m == method and rx.fullmatch(path):
                return True
    return False


# ---------------------------------------------------------------- keys

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


_used_lock = threading.Lock()
_used: dict[int, float] = {}      # key id → when "last used" was last written (at most once a minute)


def key_lookup(key: str) -> tuple[dict, dict] | None:
    """The key's own record and its account, or None for an unknown, expired or withdrawn key."""
    if not key.startswith(auth.KEY_PREFIX) or len(key) > 100:
        return None
    with db() as con:
        k = con.execute("SELECT id, user_id, name, scopes, expires_at FROM assistant_keys WHERE hash = ?",
                        (_hash(key),)).fetchone()
        if not k or (k["expires_at"] and k["expires_at"] < time.time()):
            return None
        row = con.execute("SELECT id, username, is_admin, all_libraries, color, token_epoch "
                          "FROM users WHERE id = ?", (k["user_id"],)).fetchone()
    if not row:
        return None
    now = time.time()
    with _used_lock:
        write = now - _used.get(k["id"], 0) > 60
        if write:
            _used[k["id"]] = now
    if write:
        with db() as con:
            con.execute("UPDATE assistant_keys SET last_used = ? WHERE id = ?", (now, k["id"]))
    scopes = [s for s in jloads(k["scopes"], []) if s in SCOPES]
    return {"id": k["id"], "name": k["name"], "scopes": scopes}, row


def has(user: dict, scope: str) -> bool:
    """Does this request carry a key with `scope`? A person's own session never does."""
    key = user.get("key")
    return bool(key) and scope in key["scopes"]


def _clean_scopes(scopes) -> list[str]:
    if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
        raise HTTPException(400, f"scopes are a list of: {', '.join(SCOPES)}")
    return [s for s in SCOPES if s in scopes]


class NewKey(BaseModel):
    name: str = Field(max_length=NAME_MAX)
    user_id: int | None = None
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    expires_days: int | None = Field(None, ge=1, le=3650)


class KeyChange(BaseModel):
    name: str | None = Field(None, max_length=NAME_MAX)
    scopes: list[str] | None = None


def _key_out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "user_id": r["user_id"], "username": r["username"],
            "hint": r["hint"], "scopes": jloads(r["scopes"], []), "created_at": r["created_at"],
            "last_used": r["last_used"], "expires_at": r["expires_at"]}


_KEY_SELECT = ("SELECT k.*, u.username FROM assistant_keys k JOIN users u ON u.id = k.user_id")


@admin.get("/assistant-keys")
def list_keys():
    with db() as con:
        return {"keys": [_key_out(r) for r in con.execute(_KEY_SELECT + " ORDER BY k.id")],
                "scopes": list(SCOPES), "default_scopes": list(DEFAULT_SCOPES)}


@admin.post("/assistant-keys")
def make_key(body: NewKey, user: dict = Depends(auth.current_user)):
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "Give the key a name, for example Jarvis")
    scopes = _clean_scopes(body.scopes)
    owner = body.user_id or user["id"]
    with db() as con:
        if not con.execute("SELECT 1 FROM users WHERE id = ?", (owner,)).fetchone():
            raise HTTPException(404, "No such account")
        key = auth.KEY_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        expires = now + body.expires_days * 86400 if body.expires_days else None
        cur = con.execute("INSERT INTO assistant_keys(user_id, name, hash, hint, scopes, created_at, expires_at) "
                          "VALUES(?, ?, ?, ?, ?, ?, ?)",
                          (owner, name, _hash(key), key[:8], json.dumps(scopes), now, expires))
        row = con.execute(_KEY_SELECT + " WHERE k.id = ?", (cur.lastrowid,)).fetchone()
    # The only time the key itself is ever said. It is not logged and cannot be read back.
    return {**_key_out(row), "key": key}


@admin.patch("/assistant-keys/{key_id}")
def change_key(key_id: int, body: KeyChange):
    with db() as con:
        if not con.execute("SELECT 1 FROM assistant_keys WHERE id = ?", (key_id,)).fetchone():
            raise HTTPException(404, "No such key")
        if body.name is not None:
            name = " ".join(body.name.split())
            if not name:
                raise HTTPException(400, "Give the key a name")
            con.execute("UPDATE assistant_keys SET name = ? WHERE id = ?", (name, key_id))
        if body.scopes is not None:
            con.execute("UPDATE assistant_keys SET scopes = ? WHERE id = ?",
                        (json.dumps(_clean_scopes(body.scopes)), key_id))
        return _key_out(con.execute(_KEY_SELECT + " WHERE k.id = ?", (key_id,)).fetchone())


@admin.delete("/assistant-keys/{key_id}")
def withdraw_key(key_id: int):
    with db() as con:
        gone = con.execute("DELETE FROM assistant_keys WHERE id = ?", (key_id,)).rowcount
    if not gone:
        raise HTTPException(404, "No such key")
    return {"ok": True}


# ---------------------------------------------------------------- titles

def describe(ids, user: dict) -> dict[int, dict]:
    """Title and year for each id this account may see — for keys with the `titles` scope only.

    An episode also carries its show's title, its season and number and its own title. Ids outside the
    account's libraries are left out, not answered with a placeholder: a device's beat says which item
    it plays, and nothing stops a page from saying any number it likes.
    """
    ids = [int(i) for i in {i for i in ids if i}]
    if not ids:
        return {}
    where, params = auth.library_filter(user, "i.library_id")
    marks = ",".join(f":id{n}" for n in range(len(ids)))
    params = {**params, **{f"id{n}": i for n, i in enumerate(ids)}}
    with db() as con:
        rows = con.execute(
            f"SELECT i.id, i.kind, i.title, i.year, i.index_number, se.parent_index AS season, "
            f"sh.title AS show_title, sh.year AS show_year FROM items i "
            f"LEFT JOIN items se ON i.kind = 'episode' AND se.id = i.parent_id "
            f"LEFT JOIN items sh ON i.kind = 'episode' AND sh.id = se.parent_id "
            f"WHERE i.id IN ({marks}) AND {where}", params).fetchall()
    out = {}
    for r in rows:
        if r["kind"] == "episode":
            out[r["id"]] = {"title": r["show_title"], "year": r["show_year"], "episode_title": r["title"],
                            "season": r["season"], "episode": r["index_number"]}
        else:
            out[r["id"]] = {"title": r["title"], "year": r["year"]}
    return out


def channel_names(ids) -> dict[int, str]:
    """A channel's name is the one in Vlad's own playlist or the broadcaster's, not TMDB's."""
    ids = [int(i) for i in {i for i in ids if i}]
    if not ids:
        return {}
    with db() as con:
        return {r["id"]: r["name"] for r in con.execute(
            f"SELECT id, name FROM tv_channels WHERE id IN ({','.join('?' * len(ids))})", ids)}


# ---------------------------------------------------------------- capabilities and contract

@router.get("/capabilities")
def capabilities(user: dict = Depends(auth.current_user)):
    """What this LENTA can do for the caller. A feature missing from `features` is not available — on
    this version, or to this key — and the number is the feature's own version, raised only when it
    changes in a way that could break a program that reads it."""
    from . import players
    key = user.get("key")
    scopes = key["scopes"] if key else list(SCOPES)           # a person's session has the account's rights
    admin_ok = bool(user["is_admin"]) and "admin" in scopes
    live = True
    try:
        auth.live_tv_viewer(user)
    except HTTPException:
        live = False
    f: dict = {}
    if "watch" in scopes:
        f["find"] = {"version": 3, "kinds": ["film", "show", "any", "photos", "channel"],
                     "filters": ["genre", "no_genre", "year_from", "year_to", "library", "no_library",
                                 "unwatched", "exclude"]}
        f["now_playing"] = 1
        f["players"] = {"version": 2, "actions": list(players.ACTIONS)}
        f["events"] = {"version": 1, "types": events_types(scopes, admin_ok)}
        if live:
            f["live_tv"] = 1
    if "titles" in scopes:
        f["titles"] = 1
        f["browse"] = 1
    if "library" in scopes:
        f["library_marks"] = 1
    if admin_ok:
        f["admin"] = {"version": 1, "actions": ["scan", "scan_library", "refresh_item"]}
        f["health"] = 1
    return {"lenta": VERSION, "contract": CONTRACT,
            "account": {"id": user["id"], "admin": bool(user["is_admin"])},
            "key": {"name": key["name"], "scopes": key["scopes"]} if key else None,
            "features": f}


def events_types(scopes: list[str], admin_ok: bool) -> list[str]:
    from .events import TYPES, ADMIN_TYPES
    return [t for t in TYPES if t not in ADMIN_TYPES or admin_ok]


@router.get("/contract")
def contract(user: dict = Depends(auth.current_user)):
    """The contract a program is built against, from this server's own copy. Jarvis's tests compare
    their copy with this answer, so the two sides cannot drift apart without one of them noticing."""
    try:
        return JSONResponse(json.loads(CONTRACT_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise HTTPException(500, "The contract file is missing or unreadable")


# ---------------------------------------------------------------- health

def _free(path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


LOW_DISK = 20 * 1024 ** 3            # under 20 GB free on the data or transcode folder is worth saying


@router.get("/health")
def health(user: dict = Depends(auth.admin_user)):
    """Is LENTA well? Each problem is a short record a program can say aloud, not a sentence.

    Only LENTA's own folders are measured. Library folders are not: on a network share, `disk_usage`
    waits for the mount, which is why the Settings pages stopped asking for it (1.16.0).
    """
    from . import scanner, transcode
    from .api_admin import STARTED
    from . import logs
    problems = []
    with db() as con:
        libs = [dict(r) for r in con.execute("SELECT id, name, kind, last_scan, scan_report FROM libraries "
                                             "ORDER BY position, id")]
    out_libs = []
    for lib in libs:
        rep = jloads(lib.get("scan_report"), {}) or {}
        entry = {"id": lib["id"], "name": lib["name"], "kind": lib["kind"], "last_scan": lib["last_scan"],
                 "ok": rep.get("ok"), "added": rep.get("added"), "failed": rep.get("failed")}
        out_libs.append(entry)
        if rep.get("ok") is False:
            problems.append({"what": "scan_failed", "library_id": lib["id"]})
        elif rep.get("failed"):
            problems.append({"what": "files_unreadable", "library_id": lib["id"], "count": rep["failed"]})
    disks = {"data": _free(DATA_DIR), "transcode": _free(TRANSCODE_DIR)}
    for name, free in disks.items():
        if free is not None and free < LOW_DISK:
            problems.append({"what": "disk_low", "folder": name, "free_bytes": free})
    hw = transcode.hw
    if hw.get("accel") == "none":
        problems.append({"what": "no_hardware_encoder"})
    refused = sorted(transcode.nvdec_refused())
    hour = time.time() - 3600
    recent = [r for r in logs.recent(500) if (r.get("time") or 0) >= hour]
    errors = sum(1 for r in recent if r.get("level") in ("ERROR", "CRITICAL"))
    warnings = sum(1 for r in recent if r.get("level") == "WARNING")
    if errors:
        problems.append({"what": "errors_logged", "count": errors})
    with transcode._sessions_lock:
        conversions = sum(1 for s in transcode.sessions.values() if s.alive())
    return {
        "ok": not problems, "problems": problems, "lenta": VERSION,
        "uptime_s": round(time.time() - STARTED), "libraries": out_libs,
        "scans_running": [s.get("library_id") for s in scanner.running()],
        "free_bytes": disks, "encoder": {"accel": hw.get("accel"), "encoder": hw.get("encoder")},
        "gpu_cannot_decode": refused, "conversions_running": conversions,
        "log_last_hour": {"errors": errors, "warnings": warnings},
    }
