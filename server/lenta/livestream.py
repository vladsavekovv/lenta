"""Watching a channel: hold a tuner on its multiplex and turn the programme into HLS for the player.

A tuner input can only sit on one frequency at a time, so a stick with two inputs can show two
different channels at once — however many people are watching each of them. Everyone watching the
same channel shares one tuner and one FFmpeg; a second channel takes the other input; a third is
refused with a message naming what is already on.

The chain, once a channel is asked for:

    frontend  tuned to the channel's frequency and left open, which is what holds the lock
    demux     asked for the whole multiplex, sent out of the adapter's dvr device
    FFmpeg    reads the dvr device, picks this programme out of the multiplex by its service id,
              and writes a sliding window of HLS segments
    player    reads index.m3u8 like any other LENTA stream

Codecs are decided from the PMT before FFmpeg starts, so a channel already in H.264 and AAC is
copied rather than re-encoded. Bulgarian cable carries a good deal of MPEG-2 and MPEG audio, which
no browser will play, so those are re-encoded — on the GPU when there is one.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import dvb, epg, logs
from .config import DEV_VP9, FFMPEG, TRANSCODE_DIR
from .db import db, get_setting
from .transcode import hw

log = logs.get("livestream")


class LiveError(Exception):
    """Something the viewer should be told in plain words, not a stack trace."""

LIVE_DIR = TRANSCODE_DIR / "live"
# How long a channel takes to appear is mostly this number times the one below it: nothing can play
# until a few whole segments exist, so two-second segments cost six seconds before the first picture.
# One second is a request, not a promise — copying a broadcast can only cut on the keyframes the
# broadcaster sends, so a channel with a two-second GOP still gets two-second segments and simply
# gains nothing here. Re-encoded channels are told to put a keyframe exactly one second apart.
SEGMENT_SECONDS = 1
WINDOW = 24                  # segments kept on disk — still about 24 seconds of catch-up
VIEWER_TIMEOUT = 40          # a player that stops asking for segments has gone away
# Going back to a channel you were just watching should be instant, so a channel is not torn down when
# you leave it: the tuner stays locked and the converter keeps writing, and coming back simply joins what
# is already running. What that costs is a tuner input, and inputs are the scarce thing — a dual stick has
# two. So the cache is bounded at both ends: a warm channel is never dropped for being idle before
# CACHE_IDLE, but it is always dropped the moment somebody wants its input for a channel to actually watch.
CACHE_IDLE = 30 * 60         # how long a channel nobody is watching is kept ready to return to
CACHE_MAX = 5                # how many may be kept warm at once, inputs permitting
CACHE_DEAD = 6               # keeper rounds a warm channel may write nothing before it is not worth keeping
MAX_RESTARTS = 5             # how often to pick the converter up before admitting the channel is broken
START_TIMEOUT = 25           # copying: the first segments should appear almost at once
TRANSCODE_TIMEOUT = 60      # re-encoding, especially in software, takes a while to get going

_feeds: dict[str, "Feed"] = {}
_failures: list[dict] = []           # channels that would not start, newest last — kept for the page
_lock = threading.RLock()
_keeper: threading.Thread | None = None


@dataclass
class Feed:
    """One tuner input, tuned to one channel, with everyone watching it."""
    id: str
    channel_id: int
    name: str
    number: int | None
    device: str                      # which stick, so a scan knows not to touch it
    adapter: int
    index: int
    frequency: int
    service_id: int
    directory: Path
    source_id: int = 0               # whose channel this is, so the guide knows where to file what it hears
    tuner: dvb.Tuner | None = None
    demux_fds: list[int] = field(default_factory=list)   # one per pid; all must stay open
    proc: subprocess.Popen | None = None
    viewers: dict[str, float] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    t0: float = field(default_factory=time.monotonic)   # for the stopwatch on every diagnostic line
    last_empty: float = 0.0          # when the last viewer left, so we know how long it has idled
    video: str = ""
    audio: str = ""
    mode: str = "copy"
    error: str = ""
    stopping: bool = False
    notes: list[str] = field(default_factory=list)     # what FFmpeg said, newest last
    segments: int = 0                                  # how many it has written since starting
    last_count: int = 0
    stalled: int = 0
    restarts: int = 0
    cmd: list = field(default_factory=list)      # kept so the converter can be started again

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def info(self, *, admin: bool = True) -> dict:
        """What this feed is doing. `admin=False` leaves out the two things a viewer must not be told:
        the tuner's **device path**, which is a filesystem path, and the feed's **id**, which is the
        only thing standing between somebody else and this stream (see `known_viewer`)."""
        return {**({"id": self.id, "device": self.device} if admin else {}),
                "channel_id": self.channel_id, "name": self.name, "number": self.number,
                "input": f"{self.adapter}.{self.index}",
                "frequency": self.frequency, "viewers": len(self.viewers), "mode": self.mode,
                "video": self.video, "audio": self.audio, "running": self.alive(),
                "created": self.created, "error": self.error, "segments": self.segments,
                "seconds": round(time.time() - self.created, 1),
                # Kept ready to go back to, rather than being watched by anybody right now.
                "warm": not self.viewers and bool(self.last_empty),
                "idle": round(time.time() - self.last_empty) if not self.viewers and self.last_empty else 0,
                "keeps_for": CACHE_IDLE}

    def say(self, line: str) -> None:
        """A line for the diagnostic, kept short — this is what gets sent when a channel misbehaves.

        Each line carries how long the channel has been starting up, because "slow to appear" is
        several different faults wearing the same coat: a tuner that takes its time to lock, tables
        that are slow to come round, or a converter that is simply waiting for enough picture. The
        stopwatch says which one, without having to add a timer to each step in turn.
        """
        self.notes.append(f"{time.strftime('%H:%M:%S')}  +{time.monotonic() - self.t0:5.1f}s  {line}")
        del self.notes[:-200]


# ---------------------------------------------------------------- what is on right now
def active() -> list[Feed]:
    with _lock:
        return list(_feeds.values())


def busy_devices() -> set[str]:
    """Sticks that must not be scanned: a scan would retune the input out from under a viewer."""
    with _lock:
        return {f.device for f in _feeds.values()}


def feed(fid: str) -> Feed | None:
    with _lock:
        return _feeds.get(fid)


def _free_input(channel_device: str | None) -> dvb.Frontend | None:
    """An input nobody is watching on. Inputs of the wanted stick first, then anything else.

    A feed with no viewers is only being kept warm for a few seconds in case this is channel flipping.
    Someone asking for a channel right now outranks that, so when every input is held by warm feeds the
    one left empty longest gives its input up. Without this, flipping through more channels than the
    stick has inputs ends in "every tuner input is in use" while nobody is actually watching anything.
    """
    def pick() -> dvb.Frontend | None:
        with _lock:
            # Both in the one lock: the guide sweep claims its input under this same lock, so a channel
            # starting at the very moment a sweep begins cannot be handed the input the sweep just took.
            taken = {(f.adapter, f.index) for f in _feeds.values()} | epg.sweep_inputs()
        usable = [fe for fe in dvb.adapters() if not fe.error and (fe.adapter, fe.index) not in taken]
        usable.sort(key=lambda fe: (fe.device != channel_device, fe.adapter, fe.index))
        return usable[0] if usable else None

    got = pick()
    if got is not None:
        return got
    # Nothing free. The guide sweep is the lowest claim on a tuner — it is filling in programmes nobody
    # has asked for yet — so it gives way before a channel somebody may want to return to.
    if epg.yield_input():
        got = pick()
        if got is not None:
            return got
    with _lock:
        warm = sorted((f for f in _feeds.values() if not f.viewers and f.last_empty),
                      key=lambda f: f.last_empty)
    if not warm:
        return None
    giving_up = warm[0]
    log.info("no input free: taking %d.%d back from %s, which nobody is watching",
             giving_up.adapter, giving_up.index, giving_up.name)
    stop(giving_up.id)
    return pick()


def reserve_input(device: str | None, claim) -> dvb.Frontend | None:
    """An input with no feed on it at all, claimed before the books are unlocked.

    This is what the guide sweep borrows between viewings. It takes nothing away from anybody: if every
    input is spoken for, even by a channel merely being kept warm, the sweep waits for another time.

    Finding and claiming happen under one lock on purpose. Done in two steps there is a moment — short,
    but real — where the sweep has chosen an input and not yet said so, and a channel starting in that
    moment would be handed the same input. Two things would then drive one tuner, and the viewer would
    be the one to notice, because the sweep would retune the frontend out from under the picture.
    """
    with _lock:
        taken = {(f.adapter, f.index) for f in _feeds.values()} | epg.sweep_inputs()
        free = [fe for fe in dvb.adapters() if not fe.error and (fe.adapter, fe.index) not in taken]
        free.sort(key=lambda fe: (fe.device != device, fe.adapter, fe.index))
        if not free:
            return None
        claim((free[0].adapter, free[0].index))
        return free[0]


# ---------------------------------------------------------------- starting and joining
def watch(channel: dict, *, scanning: set[str] | None = None) -> tuple[Feed, str]:
    """Start watching, or join what is already on. Returns the feed and this viewer's token."""
    tune = channel.get("tune") or {}
    service_id = tune.get("service_id")
    frequency = tune.get("frequency")
    if not service_id or not frequency:
        raise LiveError("That channel has no tuning details — scan the tuner again.")

    with _lock:
        for f in _feeds.values():                       # already on? just join it
            if f.channel_id == channel["id"] and f.alive():
                token = secrets.token_urlsafe(8)
                f.viewers[token] = time.time()
                f.last_empty = 0.0
                log.info("%s: another viewer joined (%d now)", f.name, len(f.viewers))
                return f, token

    device = (channel.get("source_config") or {}).get("device")
    if scanning and device in scanning:
        raise LiveError("That tuner is being scanned at the moment. Stop the scan and try again.")

    fe = _free_input(device)
    if fe is None:
        with _lock:
            on = ", ".join(sorted(f.name for f in _feeds.values()))
        raise LiveError(f"Every tuner input is in use ({on}). Stop watching one of those first."
                        if on else "No tuner input is free.")

    fid = secrets.token_urlsafe(9)
    out = LIVE_DIR / fid
    out.mkdir(parents=True, exist_ok=True)
    f = Feed(id=fid, channel_id=channel["id"], name=channel["name"], number=channel.get("number"),
             device=fe.device, adapter=fe.adapter, index=fe.index, frequency=frequency,
             service_id=service_id, source_id=channel.get("source_id") or 0, directory=out)
    try:
        _open(f, fe, tune)
    except LiveError as ex:
        f.error = f.error or str(ex)
        with _lock:                       # remember it even though FFmpeg never ran
            _feeds[fid] = f
        _teardown(f)
        with _lock:
            _feeds.pop(fid, None)
        raise
    except OSError as ex:
        _teardown(f)
        raise LiveError(f"The tuner stopped responding ({ex.strerror or ex}).") from ex

    token = secrets.token_urlsafe(8)
    f.viewers[token] = time.time()
    with _lock:
        _feeds[fid] = f
    start_keeper()
    # The tuner is locked to this multiplex anyway, and the multiplex is already sending the programme
    # guide for every service on it. Reading it costs a filter and a few tens of kbit/s of text, so an
    # evening of ordinary viewing fills the guide in without anyone asking for it.
    if f.source_id and epg.collect_alongside(f.tuner, f.source_id, (f.adapter, f.index), f.alive):
        # Said out loud in the stream's own log, so that if a channel does misbehave it is plain whether
        # the guide was being read at the time rather than a matter of opinion.
        f.say("also reading the programme guide for this multiplex on a filter of its own")
    return f, token


def _open(f: Feed, fe: dvb.Frontend, tune: dict) -> None:
    """Tune, work out what the programme is made of, tap the multiplex and start FFmpeg."""
    f.tuner = dvb.Tuner(fe)
    # The same ladder the scan climbs: the saved parameters first, then the other ways this frequency
    # could be carried. Channels scanned before LENTA 1.6.3 may have the wrong symbol rate saved, and
    # a tuner that has just been let go sometimes needs a second ask before it locks.
    attempts: list[str] = []
    f.say(f"tuning {f.frequency // 1_000_000} MHz, service {f.service_id}, with "
          + ", ".join(f"{k}={v}" for k, v in sorted(tune.items()) if k != "label"))
    won = dvb.lock(f.tuner, tune, wait=5.0, tried=attempts)
    if won is None:
        # A frontend handed over moments ago — by a scan, or by the last channel — can need a beat
        # before it hears anything at all. One silent pass is not evidence that nothing is there.
        time.sleep(2.0)
        attempts.append("--- nothing on the first pass; letting the frontend settle and trying again ---")
        won = dvb.lock(f.tuner, tune, wait=6.0, tried=attempts)
    for line in attempts:
        f.say("  " + line)
    try:
        f.say(f"  the tuner reports {f.tuner.signal_text()}")
    except (OSError, AttributeError):
        pass
    if won is None:
        # The best any attempt managed, not merely the last one: the last is often a long retry whose
        # one-line summary says nothing about how far the demodulator actually got.
        ranked = ["sync", "viterbi", "carrier", "signal"]
        reached = next((word for word in ranked if any(word in a for a in attempts)), "nothing")
        raise LiveError(
            f"The tuner could not lock onto {f.frequency // 1_000_000} MHz after {len(attempts)} "
            f"attempts — the best the demodulator managed was: {reached}. "
            + ("The multiplex is there but too weak or too dirty to decode. Check the aerial lead and "
               "the wall socket, and run `sudo /opt/lenta/deploy/tv-check.py --freq "
               f"{f.frequency // 1_000_000}` to see the signal level."
               if reached != "nothing"
               else "Nothing is arriving at all: check the aerial lead, and whether something else "
                    "(Tvheadend, for one) has taken the tuner."))
    if {k: won.get(k) for k in ("delsys", "symbol_rate", "modulation")} != \
       {k: tune.get(k) for k in ("delsys", "symbol_rate", "modulation")}:
        f.say(f"locked on {won.get('delsys')} {won.get('symbol_rate') or ''} — not what was saved; "
              "correcting the channel so next time is immediate")
        _remember_tuning(f.channel_id, won)

    parts = _programme(f.tuner, f.service_id)
    f.video = (parts["video"][0]["codec"] if parts["video"] else "") or "?"
    f.audio = (parts["audio"][0]["codec"] if parts["audio"] else "") or "?"
    copy_video = bool(parts["video"]) and parts["video"][0]["browser_ready"]
    copy_audio = bool(parts["audio"]) and parts["audio"][0]["browser_ready"]
    f.mode = "copy" if copy_video and copy_audio else ("remux" if copy_video else "transcode")

    # Ask the demux for this programme's packets only. Carrying the whole multiplex means pushing every
    # channel on the frequency — fifty-odd megabits on cable — down a USB link that only needs to deliver
    # one, and the kernel's buffer holds barely a second of that. The PMT told us exactly what to ask for.
    pids = _service_pids(parts)
    if pids:
        try:
            f.demux_fds = f.tuner.tap_service(pids)
            f.say(f"taking {len(pids)} pids off the multiplex: " + ", ".join(str(p) for p in pids))
        except (OSError, AttributeError) as ex:
            # Drivers differ in what they will accept. Carrying the whole multiplex is wasteful, but it
            # is far better than refusing to show the channel at all.
            f.say(f"the demux would not filter by pid ({ex.strerror or ex}); taking the whole multiplex")
            log.info("%s: per-pid filtering refused (%s), falling back to the whole multiplex", f.name, ex)
            pids = []
    if not pids:
        f.demux_fds = [f.tuner.tap_mux()]
        if not f.notes or "whole multiplex" not in f.notes[-1]:
            f.say("the programme's parts are unknown, so taking the whole multiplex — expect it to be heavy")
    cmd = f.cmd = build_command(f, fe.dvr, copy_video, copy_audio)
    log.info("%s: %s on input %d.%d (%s/%s, %s)", f.name, f.frequency // 1_000_000, f.adapter, f.index,
             f.video, f.audio, f.mode)
    f.say(f"{f.video}/{f.audio} -> {f.mode}; reading {fe.dvr}")
    f.say(" ".join(cmd))
    f.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, start_new_session=True)
    threading.Thread(target=_watch_errors, args=(f,), daemon=True).start()


def _service_pids(parts: dict) -> list[int]:
    """Everything needed to decode one programme, and nothing else.

    The two tables a player needs to make sense of the stream (PAT and this programme's PMT), the clock
    reference, and the first video and audio track. Extra audio tracks and subtitles are left out: they
    are bandwidth we would carry and FFmpeg would discard.
    """
    pcr = parts.get("pcr_pid") or 0
    pmt = parts.get("pmt_pid") or 0
    if not parts.get("video") and not parts.get("audio"):
        return []                                # nothing was read: fall back to the whole multiplex
    pids = {0}                                   # 0 is the PAT, and zero is a real pid, not "unset"
    for pid in (pmt, pcr):
        if pid:
            pids.add(pid)
    if parts.get("video"):
        pids.add(parts["video"][0]["pid"])
    if parts.get("audio"):
        pids.add(parts["audio"][0]["pid"])
    return sorted(pids)


def _remember_tuning(channel_id: int, won: dict) -> None:
    """Save the parameters that actually locked, so this channel starts straight away next time."""
    try:
        with db() as con:
            row = con.execute("SELECT tune FROM tv_channels WHERE id = ?", (channel_id,)).fetchone()
            if not row:
                return
            tune = json.loads(row["tune"] or "{}")
            tune.update({k: v for k, v in won.items() if k != "label"})
            con.execute("UPDATE tv_channels SET tune = ? WHERE id = ?", (json.dumps(tune), channel_id))
    except (OSError, ValueError) as ex:
        log.info("could not save the corrected tuning for channel %s: %s", channel_id, ex)


def _programme(tuner: dvb.Tuner, service_id: int) -> dict:
    """What this service carries, from the stream's own tables. Empty if it doesn't answer in time."""
    empty = {"video": [], "audio": [], "other": []}
    pmt_pid = 0
    for section in tuner.section(0x00, 0x00, timeout=3.0, want=1):
        pmt_pid = dvb.parse_pat(section).get(service_id, 0)
        if pmt_pid:
            break
    if not pmt_pid:
        log.info("no PMT pid for service %d — letting FFmpeg work the programme out", service_id)
        return empty
    for section in tuner.section(pmt_pid, 0x02, timeout=3.0, want=1):
        pmt = dvb.parse_pmt(section)
        if pmt.get("service_id") == service_id:
            return {**pmt, "pmt_pid": pmt_pid}
    return empty


def build_command(f: Feed, dvr_path: str, copy_video: bool, copy_audio: bool) -> list[str]:
    """FFmpeg reading the multiplex off the dvr device and writing a sliding HLS window.

    A live multiplex never ends and has no neat beginning: FFmpeg is told to expect that, to take its
    timing from the stream, and to pick one programme out by service id rather than guessing.
    """
    if DEV_VP9:                      # development only: a browser build without H.264 or AAC
        copy_video = copy_audio = False
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if not copy_video and hw["accel"] == "nvenc":
        cmd += ["-hwaccel", "cuda"]
    elif not copy_video and hw["accel"] == "vaapi":
        cmd += ["-vaapi_device", get_setting("vaapi_device") or "/dev/dri/renderD128"]
    cmd += ["-fflags", "+genpts+discardcorrupt", "-analyzeduration", "4M", "-probesize", "12M",
            "-err_detect", "ignore_err", "-f", "mpegts", "-i", dvr_path]
    # -map by programme: a multiplex holds a dozen channels and they all arrive on the same device.
    cmd += ["-map", f"0:p:{f.service_id}:v:0?", "-map", f"0:p:{f.service_id}:a:0?"]
    if copy_video:
        cmd += ["-c:v", "copy"]
    elif DEV_VP9:
        cmd += ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "1500k",
                "-g", str(SEGMENT_SECONDS * 25)]
    else:
        bitrate = int(get_setting("livetv_bitrate") or 4_000_000)
        if hw["accel"] == "nvenc":
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "cbr", "-b:v", str(bitrate),
                    "-profile:v", "high", "-forced-idr", "1"]
        elif hw["accel"] == "qsv":
            cmd += ["-c:v", "h264_qsv", "-preset", "faster", "-b:v", str(bitrate)]
        elif hw["accel"] == "vaapi":
            cmd += ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-b:v", str(bitrate)]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                    "-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.3)),
                    "-bufsize", str(bitrate * 2), "-profile:v", "high"]
        cmd += ["-g", str(SEGMENT_SECONDS * 25), "-sc_threshold", "0"]
    if copy_audio:
        cmd += ["-c:a", "copy"]
    elif DEV_VP9:
        cmd += ["-c:a", "libopus", "-ac", "2", "-b:a", "128k"]
    else:
        # aresample fills what a dropped frame left behind rather than letting the gap travel downstream.
        cmd += ["-af", "aresample=async=1:min_hard_comp=0.100:first_pts=0",
                "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
    # VP9 cannot travel in an MPEG-TS segment, so development uses fragmented MP4 as the film side does.
    ext = "m4s" if DEV_VP9 else "ts"
    # A live multiplex is not a clean file. One corrupt audio frame must not hold up the video: without
    # this the muxer waits for the lagging stream, stops writing segments, and the channel starves while
    # FFmpeg is still happily running.
    cmd += ["-max_interleave_delta", "0", "-avoid_negative_ts", "disabled",
            "-sn", "-dn", "-max_muxing_queue_size", "4096",
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_list_size", str(WINDOW),
            "-hls_flags", "delete_segments+independent_segments+temp_file+omit_endlist",
            "-hls_delete_threshold", "10",
            "-hls_segment_type", "fmp4" if DEV_VP9 else "mpegts",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_allow_cache", "0", "-start_number", "0",
            "-hls_segment_filename", str(f.directory / f"seg%05d.{ext}"), str(f.directory / "index.m3u8")]
    return cmd


def _watch_errors(f: Feed) -> None:
    """Keep FFmpeg's complaints, so a feed that dies can say why rather than just stopping."""
    if not f.proc or not f.proc.stderr:
        return
    last = ""
    for raw in f.proc.stderr:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            last = line
            f.say(line)
            log.info("%s: %s", f.name, line)
    code = f.proc.wait()
    # 255 is what FFmpeg returns when it is asked to stop and winds up cleanly: not a fault — unless it
    # never produced a single segment, in which case something was wrong from the start.
    clean = (0, 255, -signal.SIGTERM, -signal.SIGKILL)
    if (code not in clean or not f.segments) and not f.stopping and not f.error:
        f.error = last or f"the stream stopped (code {code})"
        log.warning("%s: %s", f.name, f.error)


def wait_for_start(f: Feed, timeout: float = 0, want: int = 2) -> bool:
    """Wait until the playlist has a couple of segments in it, not just one.

    Handing a live player a playlist with a single segment is what makes a channel show a second of
    picture and then sit buffering: it plays what little there is and runs out before the next one is
    written. A few segments in hand means playback starts behind the live edge and stays there.
    """
    timeout = timeout or (START_TIMEOUT if f.mode == "copy" else TRANSCODE_TIMEOUT)
    playlist = f.directory / "index.m3u8"
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while time.monotonic() < deadline:
        if not f.alive():
            # It has gone. The thread reading its complaints may not have caught up yet, and without
            # that the only thing left to say is "did not start", which explains nothing.
            for _ in range(20):
                if f.error:
                    break
                time.sleep(0.1)
            if not f.error:
                code = f.proc.returncode if f.proc else None
                f.error = f"FFmpeg stopped at once (exit code {code}) without saying why"
            f.say(f"gave up: {f.error}")
            return False
        if f.error:
            f.say(f"gave up: {f.error}")
            return False
        try:
            text = playlist.read_text(errors="ignore") if playlist.exists() else ""
        except OSError:
            text = ""
        n = len(re.findall(r"\.(?:ts|m4s)\b", text))
        f.segments = max(f.segments, n)
        if n >= want:
            f.say(f"ready: {n} segments after {time.monotonic() - started:.1f}s")
            return True
        time.sleep(0.2)
    # Out of time, but one segment is still better than nothing: let the player try.
    if f.segments:
        f.say(f"slow: only {f.segments} segment(s) in {timeout:.0f}s — starting anyway")
        return True
    f.say(f"no segments at all in {timeout:.0f}s")
    return False


# ---------------------------------------------------------------- viewers coming and going
def known_viewer(f: Feed, token: str) -> bool:
    """Was this token handed out for this feed? Until 1.19.0 nothing asked.

    `touch` below updates a token it recognises and **silently does nothing otherwise**, which is the
    right behaviour for a keep-alive and was quietly load-bearing as an access check. It is not one: the
    playlist and the segments were served to anyone who named the feed, and `/api/livetv/live` handed
    out every feed's id to any signed-in user. So one viewer could watch another's stream. The id is now
    admin-only and the token is actually required.
    """
    with _lock:
        return token in f.viewers


def touch(f: Feed, token: str) -> None:
    with _lock:
        if token in f.viewers:
            f.viewers[token] = time.time()
            f.last_empty = 0.0


def leave(f: Feed, token: str) -> None:
    with _lock:
        f.viewers.pop(token, None)
        if not f.viewers:
            f.last_empty = time.time()
    log.info("%s: a viewer left (%d left)", f.name, len(f.viewers))


def stop(fid: str) -> bool:
    """Shut a feed down and only then declare its input free.

    Letting go of the entry first would let the next viewer claim an input whose frontend is still
    being closed — the tuner is then asked to lock while the driver is still settling, and fails.
    """
    with _lock:
        f = _feeds.get(fid)
        if f is None or f.stopping:
            return False
        f.stopping = True
    _teardown(f)
    with _lock:
        _feeds.pop(fid, None)
    return True


def stop_all() -> None:
    for fid in [f.id for f in active()]:
        stop(fid)


def failures() -> list[dict]:
    """Channels that would not start lately. A feed is gone by the time anyone asks what happened."""
    with _lock:
        return list(_failures)


def _teardown(f: Feed) -> None:
    """Let go of everything, in the order that stops the data first and frees the tuner last."""
    if not f.segments:                            # it never produced anything: worth remembering why
        with _lock:
            _failures.append({"name": f.name, "channel_id": f.channel_id, "when": time.time(),
                              "input": f"{f.adapter}.{f.index}", "mode": f.mode,
                              "video": f.video, "audio": f.audio, "error": f.error,
                              "lines": list(f.notes)})
            del _failures[:-5]
    f.stopping = True
    if f.proc and f.proc.poll() is None:
        try:
            os.killpg(os.getpgid(f.proc.pid), signal.SIGTERM)
            f.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(f.proc.pid), signal.SIGKILL)
            except OSError:
                pass
    for fd in f.demux_fds:
        try:
            os.close(fd)
        except OSError:
            pass
    f.demux_fds = []
    if f.tuner:
        f.tuner.close()
        f.tuner = None
    try:
        for name in os.listdir(f.directory):
            os.unlink(f.directory / name)
        os.rmdir(f.directory)
    except OSError:
        pass
    log.info("%s: stopped, input %d.%d free again", f.name, f.adapter, f.index)


def _teardown_proc(f: Feed) -> None:
    """Stop the converter but keep the tuner, the filters and the segments on disk."""
    if f.proc and f.proc.poll() is None:
        try:
            os.killpg(os.getpgid(f.proc.pid), signal.SIGTERM)
            f.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(f.proc.pid), signal.SIGKILL)
            except OSError:
                pass


def _restart(f: Feed) -> None:
    """Start the converter again on a feed whose tuner is still good."""
    f.restarts += 1
    was = f.error or "it stopped"
    f.error = ""
    # Carry on numbering where the last run left off. Starting again from zero would rewind the
    # playlist under a player that is partway through it, which it reads as the stream breaking.
    cmd = list(f.cmd)
    if "-start_number" in cmd:
        cmd[cmd.index("-start_number") + 1] = str(f.segments + 2)
    f.say(f"the converter stopped ({was}); starting it again from segment {f.segments + 2} "
          f"— attempt {f.restarts} of {MAX_RESTARTS}")
    log.info("%s: restarting FFmpeg (%d/%d) after: %s", f.name, f.restarts, MAX_RESTARTS, was)
    try:
        f.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, start_new_session=True)
    except OSError as ex:
        f.error = f"could not start the converter again: {ex}"
        return
    threading.Thread(target=_watch_errors, args=(f,), daemon=True).start()


def _keep(interval: float = 5.0) -> None:
    """Drop viewers who stopped asking, and feeds nobody is watching. A tuner is worth reclaiming."""
    while True:
        time.sleep(interval)
        now = time.time()
        for f in active():
            with _lock:
                gone = [t for t, seen in f.viewers.items() if now - seen > VIEWER_TIMEOUT]
                for t in gone:
                    f.viewers.pop(t, None)
                if gone and not f.viewers:
                    f.last_empty = now
            # How many segments have been written in all, not how many are on disk: the window slides,
            # so counting files would sit at twelve for ever and say nothing about whether it is keeping up.
            try:
                made = [int(m.group(1)) for m in
                        (re.match(r"seg(\d+)\.(?:ts|m4s)$", x) for x in os.listdir(f.directory)) if m]
            except OSError:
                made = []
            n = (max(made) + 1) if made else 0
            f.segments = max(f.segments, n)
            stuck = bool(n) and n == f.last_count and f.alive()
            f.stalled = f.stalled + 1 if stuck else 0
            f.last_count = n
            if stuck and f.viewers and f.stalled == 4 and f.restarts < MAX_RESTARTS:
                # Still running, but producing nothing — a stream it cannot get past. Picking it up
                # costs a two-second gap; leaving it costs the channel.
                f.say("the converter is running but has written nothing for 20 seconds")
                f.stalled = 0
                _teardown_proc(f)
                _restart(f)
            elif stuck and not f.viewers and f.stalled >= CACHE_DEAD:
                # Warm, but no longer producing anything. Coming back to it would find a dead stream, so
                # it is not worth an input: let it go and leave the tuner for something that works.
                log.info("%s: kept warm but writing nothing — letting it go", f.name)
                stop(f.id)
                continue
            if not f.alive() and f.viewers and f.restarts < MAX_RESTARTS:
                # The tuner is still ours and still locked; only the converter fell over. Broadcast
                # streams glitch — a moment of interference is enough — and ending the channel for
                # that is far worse than a two-second gap while FFmpeg is started again.
                _restart(f)
            elif f.error or not f.alive():
                log.info("%s: %s", f.name, f.error or "the stream ended")
                stop(f.id)
            elif not f.viewers and f.last_empty and now - f.last_empty > CACHE_IDLE:
                log.info("%s: nobody has watched it for %d minutes — letting the tuner go",
                         f.name, CACHE_IDLE // 60)
                stop(f.id)

        trim_cache()


def cached() -> list["Feed"]:
    """The channels being kept warm, the one left longest first — the order they would be given up in."""
    return sorted((f for f in active() if not f.viewers and f.last_empty), key=lambda f: f.last_empty)


def trim_cache() -> list[str]:
    """Keep no more than CACHE_MAX channels warm, dropping the ones left longest ago.

    The ones you left most recently are the likeliest to be wanted again, so it is the stalest that
    gives way — the same order in which a channel somebody wants now takes an input back.
    """
    warm = cached()
    dropped = []
    for spare in warm[:max(0, len(warm) - CACHE_MAX)]:
        log.info("%s: more than %d channels kept warm — dropping the one left longest",
                 spare.name, CACHE_MAX)
        stop(spare.id)
        dropped.append(spare.id)
    return dropped


def start_keeper() -> None:
    global _keeper
    with _lock:
        if _keeper and _keeper.is_alive():
            return
        _keeper = threading.Thread(target=_keep, daemon=True, name="livestream-keeper")
        _keeper.start()

