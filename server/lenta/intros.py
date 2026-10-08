"""Intro detection for TV shows (like Jellyfin's Intro Skipper).

Episodes of a season share their opening: the same theme tune, at the same length. LENTA reads the audio of the
first part of every episode (at most 10 minutes), turns it into a fingerprint (one 32-bit number per ~0.1 s,
the Haitsma-Kalker method behind most audio fingerprinting) and compares each episode with others of the same
season. The longest stretch of sound two episodes have in common, 15 seconds to 2.5 minutes long, is the intro.

Runs in the background, one season at a time at low priority; only the first minutes of each file are read.
Results are kept in the intros table (also "no intro found", so nothing is analysed twice); fingerprints are
cached in <data folder>/intros/ so a new episode only needs its own audio read.
"""
import subprocess
import shutil
import threading
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from . import auth, logs
from .config import DATA_DIR, FFMPEG
from .db import db, get_setting

log = logs.get("intros")
router = APIRouter(prefix="/api")
CACHE = DATA_DIR / "intros"
VERSION = 1
RATE = 11025
WIN, HOP = 4096, 1024                 # 0.37 s windows every 0.093 s
FPS = RATE / HOP
MAX_SCAN = 600                        # seconds read from the start of an episode
MIN_INTRO, MAX_INTRO = 15, 150        # seconds
MAX_BITS = 9                          # frames whose fingerprints differ in more bits than this don't match
GAP = int(1.5 * FPS)                  # short glitches inside a match are allowed

state = {"running": False, "done": 0, "total": 0, "current": None}
_lock = threading.Lock()
_wake = threading.Event()
_priority: list[int] = []             # season ids
_started = False

SCHEMA = """CREATE TABLE IF NOT EXISTS intros (
    file_id  INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    start    REAL,                    -- NULL: checked, no intro found
    end      REAL,
    source   TEXT,                    -- 'audio' (detected) or 'manual'
    sig      TEXT,
    checked  REAL
)"""


def enabled() -> bool:
    return (get_setting("intro_detection") or "1") != "0"


def _sig(f: dict) -> str:
    return f"{f.get('size')}:{int(f.get('mtime') or 0)}:{VERSION}"


# ---- fingerprints ---------------------------------------------------------------------------------

_bands = None


def _band_matrix() -> np.ndarray:
    """33 log-spaced bands from 300 Hz to 3 kHz over the FFT bins."""
    global _bands
    if _bands is None:
        freqs = np.fft.rfftfreq(WIN, 1 / RATE)
        edges = np.geomspace(300, 3000, 34)
        m = np.zeros((33, len(freqs)), dtype=np.float32)
        for b in range(33):
            m[b, (freqs >= edges[b]) & (freqs < edges[b + 1])] = 1
        _bands = m
    return _bands


def fingerprint_pcm(pcm: np.ndarray) -> np.ndarray:
    """uint32 per frame: bit b = sign of the change, over time, of the energy difference of bands b and b+1."""
    if len(pcm) < WIN * 2:
        return np.zeros(0, dtype=np.uint32)
    n = 1 + (len(pcm) - WIN) // HOP
    window = np.hanning(WIN).astype(np.float32)
    bands = _band_matrix().T
    parts = []
    for c in range(0, n, 512):                    # in chunks: a 10-minute clip would need ~300 MB at once
        idx = np.arange(WIN)[None, :] + HOP * np.arange(c, min(n, c + 512))[:, None]
        spec = np.fft.rfft(pcm[idx] * window, axis=1)
        parts.append(((spec.real ** 2 + spec.imag ** 2).astype(np.float32)) @ bands)
    energy = np.concatenate(parts)                                                                # n × 33
    diff = energy[:, :-1] - energy[:, 1:]                                                         # n × 32
    bits = (diff[1:] - diff[:-1]) > 0                                                             # (n-1) × 32
    weights = (1 << np.arange(32, dtype=np.uint64)).astype(np.uint64)
    return (bits.astype(np.uint64) * weights).sum(axis=1).astype(np.uint32)


def _read_audio(path: str, seconds: float) -> np.ndarray | None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-t", f"{seconds:.0f}", "-i", path,
           "-map", "0:a:0", "-vn", "-sn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []
    ionice = ["ionice", "-c", "3"] if shutil.which("ionice") else []
    try:
        r = subprocess.run(ionice + nice + cmd, capture_output=True, timeout=900)
    except (subprocess.SubprocessError, OSError) as exc:
        log.info("Reading audio of %s failed: %s", path, exc)
        return None
    if r.returncode != 0 or len(r.stdout) < RATE * 2 * 30:
        return None
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768


def fingerprint(f: dict) -> np.ndarray | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{f['id']}.npz"
    sig = _sig(f)
    try:
        with np.load(p) as d:
            if str(d["sig"]) == sig:
                return d["fp"]
    except (OSError, KeyError, ValueError):
        pass
    seconds = min(MAX_SCAN, max(60.0, (f.get("duration") or MAX_SCAN) * 0.4))
    pcm = _read_audio(f["path"], seconds)
    if pcm is None:
        return None
    fp = fingerprint_pcm(pcm)
    np.savez_compressed(p, fp=fp, sig=np.array(sig))
    return fp


# ---- comparing two episodes -----------------------------------------------------------------------

def _popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    x = x.astype(np.uint32)
    x = x - ((x >> 1) & 0x55555555)
    x = (x & 0x33333333) + ((x >> 2) & 0x33333333)
    return (((x + (x >> 4)) & 0x0F0F0F0F) * 0x01010101) >> 24


def _longest_run(good: np.ndarray) -> tuple[int, int]:
    """Longest stretch of True, letting gaps of up to GAP frames through. (start, end) frame indexes."""
    best, start, last = (0, 0), None, None
    for i in np.flatnonzero(good):
        if start is None or i - last > GAP:
            start = i
        last = i
        if last - start > best[1] - best[0]:
            best = (start, last + 1)
    return best


def compare(a: np.ndarray, b: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """The longest stretch of shared audio: ((start, end) in a, (start, end) in b) in seconds, or None."""
    if len(a) < MIN_INTRO * FPS or len(b) < MIN_INTRO * FPS:
        return None
    # Score every alignment of the two clips by how many frames nearly match (unrelated audio differs in about
    # 16 of 32 bits, the same music in about 8), then look closely at the best few.
    lo, hi = -(len(b) - int(MIN_INTRO * FPS)), len(a) - int(MIN_INTRO * FPS)
    scores = np.zeros(hi - lo, dtype=np.int32)
    for off in range(lo, hi):
        a0, b0 = max(0, off), max(0, -off)
        n = min(len(a) - a0, len(b) - b0)
        scores[off - lo] = np.count_nonzero(_popcount(a[a0:a0 + n] ^ b[b0:b0 + n]) <= MAX_BITS)
    vals = np.argsort(scores)[::-1][:8] + lo
    best = None
    for off in vals:
        a0, b0 = max(0, off), max(0, -off)
        n = min(len(a) - a0, len(b) - b0)
        if n < MIN_INTRO * FPS:
            continue
        dist = _popcount(a[a0:a0 + n] ^ b[b0:b0 + n])
        good = dist <= MAX_BITS
        s, e = _longest_run(good)
        if (e - s) / FPS < MIN_INTRO or (e - s) / FPS > MAX_INTRO:
            continue
        if good[s:e].mean() < 0.5:        # mostly noise bridged by the gap rule
            continue
        if best is None or e - s > best[1] - best[0]:
            best = (s, e, a0, b0)
    if not best:
        return None
    s, e, a0, b0 = best
    hop = HOP / RATE
    sec = lambda frame: round(frame * hop + WIN / RATE / 2, 2)
    return (sec(a0 + s), sec(a0 + e)), (sec(b0 + s), sec(b0 + e))


# ---- seasons ----------------------------------------------------------------------------------------

def _season_files(season_id: int) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute(
            "SELECT f.id, f.path, f.size, f.mtime, f.duration, e.index_number FROM files f JOIN items e ON e.id = f.item_id "
            "WHERE e.kind = 'episode' AND e.parent_id = ? ORDER BY e.index_number, f.id", (season_id,))]


def _pending_seasons() -> list[int]:
    with db() as con:
        rows = con.execute(
            "SELECT e.parent_id AS season, f.id, f.size, f.mtime, i.sig, i.source FROM files f "
            "JOIN items e ON e.id = f.item_id LEFT JOIN intros i ON i.file_id = f.id "
            "WHERE e.kind = 'episode' AND e.parent_id IS NOT NULL ORDER BY e.added_at DESC").fetchall()
    out = []
    for r in rows:
        if r["source"] == "manual":
            continue
        if r["sig"] != _sig(dict(r)) and r["season"] not in out:
            out.append(r["season"])
    return out


def _save(file_id: int, sig: str, span: tuple[float, float] | None) -> None:
    with db() as con:
        if con.execute("SELECT 1 FROM intros WHERE file_id = ? AND source = 'manual'", (file_id,)).fetchone():
            return
        con.execute("INSERT OR REPLACE INTO intros(file_id, start, end, source, sig, checked) VALUES(?, ?, ?, 'audio', ?, ?)",
                    (file_id, float(span[0]) if span else None, float(span[1]) if span else None, sig, time.time()))


def analyse_season(season_id: int) -> int:
    """Detect the intro of every episode of a season. Returns how many episodes got one."""
    files = _season_files(season_id)
    if len(files) < 2:
        for f in files:
            _save(f["id"], _sig(f), None)
        return 0
    fps = {}
    for f in files:
        fp = fingerprint(f)
        if fp is not None and len(fp):
            fps[f["id"]] = fp
    ids = [f["id"] for f in files if f["id"] in fps]
    found: dict[int, tuple[float, float]] = {}
    done = set()

    def keep(fid, span):                      # each episode keeps the longest match it takes part in
        if fid not in found or span[1] - span[0] > found[fid][1] - found[fid][0]:
            found[fid] = span

    for n, fid in enumerate(ids):
        # the next episodes first (same intro most likely), then the ones before; each pair once
        for other in ids[n + 1:n + 4] + ids[max(0, n - 3):n]:
            pair = frozenset((fid, other))
            if pair in done:
                continue
            done.add(pair)
            r = compare(fps[fid], fps[other])
            if r:
                keep(fid, r[0])
                keep(other, r[1])
    for f in files:
        _save(f["id"], _sig(f), found.get(f["id"]))
    return sum(1 for f in files if f["id"] in found)


def _clean() -> None:
    if not CACHE.exists():
        return
    with db() as con:
        ids = {str(r[0]) for r in con.execute("SELECT id FROM files")}
    for p in CACHE.glob("*.npz"):
        if p.stem not in ids:
            p.unlink(missing_ok=True)


def _loop() -> None:
    while True:
        _wake.wait(timeout=6 * 3600)
        _wake.clear()
        if not enabled():
            continue
        try:
            _clean()
            todo = _pending_seasons()
            state.update(running=True, done=0, total=len(todo))
            if todo:
                log.info("Looking for intros in %d seasons", len(todo))
            found = 0
            while todo and enabled():
                with _lock:
                    first = next((s for s in _priority if s in todo), None)
                    _priority[:] = [s for s in _priority if s in todo and s != first]
                if first is None:
                    from .transcode import busy
                    if busy():                                 # someone is watching: wait
                        state["paused"] = True
                        time.sleep(20)
                        continue
                    state["paused"] = False
                season = first if first is not None else todo[0]
                todo.remove(season)
                with db() as con:
                    row = con.execute("SELECT sh.title, se.parent_index, se.title AS st FROM items se "
                                      "LEFT JOIN items sh ON sh.id = se.parent_id WHERE se.id = ?", (season,)).fetchone()
                state["current"] = f"{row['title']} · {row['st']}" if row else str(season)
                try:
                    found += analyse_season(season)
                except Exception as exc:
                    log.info("Intro detection for season %s failed: %s", season, exc)
                state["done"] += 1
            if state["total"]:
                log.info("Intro detection finished: %d episodes have an intro", found)
        except Exception as exc:
            log.warning("Intro detection stopped: %s", exc)
        finally:
            state.update(running=False, current=None)


def start() -> None:
    global _started
    with db() as con:
        con.execute(SCHEMA)
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=_loop, name="intros", daemon=True).start()
    _wake.set()


def request(season_id: int | None = None) -> None:
    if season_id is not None:
        with _lock:
            if season_id in _priority:
                _priority.remove(season_id)
            _priority.insert(0, season_id)
    if not state["running"]:
        _wake.set()


def counts() -> dict:
    with db() as con:
        con.execute(SCHEMA)
        episodes = con.execute("SELECT COUNT(*) FROM files f JOIN items e ON e.id = f.item_id WHERE e.kind = 'episode'").fetchone()[0]
        row = con.execute("SELECT COUNT(*) AS checked, SUM(start IS NOT NULL) AS found FROM intros").fetchone()
    return {"enabled": enabled(), "episodes": episodes, "checked": row["checked"] or 0, "found": row["found"] or 0, **state}


# ---- API ------------------------------------------------------------------------------------------------

@router.get("/files/{file_id}/segments")
def segments(file_id: int, user: dict = Depends(auth.current_user)):
    """Parts of an episode the player can skip: {"intro": {"start": s, "end": s}} (empty when unknown)."""
    with db() as con:
        con.execute(SCHEMA)
        f = con.execute("SELECT f.id, f.library_id, f.size, f.mtime, e.parent_id AS season, e.kind FROM files f "
                        "JOIN items e ON e.id = f.item_id WHERE f.id = ?", (file_id,)).fetchone()
        if not f:
            raise HTTPException(404, "Not found")
        auth.check_item_access(user, f["library_id"])
        row = con.execute("SELECT start, end, source, sig FROM intros WHERE file_id = ?", (file_id,)).fetchone()
    if f["kind"] != "episode" or not enabled():
        return {}
    if not row or (row["source"] != "manual" and row["sig"] != _sig(dict(f))):
        if f["season"]:
            request(f["season"])                  # watched now: this season goes next
        return {"pending": True}
    if row["start"] is None:
        return {}
    return {"intro": {"start": row["start"], "end": row["end"], "source": row["source"]}}
