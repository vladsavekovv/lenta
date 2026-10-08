"""Library scanner: walks media folders, parses names, probes files, builds the item tree.

Movies:  Movie Name (2010)/Movie.Name.2010.1080p.mkv  -> movie
Shows:   Show Name/Season 01/Show.Name.S01E02.mkv     -> show > season > episode
Music:   Artist/Album/01 - Track.flac (tags win)      -> album > track
Photos:  Holidays 2024/IMG_0001.jpg                   -> photoalbum > photo
"""
import base64
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from guessit import guessit

from . import images, logs, metadata, probe
from .config import AUDIO_EXT, FFMPEG, PHOTO_EXT, SUB_EXT, VIDEO_EXT
from .db import db, get_setting, jloads, now, set_setting

log = logs.get("scanner")

SKIP_DIRS = {"@eadir", "#recycle", "$recycle.bin", "lost+found", ".trickplay", "#snapshot", "extras",
             "featurettes", "behind the scenes", "deleted scenes", "trailers"}
POSTER_NAMES = ("poster", "folder", "cover", "movie", "show")
BACKDROP_NAMES = ("fanart", "backdrop", "background", "art")
ALBUM_ART_NAMES = ("cover", "folder", "front", "album", "albumart")
LEAF_KINDS = ("movie", "episode", "track", "photo")

# Every library scans on its own: its own queue, worker thread and progress, so new films in one library
# don't wait for another library's long scan (artwork, metadata). Scans of the same library run one by one.
_IDLE = {"running": False, "library_id": None, "library": None, "phase": "idle",
         "done": 0, "total": 0, "started": None, "finished": None, "message": ""}
scans: dict[int, dict] = {}             # library id -> its scan progress (while it runs, then its last result)
_queues: dict[int, list] = {}           # library id -> [[refresh_metadata, folders or None (= the whole library)], ...]
_workers: dict[int, threading.Thread] = {}
_queue_lock = threading.Lock()
_tls = threading.local()                # the scan running in this thread (its progress dict)


class _State:
    """`state` as the scan code uses it: inside a scan, that library's own progress; elsewhere a summary
    of all scans (running if any library is scanning)."""
    def _cur(self):
        return getattr(_tls, "state", None)

    def update(self, **kw):
        cur = self._cur()
        if cur is not None:
            cur.update(kw)

    def __setitem__(self, key, value):
        cur = self._cur()
        if cur is not None:
            cur[key] = value

    def __getitem__(self, key):
        cur = self._cur()
        return cur[key] if cur is not None else overview()[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return overview().keys()


state = _State()


def running() -> list[dict]:
    """The scans running now, one per library."""
    with _queue_lock:
        return [dict(v) for v in scans.values() if v.get("running")]


def library_status(library_id: int) -> dict | None:
    with _queue_lock:
        cur = scans.get(library_id)
        return dict(cur) if cur else None


def overview() -> dict:
    """A summary of all scans: the first running one (or idle), the last finish time and message."""
    with _queue_lock:
        all_ = [dict(v) for v in scans.values()]
    run = [v for v in all_ if v.get("running")]
    out = dict(run[0]) if run else dict(_IDLE)
    done = [v for v in all_ if v.get("finished")]
    if done:
        last = max(done, key=lambda v: v["finished"])
        out["finished"] = last["finished"]
        if not run:
            out["message"] = last.get("message", "")
    return out


# ---- queue -----------------------------------------------------------------

def _start_worker(library_id: int) -> None:
    # (called with _queue_lock held) The worker removes itself under this lock as it exits, so a request
    # can't be stranded in a queue by arriving just as the worker finishes.
    if library_id not in _workers:
        t = threading.Thread(target=_run_queue, args=(library_id,), name=f"scanner-{library_id}", daemon=True)
        _workers[library_id] = t
        t.start()


def request_scan(library_id: int | None = None, refresh_metadata: bool = False) -> None:
    """Queue one library (or all of them) for scanning in the background. Each library has its own queue."""
    with db() as con:
        ids = [r["id"] for r in con.execute("SELECT id FROM libraries ORDER BY position, id")] \
            if library_id is None else [library_id]
    with _queue_lock:
        for lid in ids:
            q = _queues.setdefault(lid, [])
            if [refresh_metadata, None] not in q:
                q[:] = [e for e in q if e[1] is None]      # a full scan covers the folder scans
                q.append([refresh_metadata, None])
            _start_worker(lid)


def request_scan_folders(library_id: int, folders: list[str]) -> None:
    """Scan only these folders of a library (new or changed files found by the watcher): quick even in a
    big library, and the new titles get their metadata straight away."""
    with _queue_lock:
        q = _queues.setdefault(library_id, [])
        for e in q:
            if e[1] is None:
                break                                              # a full scan of it is queued anyway
            if not e[0]:
                e[1] = sorted(set(e[1]) | set(folders))
                break
        else:
            q.append([False, sorted(set(folders))])
        _start_worker(library_id)


def queued() -> list[int]:
    """Libraries with a scan waiting (behind a scan of the same library)."""
    with _queue_lock:
        return [lid for lid, q in _queues.items() for _ in q]


def _run_queue(library_id: int) -> None:
    while True:
        with _queue_lock:
            q = _queues.get(library_id) or []
            if not q:
                _queues.pop(library_id, None)
                _workers.pop(library_id, None)
                cur = scans.get(library_id)
                if cur:
                    cur.update(running=False, phase="idle", finished=now())
                return
            refresh, folders = q.pop(0)
        try:
            scan_library(library_id, refresh, folders)
        except Exception as exc:  # keep the worker alive
            log.exception("Scan of library %s failed: %s", library_id, exc)


# ---- helpers ---------------------------------------------------------------

def norm(text: str) -> str:
    """Grouping key for a title. Latin letters and digits as before (so existing keys don't change);
    titles with none of those (Cyrillic, Greek...) keep their own letters instead of becoming empty."""
    text = (text or "").lower()
    return re.sub(r"[^a-z0-9]+", "", text) or re.sub(r"[\W_]+", "", text)


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _walk(roots: list[str], extensions: set[str], report: dict | None = None):
    """Video/audio/photo files under the roots. Follows symlinks, but each real folder is visited once,
    so a link pointing back up the tree can't send the scan round in circles."""
    report = report if report is not None else {}
    problems = report.setdefault("problems", [])

    def note(msg: str) -> None:
        log.warning(msg)
        if len(problems) < 20:
            problems.append(msg)

    for root in roots:
        if not os.path.isdir(root):
            note(f"Folder missing or not readable by the server: {root}")
            continue
        if not os.access(root, os.R_OK | os.X_OK):
            note(f"No permission to read {root}")
            continue
        seen: set[tuple[int, int]] = set()
        for dirpath, dirnames, filenames in os.walk(
                root, followlinks=True, onerror=lambda e: note(f"Can't read folder {e.filename}: {e.strerror}")):
            try:
                st = os.stat(dirpath)
                ident = (st.st_dev, st.st_ino)
            except OSError:
                dirnames[:] = []
                continue
            if ident in seen:
                dirnames[:] = []
                continue
            seen.add(ident)
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS]
            for name in filenames:
                if name.startswith("."):
                    continue
                try:
                    name.encode("utf-8")
                except UnicodeEncodeError:
                    report["bad_names"] = report.get("bad_names", 0) + 1
                    if report["bad_names"] == 1:
                        note(f"File names in {dirpath!r} are not UTF-8 and were skipped. Mount the share "
                             "with UTF-8 file names (e.g. iocharset=utf8 for CIFS).")
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in extensions:
                    continue
                path = os.path.join(dirpath, name)
                if ext in VIDEO_EXT and _is_trailer(path):
                    continue                     # Movie-trailer.mp4: the title's trailer, not a title
                if ext in VIDEO_EXT and re.search(r"(^|[\W_])sample([\W_]|$)", name.lower()):
                    try:
                        if os.path.getsize(path) < 300 * 1024 * 1024:
                            continue
                    except OSError:
                        continue
                yield root, path


def _is_trailer(path: str) -> bool:
    from .trailer_sources import is_trailer_file
    return is_trailer_file(path)


def _find_art(directory: str, names: tuple, stem: str | None = None) -> str | None:
    try:
        entries = {e.lower(): e for e in os.listdir(directory)}
    except OSError:
        return None
    candidates = []
    if stem:
        candidates += [f"{stem}-{n}" for n in names]
    candidates += list(names)
    for base in candidates:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            hit = entries.get(f"{base.lower()}{ext}")
            if hit:
                return os.path.join(directory, hit)
    return None


def _external_subs(video_path: str) -> list[dict]:
    """Sidecar subtitles: Movie.srt, Movie.en.srt, Movie.bg.forced.srt ..."""
    directory, filename = os.path.split(video_path)
    stem = os.path.splitext(filename)[0]
    subs = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return subs
    for name in names:
        base, ext = os.path.splitext(name)
        if ext.lower() not in SUB_EXT or not base.startswith(stem):
            continue
        tokens = [t.lower() for t in base[len(stem):].split(".") if t]
        forced = "forced" in tokens
        lang = next((t for t in tokens if t not in ("forced", "sdh", "cc", "default") and 2 <= len(t) <= 3), "und")
        subs.append({"key": f"ext{len(subs)}", "path": os.path.join(directory, name), "codec": ext[1:].lower(),
                     "language": lang, "title": "", "forced": forced, "default": False,
                     "text": True, "image": False, "external": True})
    return subs


def _upsert_item(con, library_id: int, kind: str, key: str, parent_id: int | None, fields: dict) -> tuple[int, bool]:
    row = con.execute("SELECT id FROM items WHERE library_id = ? AND key = ?", (library_id, key)).fetchone()
    if row:
        return row["id"], False
    fields = {**fields, "library_id": library_id, "kind": kind, "key": key, "parent_id": parent_id,
              "added_at": now(), "updated_at": now()}
    if "sort_title" not in fields:
        fields["sort_title"] = metadata.sort_title(fields.get("title", ""))
    cols = ", ".join(fields)
    cur = con.execute(f"INSERT INTO items({cols}) VALUES({', '.join('?' * len(fields))})", list(fields.values()))
    return cur.lastrowid, True


def _insert_file(con, item_id: int, library_id: int, path: str, st: os.stat_result, info: dict) -> None:
    con.execute(
        "INSERT INTO files(item_id, library_id, path, size, mtime, container, duration, bitrate, video_codec, "
        "width, height, pix_fmt, hdr, audio, subtitles, added_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET item_id = excluded.item_id, size = excluded.size, mtime = excluded.mtime, "
        "container = excluded.container, duration = excluded.duration, bitrate = excluded.bitrate, "
        "video_codec = excluded.video_codec, width = excluded.width, height = excluded.height, "
        "pix_fmt = excluded.pix_fmt, hdr = excluded.hdr, audio = excluded.audio, subtitles = excluded.subtitles",
        (item_id, library_id, path, st.st_size, st.st_mtime, info.get("container"), info.get("duration"),
         info.get("bitrate"), info.get("video_codec"), info.get("width"), info.get("height"), info.get("pix_fmt"),
         info.get("hdr", 0), json.dumps(info.get("audio", [])), json.dumps(info.get("subtitles", [])), now()))


# ---- movie names ---------------------------------------------------------------
#
# A film's name can be on the file, its folder, or a folder above. Collection folders ("The Hunger
# Games Collection (2012 - 2015)", "Poltergeist Trilogy 1982,1986,1988") name several films and
# must never name one; junk file names ("wanoes.avi") defer to their folder.

PARSER_VERSION = "3"
_COLLECTION = re.compile(r"\b(collection|trilogy|duology|quadrilogy|quadrology|pentalogy|hexalogy|anthology|saga|"
                         r"box\s?set|boxset|complete|filmography|pack|series|колекция|трилогия)\b|"
                         r"(19|20)\d\d\s*[-–,&]\s*(19|20)\d\d", re.I)
_GENERIC = re.compile(r"^(cd|dis[ck]|part|pt|side|video_ts|bdmv|stream|movie|movies|film|films|sample|"
                      r"main|feature|title|vts|\d+)[\s._-]*\d*([\s._-]+\d+)*$", re.I)


def _name_from(text: str) -> tuple[str | None, int | None, bool]:
    """(title, year, usable) for one file or folder name."""
    g = guessit(text, {"type": "movie"})
    title = _first(g.get("title"))
    alt = g.get("alternative_title")
    alt = [alt] if isinstance(alt, str) else list(alt or [])
    # "The Lord of the Rings - The Two Towers - 2002": a subtitle before the year belongs to the title.
    # Stray tags after the year ("Ghostbusters 1984.J.1080p") don't.
    year_at = re.search(r"(19|20)\d\d", text)
    alt = [a for a in map(str, alt) if len(re.sub(r"\W", "", a)) >= 3
           and (year_at is None or 0 <= text.lower().find(a.lower()[:12]) < year_at.start())]
    if title and alt:
        title = " ".join([str(title), *alt])
    part = _first(g.get("part"))
    if title and part and not re.search(rf"\bpart\s*{part}\b", str(title), re.I):
        title = f"{title} Part {part}"      # "The Hunger Games Mockingjay" + part 1
    volume = _first(g.get("volume"))
    if title and volume and not re.search(r"\bvol", str(title), re.I):
        title = f"{title} Vol {volume}"     # "Kill Bill" + volume 1
    year = g.get("year")
    several_years = isinstance(year, list) and len(set(year)) > 1
    year = _first(year)
    title = str(title).strip() if title else None
    letters = len(re.sub(r"\W", "", title or ""))       # Cyrillic counts too
    usable = bool(title) and (letters >= 3 or (letters >= 1 and year is not None)) and not several_years \
        and not _COLLECTION.search(text) and not _GENERIC.match(text.strip())
    # A lone made-up word with no year ("wanoes", "xvid-abc") is a release name, not a title.
    if usable and year is None and not re.search(r"[\s._-]", text.strip()) and len(text) <= 12:
        usable = False
    return title, year, usable


def parse_movie(root: str, path: str) -> tuple[str, int | None, list]:
    """Best (title, year) for a movie file, plus other plausible names to search with."""
    rel = Path(os.path.relpath(path, root))
    names = [rel.stem] + [p for p in reversed(rel.parts[:-1])]
    good, fallback = [], None
    for n in names:
        title, year, usable = _name_from(n)
        if usable:
            good.append((title, year))
        elif fallback is None and title:
            fallback = (title, year)
    if not good:
        title, year = fallback or (Path(path).stem, None)
        return title, year, []
    best = next((g for g in good if g[1]), good[0])      # nearest name that carries a year
    if best[1] is None:                                  # borrow a single year from a folder
        best = (best[0], next((g[1] for g in good if g[1]), None))
    alts = [list(g) for g in good if g != best][:3]
    return best[0], best[1], alts


# ---- per-kind handlers -------------------------------------------------------

def _add_movie(lib: dict, root: str, path: str, st) -> None:
    title, year, alts = parse_movie(root, path)
    info = probe.summarize(path)
    if not info:
        log.warning("Unreadable video, skipped: %s", path)
        return
    info["subtitles"] = info.get("subtitles", []) + _external_subs(path)
    directory = os.path.dirname(path)
    in_own_folder = os.path.normpath(directory) != os.path.normpath(root)
    stem = Path(path).stem
    poster = _find_art(directory, POSTER_NAMES if in_own_folder else (), stem)
    backdrop = _find_art(directory, BACKDROP_NAMES if in_own_folder else (), stem)
    with db() as con:
        item_id, created = _upsert_item(con, lib["id"], "movie", f"movie:{norm(title)}:{year or ''}", None, {
            "title": title, "year": year,
            "runtime": round(info["duration"] / 60) if info.get("duration") else None,
            "extra": json.dumps({"parsed_title": title, "parsed_year": year, "parsed_alt": alts}),
        })
        if created:
            updates = {}
            if poster:
                updates["poster"] = images.save_local_file(poster)
            if backdrop:
                updates["backdrop"] = images.save_local_file(backdrop)
            for k, v in updates.items():
                con.execute(f"UPDATE items SET {k} = ? WHERE id = ?", (v, item_id))
        _insert_file(con, item_id, lib["id"], path, st, info)


_SEASON_DIR = re.compile(r"(?:season|series|staffel|saison|sezon|сезон)[\s._-]*(\d{1,3})", re.I)
_EP_FALLBACK = re.compile(r"(?:^|[^a-z])(?:e|ep|episode)[\s._-]*(\d{1,4})", re.I)


def _add_episode(lib: dict, root: str, path: str, st) -> None:
    rel = os.path.relpath(path, root)
    parts = Path(rel).parts
    g = guessit(rel, {"type": "episode"})
    if len(parts) > 1:
        gs = guessit(parts[0], {"type": "episode"})
        show_title = str(_first(gs.get("title")) or parts[0])
        show_year = _first(gs.get("year"))
    else:
        show_title = str(_first(g.get("title")) or Path(path).stem)
        show_year = _first(g.get("year"))

    season = _first(g.get("season"))
    if season is None:
        for part in parts[:-1]:
            m = _SEASON_DIR.search(part)
            if m:
                season = int(m.group(1))
                break
            if part.lower() in ("specials", "special"):
                season = 0
                break
    season = 1 if season is None else int(season)
    episode = _first(g.get("episode"))
    if episode is None:
        m = _EP_FALLBACK.search(Path(path).stem)
        episode = int(m.group(1)) if m else None
    ep_title = _first(g.get("episode_title"))

    info = probe.summarize(path)
    if not info:
        log.warning("Unreadable video, skipped: %s", path)
        return
    info["subtitles"] = info.get("subtitles", []) + _external_subs(path)

    with db() as con:
        show_id, show_new = _upsert_item(con, lib["id"], "show", f"show:{norm(show_title)}", None, {
            "title": show_title, "year": show_year,
            "extra": json.dumps({"parsed_title": show_title, "parsed_year": show_year}),
        })
        if show_new and len(parts) > 1:
            show_dir = os.path.join(root, parts[0])
            for field, names in (("poster", POSTER_NAMES), ("backdrop", BACKDROP_NAMES)):
                art = _find_art(show_dir, names)
                if art:
                    con.execute(f"UPDATE items SET {field} = ? WHERE id = ?", (images.save_local_file(art), show_id))
        season_title = "Specials" if season == 0 else f"Season {season}"
        season_id, _ = _upsert_item(con, lib["id"], "season", f"season:{show_id}:{season}", show_id, {
            "title": season_title, "parent_index": season, "sort_title": f"{season:04d}",
        })
        key = f"ep:{show_id}:{season}:{episode}" if episode is not None else f"ep:{show_id}:{season}:{path}"
        title = str(ep_title) if ep_title else (f"Episode {episode}" if episode is not None else Path(path).stem)
        ep_id, _ = _upsert_item(con, lib["id"], "episode", key, season_id, {
            "title": title, "index_number": episode, "parent_index": season,
            "sort_title": f"{season:04d}-{episode or 0:05d}",
            "runtime": round(info["duration"] / 60) if info.get("duration") else None,
        })
        _insert_file(con, ep_id, lib["id"], path, st, info)


_AUDIO_CODECS = {".mp3": "mp3", ".flac": "flac", ".m4a": "aac", ".aac": "aac", ".ogg": "vorbis", ".oga": "vorbis",
                 ".opus": "opus", ".wav": "pcm", ".wma": "wma", ".ape": "ape", ".aiff": "pcm", ".aif": "pcm",
                 ".alac": "alac", ".wv": "wavpack"}


def _embedded_cover(path: str) -> bytes | None:
    try:
        import mutagen
        from mutagen.flac import Picture
        f = mutagen.File(path)
        if f is None:
            return None
        if getattr(f, "pictures", None):
            return f.pictures[0].data
        tags = f.tags
        if tags is None:
            return None
        if "covr" in tags:
            return bytes(tags["covr"][0])
        for key in list(tags.keys()):
            if str(key).startswith("APIC"):
                return tags[key].data
        if "metadata_block_picture" in tags:
            return Picture(base64.b64decode(tags["metadata_block_picture"][0])).data
    except Exception:
        return None
    return None


def _add_track(lib: dict, root: str, path: str, st) -> None:
    import mutagen
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    tags = (f.tags if f is not None and f.tags is not None else {}) or {}

    def tag(name):
        try:
            value = tags.get(name)
        except Exception:
            return None
        return str(value[0]).strip() if value else None

    parts = Path(os.path.relpath(path, root)).parts
    folder_album = parts[-2] if len(parts) >= 2 else None
    folder_artist = parts[-3] if len(parts) >= 3 else None
    artist = tag("albumartist") or tag("artist") or folder_artist or "Unknown Artist"
    album = tag("album") or folder_album or "Unknown Album"
    title = tag("title") or re.sub(r"^\d+[\s._-]+", "", Path(path).stem)
    try:
        track_no = int((tag("tracknumber") or "").split("/")[0])
    except ValueError:
        m = re.match(r"^(\d{1,3})", Path(path).stem)
        track_no = int(m.group(1)) if m else None
    try:
        disc = int((tag("discnumber") or "1").split("/")[0])
    except ValueError:
        disc = 1
    year = None
    if tag("date"):
        m = re.match(r"(\d{4})", tag("date"))
        year = int(m.group(1)) if m else None
    genre = tag("genre")
    duration = float(getattr(getattr(f, "info", None), "length", 0) or 0)
    ext = Path(path).suffix.lower()
    codec = _AUDIO_CODECS.get(ext, ext[1:])
    if ext == ".m4a" and "alac" in str(getattr(getattr(f, "info", None), "codec", "")):
        codec = "alac"
    info = {"container": ext[1:], "duration": duration,
            "bitrate": int(getattr(getattr(f, "info", None), "bitrate", 0) or 0),
            "audio": [{"index": 0, "codec": codec, "channels": getattr(getattr(f, "info", None), "channels", 2)}],
            "subtitles": []}

    with db() as con:
        album_id, album_new = _upsert_item(con, lib["id"], "album", f"album:{norm(artist)}:{norm(album)}", None, {
            "title": album, "artist": artist, "year": year,
            "genres": json.dumps([genre] if genre else []),
        })
        row = con.execute("SELECT poster FROM items WHERE id = ?", (album_id,)).fetchone()
        if not row["poster"]:
            art = _find_art(os.path.dirname(path), ALBUM_ART_NAMES)
            ref = images.save_local_file(art) if art else None
            if not ref:
                data = _embedded_cover(path)
                ref = images.save_local(data, ".jpg") if data else None
            if ref:
                con.execute("UPDATE items SET poster = ? WHERE id = ?", (ref, album_id))
        if year and album_new is False:
            con.execute("UPDATE items SET year = COALESCE(year, ?) WHERE id = ?", (year, album_id))
        track_id, _ = _upsert_item(con, lib["id"], "track", f"track:{path}", album_id, {
            "title": title, "artist": tag("artist") or artist, "index_number": track_no, "parent_index": disc,
            "sort_title": f"{disc:03d}-{track_no or 0:04d}", "year": year,
            "runtime": round(duration / 60) if duration else None,
        })
        _insert_file(con, track_id, lib["id"], path, st, info)


def _exif(path: str) -> dict:
    from PIL import Image
    out = {}
    try:
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            exif = im.getexif()
            sub = exif.get_ifd(0x8769) if exif else {}
            taken = sub.get(36867) or exif.get(306)
            if taken:
                out["taken"] = str(taken).replace(":", "-", 2)
            model = exif.get(272)
            if model:
                out["camera"] = str(model).strip()
            if exif.get(274) in (5, 6, 7, 8):  # rotated 90°: swap reported size
                out["width"], out["height"] = out["height"], out["width"]
    except Exception:
        pass
    return out


def _add_photo(lib: dict, root: str, path: str, st) -> None:
    rel_dir = os.path.dirname(os.path.relpath(path, root))
    album_title = rel_dir.replace(os.sep, " / ") if rel_dir else lib["name"]
    meta = _exif(path)
    taken = meta.get("taken") or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
    with db() as con:
        album_id, _ = _upsert_item(con, lib["id"], "photoalbum", f"photoalbum:{root}:{rel_dir}", None,
                                   {"title": album_title})
        photo_id, _ = _upsert_item(con, lib["id"], "photo", f"photo:{path}", album_id, {
            "title": Path(path).stem, "year": int(taken[:4]) if taken[:4].isdigit() else None,
            "release_date": taken, "sort_title": taken, "extra": json.dumps(meta),
        })
        _insert_file(con, photo_id, lib["id"], path, st,
                     {"container": Path(path).suffix[1:].lower(), "width": meta.get("width"),
                      "height": meta.get("height")})
        # Albums sort by their newest photo.
        con.execute("UPDATE items SET year = (SELECT MAX(year) FROM items WHERE parent_id = ?), "
                    "release_date = (SELECT MAX(release_date) FROM items WHERE parent_id = ?) WHERE id = ?",
                    (album_id, album_id, album_id))


HANDLERS = {
    "movies": (VIDEO_EXT, _add_movie),
    "shows": (VIDEO_EXT, _add_episode),
    "music": (AUDIO_EXT, _add_track),
    "photos": (PHOTO_EXT, _add_photo),
}


# ---- frame grabs for items without art ------------------------------------

def _grab_frame(path: str, at: float, hdr: bool) -> bytes | None:
    vf = "scale=1280:-2"
    if hdr:
        vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
              "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=1280:-2")
    from .transcode import hw
    accel = ["-hwaccel", "cuda"] if hw.get("accel") == "nvenc" else []   # falls back to CPU decode by itself
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", *accel, "-ss", f"{at:.2f}", "-i", path,
           "-frames:v", "1", "-vf", vf, "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60)
        if out.returncode != 0 and hdr:
            return _grab_frame(path, at, False)
        return out.stdout or None
    except (subprocess.SubprocessError, OSError):
        return None


def _fill_frames(library_id: int) -> None:
    from . import artwork
    with db() as con:
        kind = con.execute("SELECT kind FROM libraries WHERE id = ?", (library_id,)).fetchone()
    if kind and not artwork.enabled("show" if kind["kind"] == "shows" else "movie", "screen"):
        return                                   # Screen grabber switched off (Artwork sources)
    with db() as con:
        rows = con.execute(
            "SELECT i.id, f.path, f.duration, f.hdr, i.kind FROM items i JOIN files f ON f.item_id = i.id "
            "WHERE i.library_id = ? AND i.kind IN ('movie','episode') AND i.thumb IS NULL "
            "AND f.video_codec IS NOT NULL GROUP BY i.id", (library_id,)).fetchall()
    state.update(phase="artwork", done=0, total=len(rows))
    for n, row in enumerate(rows, 1):
        duration = row["duration"] or 0
        at = duration * (0.12 if row["kind"] == "movie" else 0.25) if duration else 5
        data = _grab_frame(row["path"], min(at, max(duration - 1, 0)) if duration else 5, bool(row["hdr"]))
        if data:
            try:
                with db() as con:
                    con.execute("UPDATE items SET thumb = ? WHERE id = ?", (images.save_local(data, ".jpg"), row["id"]))
            except OSError as exc:
                log.warning("Could not save a frame for item %s: %s", row["id"], exc)
        state["done"] = n
    with db() as con:  # shows without art borrow a frame from their first episode
        con.execute("UPDATE items SET thumb = (SELECT e.thumb FROM items se JOIN items e ON e.parent_id = se.id "
                    "WHERE se.parent_id = items.id AND e.thumb IS NOT NULL "
                    "ORDER BY (se.parent_index = 0), se.parent_index, e.index_number LIMIT 1) "
                    "WHERE library_id = ? AND kind = 'show' AND thumb IS NULL", (library_id,))


# ---- main scan ---------------------------------------------------------------

def _cleanup(con, library_id: int) -> int:
    removed = 0
    for kind in LEAF_KINDS:
        removed += con.execute(
            "DELETE FROM items WHERE library_id = ? AND kind = ? AND id NOT IN (SELECT item_id FROM files)",
            (library_id, kind)).rowcount
    for kind in ("season", "show", "album", "photoalbum"):  # order matters: seasons before shows
        con.execute("DELETE FROM items WHERE library_id = ? AND kind = ? AND id NOT IN "
                    "(SELECT parent_id FROM items WHERE parent_id IS NOT NULL)", (library_id, kind))
    return removed


def scan_library(library_id: int, refresh_metadata: bool = False, folders: list[str] | None = None) -> None:
    with db() as con:
        row = con.execute("SELECT * FROM libraries WHERE id = ?", (library_id,)).fetchone()
    if not row:
        return
    lib = dict(row)
    roots = jloads(lib["paths"])
    extensions, handler = HANDLERS[lib["kind"]]
    started = time.time()
    with _queue_lock:
        _tls.state = scans.setdefault(library_id, dict(_IDLE))     # this thread's progress (see _State)
    state.update(running=True, library_id=library_id, library=lib["name"], phase="discovering",
                 done=0, total=0, started=started, message="")
    if folders:
        log.info("Scanning new or changed folders in '%s': %s", lib["name"], ", ".join(folders))
    else:
        log.info("Scanning library '%s' (%s)", lib["name"], ", ".join(roots))

    report: dict = {"started": started}
    try:
        _scan(lib, roots, extensions, handler, refresh_metadata, report, folders)
        report["ok"] = True
    except Exception as exc:
        log.exception("Scan of library '%s' failed", lib["name"])
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        state["message"] = f"{lib['name']}: scan failed — {exc}"
    finally:
        report["finished"] = now()
        if not folders:                       # a folder scan doesn't replace the library's scan report
            with db() as con:
                con.execute("UPDATE libraries SET last_scan = ?, scan_report = ? WHERE id = ?",
                            (now(), json.dumps(report), library_id))


def _reparse_movies(lib: dict, roots: list) -> int:
    """Re-read every movie name with the current rules. Films that an older version filed under a
    collection folder's name (several films merged into one entry) are split into their own entries;
    entries whose name changed are renamed in place (keeping watch history) and matched again.
    Titles fixed by hand (locked) are left alone. Returns the number of entries changed."""
    from .metadata import _FIELDS
    changed = 0
    with db() as con:
        rows = con.execute("SELECT f.id AS fid, f.path, i.id AS iid, i.key, i.match_locked, i.extra FROM files f "
                           "JOIN items i ON i.id = f.item_id WHERE i.library_id = ? AND i.kind = 'movie'",
                           (lib["id"],)).fetchall()
    by_item: dict[int, list] = {}
    for r in rows:
        if r["match_locked"] or "title" in metadata.locked_fields(r["extra"]):   # fixed or renamed by hand
            continue
        root = next((x for x in roots if r["path"].startswith(os.path.join(x, ""))), os.path.dirname(r["path"]))
        title, year, alts = parse_movie(root, r["path"])
        by_item.setdefault(r["iid"], []).append((r, f"movie:{norm(title)}:{year or ''}", title, year, alts))
    for iid, entries in by_item.items():
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e[1], []).append(e)
        current = entries[0][0]["key"]
        ordered = sorted(groups.items(), key=lambda kv: (kv[0] != current, -len(kv[1])))
        with db() as con:
            for n, (key, members) in enumerate(ordered):
                _, _, title, year, alts = members[0]
                extra = json.dumps({"parsed_title": title, "parsed_year": year, "parsed_alt": alts})
                if n == 0 and key == current:
                    con.execute("UPDATE items SET extra = json_patch(COALESCE(extra, '{}'), ?) WHERE id = ?",
                                (extra, iid))
                    continue
                other = con.execute("SELECT id FROM items WHERE library_id = ? AND key = ? AND id != ?",
                                    (lib["id"], key, iid)).fetchone()
                if n == 0 and not other:
                    # Same entry, better name: rename in place and forget metadata found for the old name.
                    resets = ", ".join(f"{f} = CASE WHEN {f} LIKE 'tmdb:%' THEN NULL ELSE {f} END"
                                       if f in ("poster", "backdrop") else f"{f} = NULL"
                                       for f in _FIELDS if f not in ("title", "year", "runtime"))
                    con.execute(f"UPDATE items SET key = ?, title = ?, year = ?, sort_title = ?, extra = ?, "
                                f"{resets}, genres = NULL, tmdb_id = NULL, matched = 0, updated_at = ? WHERE id = ?",
                                (key, title, year, metadata.sort_title(title), extra, now(), iid))
                    changed += 1
                    continue
                target = other["id"] if other else _upsert_item(con, lib["id"], "movie", key, None, {
                    "title": title, "year": year, "extra": extra})[0]
                con.executemany("UPDATE files SET item_id = ? WHERE id = ?", [(target, m[0]["fid"]) for m in members])
                changed += 1
    if changed:
        log.info("Re-read movie names in '%s': %d entries renamed or split", lib["name"], changed)
    return changed


def _under(path: str, folders: list[str]) -> bool:
    return any(path == f or path.startswith(os.path.join(f, "")) for f in folders)


def _scan(lib: dict, roots: list, extensions: set, handler, refresh_metadata: bool, report: dict,
          folders: list[str] | None = None) -> None:
    library_id = lib["id"]
    started = report["started"]
    version_key = f"parser_version_{library_id}"
    if not folders and lib["kind"] == "movies" and get_setting(version_key) != PARSER_VERSION:
        state.update(phase="re-reading names")
        report["renamed"] = _reparse_movies(lib, roots)
        set_setting(version_key, PARSER_VERSION)
    if folders:
        # only these folders; names are still read relative to the library folder they are in
        found = []
        for f in folders:
            root = next((r for r in roots if _under(f, [os.path.normpath(r)])), None)
            if root and os.path.isdir(f):
                found += [(root, p) for _, p in _walk([f], extensions, {})]
    else:
        found = list(_walk(roots, extensions, report))
    report["found"] = len(found)
    with db() as con:
        known = {r["path"]: (r["size"], r["mtime"]) for r in
                 con.execute("SELECT path, size, mtime FROM files WHERE library_id = ?", (library_id,))
                 if not folders or _under(r["path"], folders)}
    present = {p for _, p in found}
    gone = [p for p in known if p not in present]
    # A library folder that vanished (unmounted NAS share) must not wipe the library.
    if roots and not any(os.path.isdir(r) for r in roots):
        log.warning("No library folders reachable for '%s'; skipping removal of %d files", lib["name"], len(gone))
        gone = []

    todo = []
    for root, path in found:
        try:
            st = os.stat(path)
        except OSError:
            continue
        old = known.get(path)
        if old is None or old[0] != st.st_size or abs((old[1] or 0) - st.st_mtime) > 1:
            todo.append((root, path, st))

    state.update(phase="reading files", total=len(todo), done=0)
    added = failed = 0
    for n, (root, path, st) in enumerate(todo, 1):
        try:
            handler(lib, root, path, st)
            added += 1
        except Exception as exc:
            failed += 1
            log.warning("Failed to add %s: %s", path, exc)
            if failed <= 5:
                report.setdefault("problems", []).append(f"Couldn't add {os.path.basename(path)}: {exc}")
        state["done"] = n
    report.update(added=added, failed=failed)

    with db() as con:
        for path in gone:
            con.execute("DELETE FROM files WHERE path = ?", (path,))
        removed = _cleanup(con, library_id)

    if lib["kind"] in ("movies", "shows"):
        tmdb = metadata.TMDB()
        if tmdb.enabled:
            kind = "movie" if lib["kind"] == "movies" else "show"
            cond = "match_locked = 0" if refresh_metadata else "matched = 0 AND match_locked = 0"
            with db() as con:
                pending = [r["id"] for r in con.execute(
                    f"SELECT id FROM items WHERE library_id = ? AND kind = ? AND {cond} ORDER BY added_at DESC",
                    (library_id, kind))]
                if folders:                   # a folder scan: only the titles in those folders
                    rows = con.execute(
                        "SELECT COALESCE(sh.id, i.id) AS id, f.path FROM files f JOIN items i ON i.id = f.item_id "
                        "LEFT JOIN items se ON se.id = i.parent_id AND i.kind = 'episode' "
                        "LEFT JOIN items sh ON sh.id = se.parent_id WHERE f.library_id = ?", (library_id,)).fetchall()
                    mine = {r["id"] for r in rows if _under(r["path"], folders)}
                    pending = [i for i in pending if i in mine]
            state.update(phase="fetching metadata", total=len(pending), done=0)
            for n, item_id in enumerate(pending, 1):
                try:
                    metadata.match_item(item_id, tmdb)
                except Exception as exc:   # one bad title must not stop the rest
                    log.warning("Metadata for item %s failed: %s", item_id, exc)
                state["done"] = n
        from . import trailers
        trailers.request_index()          # newly matched titles get their trailers looked up
        from . import trickplay
        trickplay.request()               # seek-bar thumbnails for new videos
        from . import intros
        intros.request()                  # intros of new episodes
        try:
            _fill_frames(library_id)
        except Exception as exc:
            log.warning("Artwork from video frames failed: %s", exc)

    report["removed"] = removed
    state["message"] = f"{lib['name']}: {added} added or updated, {removed} removed"
    log.info("Scan of '%s' done in %.1fs: %d added/updated, %d removed", lib["name"],
             time.time() - started, added, removed)
