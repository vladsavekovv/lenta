"""Transcoding engine: hardware detection and FFmpeg HLS sessions.

Playback modes (decided in api_playback):
    direct     the browser plays the original file (HTTP range requests)
    remux      video copied as-is, audio converted, repackaged as HLS (cheap)
    transcode  video re-encoded to H.264 (NVENC / QSV / VAAPI / x264), HLS

Each HLS session is one FFmpeg process writing 4-second segments into its own
folder. Sessions start at an offset; seeking outside what has been produced
starts a new session at the new position. FFmpeg is paused when it gets far
ahead of the viewer and killed when the viewer goes away.
"""
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import logs
from .config import DEV_VP9, FFMPEG, TRANSCODE_DIR
from .db import get_setting

log = logs.get("transcode")

SEGMENT_SECONDS = 4
AHEAD_LIMIT = 45            # segments FFmpeg may run ahead of the viewer before being paused
IDLE_TIMEOUT = 90           # seconds without a request before a session is killed
QUALITIES = {
    "original": None,
    "1080p": (1080, 10_000_000),
    "720p": (720, 4_000_000),
    "480p": (480, 1_500_000),
}

hw = {"accel": "none", "encoder": "libx264", "available": [], "tonemap": False, "scale_cuda": False,
      "tonemap_cuda": False, "checked_at": None}
_hw_lock = threading.Lock()


# ---- hardware detection --------------------------------------------------------

def _test(args: list[str]) -> bool:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", *args]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _filter_names() -> set[str]:
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, timeout=20).stdout.decode()
    except (subprocess.SubprocessError, OSError):
        return set()
    return {parts[1] for parts in (line.split() for line in out.splitlines()) if len(parts) >= 3}


# What NVDEC can decode straight into GPU memory (codec -> pixel formats). Anything else is decoded
# on the CPU, because frames would not stay on the GPU anyway.
NVDEC_FORMATS = {
    "h264": {"yuv420p", "yuvj420p"},
    "hevc": {"yuv420p", "yuvj420p", "yuv420p10le", "yuv420p12le"},
    "av1": {"yuv420p", "yuv420p10le"},
    "vp9": {"yuv420p", "yuv420p10le"},
    "vp8": {"yuv420p"},
    "mpeg2video": {"yuv420p"},
    "mpeg1video": {"yuv420p"},
    "vc1": {"yuv420p"},
    "mpeg4": {"yuv420p"},          # XviD / DivX (MPEG-4 Part 2); if the card can't, the CPU pipeline takes over
}


def gpu_pipeline_possible(f: dict, burn_sub_index: int | None) -> tuple[bool, str]:
    """Can this file be decoded, scaled and encoded without frames leaving the GPU?"""
    if hw["accel"] != "nvenc" or not hw["scale_cuda"]:
        return False, "no CUDA scaler"
    if burn_sub_index is not None:
        return False, "picture subtitles are drawn on the CPU"
    if f.get("pix_fmt") not in NVDEC_FORMATS.get(f.get("video_codec") or "", set()):
        return False, f"NVDEC can't decode {f.get('video_codec')} {f.get('pix_fmt')}"
    if f.get("hdr") and not hw["tonemap_cuda"]:
        return False, "HDR tone mapping runs on the CPU with this FFmpeg build"
    return True, ""


PIPELINE_LABELS = {
    "gpu": "GPU decode → GPU scale → NVENC",
    "hybrid-nvenc": "GPU/CPU decode → CPU filters → NVENC",
    "qsv": "Quick Sync",
    "vaapi": "VA-API",
    "cpu": "CPU (x264)",
    "copy": "copy (no video encode)",
}


def detect_hardware() -> dict:
    """Probe which H.264 encoders really work on this machine (driver present, device reachable)."""
    with _hw_lock:
        src = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=0.4"]
        device = get_setting("vaapi_device") or "/dev/dri/renderD128"
        tests = {
            "nvenc": src + ["-c:v", "h264_nvenc", "-f", "null", "-"],
            "qsv": src + ["-vf", "format=nv12", "-c:v", "h264_qsv", "-f", "null", "-"],
            "vaapi": ["-vaapi_device", device] + src + ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi",
                                                        "-f", "null", "-"],
        }
        available = [name for name, args in tests.items()
                     if (name != "vaapi" or os.path.exists(device)) and _test(args)]
        tonemap = _test(src + ["-vf", "format=yuv420p10le,setparams=color_trc=smpte2084:color_primaries=bt2020:"
                                      "colorspace=bt2020nc:range=tv,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,"
                                      "zscale=t=bt709:m=bt709:r=tv,format=yuv420p", "-f", "null", "-"])
        wanted = get_setting("hw_accel") or "auto"
        if wanted == "auto":
            accel = available[0] if available else "none"
        elif wanted in available:
            accel = wanted
        else:
            if wanted != "none":
                log.warning("Hardware acceleration '%s' requested but not working; using software", wanted)
            accel = "none"
        encoder = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "vaapi": "h264_vaapi"}.get(accel, "libx264")
        filters = _filter_names()
        scale_cuda = "scale_cuda" in filters
        tonemap_cuda = "tonemap_cuda" in filters      # jellyfin-ffmpeg has it; stock builds don't
        if accel == "nvenc" and scale_cuda:
            # Prove the whole chain works on this card/driver: CUDA frames -> scale_cuda -> NVENC.
            scale_cuda = _test(["-init_hw_device", "cuda=gpu", "-filter_hw_device", "gpu"] + src
                               + ["-vf", "format=nv12,hwupload_cuda,scale_cuda=320:-2:format=nv12",
                                  "-c:v", "h264_nvenc", "-f", "null", "-"])
        hw.update(accel=accel, encoder=encoder, available=available, tonemap=tonemap, scale_cuda=scale_cuda,
                  tonemap_cuda=tonemap_cuda, checked_at=time.time())
        log.info("Transcoding with %s (hardware available: %s; full GPU pipeline: %s; HDR tone mapping: %s)",
                 encoder, ", ".join(available) or "none",
                 "yes" if accel == "nvenc" and scale_cuda else "no",
                 "GPU" if accel == "nvenc" and tonemap_cuda else ("CPU" if tonemap else "no"))
        return dict(hw)


# ---- sessions -------------------------------------------------------------------

@dataclass
class Session:
    id: str
    user_id: int
    username: str
    item_id: int
    file_id: int
    title: str
    mode: str
    quality: str
    start: float
    duration: float
    directory: Path
    cmd: list
    proc: subprocess.Popen | None = None
    created: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    last_segment: int = 0
    paused: bool = False
    speed: str = ""
    out_time: float = 0.0       # FFmpeg's output clock: the film's own timestamps (-copyts)
    origin: float = 0.0         # the file's start_time, subtracted to get a position in the film
    error: str = ""
    pipeline: str = "cpu"
    fallback_cmd: list | None = None
    stopped: bool = False
    done: threading.Event = field(default_factory=threading.Event)

    def produced_segments(self) -> int:
        try:
            return sum(1 for n in os.listdir(self.directory) if n.endswith((".ts", ".m4s")))
        except OSError:
            return 0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def info(self) -> dict:
        return {"id": self.id, "user": self.username, "item_id": self.item_id, "title": self.title,
                "mode": self.mode, "quality": self.quality, "encoder": hw["encoder"] if self.mode == "transcode"
                else "copy", "start": self.start, "speed": self.speed, "position": max(self.start, self.out_time - self.origin),
                "duration": self.duration, "paused": self.paused, "running": self.alive(),
                "created": self.created, "error": self.error, "pipeline": PIPELINE_LABELS.get(self.pipeline, self.pipeline)}


sessions: dict[str, Session] = {}


def busy() -> bool:
    """Something is being converted for a viewer right now (background jobs wait for that)."""
    return any(s.alive() for s in list(sessions.values()))
_sessions_lock = threading.Lock()


def _target_bitrate(height: int | None) -> int:
    h = height or 1080
    if h > 1440:
        return 20_000_000
    if h > 1080:
        return 14_000_000
    if h > 720:
        return 10_000_000
    if h > 480:
        return 5_000_000
    return 2_500_000


_readrate_ok: bool | None = None


def _readrate_supported() -> bool:
    """FFmpeg 6.1+ can pace its reading (-readrate with a quick start); older builds just run flat out."""
    global _readrate_ok
    if _readrate_ok is None:
        try:
            r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-readrate", "4",
                                "-readrate_initial_burst", "1", "-f", "lavfi", "-i", "nullsrc=d=0.2",
                                "-f", "null", "-"], capture_output=True, timeout=20)
            _readrate_ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _readrate_ok = False
    return _readrate_ok


# Pacing: the first 2 minutes are made as fast as possible (quick start, quick seeking), then 4x the
# film's speed - far ahead of the viewer, without keeping the processor busy flat out for the whole film.
READ_BURST, READ_RATE = 120, 4


def build_command(f: dict, out: Path, start: float, mode: str, quality: str,
                  audio_index: int | None, burn_sub_index: int | None, gpu: bool = False) -> list[str]:
    """gpu=True keeps frames in GPU memory end to end (NVDEC -> scale_cuda -> NVENC)."""
    accel = hw["accel"]
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    if mode == "transcode":
        if accel == "nvenc" and gpu:
            cmd += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-extra_hw_frames", "4"]
        elif accel == "nvenc":
            cmd += ["-hwaccel", "cuda"]            # GPU decode when possible, frames come back for CPU filters
        elif accel == "vaapi":
            cmd += ["-vaapi_device", get_setting("vaapi_device") or "/dev/dri/renderD128"]
    # -copyts keeps the film's own timestamps in the stream (and mpegts_copyts=1 below stops the TS
    # muxer adding its 1.4 s), so the player can read exactly which moment of the film the stream
    # starts at, whatever keyframe FFmpeg's seek lands on. That is what keeps subtitles in step.
    cmd += ["-copyts"]
    if start > 0:
        cmd += ["-ss", f"{start:.6f}"]
    if _readrate_supported():
        cmd += ["-readrate", str(READ_RATE), "-readrate_initial_burst", str(READ_BURST)]
    cmd += ["-i", f["path"]]

    # -- video
    if mode == "remux":
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    else:
        src_h = f.get("height") or 1080
        target = QUALITIES.get(quality)
        if target:
            height = min(src_h, target[0])
            bitrate = min(target[1], _target_bitrate(height))
        else:
            height = src_h if accel != "none" else min(src_h, 1080)  # 4K x264 in real time is too slow
            bitrate = _target_bitrate(height)
        filters = []
        if gpu:
            # Frames never leave the GPU: tone map (if the build has it), resize and convert to 8-bit there.
            if f.get("hdr"):
                filters.append("tonemap_cuda=format=nv12:p=bt709:t=bt709:m=bt709:tonemap=bt2390:peak=100:desat=0")
            size = f"-2:{height}:" if height < src_h else ""
            filters.append(f"scale_cuda={size}format=nv12")
        elif f.get("hdr") and hw["tonemap"]:
            filters.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
                           "zscale=t=bt709:m=bt709:r=tv")
        if height < src_h and not gpu:
            filters.append(f"scale=-2:{height}")
        if gpu:
            pass
        elif accel == "vaapi":
            filters.append("format=nv12,hwupload")
        elif accel == "qsv":
            filters.append("format=nv12")
        else:
            filters.append("format=yuv420p")
        chain = ",".join(filters)
        if burn_sub_index is not None:
            # Image subtitles (PGS/VobSub) are drawn into the picture.
            cmd += ["-filter_complex", f"[0:v:0][0:{burn_sub_index}]overlay=eof_action=pass[vb];[vb]{chain}[vout]",
                    "-map", "[vout]"]
        else:
            cmd += ["-map", "0:v:0", "-vf", chain]
        rate = ["-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.5)), "-bufsize", str(bitrate * 2)]
        if DEV_VP9:
            cmd += ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-b:v", "1500k"]
        elif accel == "nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", *rate,
                    "-profile:v", "high", "-forced-idr", "1"]
        elif accel == "qsv":
            cmd += ["-c:v", "h264_qsv", "-preset", "faster", *rate, "-profile:v", "high"]
        elif accel == "vaapi":
            cmd += ["-c:v", "h264_vaapi", *rate]
        else:
            cmd += ["-c:v", "libx264", "-preset", get_setting("x264_preset") or "veryfast", "-crf", "21",
                    "-maxrate", str(int(bitrate * 1.5)), "-bufsize", str(bitrate * 2),
                    "-profile:v", "high", "-level:v", "5.1", "-sc_threshold", "0"]
        # Every SEGMENT_SECONDS from the first frame (t is the film's own time because of -copyts).
        cmd += ["-force_key_frames", f"expr:if(isnan(prev_forced_t),1,gte(t-prev_forced_t,{SEGMENT_SECONDS}))"]

    # -- audio
    audio = next((a for a in f.get("audio", []) if a["index"] == audio_index), None) if audio_index is not None \
        else (f.get("audio") or [None])[0]
    if audio:
        cmd += ["-map", f"0:{audio['index']}"]
        if DEV_VP9:
            cmd += ["-c:a", "libopus", "-ac", "2", "-b:a", "128k"]
        elif audio.get("codec") == "aac" and (audio.get("channels") or 2) <= 2:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]
    cmd += ["-sn", "-dn", "-max_muxing_queue_size", "4096", "-progress", "pipe:1", "-nostats",
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", "0",
            "-hls_playlist_type", "event", "-hls_flags", "independent_segments+temp_file",
            # fMP4 must write the real time into each fragment (tfdt), not just an edit list players ignore.
            "-hls_segment_options", "movflags=+frag_discont" if DEV_VP9 else "mpegts_copyts=1",
            "-hls_segment_type", "fmp4" if DEV_VP9 else "mpegts", "-hls_fmp4_init_filename", "init.mp4",
            "-start_number", "0",
            "-hls_segment_filename", str(out / ("seg%05d.m4s" if DEV_VP9 else "seg%05d.ts")), str(out / "index.m3u8")]
    return cmd


def _launch(session: Session, cmd: list) -> None:
    errlog = open(session.directory / "ffmpeg.log", "wb")
    session.cmd = cmd
    session.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, stdin=subprocess.DEVNULL)
    errlog.close()


def _read_progress(session: Session) -> None:
    try:
        while True:
            proc = session.proc
            if not proc or not proc.stdout:
                return
            for raw in proc.stdout:
                line = raw.decode(errors="ignore").strip()
                if line.startswith("speed="):
                    session.speed = line[6:]
                elif line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        session.out_time = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        pass
            code = proc.wait()
            if code in (0, -signal.SIGKILL, -signal.SIGTERM) or session.stopped:
                return
            try:
                detail = (session.directory / "ffmpeg.log").read_text(errors="ignore")[-1500:]
            except OSError:
                detail = f"FFmpeg exited with code {code}"
            if session.fallback_cmd and session.produced_segments() == 0:
                # The all-GPU chain failed on this file (driver, codec profile...): redo it the safe way.
                log.warning("Full GPU pipeline failed for '%s', retrying with CPU filters: %s",
                            session.title, detail.strip().splitlines()[-1:] or detail)
                cmd, session.fallback_cmd = session.fallback_cmd, None
                session.pipeline = "hybrid-nvenc"
                _launch(session, cmd)
                continue
            session.error = detail
            log.warning("FFmpeg for '%s' exited with %s: %s", session.title, code, detail[-300:])
            return
    finally:
        session.done.set()


def start_session(*, user: dict, item: dict, f: dict, mode: str, quality: str, start: float,
                  audio_index: int | None, burn_sub_index: int | None, replace: str | None = None,
                  origin: float = 0.0) -> Session:
    if replace:
        stop_session(replace)
    _enforce_limit()
    sid = secrets.token_urlsafe(9)
    out = TRANSCODE_DIR / sid
    out.mkdir(parents=True, exist_ok=True)
    gpu, why_not = (gpu_pipeline_possible(f, burn_sub_index) if mode == "transcode" and not DEV_VP9
                    else (False, ""))
    cmd = build_command(f, out, start, mode, quality, audio_index, burn_sub_index, gpu=gpu)
    if mode == "remux":
        pipeline = "copy"
    elif gpu:
        pipeline = "gpu"
    else:
        pipeline = {"nvenc": "hybrid-nvenc", "qsv": "qsv", "vaapi": "vaapi"}.get(hw["accel"], "cpu")
    session = Session(id=sid, user_id=user["id"], username=user["username"], item_id=item["id"], file_id=f["id"],
                      title=item.get("display_title") or item["title"], mode=mode, quality=quality, start=start,
                      duration=f.get("duration") or 0, directory=out, cmd=cmd, pipeline=pipeline,
                      origin=origin)
    if gpu:
        session.fallback_cmd = build_command(f, out, start, mode, quality, audio_index, burn_sub_index, gpu=False)
    log.info("Starting %s session %s for '%s' at %.0fs (%s%s)", mode, sid, session.title, start,
             PIPELINE_LABELS[pipeline], f"; {why_not}" if why_not and hw["accel"] == "nvenc" else "")
    _launch(session, cmd)
    threading.Thread(target=_read_progress, args=(session,), daemon=True).start()
    with _sessions_lock:
        sessions[sid] = session
    return session


def _enforce_limit() -> None:
    try:
        limit = max(1, int(get_setting("max_transcodes") or 4))
    except ValueError:
        limit = 4
    with _sessions_lock:
        running = sorted((s for s in sessions.values() if s.alive()), key=lambda s: s.last_access)
    while len(running) >= limit:
        stop_session(running.pop(0).id)


def stop_session(sid: str) -> None:
    with _sessions_lock:
        session = sessions.pop(sid, None)
    if not session:
        return
    session.stopped = True
    if session.alive():
        try:
            if session.paused:
                session.proc.send_signal(signal.SIGCONT)
            session.proc.kill()
            session.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    shutil.rmtree(session.directory, ignore_errors=True)
    log.info("Stopped session %s ('%s')", sid, session.title)


def touch(session: Session, segment: int | None = None) -> None:
    session.last_access = time.time()
    if segment is not None:
        session.last_segment = max(segment, 0)
        if session.paused and session.produced_segments() - session.last_segment < AHEAD_LIMIT // 2:
            _resume(session)


def _resume(session: Session) -> None:
    if session.alive() and session.paused:
        session.proc.send_signal(signal.SIGCONT)
        session.paused = False


def wait_for(session: Session, path: Path, timeout: float = 40) -> bool:
    """Block until FFmpeg has written a file (playlist or segment)."""
    deadline = time.time() + timeout
    _resume(session)
    while time.time() < deadline:
        if path.exists():
            if path.name != "index.m3u8" or re.search(r"\.(ts|m4s)\b", path.read_text(errors="ignore")):
                return True
        if not session.alive() and session.done.is_set():   # finished for good (no fallback pending)
            return path.exists()
        time.sleep(0.15)
    return False


def _housekeeping() -> None:
    while True:
        time.sleep(3)
        now = time.time()
        with _sessions_lock:
            current = list(sessions.values())
        for s in current:
            if now - s.last_access > IDLE_TIMEOUT:
                stop_session(s.id)
                continue
            if s.alive() and not s.paused and s.produced_segments() - s.last_segment > AHEAD_LIMIT:
                s.proc.send_signal(signal.SIGSTOP)
                s.paused = True


def start_housekeeping() -> None:
    shutil.rmtree(TRANSCODE_DIR, ignore_errors=True)
    TRANSCODE_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_housekeeping, name="transcode-housekeeping", daemon=True).start()


def stop_all() -> None:
    for sid in list(sessions):
        stop_session(sid)
