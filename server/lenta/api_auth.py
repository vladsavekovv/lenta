"""Server info, first-run setup, sign in / out."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth
from .config import VERSION
from .db import db, get_setting, now, set_setting

router = APIRouter(prefix="/api")


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
    response.set_cookie(auth.COOKIE, auth.issue_token(user_id), max_age=auth.TOKEN_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https", path="/")


@router.get("/server")
def server_info(request: Request, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"      # the Samsung TV app checks the address from its own page
    setup_required = auth.user_count() == 0
    info = {"name": get_setting("server_name"), "version": VERSION, "setup_required": setup_required,
            "signed_in": auth.user_from_request(request) is not None,
            "theme": get_setting("default_theme") or "projector"}
    if not setup_required and get_setting("show_users_on_login") == "1":
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
    with db() as con:
        row = con.execute("SELECT id, password_hash FROM users WHERE username = ?", (body.username.strip(),)).fetchone()
    if not row or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "That username and password don't match")
    _set_cookie(response, request, row["id"])
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(auth.current_user)):
    from .api_prefs import features, get_prefs
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"]),
            "color": user["color"], "server_name": get_setting("server_name"),
            "prefs": get_prefs(user["id"]), "features": features()}


@router.post("/auth/password")
def change_password(body: PasswordBody, user: dict = Depends(auth.current_user)):
    with db() as con:
        row = con.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not auth.verify_password(body.current, row["password_hash"]):
            raise HTTPException(400, "Your current password is not right")
        validate_new_user(user["username"], body.new)
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (auth.hash_password(body.new), user["id"]))
    return {"ok": True}
