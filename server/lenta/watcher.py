"""Library watcher: notices new, changed and removed media in the library folders and scans just those folders,
so a film copied to the NAS shows up in LENTA with its poster and details a minute or two later.

Network shares (SMB/CIFS, NFS) don't tell Linux when another computer adds a file, so instead of waiting for
notifications this looks at the folders' modification times every minute or so (folders only, never the files'
contents: cheap even for thousands of titles). A folder whose contents changed is scanned once its files have
stopped growing, i.e. when the copy has finished.
"""
import os
import threading
import time

from . import logs, scanner
from .config import VIDEO_EXT, AUDIO_EXT, PHOTO_EXT
from .db import db, get_setting, jloads

log = logs.get("watcher")
SETTLE = 45                    # seconds a changed folder must stay unchanged (copy finished) before it is scanned
EXT = {"movies": VIDEO_EXT, "shows": VIDEO_EXT, "music": AUDIO_EXT, "photos": PHOTO_EXT}

state = {"running": False, "last_check": None, "last_took": 0.0, "folders": 0, "waiting": 0, "scanned": 0}
_snap: dict[int, dict[str, float]] = {}        # library id -> {folder: mtime}
_loose: dict[int, set] = {}                    # library id -> media files lying directly in a library folder
_pending: dict[str, dict] = {}                 # folder -> {"library": id, "sig": ..., "since": t}
_started = False
_lock = threading.Lock()


def enabled() -> bool:
    return (get_setting("watch_libraries") or "1") != "0"


def interval() -> int:
    try:
        return max(30, min(1800, int(get_setting("watch_interval") or 60)))
    except ValueError:
        return 60


def _folders(roots: list[str]) -> dict[str, float]:
    """Every folder under the roots with its modification time (follows links, each real folder once)."""
    out, seen = {}, set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                st = os.stat(d)
            except OSError:
                continue
            if (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            out[d] = st.st_mtime
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.name.startswith(".") or e.name.lower() in scanner.SKIP_DIRS:
                            continue
                        try:
                            if e.is_dir(follow_symlinks=True):
                                stack.append(e.path)
                        except OSError:
                            pass
            except OSError:
                pass
    return out


def _signature(folder: str, exts) -> tuple:
    """Names, sizes and times of the media files in a folder (and below): changes while a copy is running."""
    sig = []
    newest = 0.0
    for dirpath, _, files in os.walk(folder, followlinks=True):
        for n in files:
            if os.path.splitext(n)[1].lower() in exts:
                try:
                    st = os.stat(os.path.join(dirpath, n))
                except OSError:
                    continue
                sig.append((dirpath, n, st.st_size, int(st.st_mtime)))
                newest = max(newest, st.st_mtime)
    return tuple(sorted(sig)), newest


def _top(folder: str, roots: list[str]) -> str:
    """The title's own folder: the first folder below the library folder (Movies/Inception (2010), TV/Show).
    Scanning that one covers a new season or a file added deeper inside."""
    for r in roots:
        r = os.path.normpath(r)
        if folder.startswith(os.path.join(r, "")):
            first = os.path.relpath(folder, r).split(os.sep)[0]
            return os.path.join(r, first)
    return folder


def check() -> None:
    """One round: compare folder times with the last round, then scan folders whose copy has finished."""
    t0 = time.time()
    with db() as con:
        libs = [dict(r) for r in con.execute("SELECT id, kind, paths FROM libraries")]
    total = 0
    for lib in libs:
        roots = [os.path.normpath(p) for p in jloads(lib["paths"], [])]
        if not roots or not any(os.path.isdir(r) for r in roots):
            continue                                  # NAS not mounted right now: don't treat everything as gone
        now_folders = _folders(roots)
        total += len(now_folders)
        before = _snap.get(lib["id"])
        _snap[lib["id"]] = now_folders
        loose_before = _loose.get(lib["id"])
        loose = set()
        for r in roots:                              # files lying directly in a library folder (no folder of their own)
            try:
                loose |= {(e.path, e.stat().st_size) for e in os.scandir(r)
                          if e.is_file() and os.path.splitext(e.name)[1].lower() in EXT[lib["kind"]]}
            except OSError:
                pass
        _loose[lib["id"]] = loose
        if loose_before is not None and loose != loose_before:
            log.info("New or changed media directly in a library folder: scanning the library")
            scanner.request_scan(lib["id"])
        if before is None:
            continue                                  # first look: nothing to compare with yet
        changed = {d for d, m in now_folders.items() if before.get(d) != m}
        removed = set(before) - set(now_folders)
        for d in changed | removed:
            if os.path.normpath(d) in roots:          # a title folder added or removed directly in the library folder
                continue                              # (the title's own folder is listed separately)
            top = _top(d, roots)
            _pending.setdefault(top, {"library": lib["id"], "sig": None, "since": time.time(), "roots": roots})
    # scan folders whose files stopped changing
    ready: dict[int, list[str]] = {}
    for folder, p in list(_pending.items()):
        lib_kind = next((l["kind"] for l in libs if l["id"] == p["library"]), None)
        if lib_kind is None:
            _pending.pop(folder)
            continue
        if not os.path.isdir(folder):                 # removed: the scan takes its titles out
            ready.setdefault(p["library"], []).append(folder)
            _pending.pop(folder)
            continue
        sig, newest = _signature(folder, EXT[lib_kind])
        if sig and sig == p["sig"] and time.time() - newest > SETTLE:
            ready.setdefault(p["library"], []).append(folder)
            _pending.pop(folder)
        else:
            p["sig"] = sig
            if time.time() - p["since"] > 6 * 3600:   # never settles (a file being written for hours): stop waiting
                _pending.pop(folder)
    for lib_id, folders in ready.items():
        log.info("New or changed media in %d folder(s): %s", len(folders), ", ".join(os.path.basename(f) for f in folders[:5]))
        scanner.request_scan_folders(lib_id, folders)
        state["scanned"] += len(folders)
    state.update(last_check=time.time(), last_took=round(time.time() - t0, 1), folders=total, waiting=len(_pending))


def _loop() -> None:
    while True:
        if enabled():
            state["running"] = True
            try:
                check()
            except Exception as exc:
                log.warning("Library watcher check failed: %s", exc)
            # a slow share gets checked less often (at most ~10% of the time spent looking)
            pause = max(interval(), state["last_took"] * 10)
            if _pending:
                pause = min(pause, 20)                # a copy is under way: look again soon
        else:
            state["running"] = False
            _snap.clear()
            _pending.clear()
            pause = 60
        time.sleep(pause)


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="library-watcher", daemon=True).start()


def status() -> dict:
    return {**state, "enabled": enabled(), "interval": interval()}
