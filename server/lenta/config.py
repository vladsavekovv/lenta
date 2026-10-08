"""Static configuration for LENTA Media Server, read from environment variables.

Runtime settings (TMDB key, hardware acceleration, scan interval...) live in the
database and are edited from the admin dashboard; see db.SETTING_DEFAULTS.
"""
import os
from pathlib import Path

VERSION = "1.1.0"
APP_NAME = "LENTA Media Server"

_ROOT = Path(__file__).resolve().parents[2]  # .../lenta

DATA_DIR = Path(os.environ.get("LENTA_DATA_DIR", Path.home() / ".local/share/lenta")).expanduser()
WEB_DIR = Path(os.environ.get("LENTA_WEB_DIR", _ROOT / "web")).expanduser()
TRANSCODE_DIR = Path(os.environ.get("LENTA_TRANSCODE_DIR", DATA_DIR / "transcode")).expanduser()

HOST = os.environ.get("LENTA_HOST", "0.0.0.0")
PORT = int(os.environ.get("LENTA_PORT", "8484"))

FFMPEG = os.environ.get("LENTA_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("LENTA_FFPROBE", "ffprobe")

# Development only: stream VP9/Opus fMP4 instead of H.264 (for browsers built without H.264, e.g. test Chromium).
DEV_VP9 = os.environ.get("LENTA_DEV_VP9") == "1"

DB_PATH = DATA_DIR / "lenta.db"
IMAGES_DIR = DATA_DIR / "images"      # art we own (sidecar posters, extracted frames, album covers)
CACHE_DIR = DATA_DIR / "cache"        # TMDB downloads, resized images, converted subtitles

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm",
             ".mpg", ".mpeg", ".flv", ".ogv", ".3gp", ".vob"}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma",
             ".ape", ".aiff", ".aif", ".alac", ".wv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa"}

LIBRARY_KINDS = {"movies", "shows", "music", "photos"}


def ensure_dirs() -> None:
    for d in (DATA_DIR, IMAGES_DIR, CACHE_DIR, TRANSCODE_DIR):
        d.mkdir(parents=True, exist_ok=True)
