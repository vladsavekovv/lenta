"""Artwork: references, TMDB download cache, local art store and resizing.

An image is stored in the database as a reference string:
    tmdb:/kqjL17yufvn9OVLyXYpvtyrFfak.jpg   fetched lazily from TMDB, cached forever
    local:3f2a9c0d1e2b4a5c.jpg               a file we own in IMAGES_DIR
    web:9b1c…e2.jpg                          a picture elsewhere on the web (Fanart.tv, OMDb, AniList …);
                                             its address is kept in web_images, fetched lazily, cached forever
"""
import hashlib
import io
import os
import shutil
import threading
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from .config import CACHE_DIR, IMAGES_DIR
from . import logs

log = logs.get("images")
WIDTHS = [92, 185, 342, 500, 780, 1280, 1920]
TMDB_IMG = "https://image.tmdb.org/t/p/"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def snap_width(w: int | None) -> int | None:
    if not w:
        return None
    for size in WIDTHS:
        if w <= size:
            return size
    return WIDTHS[-1]


def url(ref: str | None, w: int | None = None) -> str | None:
    if not ref or ":" not in ref:
        return None
    kind, name = ref.split(":", 1)
    name = name.lstrip("/")
    if kind not in ("tmdb", "local", "web") or not name:
        return None
    return f"/api/img/{kind}/{name}" + (f"?w={snap_width(w)}" if w else "")


def save_local(data: bytes, ext: str = ".jpg") -> str:
    """Store image bytes we own; returns a local: reference. Content-addressed, so immutable."""
    name = hashlib.sha1(data).hexdigest()[:20] + ext.lower()
    target = IMAGES_DIR / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return f"local:{name}"


def save_local_file(path: str | Path) -> str | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return save_local(data, Path(path).suffix or ".jpg")


def _download_tmdb(name: str) -> Path | None:
    """Fetch a TMDB image once (w1280 if available, else original) into the cache."""
    target = CACHE_DIR / "tmdb" / name
    if target.exists():
        return target
    with _lock_for(name):
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        for bucket in ("w1280", "original"):
            try:
                r = httpx.get(f"{TMDB_IMG}{bucket}/{name}", timeout=30, follow_redirects=True)
            except httpx.HTTPError as exc:
                log.warning("TMDB image %s failed: %s", name, exc)
                return None
            if r.status_code == 200 and r.content:
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(target)
                return target
        log.warning("TMDB image %s not found", name)
        return None


def web_ref(address: str) -> str:
    """A web: reference for a picture on another site (downloaded the first time it is shown)."""
    from .db import db
    ext = os.path.splitext(address.split("?")[0])[1].lower()
    ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
    name = hashlib.sha1(address.encode()).hexdigest()[:24] + ext
    with db() as con:
        con.execute("INSERT OR IGNORE INTO web_images(name, url) VALUES(?, ?)", (name, address))
    return f"web:{name}"


def _download_web(name: str) -> Path | None:
    target = CACHE_DIR / "web" / name
    if target.exists():
        return target
    from .db import db
    with db() as con:
        row = con.execute("SELECT url FROM web_images WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    with _lock_for("web/" + name):
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "LENTA media server"}) as c:
                r = c.get(row["url"])
        except httpx.HTTPError as exc:
            log.warning("Image %s failed: %s", row["url"], exc)
            return None
        if r.status_code != 200 or not r.content or not r.headers.get("content-type", "image").startswith("image"):
            log.warning("Image %s: HTTP %s", row["url"], r.status_code)
            return None
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(target)
        return target


def _resized(src: Path, kind: str, name: str, width: int) -> Path:
    target = CACHE_DIR / "resized" / kind / str(width) / name
    if target.exists():
        return target
    with _lock_for(f"{kind}/{width}/{name}"):
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.width <= width:
                    shutil.copyfile(src, target)
                    return target
                height = round(im.height * width / im.width)
                im = im.resize((width, height), Image.LANCZOS)
                tmp = target.with_suffix(target.suffix + ".tmp")
                if name.lower().endswith(".png"):
                    im.save(tmp, "PNG", optimize=True)
                elif name.lower().endswith(".webp"):
                    im.save(tmp, "WEBP", quality=85)
                else:
                    im.convert("RGB").save(tmp, "JPEG", quality=85, optimize=True, progressive=True)
                tmp.replace(target)
        except Exception as exc:  # corrupt image: serve the original
            log.warning("Could not resize %s: %s", name, exc)
            return src
    return target


def resolve(kind: str, name: str, width: int | None) -> Path | None:
    """Filesystem path for an image request, downloading/resizing as needed."""
    if "/" in name or ".." in name:
        return None
    if kind == "tmdb":
        src = _download_tmdb(name)
    elif kind == "local":
        src = IMAGES_DIR / name
        src = src if src.exists() else None
    elif kind == "web":
        src = _download_web(name)
    else:
        return None
    if not src:
        return None
    w = snap_width(width)
    return _resized(src, kind, name, w) if w else src


def photo_thumbnail(path: str, width: int, item_id: int) -> Path | None:
    """Thumbnail for a photo library image (cached by item + size)."""
    w = snap_width(width) or 500
    target = CACHE_DIR / "photos" / str(w) / f"{item_id}.jpg"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((w, w * 4), Image.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
        target.write_bytes(buf.getvalue())
        return target
    except Exception as exc:
        log.warning("Photo thumbnail failed for %s: %s", path, exc)
        return None
