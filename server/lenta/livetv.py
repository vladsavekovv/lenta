"""Live TV: find the tuners, scan for channels, read playlists.

What happens without anyone asking:
  * every minute LENTA looks at /dev/dvb. A tuner that appears is identified (its USB name, the
    standards it can tune) and written down as a source, switched on.
  * a tuner that has never been scanned is scanned once, quietly, in the background.
  * what a scan finds — the services, the network's own name, the channel numbers it asks for —
    is saved as channels. The network name is the provider ("BULSATCOM", "Vivacom", "Free-to-air").

Scanning aerial and cable means trying the frequencies a broadcaster may legally use. The first
lock usually hands us the rest: the stream's NIT lists the network's other frequencies, so from
then on LENTA follows the network instead of sweeping the band.

Satellite is different: which satellite you receive depends on where the dish points, and the LNB
and any DiSEqC switch have to be told. That can't be guessed, so a satellite tuner is detected and
reported, and its scan waits for those settings.

Playlists (m3u, or an Xtream Codes login) are read straight into the same channel list.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import dvb, livestream, logs
from .db import db, get_setting

log = logs.get("livetv")

SCAN_LOCK = threading.Lock()         # one scan at a time: a tuner can only be in one place at once
_state: dict[int, dict] = {}         # source id -> what the running scan is doing (sent to the page as JSON)
_stops: dict[int, threading.Event] = {}     # source id -> its "please stop" switch (never leaves the server)
_traces: dict[int, list[str]] = {}   # source id -> what the last scan saw, frequency by frequency
_watcher: threading.Thread | None = None

USER_AGENT = "LENTA/1.0 (live tv)"


# ---------------------------------------------------------------- what to try when nothing is known
def aerial_plan(bandwidth: int = 8_000_000) -> list[dict]:
    """The UHF channels used for television across Europe (and the VHF band III below them).

    UHF 21-48 is what remains after the 700 MHz band went to mobile; 49-60 is still in use in a few
    countries, so it is tried last. Bulgaria, like most of Europe, is 8 MHz wide.
    """
    out = []
    for ch in range(21, 61):                       # 474 MHz … 786 MHz
        out.append({"delsys": "DVB-T", "frequency": (474 + (ch - 21) * 8) * 1_000_000,
                    "bandwidth": bandwidth, "label": f"aerial UHF {ch}"})
    for ch in range(5, 13):                        # band III, 7 MHz
        out.append({"delsys": "DVB-T", "frequency": (177_500 + (ch - 5) * 7_000) * 1_000,
                    "bandwidth": 7_000_000, "label": f"aerial VHF {ch}"})
    return out


def cable_plan() -> list[dict]:
    """The European cable raster: 8 MHz slots from 114 to 858 MHz.

    A cable network picks its own modulation and symbol rate, so the first pass asks the tuner to
    work them out (QAM_AUTO) and falls back to the two rates almost every European network uses.
    """
    out = []
    freq = 114_000_000
    while freq <= 858_000_000:
        out.append({"delsys": "DVB-C", "frequency": freq, "symbol_rate": 6_900_000,
                    "modulation": dvb.QAM_AUTO, "label": f"cable {freq // 1_000_000} MHz"})
        freq += 8_000_000
    return out


CABLE_FALLBACK_RATES = (6_875_000, 6_900_000, 6_111_000)
LOCK_WAIT = 2.5          # seconds to wait for a lock: a DVB-T2 demodulator can take over two


def _band(delsys: str) -> str:
    """Which aerial/cable/satellite band a standard belongs to — DVB-T and DVB-T2 are one band."""
    return FAMILY_OF.get(delsys, delsys or "?")


FAMILY_OF = {"DVB-T": "aerial", "DVB-T2": "aerial", "DVB-C": "cable", "DVB-C2": "cable",
             "DVB-S": "satellite", "DVB-S2": "satellite", "ATSC": "aerial", "ISDB-T": "aerial"}


def plan_for(families: list[str]) -> list[dict]:
    plan = []
    if "aerial" in families:
        plan += aerial_plan()
    if "cable" in families:
        plan += cable_plan()
    return plan


# ---------------------------------------------------------------- sources in the database
def sources() -> list[dict]:
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM tv_sources ORDER BY id")]
    for r in rows:
        r["config"] = json.loads(r["config"] or "{}")
        r["scan"] = _scan_state(r["id"])
    return rows


def _scan_state(sid: int) -> dict | None:
    """What a scan is doing, as plain values — anything else would break the page's JSON."""
    st = _state.get(sid)
    if st is None:
        return None
    return {k: v for k, v in st.items() if isinstance(v, (str, int, float, bool, type(None)))}


def source(sid: int) -> dict | None:
    with db() as con:
        row = con.execute("SELECT * FROM tv_sources WHERE id = ?", (sid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["config"] = json.loads(out["config"] or "{}")
    out["scan"] = _scan_state(sid)
    return out


def add_source(kind: str, name: str, config: dict) -> int:
    with db() as con:
        cur = con.execute("INSERT INTO tv_sources(kind, name, config, enabled, status, created_at) "
                          "VALUES(?,?,?,1,'idle',?)", (kind, name, json.dumps(config), time.time()))
        return cur.lastrowid


def _set(sid: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE tv_sources SET {cols} WHERE id = ?", (*fields.values(), sid))


def channels(source_id: int | None = None, include_hidden: bool = True) -> list[dict]:
    q = ("SELECT c.*, s.kind AS source_kind, s.name AS source_name FROM tv_channels c "
         "JOIN tv_sources s ON s.id = c.source_id")
    where, args = [], []
    if source_id:
        where.append("c.source_id = ?")
        args.append(source_id)
    if not include_hidden:
        where.append("c.hidden = 0")
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY CASE WHEN c.number IS NULL THEN 1 ELSE 0 END, c.number, c.name"
    with db() as con:
        rows = [dict(r) for r in con.execute(q, args)]
    for r in rows:
        r["tune"] = json.loads(r["tune"] or "{}")
    return rows


def channel(cid: int) -> dict | None:
    """One channel, with the tuning details and the stick it belongs to — what watching it needs."""
    with db() as con:
        row = con.execute(
            "SELECT c.*, s.kind AS source_kind, s.name AS source_name, s.config AS source_config_raw "
            "FROM tv_channels c JOIN tv_sources s ON s.id = c.source_id WHERE c.id = ?", (cid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["tune"] = json.loads(out["tune"] or "{}")
    out["source_config"] = json.loads(out.pop("source_config_raw") or "{}")
    return out


def _dedupe(found: list[dict]) -> list[dict]:
    """One entry per channel when a network carries the same one twice.

    Cable operators often repeat a channel on a second frequency — a spare, a regional feed, a leftover
    from a reshuffle. They are the same thing to a viewer, so only one is kept: the one the network gave
    a channel number to, failing that the one that isn't scrambled, failing that the first one found.
    An SD and an HD version are different names, so they both stay. Returns the keepers and the uids
    that lost, so an earlier incremental save of a duplicate can be taken back out again.
    """
    best: dict[tuple[str, str], dict] = {}
    dropped: set[str] = set()
    for ch in found:
        key = (ch.get("kind", "tv"), " ".join((ch.get("name") or "").split()).casefold())
        rank = (ch.get("number") is not None, not ch.get("scrambled"))
        prev = best.get(key)
        if prev is None:
            best[key] = ch
        elif rank > (prev.get("number") is not None, not prev.get("scrambled")):
            dropped.add(prev["uid"])
            best[key] = ch
        else:
            dropped.add(ch["uid"])
    return list(best.values()), dropped


def _save_channels(sid: int, found: list[dict], drop: set[str] | None = None) -> int:
    """Write what a scan found. Channels that are still there keep their place and their settings."""
    now = time.time()
    with db() as con:
        for uid in drop or ():
            con.execute("DELETE FROM tv_channels WHERE source_id = ? AND uid = ?", (sid, uid))
        for ch in found:
            # A scrambled channel needs the operator's card in a CAM, which a USB stick has no slot for.
            # It is hidden when first saved rather than dropped — unhide it if you do have a way in.
            scrambled = 1 if ch.get("scrambled") else 0
            con.execute(
                "INSERT INTO tv_channels(source_id, uid, name, number, provider, kind, logo, tune, scrambled, hidden, last_seen) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id, uid) DO UPDATE SET name=excluded.name, number=excluded.number, "
                "provider=excluded.provider, kind=excluded.kind, logo=COALESCE(excluded.logo, tv_channels.logo), "
                "tune=excluded.tune, scrambled=excluded.scrambled, last_seen=excluded.last_seen",
                (sid, ch["uid"], ch["name"], ch.get("number"), ch.get("provider"), ch.get("kind", "tv"),
                 ch.get("logo"), json.dumps(ch.get("tune", {})), scrambled, scrambled, now))
        total = con.execute("SELECT COUNT(*) FROM tv_channels WHERE source_id = ?", (sid,)).fetchone()[0]
    return total


# ---------------------------------------------------------------- finding tuners, quietly
def detect(write: bool = True) -> list[dict]:
    """Look at /dev/dvb and keep one source per stick — not per input.

    A twin tuner (the WinTV-dualHD, for one) shows the kernel two frontends. They are one thing on the
    desk with one aerial lead, so LENTA keeps one entry listing both inputs, and scans once.
    """
    # Never question an input that is carrying a channel: see dvb.adapters().
    found = dvb.adapters(skip={(f.adapter, f.index) for f in livestream.active()})
    out = [fe.as_dict() for fe in found]
    if not write:
        return out

    devices: dict[str, list[dvb.Frontend]] = {}
    for fe in found:
        devices.setdefault(fe.device, []).append(fe)

    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM tv_sources WHERE kind = 'tuner'")]
    for r in rows:
        r["cfg"] = json.loads(r["config"] or "{}")

    matched: set[int] = set()
    for device, inputs in devices.items():
        inputs.sort(key=lambda f: (f.adapter, f.index))
        first = inputs[0]
        delsys, families = [], []
        for fe in inputs:
            delsys += [d for d in fe.delsys if d not in delsys]
            families += [f for f in fe.families if f not in families]
        name = first.product or first.name or f"Tuner {first.adapter}.{first.index}"
        config = {
            "device": device, "usb_id": first.usb_id, "serial": first.serial,
            "product": first.product, "vendor": first.vendor, "frontend": first.name,
            "delsys": delsys, "families": families,
            "inputs": [{"adapter": fe.adapter, "index": fe.index, "name": fe.name,
                        "delsys": fe.delsys, "error": fe.error} for fe in inputs],
            # kept so an older entry still matches after an update
            "key": device, "adapter": first.adapter, "index": first.index,
        }
        row = _match_row(rows, device, inputs, matched)
        if row:
            matched.add(row["id"])
            # busy, in use, or no permission: keep what we already know rather than blanking it
            if any(fe.error or fe.in_use for fe in inputs):
                config = {**row["cfg"], **{k: v for k, v in config.items() if v}}
                name = row["name"] or name
            _set(row["id"], name=name, config=json.dumps(config))
        else:
            sid = add_source("tuner", name, config)
            matched.add(sid)
            log.info("found a tuner: %s, %d input%s (%s)", name, len(inputs), "" if len(inputs) == 1 else "s",
                     ", ".join(delsys) or "standard unknown")
            _set(sid, status="idle")

    # entries left over from an earlier way of counting inputs: drop the empty ones, keep any with channels
    for r in rows:
        if r["id"] in matched:
            continue
        with db() as con:
            n = con.execute("SELECT COUNT(*) FROM tv_channels WHERE source_id = ?", (r["id"],)).fetchone()[0]
        if n == 0 and devices:
            with db() as con:
                con.execute("DELETE FROM tv_sources WHERE id = ?", (r["id"],))
            log.info("removed a stale tuner entry (%s)", r["name"])
        else:
            _set(r["id"], status="not plugged in")
    return out


def _match_row(rows: list[dict], device: str, inputs: list[dvb.Frontend], used: set[int]) -> dict | None:
    """Find this stick's existing entry, however an older LENTA wrote it down."""
    places = {(fe.adapter, fe.index) for fe in inputs}
    keys = {device, *(fe.key for fe in inputs)}
    for r in rows:                                   # the device, or any of its inputs' old keys
        if r["id"] not in used and (r["cfg"].get("device") in keys or r["cfg"].get("key") in keys):
            return r
    for r in rows:                                   # or simply the same place in /dev
        if r["id"] in used:
            continue
        cfg = r["cfg"]
        if (cfg.get("adapter"), cfg.get("index")) in places:
            return r
    return None


def _tuner_inputs(cfg: dict) -> list[dvb.Frontend]:
    """This stick's inputs that are plugged in right now, the free ones first."""
    found = dvb.adapters()
    device = cfg.get("device") or cfg.get("key")
    mine = [fe for fe in found if fe.device == device]
    if not mine:                                     # an entry from before, or /dev moved around
        places = {(i.get("adapter"), i.get("index")) for i in cfg.get("inputs") or []}
        places.add((cfg.get("adapter"), cfg.get("index")))
        mine = [fe for fe in found if (fe.adapter, fe.index) in places]
    return sorted(mine, key=lambda fe: (bool(fe.error), fe.adapter, fe.index))


# ---------------------------------------------------------------- scanning a tuner
def _read_stream(tuner: dvb.Tuner, tune_info: dict) -> dict:
    """What this frequency carries: the services, the network's name and its other frequencies."""
    out = {"services": [], "network_name": "", "transports": [], "lcn": {}, "tsid": None, "onid": None}
    for section in tuner.section(0x11, 0x42, timeout=4.0, want=8):        # SDT (this stream)
        sdt = dvb.parse_sdt(section)
        if sdt:
            out["tsid"], out["onid"] = sdt["tsid"], sdt["onid"]
            out["services"] += sdt["services"]
    for section in tuner.section(0x10, 0x40, timeout=4.0, want=8):        # NIT (this network)
        nit = dvb.parse_nit(section)
        if nit:
            out["network_name"] = out["network_name"] or nit.get("network_name", "")
            out["transports"] += nit.get("transports", [])
            out["lcn"].update(nit.get("lcn", {}))
    if not out["services"]:                                              # no SDT: fall back to the PAT
        for section in tuner.section(0x00, 0x00, timeout=3.0, want=1):
            for num in dvb.parse_pat(section):
                out["services"].append({"service_id": num, "name": f"Service {num}", "provider": "",
                                        "type": "tv", "scrambled": False})
    return out


def _attempted(sid: int, **fields) -> None:
    """Write down that a scan was tried, whatever came of it: one try, never an endless retry."""
    _set(sid, last_scan=time.time(), **fields)


def scan_tuner(sid: int, *, deep: bool = False, stop: threading.Event | None = None) -> dict:
    """Try the band, then follow the network. Writes channels as it goes, so nothing is lost."""
    src = source(sid)
    if not src:
        return {"error": "That source is gone."}
    # Every way out of here leaves a line behind: a scan that gave up before tuning anything used to be
    # indistinguishable from one that swept the band and heard nothing.
    trace: list[str] = _traces.setdefault(sid, [])
    trace.clear()
    say = lambda line: trace.append(f"{time.strftime('%H:%M:%S')}  {line}")
    cfg = src["config"]
    inputs = _tuner_inputs(cfg)
    if not inputs:
        say("gave up: the tuner is not plugged in")
        _attempted(sid, status="error: the tuner is not plugged in")
        return {"error": "The tuner is not plugged in."}
    # A scan retunes an input, which would pull the picture out from under anyone watching it. Use a
    # free input of the same stick if there is one; refuse outright if every input is in use.
    watching = livestream.active()
    in_use = {(f.adapter, f.index) for f in watching}
    free = [fe for fe in inputs if (fe.adapter, fe.index) not in in_use]
    if not free:
        on = ", ".join(sorted(f.name for f in watching))
        say(f"gave up: every input is being watched ({on})")
        _attempted(sid, status="error: someone is watching this tuner")
        return {"error": f"Someone is watching this tuner ({on}). Stop that first, then scan."}
    inputs = free
    fe = inputs[0]
    if fe.error:
        say(f"gave up: {fe.adapter}.{fe.index} could not be opened ({fe.error})")
        _attempted(sid, status=f"error: {fe.error}")
        return {"error": f"The tuner can't be opened ({fe.error})."}
    # What the frontend says now, else what it said when it was found, else try everything a stick like
    # this could carry. An input that answers "no standards" must never quietly scan nothing at all.
    families = fe.families or cfg.get("families") or ["aerial", "cable"]
    if not fe.families:
        say(f"the input reported no standards; going by {', '.join(families)} instead")
    if families == ["satellite"]:
        say("gave up: a satellite tuner needs its dish settings first")
        _attempted(sid, status="needs dish settings")
        return {"error": "A satellite tuner needs the dish settings (LNB and DiSEqC) before it can scan."}

    plan = plan_for(families)
    if not plan:
        say(f"gave up: no frequencies to try for {', '.join(families) or 'no standards'}")
        _attempted(sid, status="error: no frequencies to try for " + ", ".join(families))
        return {"error": f"Nothing to scan: the tuner reports {', '.join(families) or 'no standards'}."}
    state = _state.setdefault(sid, {})
    stop = stop or _stops.setdefault(sid, threading.Event())
    stop.clear()
    state.update({"running": True, "done": 0, "total": len(plan), "found": 0, "label": "starting",
                  "started": time.time(), "network": ""})
    _set(sid, status="scanning")
    found: dict[str, dict] = {}
    numbers: dict[int, int] = {}          # the network's channel numbers, pooled from every NIT we read
    say(f"{fe.adapter}.{fe.index} ({fe.name or '?'}) · {', '.join(families)} · "
        f"{len(plan)} frequencies to try")
    # What has been tried, as (standard, frequency) — NOT frequency alone. The aerial UHF channels and
    # the cable raster share exactly the same 40 frequencies from 474 to 786 MHz, so keying on frequency
    # made the aerial pass swallow the whole middle of the cable band, which is where channels live.
    network, seen, queue = "", set(), list(plan)
    try:
        with dvb.Tuner(fe) as tuner:
            while queue:
                if stop.is_set():
                    break
                entry = queue.pop(0)
                freq = entry["frequency"]
                here = (_band(entry.get("delsys", "DVB-T")), freq)
                if here in seen:
                    continue
                seen.add(here)
                state.update({"label": entry.get("label") or f"{freq // 1_000_000} MHz",
                              "done": len(seen), "total": max(len(seen) + len(queue), 1)})
                locked = _tune_entry(tuner, entry)
                if not locked:
                    trace.append(f"  --  {entry.get('label') or freq // 1_000_000} · no lock "
                                 f"({dvb.flag_text(tuner.status())})")
                    continue
                info = _read_stream(tuner, entry)
                trace.append(f"  ok  {entry.get('label') or freq // 1_000_000} · {tuner.signal_text()} · "
                             f"{len(info['services'])} service(s)"
                             + (f" · network {info['network_name']}" if info["network_name"] else ""))
                network = network or info["network_name"]
                state["network"] = network
                # A NIT numbers the whole network, not just the frequency it arrived on, so every one we
                # read fills in numbers for channels found elsewhere — including ones already saved.
                numbers.update(info["lcn"])
                for svc in info["services"]:
                    if svc.get("type") == "data" or not svc.get("name"):
                        continue
                    uid = f"{info['onid'] or 0}:{info['tsid'] or 0}:{svc['service_id']}"
                    found[uid] = {
                        "uid": uid, "name": svc["name"], "kind": svc.get("type", "tv"),
                        "number": numbers.get(svc["service_id"]),
                        "provider": svc.get("provider") or network, "scrambled": svc.get("scrambled"),
                        "tune": {**{k: v for k, v in entry.items() if k != "label"},
                                 "service_id": svc["service_id"], "tsid": info["tsid"], "onid": info["onid"]},
                    }
                for ch in found.values():       # back-fill the ones found before their number turned up
                    if ch["number"] is None:
                        ch["number"] = numbers.get(ch["tune"]["service_id"])
                state["found"] = len(found)
                _save_channels(sid, *_dedupe(list(found.values())))
                if not deep:                      # follow the network instead of sweeping the rest
                    for t in info["transports"]:
                        if t.get("frequency") and (_band(t.get("delsys", "")), t["frequency"]) not in seen:
                            t.setdefault("label", f"{t['frequency'] // 1_000_000} MHz (from the network)")
                            queue.append(t)
                    if info["transports"]:
                        queue = [q for q in queue if q.get("delsys") not in ("DVB-T", "DVB-C")
                                 or q.get("label", "").endswith("(from the network)")]
    except OSError as ex:
        busy = getattr(ex, "errno", None) == 16
        _attempted(sid, status="error: the tuner is in use by something else" if busy else f"error: {ex.strerror or ex}")
        state["running"] = False
        return {"error": "Something else is using the tuner." if busy
                else f"The tuner stopped responding ({ex.strerror or ex})."}
    total = _save_channels(sid, *_dedupe(list(found.values()))) if found else 0
    stopped = stop.is_set()
    state.update({"running": False, "done": state.get("total", 0), "found": len(found)})
    # what was kept, not what was seen — a repeat of one channel on two frequencies counts once
    _attempted(sid, status=("stopped — " if stopped else "") + f"found {total} channels",
               provider=network or None, found=total)
    say(f"finished: {total} channels kept of {len(found)} found"
        + (" (stopped early)" if stopped else ""))
    for line in trace:
        log.info("scan %s: %s", src["name"], line)
    log.info("%s: %d channels%s", src["name"], len(found), f" on {network}" if network else "")
    return {"found": len(found), "network": network}


def _tune_entry(tuner: dvb.Tuner, entry: dict) -> bool:
    """One frequency, trying the ways it might be carried — and writing back the one that worked.

    What locks here is what gets saved as the channel's tuning, so the player can go straight to it
    later. Keeping the parameters we started with instead would leave channels that scan perfectly
    and then refuse to play.
    """
    won = dvb.lock(tuner, entry, wait=LOCK_WAIT)
    if won is None:
        return False
    entry.update(won)
    return True


# ---------------------------------------------------------------- playlists
RADIO_HINT = re.compile(r"\b(radio|r[aá]dio|fm)\b", re.I)
RADIO_EXT = (".aac", ".mp3", ".ogg", ".m4a")


def _playlist_kind(name: str, group: str, url: str) -> str:
    """A playlist doesn't say what a channel is; its group, name or file type usually does."""
    if url.split("?", 1)[0].lower().endswith(RADIO_EXT):
        return "radio"
    return "radio" if RADIO_HINT.search(group or "") or RADIO_HINT.search(name or "") else "tv"


def parse_m3u(text: str) -> list[dict]:
    """An #EXTM3U playlist: name, logo, group and channel number where the file gives them."""
    out, pending = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            name = line.split(",", 1)[1].strip() if "," in line else ""
            pending = {"name": attrs.get("tvg-name") or name or "Channel",
                       "logo": attrs.get("tvg-logo") or None,
                       "uid": attrs.get("tvg-id") or attrs.get("tvg-name") or name,
                       "group": attrs.get("group-title") or "",
                       "number": int(attrs["tvg-chno"]) if attrs.get("tvg-chno", "").isdigit() else None}
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending["tune"] = {"url": line}
            group = pending.pop("group", "")
            pending["provider"] = group or None
            pending["uid"] = pending["uid"] or line
            pending["kind"] = _playlist_kind(pending["name"], group, line)
            out.append(pending)
            pending = None
        else:                                     # a bare list of addresses
            out.append({"uid": line, "name": line.rsplit("/", 1)[-1] or line, "tune": {"url": line}})
    return out


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def scan_playlist(sid: int) -> dict:
    """Read an m3u playlist, or ask an Xtream Codes server for its channel list."""
    src = source(sid)
    if not src:
        return {"error": "That source is gone."}
    cfg, state = src["config"], _state.setdefault(sid, {})
    state.update({"running": True, "done": 0, "total": 1, "found": 0, "label": "reading the playlist"})
    _set(sid, status="scanning")
    try:
        if src["kind"] == "xtream":
            base = cfg["url"].rstrip("/")
            auth = f"username={urllib.parse.quote(cfg['username'])}&password={urllib.parse.quote(cfg['password'])}"
            raw = _fetch(f"{base}/player_api.php?{auth}&action=get_live_streams")
            items = json.loads(raw)
            found = [{"uid": str(it.get("stream_id")), "name": it.get("name") or f"Channel {it.get('stream_id')}",
                      "logo": it.get("stream_icon") or None,
                      "number": int(it["num"]) if str(it.get("num", "")).isdigit() else None,
                      "provider": src["name"],
                      "tune": {"url": f"{base}/live/{cfg['username']}/{cfg['password']}/{it.get('stream_id')}.ts"}}
                     for it in items if it.get("stream_id")]
        else:
            found = parse_m3u(_fetch(cfg["url"]))
    except (urllib.error.URLError, OSError, ValueError, KeyError) as ex:
        msg = getattr(ex, "reason", None) or str(ex)
        _attempted(sid, status=f"error: {msg}")
        state["running"] = False
        return {"error": f"The playlist could not be read ({msg})."}
    total = _save_channels(sid, found)
    provider = next((c.get("provider") for c in found if c.get("provider")), None)
    state.update({"running": False, "done": 1, "found": len(found)})
    _attempted(sid, status=f"found {len(found)} channels", provider=provider, found=total)
    log.info("%s: %d channels from the playlist", src["name"], len(found))
    return {"found": len(found)}


def scan(sid: int, **kw) -> dict:
    src = source(sid)
    if not src:
        return {"error": "That source is gone."}
    with SCAN_LOCK:
        if src["kind"] == "tuner":
            return scan_tuner(sid, **kw)
        return scan_playlist(sid)


def trace(sid: int) -> list[str]:
    """What the last scan on this source saw, frequency by frequency. Empty until one has run."""
    return list(_traces.get(sid) or [])


def stop_scan(sid: int) -> bool:
    """Ask a running scan to finish now and let go of the tuner."""
    ev = _stops.get(sid)
    if ev is None or not (_state.get(sid) or {}).get("running"):
        return False
    ev.set()
    return True


def scan_async(sid: int, **kw) -> None:
    threading.Thread(target=scan, args=(sid,), kwargs=kw, daemon=True,
                     name=f"livetv-scan-{sid}").start()


# ---------------------------------------------------------------- the quiet background work
def _watch_loop() -> None:
    """Look for tuners now and then; scan a new one once, without being asked."""
    first = True
    while True:
        try:
            detect()
            auto = (get_setting("livetv_auto_scan") or "1") != "0"
            for src in sources():
                if not auto or not src["enabled"] or src["kind"] != "tuner" or src["last_scan"]:
                    continue
                if _state.get(src["id"], {}).get("running"):
                    continue
                if "satellite" in (src["config"].get("families") or []) and len(src["config"].get("families")) == 1:
                    continue                      # waits for the dish settings
                log.info("first scan of %s", src["name"])
                with SCAN_LOCK:
                    scan_tuner(src["id"])
        except Exception as ex:                   # a tuner that misbehaves must not stop the server
            log.warning("background check failed: %s", ex)
        time.sleep(20 if first else 60)
        first = False


def start_background() -> None:
    global _watcher
    if _watcher and _watcher.is_alive():
        return
    _watcher = threading.Thread(target=_watch_loop, daemon=True, name="livetv-watch")
    _watcher.start()
