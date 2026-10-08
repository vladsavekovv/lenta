"""Accounts, password hashing, JWT session cookies and library permissions."""
import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import Depends, HTTPException, Request

from .db import db, get_setting, set_setting

COOKIE = "lenta_token"
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


def issue_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "iat": int(time.time()), "exp": int(time.time()) + TOKEN_DAYS * 86400}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    if request.cookies.get(COOKIE):
        return request.cookies[COOKIE]
    # Lets external players (VLC, mpv) open a stream URL directly.
    return request.query_params.get("token")


def user_from_request(request: Request) -> dict | None:
    token = _token_from_request(request)
    if not token:
        return None
    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    with db() as con:
        row = con.execute("SELECT id, username, is_admin, all_libraries, color FROM users WHERE id = ?",
                          (int(payload["sub"]),)).fetchone()
    return dict(row) if row else None


def current_user(request: Request) -> dict:
    user = user_from_request(request)
    if not user:
        raise HTTPException(401, "Sign in to continue")
    return user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Only administrators can do this")
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
