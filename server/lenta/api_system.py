"""What this LENTA is: the facts behind Settings › About.

Not to be confused with about.py, which fills the About panel of a film or a show (TMDB,
Wikipedia, ratings). This module describes the installation itself: version, licence, the
machine it runs on, what the library holds.

Everyone signed in may read it. The counts are the libraries that user is allowed to see,
so a restricted account does not learn the size of a library it cannot open. Paths, free
space, the service account and the listening address are administrators only — and nothing
here ever returns a key, a token or a password.
"""
import platform
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from . import auth, transcode
from .api_admin import STARTED, ffmpeg_version
from .config import APP_NAME, DATA_DIR, DB_PATH, HOST, PORT, TRANSCODE_DIR, VERSION
from .db import db, get_setting

router = APIRouter(prefix="/api")

LICENCE = "The Unlicense — public domain"
PROJECT_URL = "https://github.com/vladsavekovv/lenta"

_os_name = None


def os_name() -> str:
    """The distribution's own name if it gives one, otherwise whatever Python knows."""
    global _os_name
    if _os_name is None:
        name = ""
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
        _os_name = name or f"{platform.system()} {platform.release()}".strip()
    return _os_name


def _version(module: str) -> str:
    try:
        from importlib.metadata import version
        return version(module)
    except Exception:                       # not installed, or no metadata (running from source)
        return ""


def _service_user() -> str:
    try:
        import os
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return ""


def _free(path: Path) -> int | None:
    try:
        import shutil
        return shutil.disk_usage(path).free
    except OSError:
        return None


@router.get("/system")
def system_info(user: dict = Depends(auth.current_user)):
    where, params = auth.library_filter(user, "library_id")
    allowed = auth.allowed_libraries(user)
    with db() as con:
        counts = {r["kind"]: r["n"] for r in
                  con.execute(f"SELECT kind, COUNT(*) AS n FROM items WHERE {where} GROUP BY kind", params)}
        size = con.execute(f"SELECT COALESCE(SUM(size), 0) FROM files WHERE {where}", params).fetchone()[0]
        libs = [dict(r) for r in con.execute("SELECT id, kind FROM libraries")]
        mine = [lb for lb in libs if allowed is None or lb["id"] in allowed]
        if any(lb["kind"] == "livetv" for lb in mine):
            counts["channel"] = con.execute("SELECT COUNT(*) FROM tv_channels WHERE hidden = 0").fetchone()[0]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] if user["is_admin"] else None

    info = {
        "app": APP_NAME,
        "version": VERSION,
        "licence": LICENCE,
        "project": PROJECT_URL,
        "server_name": get_setting("server_name") or "",
        "uptime": time.time() - STARTED,
        "python": platform.python_version(),
        "fastapi": _version("fastapi"),
        "uvicorn": _version("uvicorn"),
        "sqlite": sqlite3.sqlite_version,
        "os": os_name(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "ffmpeg": ffmpeg_version(),
        "hardware": {"accel": transcode.hw.get("accel", "none"), "encoder": transcode.hw.get("encoder", "")},
        "counts": counts,
        "libraries": len(mine),
        "media_size": size,
    }
    if user["is_admin"]:
        info["admin"] = {
            "users": users,
            "data_dir": str(DATA_DIR),
            "data_free": _free(DATA_DIR),
            "transcode_dir": str(TRANSCODE_DIR),
            "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else None,
            "listening": f"{HOST}:{PORT}",
            "service_user": _service_user(),
        }
    return info
