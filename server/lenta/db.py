"""SQLite storage: schema, connections and runtime settings."""
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import DB_PATH

log = logging.getLogger("lenta.db")

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
    created_at    REAL,
    -- Bumped to invalidate every session token this account has already been issued: signing out,
    -- changing the password, an administrator resetting it. A token carries the epoch it was made
    -- under and is refused once the two disagree. See auth.issue_token / auth.user_from_request.
    token_epoch   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS libraries (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- movies | shows | music | audiobooks | photos | livetv
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
    profile     TEXT,                   -- H.264/HEVC profile name, e.g. "High"
    level       INTEGER,                -- the codec's own number: H.264 4.1 is 41, HEVC 4.1 is 123
    refs        INTEGER,                -- reference frames; an old hardware decoder has a ceiling
    fps         REAL,
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

CREATE TABLE IF NOT EXISTS tv_sources (       -- where live channels come from
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,           -- tuner | m3u | xtream
    name       TEXT NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',   -- JSON: tuner key and dish settings, or playlist address
    enabled    INTEGER NOT NULL DEFAULT 1,
    provider   TEXT,                     -- the network this source turned out to carry
    status     TEXT,                     -- idle | scanning | ok | error, with the last message
    last_scan  REAL,
    found      INTEGER NOT NULL DEFAULT 0,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS tv_channels (      -- one channel, from a tuner or a playlist
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES tv_sources(id) ON DELETE CASCADE,
    uid         TEXT NOT NULL,          -- stable within the source: service id, or the playlist's id
    name        TEXT NOT NULL,
    number      INTEGER,                -- the channel number the provider asks for (LCN)
    provider    TEXT,
    kind        TEXT NOT NULL DEFAULT 'tv',   -- tv | radio | data
    logo        TEXT,
    tune        TEXT NOT NULL DEFAULT '{}',   -- JSON: how to get it (frequency and ids, or a URL)
    scrambled   INTEGER NOT NULL DEFAULT 0,
    hidden      INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL,
    UNIQUE (source_id, uid)
);

CREATE INDEX IF NOT EXISTS idx_tv_channels_source ON tv_channels(source_id);

-- What is on, read from the broadcast's own event tables while you watch.
-- Keyed by the channel's uid rather than its row: a rescan rebuilds channel rows, and the guide should
-- survive that. event_id is the broadcaster's, so a programme that is revised in a later table
-- replaces itself instead of appearing twice.
CREATE TABLE IF NOT EXISTS tv_programmes (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES tv_sources(id) ON DELETE CASCADE,
    uid         TEXT NOT NULL,          -- the channel's uid: onid:tsid:service_id
    event_id    INTEGER NOT NULL,
    start       REAL NOT NULL,          -- unix time, from the table's own UTC
    stop        REAL NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    genre       TEXT NOT NULL DEFAULT '',
    language    TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL,
    UNIQUE (source_id, uid, event_id)
);

CREATE INDEX IF NOT EXISTS idx_tv_programmes_when ON tv_programmes(source_id, uid, start);
CREATE INDEX IF NOT EXISTS idx_tv_programmes_stop ON tv_programmes(stop);

CREATE TABLE IF NOT EXISTS stream_providers (  -- streaming services available here (TMDB, data by JustWatch)
    provider_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    logo        TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 0,   -- JustWatch's own ordering: the services people here actually use
    region      TEXT NOT NULL DEFAULT '',
    fetched_at  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stream_titles (     -- what each service is showing, refreshed once a day
    provider_id INTEGER NOT NULL,
    kind        TEXT NOT NULL,                 -- movie | show
    tmdb_id     INTEGER NOT NULL,
    rank        INTEGER NOT NULL,              -- TMDB's popularity order, not the service's own chart
    title       TEXT NOT NULL DEFAULT '',
    year        INTEGER,
    poster      TEXT NOT NULL DEFAULT '',
    overview    TEXT NOT NULL DEFAULT '',
    rating      REAL,
    link        TEXT NOT NULL DEFAULT '',      -- where to watch it; opened in a new tab, never played here
    language    TEXT NOT NULL DEFAULT '',      -- the language it was made in (TMDB's original_language)
    backdrop    TEXT NOT NULL DEFAULT '',      -- the wide picture, for rows drawn in the landscape style
    fetched_at  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (provider_id, kind, tmdb_id)
);

CREATE INDEX IF NOT EXISTS idx_stream_titles_row ON stream_titles(provider_id, kind, rank);

CREATE TABLE IF NOT EXISTS stream_genres (   -- TMDB's own genre numbers, fetched rather than written
    kind       TEXT NOT NULL,                  -- down here: films and series use different ones, and
    genre_id   INTEGER NOT NULL,               -- a number guessed from memory filters the wrong thing
    name       TEXT NOT NULL,
    fetched_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, genre_id)
);

CREATE TABLE IF NOT EXISTS stream_ids (      -- a title's number on the service, so a click opens it
    tmdb_id    INTEGER NOT NULL,              -- rather than a search for its name. Looked up once and
    kind       TEXT NOT NULL,                 -- kept: these numbers do not change.
    netflix_id TEXT NOT NULL DEFAULT '',      -- '' means looked and not found, which is worth remembering
    checked_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (tmdb_id, kind)
);

CREATE TABLE IF NOT EXISTS stream_art (       -- a picture you chose yourself, instead of the one TMDB has
    tmdb_id      INTEGER NOT NULL,            -- kept apart from stream_titles, which is wiped on every
    kind         TEXT NOT NULL,               -- refresh: your choice has to outlive the data it sits on
    ref          TEXT NOT NULL DEFAULT '',    -- the upright one
    backdrop_ref TEXT NOT NULL DEFAULT '',    -- the wide one; a row drawn either way needs its own
    set_at       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (tmdb_id, kind)
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
    "streaming_services": "",    # JSON list of TMDB provider ids whose rows Home shows
    "streaming_hide_owned": "1",  # leave out anything already on this server
    "streaming_languages_off": "",  # JSON list of original-language codes to leave out of the rows
    "streaming_genres": "",       # JSON list of genre names to narrow the rows to; empty means everything
    "opensubtitles_api_key": "",
    "opensubtitles_username": "",
    "opensubtitles_password": "",
    "livetv_auto_scan": "1",             # scan a newly found TV tuner once, by itself, in the background
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
    "artwork_sources_show": "",
    # HTTP and HTTPS (Server admin › Settings › HTTP and HTTPS; listeners.py, certs.py)
    "net_http_enabled": "1",
    "net_http_port": "",                 # empty = LENTA_PORT from /etc/lenta/lenta.env (8484)
    "net_https_enabled": "1",
    "net_https_port": "8443",
    "net_cert_mode": "auto",             # auto (LENTA's own) | upload | path (files on the server)
    "net_cert_names": "",                # extra names/addresses for the automatic certificate
    "net_cert_path_crt": "",
    "net_cert_path_key": "",
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
        # 1.9.1: what language a title was made in, so rows can leave some out. The table is created by
        # the line above on a new server, but one that already ran 1.9.0 has it without this column.
        sc = {r[1] for r in con.execute("PRAGMA table_info(stream_titles)")}
        if sc and "language" not in sc:
            con.execute("ALTER TABLE stream_titles ADD COLUMN language TEXT NOT NULL DEFAULT ''")
        if sc and "backdrop" not in sc:      # 1.9.4: wide artwork, for rows drawn the way your own are
            con.execute("ALTER TABLE stream_titles ADD COLUMN backdrop TEXT NOT NULL DEFAULT ''")
        ac = {r[1] for r in con.execute("PRAGMA table_info(stream_art)")}
        if ac and "backdrop_ref" not in ac:  # 1.9.5: a picture chosen per shape, not one for both
            con.execute("ALTER TABLE stream_art ADD COLUMN backdrop_ref TEXT NOT NULL DEFAULT ''")
        cols = {r[1] for r in con.execute("PRAGMA table_info(libraries)")}
        if "scan_report" not in cols:   # outcome of the last scan: counts, skipped files, errors (JSON)
            con.execute("ALTER TABLE libraries ADD COLUMN scan_report TEXT")
        if "position" not in cols:      # order in the menu and on Home, set by dragging in the menu
            con.execute("ALTER TABLE libraries ADD COLUMN position INTEGER")
        con.execute("UPDATE libraries SET position = id WHERE position IS NULL")
        # 1.18.0: how hard a file is to decode, not just which codec it is. A television stalls rather
        # than erroring above its ceiling, so these are what keep a file off a set that cannot take it.
        # Existing rows are left NULL and filled by the next scan; a NULL is read as "unknown", which
        # never blocks direct play, so nobody's library changes behaviour until it has been scanned.
        fc = {r[1] for r in con.execute("PRAGMA table_info(files)")}
        for col, kind in (("profile", "TEXT"), ("level", "INTEGER"), ("refs", "INTEGER"), ("fps", "REAL")):
            if col not in fc:
                con.execute(f"ALTER TABLE files ADD COLUMN {col} {kind}")

        # 1.19.0: a session token can be withdrawn. Every existing token keeps working — they were all
        # issued without an epoch and are read as epoch 0, which is what every account starts at — so
        # nobody is signed out by the upgrade itself.
        uc = {r[1] for r in con.execute("PRAGMA table_info(users)")}
        if "token_epoch" not in uc:
            con.execute("ALTER TABLE users ADD COLUMN token_epoch INTEGER NOT NULL DEFAULT 0")
        # 1.19.3: undo what 1.19.0 and 1.19.1 did to the trailers.
        #
        # Those two sent `Referrer-Policy: no-referrer`, and YouTube partly decides whether an embed may
        # play on the Referer. With none, it refuses with the same error codes a genuinely blocked video
        # gives (101/150). LENTA believed them: it put each key on the sixty-day "doesn't play here"
        # list and cleared the title's trailer to look for a replacement — which then also failed, for
        # the same reason. So a library could lose years' worth of working trailers in an afternoon.
        #
        # Both halves are undone here, once. The skip list is emptied, because every entry it holds was
        # written by a browser that had been told to say nothing about where it came from, and a title
        # left with no trailer has its "looked recently, found nothing" stamp cleared so that it looks
        # again. This is safe to run even for someone who never saw the fault: an emptied skip list
        # simply means those videos are tried once more, and a video that really is blocked goes
        # straight back on it.
        if not con.execute("SELECT 1 FROM settings WHERE key = 'trailer_repair_1193'").fetchone():
            banned = con.execute("SELECT value FROM settings WHERE key = 'yt_unavailable'").fetchone()
            n = 0
            if banned and banned[0]:
                try:
                    n = len(json.loads(banned[0]))
                except ValueError:
                    n = 0
            con.execute("DELETE FROM settings WHERE key = 'yt_unavailable'")
            cleared = con.execute(
                "UPDATE items SET extra = json_set(extra, '$.trailer_checked', 0) "
                "WHERE extra IS NOT NULL AND json_valid(extra) "
                "AND json_extract(extra, '$.trailer') IS NULL "
                "AND COALESCE(json_extract(extra, '$.trailer_checked'), 0) > 0").rowcount
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('trailer_repair_1193', ?)",
                        (str(int(time.time())),))
            if n or cleared:
                log.info("Trailers: cleared %s skipped YouTube video(s) and asked %s title(s) to look "
                         "again — see the Referrer-Policy note in main.py", n, cleared)

        # Renamed from SAVEK: keep a custom server name, but update the old default.
        con.execute("UPDATE settings SET value = 'LENTA' WHERE key = 'server_name' AND value = 'SAVEK'")
        # The Redline theme is called Netflix from 1.3.1: keep everyone's choice working.
        con.execute("UPDATE user_prefs SET value = 'netflix' WHERE key = 'theme' AND value = 'redline'")
        con.execute("UPDATE settings SET value = 'netflix' WHERE key = 'default_theme' AND value = 'redline'")
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
