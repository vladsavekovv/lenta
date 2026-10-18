"""Server info, first-run setup, sign in / out."""
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth
from .config import VERSION
from .db import db, get_setting, now, set_setting

router = APIRouter(prefix="/api")

# ---- how often one address may guess ------------------------------------------------------------
# Signing in had no limit of any kind: no delay, no lockout, no counter. On a LAN that is a password
# guesser running as fast as the server will answer, and `/api/server` publishes the usernames to
# start from when the login screen shows faces. PBKDF2 at 240,000 iterations made each guess cost real
# CPU, which slowed an attacker down and was simultaneously the problem: an unauthenticated stranger
# could make the server do that work on demand.
#
# One counter per address, in memory, which is all a single-process server needs (see __main__.py:
# LENTA is one asyncio loop, not a pool of workers). It counts only **failures**, so the household
# never meets it; it is dropped on a correct password; and the table is pruned on every call so that
# being hammered from forged addresses cannot grow it without bound.
LOGIN_FAILURES = 8                  # consecutive wrong guesses from one address before it waits
LOGIN_WINDOW = 300                  # within this many seconds
LOGIN_TABLE_MAX = 2048              # addresses remembered at once; the oldest go first
_tries: dict[str, list[float]] = {}
_tries_lock = threading.Lock()


def _who(request: Request) -> str:
    return request.client.host if request.client else "?"


def _too_many(request: Request) -> int:
    """Seconds this address must wait, or 0. Prunes as it goes."""
    cutoff = time.time() - LOGIN_WINDOW
    with _tries_lock:
        for addr in [a for a, t in _tries.items() if not t or t[-1] < cutoff]:
            del _tries[addr]
        if len(_tries) > LOGIN_TABLE_MAX:
            for addr in sorted(_tries, key=lambda a: _tries[a][-1])[:len(_tries) - LOGIN_TABLE_MAX]:
                del _tries[addr]
        recent = [t for t in _tries.get(_who(request), []) if t >= cutoff]
        if len(recent) < LOGIN_FAILURES:
            return 0
        return max(1, int(recent[0] + LOGIN_WINDOW - time.time()))


def _wrong_guess(request: Request) -> None:
    cutoff = time.time() - LOGIN_WINDOW
    with _tries_lock:
        kept = [t for t in _tries.get(_who(request), []) if t >= cutoff]
        kept.append(time.time())
        _tries[_who(request)] = kept[-LOGIN_FAILURES:]


def _right_guess(request: Request) -> None:
    with _tries_lock:
        _tries.pop(_who(request), None)


class Credentials(BaseModel):
    username: str
    password: str


class SetupBody(Credentials):
    server_name: str | None = None


class PasswordBody(BaseModel):
    current: str
    new: str


def validate_new_user(username: str, password: str) -> str:
    username = username.strip()
    if not re.fullmatch(r"[\w .@-]{2,32}", username):
        raise HTTPException(400, "Usernames are 2–32 letters, numbers, spaces, dots, dashes or underscores")
    if len(password) < 6:
        raise HTTPException(400, "Passwords need at least 6 characters")
    return username


def _set_cookie(response: Response, request: Request, user_id: int) -> None:
    response.set_cookie(auth.cookie_name(request), auth.issue_token(user_id), max_age=auth.TOKEN_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")


def _theme(name: str) -> str:
    return "netflix" if name == "redline" else name            # Redline was renamed to Netflix


@router.get("/server")
def server_info(request: Request, response: Response):
    # The Samsung TV app checks this address from its own page, which is why the header is here. But it
    # also means **any website the household visits can read this answer** off the LAN, so the user
    # list below is given only to a same-origin caller: a sign-in screen showing faces is LENTA's own
    # page asking, never a stranger's. (`*` and credentials are mutually exclusive, so `signed_in`
    # already reads false cross-origin; this is about the names.)
    cross_site = bool(request.headers.get("origin")) and \
        request.headers.get("origin") != f"{request.url.scheme}://{request.headers.get('host', '')}"
    response.headers["Access-Control-Allow-Origin"] = "*"
    setup_required = auth.user_count() == 0
    info = {"name": get_setting("server_name"), "version": VERSION, "setup_required": setup_required,
            "signed_in": auth.user_from_request(request) is not None,
            "theme": _theme(get_setting("default_theme") or "projector")}
    if not setup_required and not cross_site and get_setting("show_users_on_login") == "1":
        with db() as con:
            info["users"] = [{"username": r["username"], "color": r["color"]}
                             for r in con.execute("SELECT username, color FROM users ORDER BY id")]
    return info


@router.post("/setup")
def setup(body: SetupBody, request: Request, response: Response):
    if auth.user_count() > 0:
        raise HTTPException(409, "This server is already set up. Sign in instead.")
    username = validate_new_user(body.username, body.password)
    if body.server_name and body.server_name.strip():
        set_setting("server_name", body.server_name.strip()[:40])
    with db() as con:
        cur = con.execute("INSERT INTO users(username, password_hash, is_admin, all_libraries, color, created_at) "
                          "VALUES(?, ?, 1, 1, ?, ?)",
                          (username, auth.hash_password(body.password), auth.AVATAR_COLORS[0], now()))
    _set_cookie(response, request, cur.lastrowid)
    return {"ok": True}


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response):
    wait = _too_many(request)
    if wait:
        # 429 before the hash is computed, so a guesser cannot spend the server's CPU either.
        raise HTTPException(429, f"Too many attempts. Try again in {wait // 60 + 1} minute"
                                 f"{'s' if wait // 60 else ''}.",
                            headers={"Retry-After": str(wait)})
    with db() as con:
        row = con.execute("SELECT id, password_hash FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        _wrong_guess(request)
        raise HTTPException(401, "That username and password don't match")
    _right_guess(request)
    _set_cookie(response, request, row["id"])
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    # Deleting the cookie is what the browser needs; withdrawing the token is what "signed out" means.
    # Until 1.19.0 only the first happened, so a token captured from a URL or a shared machine kept
    # working for the rest of its thirty days after the person had, as far as they knew, signed out.
    # It signs this account out everywhere, which is the honest reading of the button on a server
    # where the alternative is a token nothing can take back.
    user = auth.user_from_request(request)
    if user:
        auth.revoke_tokens(user["id"])
    response.delete_cookie(auth.cookie_name(request), path="/", secure=request.url.scheme == "https")
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(auth.current_user)):
    from .api_prefs import features, get_prefs
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"]),
            "color": user["color"], "server_name": get_setting("server_name"),
            "prefs": get_prefs(user["id"]), "features": features()}


@router.post("/auth/password")
def change_password(body: PasswordBody, request: Request, response: Response,
                    user: dict = Depends(auth.current_user)):
    with db() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not auth.verify_password(body.current, row["password_hash"]):
            raise HTTPException(400, "Your current password is not right")
        validate_new_user(user["username"], body.new)
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(body.new), user["id"]))
    # Changing a password is usually a response to thinking it has leaked, so every token already
    # issued for this account stops working — and this browser is then given a fresh one, so the person
    # changing it is not signed out of the page they are standing on.
    auth.revoke_tokens(user["id"])
    _set_cookie(response, request, user["id"])
    return {"ok": True}
