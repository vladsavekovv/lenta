"""Playback: the direct-play / remux / transcode decision, streams, HLS, subtitles, progress."""
import json
import math
import mimetypes
import os
import re
import subprocess
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import auth, subtitles, transcode
from .api_library import get_item, next_episode_id
from .config import DEV_VP9, FFMPEG, FFPROBE
from .db import db, jloads, now
from .serialize import EPISODE_JOIN, file_info

router = APIRouter(prefix="/api")

# Who is watching what right now (fed by progress reports).
now_playing: dict[str, dict] = {}
_np_lock = threading.Lock()

BROWSER_CONTAINERS = {"mp4": "mp4", "mov": "mp4", "m4v": "mp4", "webm": "webm"}


class Caps(BaseModel):
    video: list[str] = ["h264"]
    audio: list[str] = ["aac", "mp3"]
    containers: list[str] = ["mp4"]
    hdr: bool = False


class PlaybackBody(BaseModel):
    item_id: int
    file_id: int | None = None
    quality: str = "original"
    start: float = 0
    audio_index: int | None = None
    subtitle_key: str | None = None
    caps: Caps = Caps()
    replace_session: str | None = None


def _file_row(file_id: int) -> dict:
    with db() as con:
        row = con.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "That file is no longer available")
    f = dict(row)
    f["audio"] = jloads(f["audio"])
    f["subtitles"] = jloads(f["subtitles"]) + subtitles.downloaded(f["id"])
    return f


def _stream_start(path: str, t: float) -> tuple[float, float]:
    """(keyframe, origin): the last video keyframe at or before t, and the file's start_time.

    An HLS stream can only begin on a keyframe, so the stream (copied video in a remux, copied audio
    in a transcode) really starts there, not at t. The keyframe is the player's first estimate of
    where the stream begins; the player then reads the exact time from the stream itself (it carries
    the film's own timestamps, see transcode.build_command) and subtracts origin.
    ffprobe's read window uses absolute timestamps while -ss counts from start_time, so both are
    converted. Rounded up to the microsecond so the seek can't fall back to the previous keyframe.
    """
    origin = 0.0
    try:
        fmt = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=start_time", "-of", "json", path],
                             capture_output=True, timeout=20)
        origin = float(json.loads(fmt.stdout or b"{}").get("format", {}).get("start_time") or 0)
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    if t <= 0:
        return 0.0, origin
    target = origin + t
    try:
        for window in (15, 60, 300):
            out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                                  "-read_intervals", f"{max(target - window, 0):.6f}%{target + 0.5:.6f}",
                                  "-show_entries", "packet=pts_time,dts_time,flags", "-of", "json", path],
                                 capture_output=True, timeout=30)
            keys = []
            for pkt in json.loads(out.stdout or b"{}").get("packets", []):
                if "K" not in (pkt.get("flags") or ""):
                    continue
                try:
                    stamp = float(pkt.get("pts_time") or pkt.get("dts_time"))
                except (TypeError, ValueError):
                    continue
                if stamp <= target + 1e-6:
                    keys.append(stamp)
            if keys:
                return min(t, max(0.0, math.ceil((max(keys) - origin) * 1e6) / 1e6)), origin
            if target - window <= 0:
                break
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return t, origin


def decide(f: dict, body: PlaybackBody) -> tuple[str, str]:
    """Return (mode, reason)."""
    caps = body.caps
    audio = f["audio"]
    first_audio = audio[0]["index"] if audio else None
    chosen_audio = next((a for a in audio if a["index"] == body.audio_index), audio[0] if audio else None)
    burn = None
    if body.subtitle_key:
        sub = next((s for s in f["subtitles"] if s["key"] == body.subtitle_key), None)
        if sub and sub.get("image"):
            burn = sub
    container = BROWSER_CONTAINERS.get(f["container"] or "", f["container"])
    if (body.quality == "original" and not burn
            and container in caps.containers
            and f["video_codec"] in caps.video
            and (not f["hdr"] or caps.hdr)
            and (chosen_audio is None or (chosen_audio["index"] == first_audio and chosen_audio["codec"] in caps.audio))):
        return "direct", "Your browser plays this file as it is"
    if burn:
        return "transcode", "Picture subtitles have to be drawn into the video"
    target = transcode.QUALITIES.get(body.quality)
    h264_ok = (f["video_codec"] == "h264" and (f["pix_fmt"] or "yuv420p") in ("yuv420p", "yuvj420p")
               and not f["hdr"])
    fits = target is None or ((f["height"] or 0) <= target[0] and (f["bitrate"] or 0) <= target[1] * 1.3)
    if h264_ok and fits:
        if container not in caps.containers:
            return "remux", f"The {f['container'].upper()} container is repackaged; video is not re-encoded"
        return "remux", "The audio track is converted; video is not re-encoded"
    if f["video_codec"] not in caps.video:
        return "transcode", f"Your browser can't decode {str(f['video_codec']).upper()} video"
    if f["hdr"]:
        return "transcode", "HDR is tone-mapped to SDR for this screen"
    return "transcode", f"Converted to {body.quality}"


@router.post("/playback")
def start_playback(body: PlaybackBody, user: dict = Depends(auth.current_user)):
    item = get_item(body.item_id, user)
    if item["kind"] not in ("movie", "episode"):
        raise HTTPException(400, "Pick a movie or an episode to play")
    with db() as con:
        if body.file_id:
            frow = con.execute("SELECT id FROM files WHERE id = ? AND item_id = ?", (body.file_id, item["id"])).fetchone()
        else:
            frow = con.execute("SELECT id FROM files WHERE item_id = ? ORDER BY height DESC, size DESC LIMIT 1",
                               (item["id"],)).fetchone()
        if not frow:
            raise HTTPException(404, "No playable file for this title")
        meta = None
        if item["kind"] == "episode":
            meta = con.execute(f"{EPISODE_JOIN} WHERE e.id = ?", (item["id"],)).fetchone()
    f = _file_row(frow["id"])
    if not os.path.exists(f["path"]):
        raise HTTPException(410, "The file is missing from disk. Is the drive or network share mounted?")

    mode, reason = decide(f, body)
    if DEV_VP9 and mode == "remux":
        mode, reason = "transcode", "Development VP9 mode"
    if body.replace_session:
        transcode.stop_session(body.replace_session)
    display = item["title"]
    subtitle = None
    if meta:
        display = f"{meta['show_title']} · S{meta['parent_index']}:E{meta['index_number']} {item['title']}"
        subtitle = f"S{meta['parent_index']}:E{meta['index_number']} · {item['title']}"
    result = {
        "mode": mode, "reason": reason, "file": file_info({**f, "audio": json.dumps(f["audio"]),
                                                          "subtitles": json.dumps([s for s in f["subtitles"]
                                                                                   if not s.get("downloaded")])}),
        "duration": f["duration"], "item_id": item["id"],
        "title": meta["show_title"] if meta else item["title"], "subtitle": subtitle,
        "next_id": next_episode_id(item["id"]) if item["kind"] == "episode" else None,
        "audio_index": body.audio_index if body.audio_index is not None else (f["audio"][0]["index"] if f["audio"] else None),
        "quality": body.quality, "encoder": transcode.hw["encoder"] if mode == "transcode" else None,
    }
    if mode == "direct":
        result.update(url=f"/api/stream/file/{f['id']}", offset=0, session_id=None)
        return result

    wanted = max(0.0, min(body.start, (f["duration"] or 0) - 2)) if body.start else 0.0
    # Begin the stream on the keyframe at or before the wanted position (see _stream_start);
    # the player then skips the few seconds of lead-in, so playback still starts exactly where asked.
    start, origin = _stream_start(f["path"], wanted)
    burn = None
    if body.subtitle_key:
        sub = next((s for s in f["subtitles"] if s["key"] == body.subtitle_key), None)
        if sub and sub.get("image"):
            burn = sub["index"]
    session = transcode.start_session(user=user, item={**item, "display_title": display}, f=f, mode=mode,
                                      quality=body.quality, start=start, audio_index=body.audio_index,
                                      burn_sub_index=burn, origin=origin)
    result.update(url=f"/api/hls/{session.id}/index.m3u8", offset=start, lead=round(wanted - start, 3), origin=origin,
                  session_id=session.id,
                  burned_subtitle=body.subtitle_key if burn is not None else None)
    return result


# ---- direct streaming with HTTP range support ------------------------------------

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def _ranged_file(request: Request, path: str, media_type: str, download_name: str | None = None):
    size = os.path.getsize(path)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    header = request.headers.get("range")
    m = _RANGE.match(header or "")
    if not m or (not m.group(1) and not m.group(2)):
        return FileResponse(path, media_type=media_type, headers=headers)
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:  # suffix range: last N bytes
        start = max(size - int(m.group(2)), 0)
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
    return StreamingResponse(body(), status_code=206, media_type=media_type, headers=headers)


def _guess_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".mkv": "video/x-matroska", ".m4v": "video/mp4", ".webm": "video/webm", ".flac": "audio/flac",
            ".m4a": "audio/mp4", ".opus": "audio/ogg", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
            ".wav": "audio/wav"}.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


@router.get("/stream/file/{file_id}")
def stream_file(file_id: int, request: Request, download: int = 0, user: dict = Depends(auth.current_user)):
    f = _file_row(file_id)
    auth.check_item_access(user, f["library_id"])
    if not os.path.exists(f["path"]):
        raise HTTPException(410, "The file is missing from disk")
    return _ranged_file(request, f["path"], _guess_type(f["path"]),
                        os.path.basename(f["path"]) if download else None)


@router.get("/stream/track/{item_id}")
def stream_track(item_id: int, request: Request, transcode_audio: int = 0, user: dict = Depends(auth.current_user)):
    item = get_item(item_id, user)
    with db() as con:
        row = con.execute("SELECT * FROM files WHERE item_id = ?", (item["id"],)).fetchone()
    if not row or not os.path.exists(row["path"]):
        raise HTTPException(410, "The file is missing from disk")
    if not transcode_audio:
        return _ranged_file(request, row["path"], _guess_type(row["path"]))
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", row["path"], "-vn", "-map", "0:a:0",
           "-c:a", "libmp3lame", "-b:a", "256k", "-f", "mp3", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def body():
        try:
            while True:
                data = proc.stdout.read(64 * 1024)
                if not data:
                    break
                yield data
        finally:
            proc.kill()

    return StreamingResponse(body(), media_type="audio/mpeg")


# ---- HLS ------------------------------------------------------------------------

def _session_for(sid: str, user: dict) -> transcode.Session:
    session = transcode.sessions.get(sid)
    if not session or (session.user_id != user["id"] and not user["is_admin"]):
        raise HTTPException(404, "This stream has ended. Press play again.")
    return session


@router.get("/hls/{sid}/index.m3u8")
def hls_playlist(sid: str, user: dict = Depends(auth.current_user)):
    session = _session_for(sid, user)
    transcode.touch(session)
    path = session.directory / "index.m3u8"
    if not transcode.wait_for(session, path, timeout=45):
        detail = session.error or "The server could not start this stream in time"
        raise HTTPException(500, detail[-400:])
    return PlainTextResponse(path.read_text(), media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-cache"})


@router.get("/hls/{sid}/{segment}")
def hls_segment(sid: str, segment: str, user: dict = Depends(auth.current_user)):
    m = re.fullmatch(r"seg(\d{5})\.(ts|m4s)|init\.mp4", segment)
    if not m:
        raise HTTPException(404, "Unknown segment")
    session = _session_for(sid, user)
    transcode.touch(session, int(m.group(1)) if m.group(1) else None)
    path = session.directory / segment
    if not transcode.wait_for(session, path, timeout=40):
        raise HTTPException(404, "Segment not ready")
    media = "video/mp2t" if segment.endswith(".ts") else "video/mp4"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


@router.delete("/hls/{sid}")
def hls_stop(sid: str, user: dict = Depends(auth.current_user)):
    session = transcode.sessions.get(sid)
    if session and (session.user_id == user["id"] or user["is_admin"]):
        transcode.stop_session(sid)
    return {"ok": True}


# ---- subtitles ------------------------------------------------------------------

@router.get("/subs/{file_id}/{key}.vtt")
def subtitle_vtt(file_id: int, key: str, user: dict = Depends(auth.current_user)):
    f = _file_row(file_id)
    auth.check_item_access(user, f["library_id"])
    sub = next((s for s in f["subtitles"] if s["key"] == key), None)
    if not sub or not sub.get("text"):
        raise HTTPException(404, "Subtitle track not found")
    text = subtitles.to_vtt(f, sub)
    if text is None:
        raise HTTPException(500, "This subtitle track could not be converted")
    return PlainTextResponse(text, media_type="text/vtt; charset=utf-8")


# ---- progress / now playing -------------------------------------------------------

class ProgressBody(BaseModel):
    item_id: int
    position: float
    duration: float | None = None
    state: str = "playing"           # playing | paused | stopped
    play_id: str | None = None
    mode: str | None = None
    quality: str | None = None


@router.post("/progress")
def report_progress(body: ProgressBody, user: dict = Depends(auth.current_user)):
    item = get_item(body.item_id, user)
    key = body.play_id or f"{user['id']}-{body.item_id}"
    with _np_lock:
        if body.state == "stopped":
            now_playing.pop(key, None)
        else:
            now_playing[key] = {"user": user["username"], "item_id": item["id"], "title": item["title"],
                                "kind": item["kind"], "position": body.position, "duration": body.duration,
                                "state": body.state, "mode": body.mode, "quality": body.quality,
                                "updated": time.time()}
        for k in [k for k, v in now_playing.items() if time.time() - v["updated"] > 60]:
            now_playing.pop(k, None)
    if item["kind"] not in ("movie", "episode") or not body.duration:
        return {"ok": True}
    # Finished at 92 %, or inside the last two minutes (end credits) of anything longer than 20 minutes.
    done = body.position / body.duration >= 0.92 or (body.duration > 1200 and body.duration - body.position < 120)
    with db() as con:
        prev = con.execute("SELECT completed FROM watch_state WHERE user_id = ? AND item_id = ?",
                           (user["id"], item["id"])).fetchone()
        if done:
            con.execute("INSERT INTO watch_state(user_id, item_id, position, duration, completed, play_count, updated_at) "
                        "VALUES(?, ?, 0, ?, 1, 1, ?) ON CONFLICT(user_id, item_id) DO UPDATE SET position = 0, "
                        "duration = excluded.duration, completed = 1, updated_at = excluded.updated_at, "
                        "play_count = play_count + (completed = 0)",
                        (user["id"], item["id"], body.duration, now()))
        elif body.position > 15 or prev:
            con.execute("INSERT INTO watch_state(user_id, item_id, position, duration, completed, updated_at) "
                        "VALUES(?, ?, ?, ?, 0, ?) ON CONFLICT(user_id, item_id) DO UPDATE SET "
                        "position = excluded.position, duration = excluded.duration, completed = 0, "
                        "updated_at = excluded.updated_at",
                        (user["id"], item["id"], body.position, body.duration, now()))
    return {"ok": True, "completed": done}


def current_now_playing() -> list[dict]:
    with _np_lock:
        return sorted(({"play_id": k, **v} for k, v in now_playing.items()
                       if time.time() - v["updated"] < 60), key=lambda v: v["user"])
