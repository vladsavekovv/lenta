"""SQLite storage: schema, connections and runtime settings."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_images (       -- web: picture references -> where to download them
    name TEXT PRIMARY KEY,
    url  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    all_libraries INTEGER NOT NULL DEFAULT 1,
    color         TEXT,
    created_at    REAL
);

CREATE TABLE IF NOT EXISTS libraries (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- movies | shows | music | photos
    paths      TEXT NOT NULL,           -- JSON list of folders
    created_at REAL,
    last_scan  REAL
);

CREATE TABLE IF NOT EXISTS user_libraries (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, library_id)
);

-- One table for every browsable thing. kind is one of:
-- movie | show | season | episode | album | track | photoalbum | photo
CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY,
    library_id     INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    parent_id      INTEGER REFERENCES items(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,       -- grouping key, unique per library
    title          TEXT NOT NULL,
    sort_title     TEXT,
    original_title TEXT,
    year           INTEGER,
    release_date   TEXT,
    overview       TEXT,
    tagline        TEXT,
    genres         TEXT,                -- JSON list
    rating         REAL,
    certification  TEXT,
    runtime        INTEGER,             -- minutes
    poster         TEXT,                -- image ref: tmdb:/x.jpg | local:x.jpg
    backdrop       TEXT,
    logo           TEXT,
    thumb          TEXT,
    tmdb_id        INTEGER,
    matched        INTEGER NOT NULL DEFAULT 0,   -- 1 = online metadata applied
    match_locked   INTEGER NOT NULL DEFAULT 0,   -- 1 = admin fixed it, scanner won't rematch
    index_number   INTEGER,             -- episode / track number
    parent_index   INTEGER,             -- season / disc number
    artist         TEXT,
    extra          TEXT,                -- JSON: cast, directors, studios, exif...
    added_at       REAL,
    updated_at     REAL,
    UNIQUE (library_id, key)
);
CREATE INDEX IF NOT EXISTS idx_items_lib_kind ON items(library_id, kind);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_id);
CREATE INDEX IF NOT EXISTS idx_items_added ON items(added_at);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    library_id  INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    path        TEXT NOT NULL UNIQUE,
    size        INTEGER,
    mtime       REAL,
    container   TEXT,
    duration    REAL,
    bitrate     INTEGER,
    video_codec TEXT,
    width       INTEGER,
    height      INTEGER,
    pix_fmt     TEXT,
    hdr         INTEGER DEFAULT 0,
    audio       TEXT,                   -- JSON list of audio streams
    subtitles   TEXT,                   -- JSON list of subtitle streams (embedded + sidecar)
    added_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_files_item ON files(item_id);
CREATE INDEX IF NOT EXISTS idx_files_lib ON files(library_id);

CREATE TABLE IF NOT EXISTS watch_state (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    position   REAL NOT NULL DEFAULT 0,
    duration   REAL,
    completed  INTEGER NOT NULL DEFAULT 0,
    play_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL,
    PRIMARY KEY (user_id, item_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (user_id, key)
);

-- Subtitles fetched from online providers, stored in DATA_DIR/subtitles (media folders stay read-only).
CREATE TABLE IF NOT EXISTS subtitle_downloads (
    id                INTEGER PRIMARY KEY,
    file_id           INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    language          TEXT NOT NULL,
    path              TEXT NOT NULL,
    provider          TEXT NOT NULL,
    provider_id       TEXT,
    release           TEXT,
    hearing_impaired  INTEGER DEFAULT 0,
    created_by        INTEGER,
    created_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_subdl_file ON subtitle_downloads(file_id);

-- pictures known to have no words on them (TMDB / Fanart.tv say so): a title's logo may go on top of these
CREATE TABLE IF NOT EXISTS clean_art (
    ref TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS intros (
    file_id  INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    start    REAL,                    -- NULL: checked, no intro found (intros.py)
    end      REAL,
    source   TEXT,                    -- 'audio' (detected) or 'manual'
    sig      TEXT,
    checked  REAL
);

CREATE TABLE IF NOT EXISTS watchlist (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    added_at REAL,
    PRIMARY KEY (user_id, item_id)
);
"""

SETTING_DEFAULTS = {
    "server_name": "LENTA",
    "tmdb_api_key": "",          # v3 API key or v4 read access token
    "metadata_language": "en-US",
    "metadata_region": "US",
    "hw_accel": "auto",          # auto | nvenc | qsv | vaapi | none
    "vaapi_device": "/dev/dri/renderD128",
    "x264_preset": "veryfast",
    "max_transcodes": "4",
    "scan_interval_minutes": "60",
    "show_users_on_login": "1",
    "jwt_secret": "",
    "default_theme": "projector",        # theme for the sign-in screen and new users
    "opensubtitles_api_key": "",
    "opensubtitles_username": "",
    "opensubtitles_password": "",
    "theme_music_online": "1",           # look up soundtrack previews online when no local theme file
    "omdb_api_key": "",                  # optional: IMDb / Rotten Tomatoes / Metacritic ratings in the About panel
    "wikipedia_summaries": "1",
    "imdb_ratings": "1",
    "apple_trailers": "1",
    "kinocheck_trailers": "1",           # official trailers picked by KinoCheck (YouTube-hosted), before TMDB's list
    "kinocheck_api_key": "",             # optional: more than 1,000 KinoCheck lookups a day
    "kinocheck_usage": "",
    "yt_unavailable": "",
    "watch_libraries": "1",              # notice new media in library folders and scan them (watcher.py)
    "watch_interval": "60",              # seconds between looks at the library folders
    "trickplay": "1",
    "intro_detection": "1",              # find TV intros by comparing episodes' audio (intros.py)                    # seek-bar preview thumbnails (trickplay.py)
    "trickplay_interval": "10",          # seconds between preview thumbnails                # internal: YouTube trailers that don't play here (trailer_sources.py)               # internal: "YYYY-MM-DD:count" of today's KinoCheck requests               # look for Apple TV (iTunes) trailers before YouTube                 # IMDb ratings from IMDb's free daily datasets (imdb.py)
    "fanart_api_key": "",                # Fanart.tv project API key (needed for Fanart.tv artwork)
    "fanart_client_key": "",             # Fanart.tv personal API key (optional: newer pictures sooner)
    "artwork_sources_movie": "",         # JSON order of artwork sources; empty = the defaults (artwork.py)
    "artwork_sources_show": "",          # show the Wikipedia summary in the About panel
}

_local = threading.local()
_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_db() -> None:
    with _init_lock:
        con = _connect()
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(libraries)")}
        if "scan_report" not in cols:   # outcome of the last scan: counts, skipped files, errors (JSON)
            con.execute("ALTER TABLE libraries ADD COLUMN scan_report TEXT")
        if "position" not in cols:      # order in the menu and on Home, set by dragging in the menu
            con.execute("ALTER TABLE libraries ADD COLUMN position INTEGER")
        con.execute("UPDATE libraries SET position = id WHERE position IS NULL")
        # Renamed from SAVEK: keep a custom server name, but update the old default.
        con.execute("UPDATE settings SET value = 'LENTA' WHERE key = 'server_name' AND value = 'SAVEK'")
        con.commit()
        con.close()


@contextmanager
def db():
    """One connection per thread, reused; commits on success."""
    con = getattr(_local, "con", None)
    if con is None:
        con = _local.con = _connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise


def get_setting(key: str) -> str:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] is None:
        return SETTING_DEFAULTS.get(key, "")
    return row["value"]


def set_setting(key: str, value) -> None:
    with db() as con:
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def all_settings() -> dict:
    out = dict(SETTING_DEFAULTS)
    with db() as con:
        for row in con.execute("SELECT key, value FROM settings"):
            out[row["key"]] = row["value"]
    return out


def jloads(value, default=None):
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else []


def now() -> float:
    return time.time()
