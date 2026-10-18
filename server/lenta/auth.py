"""Accounts, password hashing, JWT session cookies and library permissions."""
import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, Request

from .db import db, get_setting, set_setting

COOKIE = "lenta_token"
COOKIE_HTTPS = "lenta_token_s"     # its own name on HTTPS: a Secure cookie must not block signing in over HTTP


def cookie_name(request: Request) -> str:
    return COOKIE_HTTPS if request.url.scheme == "https" else COOKIE
TOKEN_DAYS = 30
_ITERATIONS = 240_000
AVATAR_COLORS = ["#F2B33D", "#E86A5A", "#6FB7A8", "#8C7BE0", "#D98AC0", "#7FA6E8", "#C9C25B"]


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def _secret() -> str:
    secret = get_setting("jwt_secret")
    if not secret:
        secret = secrets.token_hex(32)
        set_setting("jwt_secret", secret)
    return secret


def _epoch(user_id: int) -> int:
    with db() as con:
        row = con.execute("SELECT token_epoch FROM users WHERE id = ?", (user_id,)).fetchone()
    return int(row["token_epoch"]) if row else 0


def revoke_tokens(user_id: int) -> None:
    """Withdraw every token this account has already been issued.

    There is no sessions table — a token is a signed statement, not a row — so until 1.19.0 **nothing
    could take one back**. Signing out deleted the browser's cookie and left the token itself valid for
    the rest of its thirty days, usable through `Authorization: Bearer` or `?token=`; changing the
    password because you thought it had leaked achieved exactly nothing. Each token now carries the
    epoch its account was on when it was minted, and bumping the epoch refuses the lot in one write.
    """
    with db() as con:
        con.execute("UPDATE users SET token_epoch = token_epoch + 1 WHERE id = ?", (user_id,))


def issue_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "iat": int(time.time()), "ep": _epoch(user_id),
               "exp": int(time.time()) + TOKEN_DAYS * 86400}
    return jwt.encode(payload, _secret(), algorithm="HS256")


# A token in the query string is for the one job that cannot carry a header: handing a URL to a
# television's own decoder, to hls.js, or to VLC. It is **not** a way to authenticate the API. Before
# 1.19.0 it was accepted everywhere, so `GET /api/admin/users?token=…` worked — a full-privilege
# credential travelling in URLs, which end up in browser history, bookmarks, a `Referer` header and
# any access log somebody later turns up to INFO. These are the paths that need it, and GET only.
QUERY_TOKEN_PATHS = ("/api/stream/", "/api/hls/", "/api/livetv/live/", "/api/subs/", "/api/photos/",
                     "/api/files/", "/api/img/")


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    token = request.cookies.get(cookie_name(request))
    if token:
        return token
    if request.method in ("GET", "HEAD") and request.url.path.startswith(QUERY_TOKEN_PATHS):
        return request.query_params.get("token")
    return None


def user_from_request(request: Request) -> dict | None:
    token = _token_from_request(request)
    if not token:
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    try:
        uid = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None                     # a token we signed but cannot read is no token at all
    with db() as con:
        row = con.execute("SELECT id, username, is_admin, all_libraries, color, token_epoch "
                          "FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        return None                     # a deleted account's token stops working at once, as it should
    if int(payload.get("ep", 0)) != int(row["token_epoch"]):
        return None                     # signed out, or the password changed, since this was issued
    return dict(row)


def current_user(request: Request) -> dict:
    user = user_from_request(request)
    if not user:
        raise HTTPException(401, "Sign in to continue")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Only administrators can do this")
    return user


def live_tv_viewer(user: dict = Depends(current_user)) -> dict:
    """Live television is a library like any other, and until 1.19.0 it was the one nobody checked.

    `/api/livetv/*` asked only for a signed-in user, so a viewer granted one film library could list
    every channel, read the whole guide, and **seize a tuner input** — taking it from whoever was
    watching. The product never meant that: `libraries.kind` has a `livetv` value, `user_libraries`
    maps people to it, the menu filters on it, and `api_library._home_rows` goes out of its way to drop
    the channels row for a viewer who has none. Every one of those is a decision that the API then did
    not keep. This is that decision, in the one place the whole router inherits it from.
    """
    allowed = allowed_libraries(user)
    with db() as con:
        if allowed is None:
            row = con.execute("SELECT 1 FROM libraries WHERE kind = 'livetv' LIMIT 1").fetchone()
        elif not allowed:
            row = None
        else:
            marks = ",".join("?" * len(allowed))
            row = con.execute(f"SELECT 1 FROM libraries WHERE kind = 'livetv' AND id IN ({marks}) LIMIT 1",
                              allowed).fetchone()
    if not row:
        # 404, not 403, for the same reason check_item_access says 404: a refusal that distinguishes
        # "not yours" from "not there" tells someone what exists.
        raise HTTPException(404, "There is no live television here")
    return user


def allowed_libraries(user: dict) -> list[int] | None:
    """None means every library."""
    if user["is_admin"] or user["all_libraries"]:
        return None
    with db() as con:
        return [r["library_id"] for r in
                con.execute("SELECT library_id FROM user_libraries WHERE user_id = ?", (user["id"],))]


def library_filter(user: dict, column: str = "library_id") -> tuple[str, dict]:
    """SQL fragment (named parameters) restricting a query to the user's libraries."""
    allowed = allowed_libraries(user)
    if allowed is None:
        return "1=1", {}
    if not allowed:
        return "0=1", {}
    params = {f"lib{n}": lid for n, lid in enumerate(allowed)}
    return f"{column} IN ({','.join(':' + k for k in params)})", params


def check_item_access(user: dict, library_id: int) -> None:
    allowed = allowed_libraries(user)
    if allowed is not None and library_id not in allowed:
        raise HTTPException(404, "Not found")


def user_count() -> int:
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def pick_color() -> str:
    with db() as con:
        n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return AVATAR_COLORS[n % len(AVATAR_COLORS)]
