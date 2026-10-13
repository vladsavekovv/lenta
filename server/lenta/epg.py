"""What is on: the programme guide, taken from the broadcast itself.

A DVB multiplex carries its own guide — the event tables on PID 0x12 — describing not just the channel
you are watching but every service on that multiplex. So the guide needs no website, no subscription
and no XMLTV file: it arrives with the picture, and watching one channel fills in all of its
neighbours. A provider with twenty multiplexes gives up its whole guide over a few evenings of normal
viewing, and a sweep can fetch the rest when nothing is on.

Two things make that workable rather than merely possible:

    collecting costs nothing you would notice — a filter of its own on the demux carrying a few tens of
    kbit/s of text, while the picture arrives through the stream the converter reads, and

    programmes are stored against the channel's uid rather than its row, so rescanning the tuner, which
    rebuilds every channel row, does not throw the guide away.

Times are UTC throughout, as the broadcast sends them. Turning that into the viewer's own clock is the
browser's job, which is the only place that reliably knows what it is.
"""
from __future__ import annotations

import threading
import time

from . import dvb, logs
from .db import db

log = logs.get("epg")

# One filter catches the lot: the ids on this pid that match 0x40 under 0xC0 are the event tables, and
# parse_eit drops the ones describing multiplexes we are not tuned to.
EIT_FILTER, EIT_MASK = 0x40, 0xC0

KEEP_PAST = 6 * 3600          # a finished programme stays this long: "what was that?" is a fair question
KEEP_AHEAD = 14 * 86400       # anything claiming to be further off than this is a misread table
HARVEST_SECONDS = 180         # a multiplex gives up a full week in two or three minutes

_busy: set[tuple[int, int]] = set()      # frontends already collecting, so two threads never collide
_lock = threading.Lock()


# ---------------------------------------------------------------- matching a table to a channel
def _channel_index(source_id: int) -> tuple[set[str], dict[int, str]]:
    """Every channel of this source, by uid and — where it is unambiguous — by service id alone.

    The scan builds a uid from the network's numbers as the NIT gave them, and the event tables carry
    those same numbers, so the two normally agree exactly. Normally: a network that leaves its original
    network id out of one table and fills it in on another would otherwise lose its whole guide, so a
    service id on its own is accepted as a fallback — but only when exactly one channel answers to it,
    because guessing here would file one channel's programmes against another.
    """
    with db() as con:
        uids = {r["uid"] for r in con.execute(
            "SELECT uid FROM tv_channels WHERE source_id = ?", (source_id,))}
    seen: dict[int, list[str]] = {}
    for u in uids:
        tail = u.rsplit(":", 1)[-1]
        if tail.isdigit():
            seen.setdefault(int(tail), []).append(u)
    return uids, {sid: us[0] for sid, us in seen.items() if len(us) == 1}


def _match(table: dict, uids: set[str], by_service: dict[int, str]) -> str:
    exact = f"{table.get('onid') or 0}:{table.get('tsid') or 0}:{table['service_id']}"
    if exact in uids:
        return exact
    return by_service.get(table["service_id"], "")


# ---------------------------------------------------------------- storing
COLUMNS = ("source_id", "uid", "event_id", "start", "stop", "title", "summary",
           "description", "genre", "language", "updated_at")
_INSERT = (f"INSERT INTO tv_programmes ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
           "ON CONFLICT (source_id, uid, event_id) DO UPDATE SET "
           "start = excluded.start, stop = excluded.stop, title = excluded.title, "
           "summary = excluded.summary, description = excluded.description, genre = excluded.genre, "
           "language = excluded.language, updated_at = excluded.updated_at")


def store(source_id: int, uid: str, events: list[dict]) -> int:
    """Keep these programmes against this channel. A programme already known is corrected, not repeated."""
    now = time.time()
    rows = []
    for e in events:
        start, title = e.get("start"), (e.get("title") or "").strip()
        # A table with no start time, or no name for what is on, describes nothing anyone can read.
        if not start or not title:
            continue
        if not (now - KEEP_PAST < start < now + KEEP_AHEAD):
            continue
        rows.append((source_id, uid, e["event_id"], float(start), float(start + (e.get("seconds") or 0)),
                     title, e.get("summary") or "", e.get("description") or "",
                     e.get("genre") or "", e.get("language") or "", now))
    if not rows:
        return 0
    with db() as con:
        con.executemany(_INSERT, rows)
    return len(rows)


def expire() -> int:
    """Drop what has finished and gone cold. The guide is only ever a few megabytes kept this way."""
    with db() as con:
        cur = con.execute("DELETE FROM tv_programmes WHERE stop < ?", (time.time() - KEEP_PAST,))
    return cur.rowcount or 0


# ---------------------------------------------------------------- collecting
def harvest(tuner, source_id: int, seconds: float = HARVEST_SECONDS, alive=None) -> dict:
    """Read the event tables off a tuned frontend and keep what they say.

    `alive`, if given, is asked between tables whether to carry on — so collecting stops the moment the
    channel it was riding along with is switched off, rather than holding the tuner for its full term.
    """
    uids, by_service = _channel_index(source_id)
    kept, tables, unknown = 0, 0, set()
    pending: dict[str, list[dict]] = {}
    try:
        for raw in tuner.sections(dvb.EIT_PID, EIT_FILTER, EIT_MASK, seconds, alive=alive):
            if alive is not None and not alive():
                break
            table = dvb.parse_eit(raw)
            if not table or not table.get("events"):
                continue
            tables += 1
            uid = _match(table, uids, by_service)
            if not uid:
                unknown.add(table["service_id"])
                continue
            pending.setdefault(uid, []).extend(table["events"])
            # Written in batches rather than per table: a multiplex sends hundreds of these, and a
            # transaction each would have the disk working harder than the tuner.
            if sum(len(v) for v in pending.values()) >= 400:
                kept += sum(store(source_id, u, evs) for u, evs in pending.items())
                pending.clear()
    except (OSError, AttributeError) as ex:
        log.info("could not read the event tables: %s", ex)
    kept += sum(store(source_id, u, evs) for u, evs in pending.items())
    if unknown:
        log.info("event tables for %d service(s) this source does not list: %s",
                 len(unknown), ", ".join(str(s) for s in sorted(unknown)[:8]))
    log.info("guide: %d programmes from %d tables", kept, tables)
    return {"programmes": kept, "tables": tables, "unmatched": len(unknown)}


def wanted(which: str) -> bool:
    """Whether this way of collecting is switched on. Both are, unless somebody says otherwise.

    They are separate switches because they carry different risks and should be ruled out separately:
    one reads a second filter on the tuner you are already watching, the other takes a spare input and
    retunes it. Someone chasing a channel that stops wants to try them one at a time.
    """
    from .db import get_setting
    return (get_setting(f"livetv_guide_{which}") or "on") != "off"


def collect_alongside(tuner, source_id: int, key: tuple[int, int], alive, seconds: float = HARVEST_SECONDS) -> bool:
    """Collect the guide in the background while a channel plays. Returns whether it started.

    Riding along with viewing is what makes the guide fill itself in: the tuner is already locked to the
    multiplex, so the tables are already arriving, and all this does is read them.
    """
    if not wanted("watching"):
        return False
    with _lock:
        if key in _busy:
            return False
        _busy.add(key)

    def run():
        try:
            harvest(tuner, source_id, seconds, alive)
        except Exception:                                    # noqa: BLE001 - never disturb the picture
            log.exception("collecting the guide failed")
        finally:
            with _lock:
                _busy.discard(key)

    threading.Thread(target=run, name=f"epg-{key[0]}.{key[1]}", daemon=True).start()
    return True


def collecting() -> int:
    with _lock:
        return len(_busy)


# ---------------------------------------------------------------- reading it back
def _one(con, source_id: int, uid: str, where: str, args: tuple, order: str = "start") -> dict | None:
    row = con.execute(
        f"SELECT * FROM tv_programmes WHERE source_id = ? AND uid = ? AND {where} "
        f"ORDER BY {order} LIMIT 1", (source_id, uid, *args)).fetchone()
    return dict(row) if row else None


def now_next(source_id: int, uid: str, at: float | None = None) -> dict:
    """What is on this channel and what follows it."""
    at = at or time.time()
    with db() as con:
        # The latest programme that has started and not finished: a table that has been revised can
        # leave two overlapping, and the later one is the newer truth.
        return {"now": _one(con, source_id, uid, "start <= ? AND stop > ?", (at, at), "start DESC"),
                "next": _one(con, source_id, uid, "start > ?", (at,))}


def now_next_many(source_id: int, uids: list[str], at: float | None = None) -> dict[str, dict]:
    """The same for a page full of channels, in one go rather than one query each."""
    at = at or time.time()
    if not uids:
        return {}
    out: dict[str, dict] = {u: {"now": None, "next": None} for u in uids}
    marks = ",".join("?" * len(uids))
    with db() as con:
        for r in con.execute(
                f"SELECT * FROM tv_programmes WHERE source_id = ? AND uid IN ({marks}) "
                f"AND stop > ? AND start < ? ORDER BY start", (source_id, *uids, at, at + 86400)):
            row = dict(r)
            slot = out.setdefault(row["uid"], {"now": None, "next": None})
            if row["start"] <= at < row["stop"]:
                slot["now"] = row
            elif row["start"] > at and slot["next"] is None:
                slot["next"] = row
    return out


def window(source_id: int, uids: list[str], start: float, stop: float) -> dict[str, list[dict]]:
    """Everything on these channels that overlaps this stretch of time — what a guide page draws."""
    if not uids:
        return {}
    marks = ",".join("?" * len(uids))
    out: dict[str, list[dict]] = {u: [] for u in uids}
    with db() as con:
        for r in con.execute(
                f"SELECT * FROM tv_programmes WHERE source_id = ? AND uid IN ({marks}) "
                f"AND stop > ? AND start < ? ORDER BY uid, start", (source_id, *uids, start, stop)):
            out.setdefault(r["uid"], []).append(dict(r))
    return out


def coverage() -> dict:
    """How much guide there is, for the Live TV settings page to report."""
    with db() as con:
        row = con.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT uid) AS channels, "
                          "MIN(start) AS first, MAX(stop) AS last FROM tv_programmes").fetchone()
    n = row["n"] or 0
    return {"programmes": n, "channels": row["channels"] or 0,
            "first": row["first"], "last": row["last"],
            "hours": round(((row["last"] or 0) - time.time()) / 3600, 1) if n else 0.0}


# ---------------------------------------------------------------- filling in the rest
# Riding along with viewing only ever covers the multiplexes somebody watches. A provider carrying
# nearly two hundred channels spreads them over twenty-odd multiplexes, so the channels you never visit
# would keep an empty guide for ever. The sweep tunes those multiplexes while nothing else wants the
# tuner, taking the one whose guide is stalest first.
SWEEP_EVERY = 240            # how often to look for something worth doing
SWEEP_SECONDS = 90           # how long to sit on one multiplex
SWEEP_STALE = 8 * 3600       # a multiplex heard from less recently than this is worth revisiting

_sweep = {"thread": None, "using": None, "stop": False, "where": "", "done": 0}


def sweep_inputs() -> set[tuple[int, int]]:
    """The input the sweep is holding, if any — so nothing else hands it out underneath us."""
    using = _sweep["using"]
    return {using} if using else set()


def yield_input() -> bool:
    """Give up the tuner for someone who actually wants to watch. True if there was one to give up."""
    if not _sweep["using"]:
        return False
    _sweep["stop"] = True
    for _ in range(40):                       # the loop checks between tables; this is well under a second
        if not _sweep["using"]:
            return True
        time.sleep(0.05)
    log.info("the guide sweep was slow to let go of its input")
    return not _sweep["using"]


def _multiplexes() -> list[dict]:
    """Every multiplex this server's tuners can reach, stalest guide first.

    A multiplex is a frequency on a source, and the tuning that reaches it is any of its channels'.
    """
    from . import livetv
    muxes: dict[tuple[int, int], dict] = {}
    for c in livetv.channels():
        tune = c.get("tune") or {}
        freq = tune.get("frequency")
        if not freq or c.get("source_kind") != "tuner":
            continue
        key = (c["source_id"], freq)
        m = muxes.setdefault(key, {"source_id": c["source_id"], "frequency": freq, "tune": tune,
                                   "device": (c.get("source_config") or {}).get("device"),
                                   "uids": [], "names": []})
        m["uids"].append(c["uid"])
        m["names"].append(c["name"])
    if not muxes:
        return []
    with db() as con:
        heard = {r["uid"]: r["seen"] for r in con.execute(
            "SELECT uid, MAX(updated_at) AS seen FROM tv_programmes GROUP BY uid")}
    for m in muxes.values():
        m["last_heard"] = max((heard.get(u, 0) for u in m["uids"]), default=0)
    return sorted(muxes.values(), key=lambda m: m["last_heard"])


def sweep_once() -> dict:
    """Listen to one multiplex that needs it. Returns what it did, for the log and the settings page."""
    from . import dvb as _dvb, livestream, livetv
    busy = {(s.get("config") or {}).get("device") for s in livetv.sources()
            if (s.get("scan") or {}).get("running")}
    due = [m for m in _multiplexes()
           if m["last_heard"] < time.time() - SWEEP_STALE and m["device"] not in busy]
    if not due:
        return {"swept": False, "why": "every multiplex has a recent guide"}
    mux = due[0]
    _sweep["stop"] = False
    fe = livestream.reserve_input(mux["device"], lambda key: _sweep.__setitem__("using", key))
    if fe is None:
        return {"swept": False, "why": "no spare tuner input"}
    _sweep["where"] = f"{mux['frequency'] // 1_000_000} MHz"
    tuner = None
    try:
        tuner = _dvb.Tuner(fe)
        if _dvb.lock(tuner, mux["tune"], wait=5.0) is None:
            return {"swept": False, "why": f"no lock on {_sweep['where']}"}
        got = harvest(tuner, mux["source_id"], SWEEP_SECONDS, alive=lambda: not _sweep["stop"])
        _sweep["done"] += 1
        log.info("guide sweep: %s (%d channels) gave %d programmes",
                 _sweep["where"], len(mux["uids"]), got["programmes"])
        return {"swept": True, "where": _sweep["where"], "channels": len(mux["uids"]), **got}
    except OSError as ex:
        return {"swept": False, "why": f"the tuner would not open ({ex})"}
    finally:
        if tuner is not None:
            try:
                tuner.close()
            except OSError:
                pass
        _sweep["using"] = None
        _sweep["where"] = ""


def _sweeping() -> None:
    while True:
        time.sleep(SWEEP_EVERY)
        try:
            expire()
            if wanted("sweep"):
                sweep_once()
        except Exception:                                 # noqa: BLE001 - a sweep must never end the thread
            log.exception("the guide sweep stumbled")


def start_sweeping() -> None:
    """Begin filling in the multiplexes nobody watches. Safe to call more than once."""
    if _sweep["thread"] is None:
        _sweep["thread"] = threading.Thread(target=_sweeping, name="epg-sweep", daemon=True)
        _sweep["thread"].start()


def sweep_state() -> dict:
    return {"running": bool(_sweep["using"]), "where": _sweep["where"], "swept": _sweep["done"]}
