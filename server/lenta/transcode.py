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
import json
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
from .db import get_setting, jloads, set_setting

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


def _nvdec_key(codec: str | None, pix_fmt: str | None) -> str:
    return f"{codec or '?'}/{pix_fmt or '?'}"


def nvdec_refused() -> set:
    """What this machine's card has actually failed to decode, learned the hard way.

    `NVDEC_FORMATS` above is what NVDEC supports **in general** — it is a reading of NVIDIA's own
    table, and it says nothing about the card in this machine. A GTX 1070 is listed there as able to
    decode AV1 and cannot: AV1 decoding arrived with the RTX 30 series, and FFmpeg says so plainly
    ("Your platform doesn't support hardware accelerated AV1 decoding"). Rather than keep a second
    table of which chip generation gained which codec — another list of assumptions, which is what
    caused this — the first failure is remembered and never repeated.

    Cleared by Settings › Dashboard › Detect hardware, which is what you press after changing the card.
    """
    try:
        return set(jloads(get_setting("nvdec_refused"), []) or [])
    except (TypeError, ValueError):
        return set()


def remember_nvdec_failure(codec: str | None, pix_fmt: str | None) -> None:
    key = _nvdec_key(codec, pix_fmt)
    known = nvdec_refused()
    if key in known:
        return
    known.add(key)
    set_setting("nvdec_refused", json.dumps(sorted(known)))
    log.warning("This card cannot decode %s on the GPU; it will not be asked to again", key)


def nvdec_can_decode(f: dict) -> tuple[bool, str]:
    """Can the card decode this file at all — never mind where the filters run?

    **This is asked in two places and both matter.** `gpu_pipeline_possible()` uses it to choose the
    pipeline, and `build_command()` uses it to decide whether to pass `-hwaccel cuda` at all. 1.21.2
    asked it in the first place only, which left the *fallback* command still saying `-hwaccel cuda` —
    and FFmpeg does not quietly decode in software when that fails. Measured on his server, 23 Oct
    2026, on the fallback LENTA had just switched to:

        [av1] Failed setup for format cuda: hwaccel initialisation returned error.
        [dec:av1] Error submitting packet to decoder: Function not implemented

    repeated for every frame, forever. So **the safe path has to be safe about the decoder too**, not
    only about the filters: without `-hwaccel`, FFmpeg decodes AV1 with libdav1d on the processor and
    the picture arrives.
    """
    codec, pix = f.get("video_codec"), f.get("pix_fmt")
    if pix not in NVDEC_FORMATS.get(codec or "", set()):
        return False, f"NVDEC can't decode {codec} {pix}"
    key = _nvdec_key(codec, pix)
    if key in nvdec_refused():
        return False, f"this card has already failed to decode {key}"
    return True, ""


def without_hwaccel(cmd: list) -> list:
    """The same command with the hardware decoder taken off it.

    The fallback command is built in `start_session`, **before** the failure that makes it necessary
    has happened, so it cannot have consulted `nvdec_refused()` — on the very first film of a codec
    the card turns out not to decode, the learned answer arrives one moment too late. Rather than
    carry the build arguments around to rebuild it, the flags are taken off the command that exists.
    `-hwaccel_output_format` and `-extra_hw_frames` are removed too, for a caller that passes a full
    GPU command; the fallback itself only ever carries `-hwaccel`.
    """
    out, skip = [], 0
    for i, a in enumerate(cmd):
        if skip:
            skip -= 1
            continue
        if a in ("-hwaccel", "-hwaccel_output_format", "-extra_hw_frames"):
            skip = 1                               # drop the flag and its value
            continue
        out.append(a)
    return out


def gpu_pipeline_possible(f: dict, burn_sub_index: int | None) -> tuple[bool, str]:
    """Can this file be decoded, scaled and encoded without frames leaving the GPU?"""
    if hw["accel"] != "nvenc" or not hw["scale_cuda"]:
        return False, "no CUDA scaler"
    if burn_sub_index is not None:
        return False, "picture subtitles are drawn on the CPU"
    can, why = nvdec_can_decode(f)
    if not can:
        return False, why
    if f.get("hdr") and not hw["tonemap_cuda"]:
        return False, "HDR tone mapping runs on the CPU with this FFmpeg build"
    return True, ""


PIPELINE_LABELS = {
    "gpu": "GPU decode → GPU scale → NVENC",
    "hybrid-nvenc": "GPU/CPU decode → CPU filters → NVENC",
    # The card encodes but cannot decode this file, so the processor does the decoding. Named
    # separately because "GPU/CPU decode" above reads as if the card might be helping, and when a
    # film is heavy on the processor he should be able to see from the panel why.
    "swdec-nvenc": "CPU decode → CPU filters → NVENC",
    "qsv": "Quick Sync",
    "vaapi": "VA-API",
    "cpu": "CPU (x264)",
    "copy": "copy (no video encode)",
}


def detect_hardware() -> dict:
    """Probe which H.264 encoders really work on this machine (driver present, device reachable)."""
    with _hw_lock:
        # Whatever this card could not decode is forgotten here, because this is the button you press
        # after changing the card — and the old card's failures say nothing about the new one.
        set_setting("nvdec_refused", "")
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
    force_fallback: bool = False    # the watchdog saw nothing come out; take the safe command
    codec: str = ""                 # what the source is, so a failed GPU attempt can be remembered
    pix_fmt: str = ""
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
        elif accel == "nvenc" and nvdec_can_decode(f)[0]:
            cmd += ["-hwaccel", "cuda"]            # GPU decode when possible, frames come back for CPU filters
        # and when the card cannot decode this file, no `-hwaccel` at all: it does not degrade to
        # software on its own, it fails on every frame. See `nvdec_can_decode`.
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
            # The declared level follows the picture we are actually making, rather than being 5.1 on
            # everything. A decoder reads the number in the stream, not the stream: a television that
            # stops at level 4.1 can be handed an easy 1080p film and still refuse it because the file
            # says 5.1. x264 trims its own reference frames to fit the level it is given, so asking for
            # 4.1 on a 1080p encode costs nothing. nvenc has always chosen the level itself, which is
            # why only this one path could say the wrong thing.
            cmd += ["-c:v", "libx264", "-preset", get_setting("x264_preset") or "veryfast", "-crf", "21",
                    "-maxrate", str(int(bitrate * 1.5)), "-bufsize", str(bitrate * 2),
                    "-profile:v", "high", "-level:v", "4.1" if height <= 1080 else "5.1",
                    "-sc_threshold", "0"]
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


# How long a session may produce nothing before the watchdog takes the safer command. Comfortably
# under the 40 s the client waits in `wait_for`, so the fallback still has time to make a segment.
STARTUP_GRACE = 12


def _watch_start(session: Session) -> None:
    """Take the safe command if the full GPU pipeline produces nothing at all.

    **The fallback in `_read_progress` only runs when FFmpeg exits**, and on a file the card cannot
    decode FFmpeg does not exit quickly: it retries the decode, throttled by `-readrate`, for fifteen
    seconds or more before giving up. The player stops the session long before that, and
    `stop_session` sets `stopped`, which makes `_read_progress` return without ever reaching the
    retry. So the recovery existed and could not be reached — every AV1 film failed with the safe
    path sitting right there unused.

    This is the half that does not depend on the client outlasting a slow failure, and it catches a
    hung encoder as well as a failing one, for the same reason the player's own watchdog exists (see
    the decode ceiling in CLAUDE.md): a stall reports nothing, so something has to go and look.
    """
    end = time.time() + STARTUP_GRACE
    while time.time() < end:
        if session.stopped or session.produced_segments() or not session.fallback_cmd:
            return
        time.sleep(0.5)
    if session.stopped or session.produced_segments() or not session.fallback_cmd:
        return
    log.warning("Nothing came out of the full GPU pipeline for '%s' in %ds; taking the CPU-filter path",
                session.title, STARTUP_GRACE)
    session.force_fallback = True
    try:
        if session.proc:
            if session.paused:
                session.proc.send_signal(signal.SIGCONT)
            session.proc.kill()                 # `_read_progress` wakes and does the relaunch
    except OSError:
        pass


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
            if session.stopped:
                return
            # A kill is normally us, so it means "leave it alone" — unless the watchdog below did the
            # killing, in which case the whole point was to come here and take the safer command.
            if code in (0, -signal.SIGKILL, -signal.SIGTERM) and not session.force_fallback:
                return
            session.force_fallback = False
            try:
                detail = (session.directory / "ffmpeg.log").read_text(errors="ignore")[-1500:]
            except OSError:
                detail = f"FFmpeg exited with code {code}"
            if session.fallback_cmd and session.produced_segments() == 0:
                # The all-GPU chain failed on this file (driver, codec profile...): redo it the safe way.
                log.warning("Full GPU pipeline failed for '%s', retrying with CPU filters: %s",
                            session.title, detail.strip().splitlines()[-1:] or detail)
                # And remember it, so the next film in this codec goes straight down the safe path
                # instead of spending fifteen seconds discovering the same thing again.
                remember_nvdec_failure(session.codec, session.pix_fmt)
                cmd, session.fallback_cmd = session.fallback_cmd, None
                # The fallback was built before the failure was known, so it is still carrying
                # `-hwaccel cuda` — which is the thing that just failed. Take it off.
                cmd = without_hwaccel(cmd)
                session.pipeline = "swdec-nvenc"
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
    elif hw["accel"] == "nvenc" and mode == "transcode" and not nvdec_can_decode(f)[0]:
        pipeline = "swdec-nvenc"               # the card encodes but cannot decode this one
    else:
        pipeline = {"nvenc": "hybrid-nvenc", "qsv": "qsv", "vaapi": "vaapi"}.get(hw["accel"], "cpu")
    session = Session(id=sid, user_id=user["id"], username=user["username"], item_id=item["id"], file_id=f["id"],
                      title=item.get("display_title") or item["title"], mode=mode, quality=quality, start=start,
                      duration=f.get("duration") or 0, directory=out, cmd=cmd, pipeline=pipeline,
                      origin=origin)
    session.codec = f.get("video_codec") or ""
    session.pix_fmt = f.get("pix_fmt") or ""
    if gpu:
        session.fallback_cmd = build_command(f, out, start, mode, quality, audio_index, burn_sub_index, gpu=False)
    log.info("Starting %s session %s for '%s' at %.0fs (%s%s)", mode, sid, session.title, start,
             PIPELINE_LABELS[pipeline], f"; {why_not}" if why_not and hw["accel"] == "nvenc" else "")
    _launch(session, cmd)
    threading.Thread(target=_read_progress, args=(session,), daemon=True).start()
    if session.fallback_cmd:
        threading.Thread(target=_watch_start, args=(session,), daemon=True).start()
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
