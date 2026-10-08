"""Seek-bar preview thumbnails ("trickplay", like Plex and Jellyfin).

For every video file LENTA makes small pictures, one every INTERVAL seconds, packed 10 × 10 into JPEG
sheets in <data folder>/trickplay/<file id>/ (0.jpg, 1.jpg … and index.json). The player loads the sheet it
needs and shows the right picture above the seek bar while you hover or drag.

Made in the background, one file at a time at the lowest CPU and disk priority, newest titles first.
FFmpeg only decodes key frames (-skip_frame nokey), so a two-hour film takes a minute or two even on a CPU;
the result is a few MB per film. A file someone opens in the player jumps to the front of the queue.
"""
import json
import os
import shutil
import subprocess
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from . import auth, logs
from .config import DATA_DIR, FFMPEG
from .db import db, get_setting

log = logs.get("trickplay")
router = APIRouter(prefix="/api")
ROOT = DATA_DIR / "trickplay"
WIDTH = 320                  # pixels per picture (shown at about 240)
COLS = ROWS = 10             # 100 pictures per sheet
VERSION = 1

state = {"running": False, "done": 0, "total": 0, "current": None, "made": 0, "failed": 0}
_lock = threading.Lock()
_wake = threading.Event()
_priority: list[int] = []
_started = False


def enabled() -> bool:
    return (get_setting("trickplay") or "1") != "0"


def interval() -> int:
    try:
        return max(2, min(60, int(get_setting("trickplay_interval") or 10)))
    except ValueError:
        return 10


def _dir(file_id: int):
    return ROOT / str(int(file_id))


def _sig(f: dict) -> str:
    return f"{f.get('size')}:{int(f.get('mtime') or 0)}:{interval()}:{VERSION}"


def index(file_id: int) -> dict | None:
    try:
        return json.loads((_dir(file_id) / "index.json").read_text())
    except (OSError, ValueError):
        return None


def _wanted() -> list[dict]:
    with db() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT f.id, f.path, f.size, f.mtime, f.duration, f.width, f.height, f.hdr FROM files f "
            "JOIN items i ON i.id = f.item_id WHERE i.kind IN ('movie','episode') AND f.duration > 30 "
            "ORDER BY i.added_at DESC, f.id DESC")]
    out = []
    for f in rows:
        idx = index(f["id"])
        if not idx or idx.get("sig") != _sig(f):
            out.append(f)
    return out


def _make(f: dict) -> bool:
    """Write the sheets for one file. True when it worked."""
    step = interval()
    out = _dir(f["id"])
    tmp = out.with_name(out.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    scale = f"scale={WIDTH}:-2"
    if f.get("hdr"):            # tone-map HDR so the pictures don't look grey (on the small picture: far less work)
        scale = (f"{scale},zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                 "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    vf = f"fps=1/{step},{scale},tile={COLS}x{ROWS}"
    from .transcode import hw
    # an NVIDIA card decodes the video (4K HEVC is heavy work for the processor); FFmpeg uses the CPU if it can't
    accel = ["-hwaccel", "cuda"] if hw.get("accel") == "nvenc" else []
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "2", *accel,
           "-skip_frame", "nokey", "-i", f["path"], "-map", "0:v:0", "-an", "-sn", "-dn",
           "-vf", vf, "-fps_mode", "vfr", "-q:v", "5", "-f", "image2", str(tmp / "%d.jpg")]
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []
    ionice = ["ionice", "-c", "3"] if shutil.which("ionice") else []
    try:
        r = subprocess.run(ionice + nice + cmd, capture_output=True, timeout=3 * 3600)
        if r.returncode != 0 and accel:                # the card didn't manage: the processor does it
            cmd = [c for c in cmd if c not in accel]
            r = subprocess.run(ionice + nice + cmd, capture_output=True, timeout=3 * 3600)
        if r.returncode != 0 and f.get("hdr"):        # FFmpeg without zscale: plain pictures will do
            cmd[cmd.index("-vf") + 1] = f"fps=1/{step},scale={WIDTH}:-2,tile={COLS}x{ROWS}"
            r = subprocess.run(ionice + nice + cmd, capture_output=True, timeout=3 * 3600)
    except (subprocess.SubprocessError, OSError) as exc:
        log.info("Preview thumbnails for %s failed: %s", f["path"], exc)
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    sheets = sorted(tmp.glob("*.jpg"), key=lambda p: int(p.stem))
    if r.returncode != 0 or not sheets:
        log.info("Preview thumbnails for %s failed: %s", f["path"], r.stderr.decode(errors="replace")[-300:])
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    for n, p in enumerate(sheets):                  # FFmpeg numbers from 1; the player counts from 0
        p.rename(tmp / f"s{n}.jpg")
    try:
        from PIL import Image
        with Image.open(tmp / "s0.jpg") as im:
            sw, sh = im.size
    except Exception:
        sw, sh = WIDTH * COLS, round(WIDTH * 9 / 16) * ROWS
    count = min(max(1, -(-int(f.get("duration") or 0) // step)), len(sheets) * COLS * ROWS)   # ceil(duration / step)
    (tmp / "index.json").write_text(json.dumps({
        "interval": step, "cols": COLS, "rows": ROWS, "width": sw // COLS, "height": sh // ROWS,
        "sheets": len(sheets), "count": count, "sig": _sig(f), "made": time.time()}))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return True


def _clean() -> None:
    """Remove pictures of files that are gone."""
    if not ROOT.exists():
        return
    with db() as con:
        ids = {str(r[0]) for r in con.execute("SELECT id FROM files")}
    for d in ROOT.iterdir():
        name = d.name.removesuffix(".part")
        if d.is_dir() and (name not in ids or d.name.endswith(".part")):
            shutil.rmtree(d, ignore_errors=True)


def _viewer_busy() -> bool:
    """While LENTA converts a film for someone, the background work waits (checked every 20 s)."""
    from .transcode import busy
    if busy():
        state["paused"] = True
        _wake.wait(timeout=20)
        _wake.clear()
        return True
    state["paused"] = False
    return False


def _loop() -> None:
    while True:
        _wake.wait(timeout=6 * 3600)
        _wake.clear()
        if not enabled():
            continue
        try:
            _clean()
            todo = _wanted()
            state.update(running=True, done=0, total=len(todo), made=0, failed=0)
            if todo:
                log.info("Making preview thumbnails for %d videos", len(todo))
            pending = {f["id"]: f for f in todo}
            while pending and enabled():
                with _lock:
                    first = next((i for i in _priority if i in pending), None)
                    _priority[:] = [i for i in _priority if i in pending and i != first]
                if first is None and _viewer_busy():
                    continue                                   # someone is watching: wait (asked-for files still go)
                f = pending.pop(first if first is not None else next(iter(pending)))
                state["current"] = os.path.basename(f["path"])
                if _make(f):
                    state["made"] += 1
                else:
                    state["failed"] += 1
                state["done"] += 1
        except Exception as exc:
            log.warning("Preview thumbnails stopped: %s", exc)
        finally:
            state.update(running=False, current=None)


def start() -> None:
    """Start the background worker (main.py, at server start) and run it now."""
    global _started
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_loop, name="trickplay", daemon=True).start()
    _wake.set()


def request(file_id: int | None = None) -> None:
    """Run again (after a scan), or move one file to the front of the queue (opened in the player)."""
    if file_id is not None:
        with _lock:
            if file_id in _priority:
                _priority.remove(file_id)
            _priority.insert(0, file_id)
    if not state["running"]:
        _wake.set()


def counts() -> dict:
    with db() as con:
        total = con.execute("SELECT COUNT(*) FROM files f JOIN items i ON i.id = f.item_id "
                            "WHERE i.kind IN ('movie','episode') AND f.duration > 30").fetchone()[0]
    have = sum(1 for d in ROOT.iterdir() if d.is_dir() and (d / "index.json").exists()) if ROOT.exists() else 0
    return {"enabled": enabled(), "videos": total, "ready": min(have, total), **state}


# ---- API ----------------------------------------------------------------------------------------

def _file_for(file_id: int, user: dict) -> dict:
    with db() as con:
        row = con.execute("SELECT id, library_id, path, size, mtime FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Not found")
    auth.check_item_access(user, row["library_id"])
    return dict(row)


@router.get("/files/{file_id}/trickplay")
def trickplay_index(file_id: int, user: dict = Depends(auth.current_user)):
    f = _file_for(file_id, user)
    idx = index(file_id) if enabled() else None
    if not idx or idx.get("sig") != _sig(f):
        if enabled():
            request(file_id)                  # someone wants it now: make this one next
        return {"ready": False}
    return {"ready": True, **{k: idx[k] for k in ("interval", "cols", "rows", "width", "height", "sheets", "count")}}


@router.get("/files/{file_id}/trickplay/{sheet}.jpg")
def trickplay_sheet(file_id: int, sheet: int, user: dict = Depends(auth.current_user)):
    _file_for(file_id, user)
    p = _dir(file_id) / f"s{int(sheet)}.jpg"
    if not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})
