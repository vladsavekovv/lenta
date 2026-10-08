"""ffprobe wrapper: turns a media file into the facts the transcoder needs."""
import json
import subprocess

from .config import FFPROBE

TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "dvb_subtitle", "xsub"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

# Container names as browsers understand them.
_CONTAINER_MAP = {
    "mov,mp4,m4a,3gp,3g2,mj2": "mp4",
    "matroska,webm": "mkv",
    "mpegts": "ts",
    "avi": "avi",
    "asf": "asf",
    "flv": "flv",
}


def ffprobe(path: str) -> dict | None:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=120, check=True)
        return json.loads(out.stdout or b"{}")
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _lang(stream: dict) -> str:
    return (stream.get("tags", {}).get("language") or "und").lower()


def _title(stream: dict) -> str:
    return stream.get("tags", {}).get("title") or ""


def summarize(path: str) -> dict:
    """Return a flat summary for the files table. Empty dict if the file can't be read."""
    data = ffprobe(path)
    if not data:
        return {}
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    container = _CONTAINER_MAP.get(fmt.get("format_name", ""), (fmt.get("format_name") or "").split(",")[0])
    if container == "mkv" and path.lower().endswith(".webm"):
        container = "webm"

    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio, subs = [], []
    for s in streams:
        disp = s.get("disposition", {})
        if s.get("codec_type") == "audio":
            audio.append({
                "index": s["index"], "codec": s.get("codec_name"), "channels": s.get("channels"),
                "layout": s.get("channel_layout"), "language": _lang(s), "title": _title(s),
                "default": bool(disp.get("default")),
            })
        elif s.get("codec_type") == "subtitle":
            codec = s.get("codec_name") or ""
            subs.append({
                "key": str(s["index"]), "index": s["index"], "codec": codec, "language": _lang(s),
                "title": _title(s), "forced": bool(disp.get("forced")), "default": bool(disp.get("default")),
                "text": codec in TEXT_SUB_CODECS, "image": codec in IMAGE_SUB_CODECS,
            })

    duration = float(fmt.get("duration") or 0) or float((video or {}).get("duration") or 0)
    hdr = 0
    if video:
        if video.get("color_transfer") in HDR_TRANSFERS:
            hdr = 1
        if any("Dolby Vision" in (sd.get("side_data_type") or "") for sd in video.get("side_data_list", [])):
            hdr = 1
    return {
        "container": container,
        "duration": duration,
        "bitrate": int(fmt.get("bit_rate") or 0),
        "video_codec": video.get("codec_name") if video else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "pix_fmt": video.get("pix_fmt") if video else None,
        "hdr": hdr,
        "audio": audio,
        "subtitles": subs,
    }
