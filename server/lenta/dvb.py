"""Talking to USB TV tuners (Linux DVB API) with nothing but Python.

A tuner shows up as /dev/dvb/adapter<N>/frontend<M> (what to tune), demux<M> (which parts of the
stream to pick out) and dvr<M> (the stream itself). Everything here is done with ioctls, so LENTA
needs no extra packages — the same approach as the Android app builder.

The numbers below were read from this machine's own /usr/include/linux/dvb headers, so they are the
kernel's, not a guess:

    FE_GET_INFO        0x80a86f3d   struct dvb_frontend_info   168 bytes
    FE_SET_PROPERTY    0x40106f52   struct dtv_properties       16 bytes
    FE_GET_PROPERTY    0x80106f53   struct dtv_property         76 bytes (packed)
    FE_READ_STATUS     0x80046f45
    DMX_SET_FILTER     0x403c6f2b   struct dmx_sct_filter_params 60 bytes
    DMX_SET_PES_FILTER 0x40146f2c   struct dmx_pes_filter_params 20 bytes
    DMX_ADD_PID        0x40026f33   __u16

What the rest of LENTA uses:
    adapters()                 the tuners plugged in right now, with their names and standards
    Tuner(...)                 tune a frequency and read the stream's own tables
    Tuner.tap_service()        send one programme's packets to the dvr device, for watching a channel
    parse_pat/sdt/nit(...)     those tables: services, their names, the network they belong to
"""
from __future__ import annotations

import calendar
import ctypes
import fcntl
import glob
import os
import re
import struct
import time
from dataclasses import dataclass, field

DVB_ROOT = "/dev/dvb"

FE_GET_INFO = 0x80A86F3D
FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53
FE_READ_STATUS = 0x80046F45

DMX_START = 0x6F29
DMX_STOP = 0x6F2A
DMX_SET_FILTER = 0x403C6F2B
DMX_SET_PES_FILTER = 0x40146F2C
DMX_ADD_PID = 0x40026F33     # add another pid to a filter that is already running
DMX_REMOVE_PID = 0x40026F34
DMX_SET_BUFFER_SIZE = 0x6F2D
DMX_IMMEDIATE_START = 4
DMX_CHECK_CRC = 1

DMX_IN_FRONTEND = 0          # take the data from the tuner, not from a file played back into the demux
DMX_OUT_TS_TAP = 2           # ...and send it out of the adapter's dvr device
DMX_PES_OTHER = 20           # not video or audio in particular: pass it through untouched
PID_WHOLE_MUX = 0x2000       # the magic pid meaning "every packet on this multiplex"

FE_HAS_SIGNAL = 0x01
FE_HAS_CARRIER = 0x02        # the demodulator has found the multiplex's carrier...
FE_HAS_VITERBI = 0x04        # ...and is decoding it...
FE_HAS_SYNC = 0x08           # ...and has found the frame boundaries
FE_HAS_LOCK = 0x10           # ...and is fully locked: data is flowing
FE_TIMEDOUT = 0x20

# the few properties we set or read
DTV_TUNE, DTV_CLEAR = 1, 2
DTV_FREQUENCY, DTV_MODULATION, DTV_BANDWIDTH_HZ = 3, 4, 5
DTV_INVERSION, DTV_SYMBOL_RATE, DTV_INNER_FEC = 6, 8, 9
DTV_DELIVERY_SYSTEM = 17
DTV_ENUM_DELSYS = 44
DTV_STREAM_ID = 42
DTV_STAT_SIGNAL_STRENGTH, DTV_STAT_CNR = 62, 63      # how modern drivers report signal and quality
SCALE_NONE, SCALE_DECIBEL, SCALE_RELATIVE, SCALE_COUNTER = 0, 1, 2, 3

# delivery systems, as the kernel numbers them
SYS = {0: "undefined", 1: "DVB-C", 2: "DVB-C/B", 3: "DVB-T", 5: "DVB-S", 6: "DVB-S2",
       8: "ISDB-T", 11: "ATSC", 16: "DVB-T2", 18: "DVB-C/C"}
SYS_ID = {v: k for k, v in SYS.items()}
# "let the tuner work it out" — the values are the kernel's own enums, read from its headers
QAM_AUTO, FEC_AUTO, INVERSION_AUTO = 6, 9, 2          # fe_modulation / fe_code_rate / fe_spectral_inversion
TRANSMISSION_MODE_AUTO, GUARD_INTERVAL_AUTO = 2, 4
QAM = {16: 1, 32: 2, 64: 3, 128: 4, 256: 5}           # fe_modulation QAM_16 … QAM_256
FE_READ_SIGNAL_STRENGTH, FE_READ_SNR = 0x80026F47, 0x80026F48

# what a group of delivery systems means to a person
FAMILY = {"DVB-T": "aerial", "DVB-T2": "aerial", "ATSC": "aerial", "ISDB-T": "aerial",
          "DVB-C": "cable", "DVB-C/B": "cable", "DVB-C/C": "cable",
          "DVB-S": "satellite", "DVB-S2": "satellite"}


# ---------------------------------------------------------------- finding the tuners
@dataclass
class Frontend:
    """One tunable input. A twin tuner has two of these, usually on one adapter."""
    adapter: int
    index: int
    name: str = ""
    delsys: list[str] = field(default_factory=list)
    product: str = ""          # what the USB stick calls itself
    vendor: str = ""
    usb_id: str = ""           # 1234:5678
    serial: str = ""
    error: str = ""            # why we couldn't ask it anything (busy, no permission…)
    in_use: bool = False       # delivering a channel: left alone rather than questioned

    @property
    def path(self) -> str:
        return f"{DVB_ROOT}/adapter{self.adapter}/frontend{self.index}"

    @property
    def demux(self) -> str:
        return f"{DVB_ROOT}/adapter{self.adapter}/demux{self.index}"

    @property
    def dvr(self) -> str:
        return f"{DVB_ROOT}/adapter{self.adapter}/dvr{self.index}"

    @property
    def key(self) -> str:
        """Stable name for this input: the USB serial when there is one, else its place in /dev.
        A twin tuner has two inputs on one stick, so the adapter number is part of it either way."""
        if self.serial:
            return f"{self.usb_id}:{self.serial}:{self.adapter}.{self.index}"
        return f"adapter{self.adapter}/frontend{self.index}"

    @property
    def device(self) -> str:
        """The stick this input belongs to. A twin tuner has two inputs and one stick, and that is
        what a person sees on the desk — so LENTA counts devices, not inputs.

        A serial number identifies a stick exactly, even with two of the same model plugged in. With no
        serial the safe assumption is one stick per adapter, so they stay apart."""
        if self.usb_id and self.serial:
            return f"usb:{self.usb_id}:{self.serial}"
        return f"adapter{self.adapter}"

    @property
    def families(self) -> list[str]:
        out = []
        for s in self.delsys:
            f = FAMILY.get(s)
            if f and f not in out:
                out.append(f)
        return out

    def as_dict(self) -> dict:
        return {"adapter": self.adapter, "index": self.index, "path": self.path, "key": self.key, "device": self.device,
                "name": self.name, "delsys": self.delsys, "families": self.families,
                "product": self.product, "vendor": self.vendor, "usb_id": self.usb_id,
                "serial": self.serial, "error": self.error, "in_use": self.in_use}


def _sysfs_usb(adapter: int, index: int) -> dict:
    """Walk up from the frontend's device to the USB stick it belongs to, for its real name."""
    out = {}
    start = f"/sys/class/dvb/dvb{adapter}.frontend{index}/device"
    try:
        path = os.path.realpath(start)
    except OSError:
        return out
    for _ in range(8):                       # frontend → i2c/usb interface → usb device
        if not path or path == "/":
            break
        if os.path.exists(os.path.join(path, "idVendor")):
            def rd(name):
                try:
                    with open(os.path.join(path, name)) as fh:
                        return fh.read().strip()
                except OSError:
                    return ""
            out = {"product": rd("product"), "vendor": rd("manufacturer"),
                   "usb_id": f"{rd('idVendor')}:{rd('idProduct')}", "serial": rd("serial")}
            break
        path = os.path.dirname(path)
    return out


def _frontend_info(fd: int) -> tuple[str, int]:
    buf = bytearray(168)
    fcntl.ioctl(fd, FE_GET_INFO, buf, True)
    name = bytes(buf[:128]).split(b"\0", 1)[0].decode("utf-8", "replace")
    (fe_type,) = struct.unpack_from("<i", buf, 128)
    return name, fe_type


def _props_struct(props: bytearray) -> bytearray:
    """struct dtv_properties { __u32 num; struct dtv_property *props; } — the pointer must be ours."""
    n = len(props) // 76
    holder = (ctypes.c_char * len(props)).from_buffer(props)
    return bytearray(struct.pack("<I4xQ", n, ctypes.addressof(holder))), holder


def _prop(cmd: int, data: int = 0) -> bytes:
    p = bytearray(76)
    struct.pack_into("<I", p, 0, cmd)
    struct.pack_into("<I", p, 16, data & 0xFFFFFFFF)
    return bytes(p)


def enum_delsys(fd: int) -> list[str]:
    """Which standards this input can tune (DVB-T2, DVB-C…). Empty when the driver won't say."""
    props = bytearray(_prop(DTV_ENUM_DELSYS))
    head, _hold = _props_struct(props)
    try:
        fcntl.ioctl(fd, FE_GET_PROPERTY, head, True)
    except OSError:
        return []
    count = struct.unpack_from("<I", props, 48)[0]          # u.buffer.len
    raw = props[16:16 + min(count, 32)]                     # u.buffer.data
    return [SYS[b] for b in raw if b in SYS and b != 0]


def _read_frontend(adapter: int, index: int) -> Frontend:
    fe = Frontend(adapter=adapter, index=index)
    fe.__dict__.update({k: v for k, v in _sysfs_usb(adapter, index).items() if v})
    try:
        fd = os.open(fe.path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as ex:
        fe.error = "busy" if ex.errno == 16 else ("no permission" if ex.errno in (1, 13) else ex.strerror or str(ex))
        return fe
    try:
        fe.name, fe_type = _frontend_info(fd)
        fe.delsys = enum_delsys(fd)
        if not fe.delsys:                                   # old driver: the deprecated type field
            fe.delsys = [{0: "DVB-S", 1: "DVB-C", 2: "DVB-T", 3: "ATSC"}.get(fe_type, "undefined")]
    except OSError as ex:
        fe.error = ex.strerror or str(ex)
    finally:
        os.close(fd)
    # some drivers only name the chip ("Sony CXD2841ER"); the stick's own name reads better
    if not fe.product and fe.name:
        fe.product = fe.name
    return fe


CABLE_RATES = (6_900_000, 6_875_000, 6_111_000)      # what European cable networks actually use


def lock(tuner: "Tuner", params: dict, wait: float = 2.5, tried: list | None = None) -> dict | None:
    """Tune, trying the ways this frequency might really be carried. Returns what worked, or None.

    A cable network picks its own symbol rate and its own modulation, and a terrestrial frequency may
    be DVB-T or DVB-T2. The network's own table says which — but it is not always right, and a tuner
    that is told QAM-256 when the mux is QAM-64 simply never locks. So the saved values are tried
    first, then the demodulator is asked to work the modulation out for itself, then the symbol rates
    European networks actually use.

    Whoever locks must be told which attempt succeeded: saving the parameters that were asked for
    rather than the ones that worked is how a channel ends up scanned but unwatchable. Pass `tried` to
    collect a line per attempt — without it, a failure can only say "no lock", which explains nothing.
    """
    delsys = params.get("delsys") or "DVB-T"
    freq = params["frequency"]
    kw = {k: v for k, v in params.items() if k in ("bandwidth", "symbol_rate", "modulation")}
    tries = [(delsys, kw)]
    if delsys == "DVB-T":
        tries.append(("DVB-T2", kw))
    elif delsys.startswith("DVB-C"):
        rates = [kw["symbol_rate"]] if kw.get("symbol_rate") else []
        rates += [r for r in CABLE_RATES if r != kw.get("symbol_rate")]
        mods = [kw["modulation"]] if kw.get("modulation") not in (None, QAM_AUTO) else []
        mods.append(QAM_AUTO)                      # let the demodulator decide: it usually can
        tries = [(delsys, {**kw, "symbol_rate": r, "modulation": m})
                 for m in mods for r in rates] or tries
    promising = None            # an attempt that found the carrier but ran out of time
    for i, (ds, extra) in enumerate(tries):
        ok = tuner.tune(ds, freq, wait=wait if i == 0 else wait * 0.8, **extra)
        if not ok and promising is None:
            try:
                if tuner.status() & (FE_HAS_CARRIER | FE_HAS_VITERBI | FE_HAS_SYNC):
                    promising = (ds, extra)
            except OSError:
                pass
        if tried is not None:
            rate = extra.get("symbol_rate")
            try:                                   # a note about the attempt must never break the attempt
                reached = flag_text(tuner.status())
            except (OSError, AttributeError) as ex:
                reached = f"the driver would not say ({ex})"
            tried.append(f"{ds} {freq // 1_000_000} MHz"
                         + (f" {rate // 1000} kBd" if rate else "")
                         + f" {MODULATION_NAMES.get(extra.get('modulation'), extra.get('modulation'))}"
                         + f" -> {reached}")
        if ok:
            return {**params, "delsys": ds, **extra}
    # One of those found the carrier and simply did not finish in time. That is the right combination
    # almost by definition — nothing else even sees the multiplex — so give it a proper go. A weak or
    # noisy feed can take several seconds to pull in, and 2.5 is a scanning budget, not a lock budget.
    if promising is not None:
        ds, extra = promising
        if tuner.tune(ds, freq, wait=max(wait * 4, 12.0), **extra):
            if tried is not None:
                tried.append(f"{ds} {freq // 1_000_000} MHz"
                             + (f" {extra['symbol_rate'] // 1000} kBd" if extra.get("symbol_rate") else "")
                             + " given longer -> LOCK")
            return {**params, "delsys": ds, **extra}
        if tried is not None:
            tried.append(f"{ds} {freq // 1_000_000} MHz given {max(wait * 4, 12.0):.0f}s -> still no lock")
    return None


MODULATION_NAMES = {None: "auto", QAM_AUTO: "auto", 1: "QAM-16", 2: "QAM-32", 3: "QAM-64",
                    4: "QAM-128", 5: "QAM-256"}


def flag_text(status: int) -> str:
    """What the demodulator reached, in words: signal, carrier, viterbi, sync, LOCK."""
    names = [(1, "signal"), (2, "carrier"), (4, "viterbi"), (8, "sync"), (16, "LOCK"), (32, "timed out")]
    on = [n for bit, n in names if status & bit]
    return ", ".join(on) if on else "nothing"


def adapters(skip: set[tuple[int, int]] | None = None) -> list[Frontend]:
    """Every tuner input plugged in right now, in /dev order.

    `skip` names inputs that must not be touched — ones currently delivering a channel. Asking a
    frontend what it is means opening it and sending it control commands, and on a USB stick (more so
    one passed through to a virtual machine) that traffic competes with the stream it is carrying.
    A skipped input is reported as present, with nothing read from the hardware.
    """
    out = []
    for path in sorted(glob.glob(f"{DVB_ROOT}/adapter*/frontend*")):
        m = re.search(r"adapter(\d+)/frontend(\d+)$", path)
        if not m:
            continue
        adapter, index = int(m.group(1)), int(m.group(2))
        if skip and (adapter, index) in skip:
            fe = Frontend(adapter=adapter, index=index)
            fe.__dict__.update({k: v for k, v in _sysfs_usb(adapter, index).items() if v})
            fe.in_use = True
            out.append(fe)
        else:
            out.append(_read_frontend(adapter, index))
    return out


def present() -> bool:
    """Is there a DVB tree at all? (No tree = no tuner, or its driver isn't loaded.)"""
    return os.path.isdir(DVB_ROOT)


# ---------------------------------------------------------------- tuning and reading tables
class Tuner:
    """Tunes one frequency and reads the stream's own tables. Use it in a `with` block."""

    def __init__(self, fe: Frontend):
        self.fe = fe
        self.fd = os.open(fe.path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self) -> "Tuner":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def set(self, pairs: list[tuple[int, int]]) -> None:
        props = bytearray(b"".join(_prop(c, v) for c, v in pairs))
        head, _hold = _props_struct(props)
        fcntl.ioctl(self.fd, FE_SET_PROPERTY, head, True)

    def status(self) -> int:
        buf = bytearray(4)
        fcntl.ioctl(self.fd, FE_READ_STATUS, buf, True)
        return struct.unpack("<I", buf)[0]

    def signal(self) -> dict:
        """How strong and how clean the signal is.

        Drivers written this decade answer through DTV statistics, in dBm and dB; the old ioctls that
        returned a 0-65535 number are left unimplemented by many of them (the Si2168 among them), which
        is why asking the old way gives a flat zero. Ask the new way first, then fall back.
        """
        out = {"strength_db": None, "quality_db": None, "strength": None, "snr": None}
        props = bytearray(_prop(DTV_STAT_SIGNAL_STRENGTH) + _prop(DTV_STAT_CNR))
        head, _hold = _props_struct(props)
        try:
            fcntl.ioctl(self.fd, FE_GET_PROPERTY, head, True)
            for n, key in ((0, "strength"), (1, "quality")):
                base = n * 76 + 16                       # u.st
                count = props[base]
                if not count:
                    continue
                scale = props[base + 1]
                (value,) = struct.unpack_from("<q", props, base + 2)
                if scale == SCALE_DECIBEL:
                    out[f"{key}_db"] = value / 1000.0    # dBm for strength, dB for quality
                elif scale == SCALE_RELATIVE:
                    out["strength" if key == "strength" else "snr"] = value
        except OSError:
            pass
        for key, req in (("strength", FE_READ_SIGNAL_STRENGTH), ("snr", FE_READ_SNR)):
            if out[key] is not None:
                continue
            buf = bytearray(2)
            try:
                fcntl.ioctl(self.fd, req, buf, True)
                out[key] = struct.unpack("<H", buf)[0]
            except OSError:
                pass
        return out

    def signal_text(self) -> str:
        """The readings in words, saying so plainly when the driver reports nothing."""
        sig = self.signal()
        bits = []
        if sig["strength_db"] is not None:
            bits.append(f"strength {sig['strength_db']:.1f} dBm")
        elif sig["strength"]:
            bits.append(f"strength {sig['strength'] * 100 // 65535}%")
        if sig["quality_db"] is not None:
            bits.append(f"quality {sig['quality_db']:.1f} dB")
        elif sig["snr"]:
            bits.append(f"quality {sig['snr'] * 100 // 65535}%")
        return " · ".join(bits) or "the driver reports no readings"

    def tune(self, delsys: str, frequency: int, *, bandwidth: int = 8_000_000,
             symbol_rate: int = 0, modulation: int = QAM_AUTO, fec: int = FEC_AUTO,
             stream_id: int | None = None, wait: float = 2.0) -> bool:
        """Tune and wait for a lock. frequency in Hz (satellite: kHz, as the kernel wants)."""
        sysid = SYS_ID.get(delsys)
        if sysid is None:
            raise ValueError(f"unknown delivery system {delsys}")
        pairs = [(DTV_CLEAR, 0), (DTV_DELIVERY_SYSTEM, sysid), (DTV_FREQUENCY, frequency),
                 (DTV_INVERSION, INVERSION_AUTO)]
        if delsys in ("DVB-T", "DVB-T2"):
            pairs.append((DTV_BANDWIDTH_HZ, bandwidth))
        if delsys.startswith("DVB-C") or delsys.startswith("DVB-S"):
            pairs += [(DTV_SYMBOL_RATE, symbol_rate), (DTV_INNER_FEC, fec)]
        if delsys.startswith("DVB-C") or delsys == "ATSC":
            pairs.append((DTV_MODULATION, modulation))
        if stream_id is None and delsys == "DVB-T2":
            stream_id = 0                      # the usual PLP; some demods need it said out loud
        if stream_id is not None:
            pairs.append((DTV_STREAM_ID, stream_id))
        pairs.append((DTV_TUNE, 0))
        self.set(pairs)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.status() & FE_HAS_LOCK:
                return True
            time.sleep(0.05)
        return False

    def tap_mux(self, buffer_bytes: int = 8 << 20) -> int:
        """Send every packet on this multiplex out of the adapter's dvr device, and return the demux fd.

        This is how a channel gets watched. The tuner is already locked to the frequency; this asks the
        demux for the whole multiplex rather than one PID, so whatever reads the dvr device sees a
        complete transport stream and can pick the programme out of it by service id.

        Three file handles have to stay open for the data to keep flowing: this tuner's frontend (which
        holds the lock), the returned demux fd (which holds the filter), and whatever opens the dvr
        device to read. Closing any of them stops the stream, so the caller owns all three.

        The buffer matters: the kernel's default is 8 kB, far too small for a whole multiplex, and an
        overrun shows up as stuttering rather than as an error.
        """
        fd = os.open(self.fe.demux, os.O_RDWR)
        try:
            fcntl.ioctl(fd, DMX_SET_BUFFER_SIZE, buffer_bytes)        # must come before the filter
            params = struct.pack("<H2xIIII", PID_WHOLE_MUX, DMX_IN_FRONTEND, DMX_OUT_TS_TAP,
                                 DMX_PES_OTHER, DMX_IMMEDIATE_START)
            fcntl.ioctl(fd, DMX_SET_PES_FILTER, params)
        except OSError:
            os.close(fd)
            raise
        return fd

    def tap_service(self, pids: list[int], buffer_bytes: int = 8 << 20) -> list[int]:
        """Send just these pids out of the dvr device. Returns the demux fds holding the filters.

        This is how a channel should be watched. Asking for the whole multiplex means carrying every
        channel on that frequency — on cable, fifty-odd megabits a second — when one programme needs
        under ten. Everything in between pays for it: the USB link, more so when the stick is passed
        through to a virtual machine, and the kernel's ring buffer, which holds barely a second of a
        full multiplex and several seconds of one programme. An overrun is not reported; it simply
        corrupts the stream, which looks like a channel that plays and then freezes.

        One filter per pid, each tapping the transport stream, and the kernel merges them onto the one
        dvr device. (The obvious shortcut — one filter and DMX_ADD_PID for the rest — is rejected with
        EINVAL: the kernel allows extra pids only on a filter that delivers to the demux handle itself,
        not to the dvr device.) Every fd must stay open for its pid to keep flowing, so the caller
        holds them all.
        """
        if not pids:
            return [self.tap_mux(buffer_bytes)]
        fds: list[int] = []
        try:
            for pid in sorted(set(pids)):
                fd = os.open(self.fe.demux, os.O_RDWR)
                fds.append(fd)
                fcntl.ioctl(fd, DMX_SET_BUFFER_SIZE, buffer_bytes)
                fcntl.ioctl(fd, DMX_SET_PES_FILTER,
                            struct.pack("<H2xIIII", pid, DMX_IN_FRONTEND, DMX_OUT_TS_TAP,
                                        DMX_PES_OTHER, DMX_IMMEDIATE_START))
        except OSError:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
        return fds

    def sections(self, pid: int, table_id: int, mask: int, seconds: float,
                 buffer_bytes: int = 1 << 20, alive=None):
        """Every table on one PID whose id matches `table_id` under `mask`, for `seconds`.

        `section` above fetches one table and stops, which suits the PAT and the PMT — small tables
        that repeat every few hundred milliseconds. The event tables are the opposite: a day of
        programmes for every service on the multiplex, spread over table ids 0x50 to 0x5F and hundreds
        of sections that take a minute or two to all come round. So this one keeps the filter open and
        yields whatever arrives, letting the caller stop when it has had enough.

        The mask is what makes a single filter enough: 0x50 under 0xF0 matches the whole schedule range
        in the hardware, instead of sixteen filters or sixteen passes.

        Opening this alongside a channel that is playing is deliberate and safe: it is a filter of its
        own on the demux, carrying only the event tables, and the picture goes on arriving through the
        separate stream the converter reads.
        """
        fd = os.open(self.fe.demux, os.O_RDWR | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, DMX_SET_BUFFER_SIZE, buffer_bytes)
            params = bytearray(60)
            struct.pack_into("<H", params, 0, pid)
            params[2] = table_id                      # filter[0]
            params[2 + 16] = mask                     # mask[0]
            struct.pack_into("<I", params, 52, 0)     # no timeout: we decide when to stop
            struct.pack_into("<I", params, 56, DMX_IMMEDIATE_START | DMX_CHECK_CRC)
            fcntl.ioctl(fd, DMX_SET_FILTER, params, True)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                # Asked between every read, not only when a table arrives: a multiplex that has gone
                # quiet would otherwise keep this filter open long after whoever opened it had finished,
                # on a frontend that may since have been retuned to something else entirely.
                if alive is not None and not alive():
                    break
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    time.sleep(0.05)
                    continue
                except OSError:
                    break
                if data and len(data) >= 12:
                    yield data
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def section(self, pid: int, table_id: int, timeout: float = 5.0, want: int = 1) -> list[bytes]:
        """Read whole SI tables off one PID (PAT, SDT, NIT…). The demux checks the CRC for us."""
        try:
            fd = os.open(self.fe.demux, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            return []
        try:
            fcntl.ioctl(fd, DMX_SET_BUFFER_SIZE, 256 * 1024)
            params = bytearray(60)
            struct.pack_into("<H", params, 0, pid)
            params[2] = table_id                     # filter[0]
            params[2 + 16] = 0xFF                    # mask[0]: match the table id exactly
            struct.pack_into("<I", params, 52, int(timeout * 1000))
            struct.pack_into("<I", params, 56, DMX_IMMEDIATE_START | DMX_CHECK_CRC)
            fcntl.ioctl(fd, DMX_SET_FILTER, params, True)
            out, seen, deadline = [], set(), time.monotonic() + timeout
            while len(out) < want and time.monotonic() < deadline:
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    time.sleep(0.02)
                    continue
                except OSError:
                    break
                if not data or len(data) < 8:
                    continue
                key = (data[3], data[4], data[6])    # version/extension/section number
                if key in seen:
                    if len(seen) >= want:
                        break
                    continue
                seen.add(key)
                out.append(data)
                last = data[7]                        # last_section_number
                if len(seen) > last and want == 1:
                    break
            return out
        finally:
            os.close(fd)


# ---------------------------------------------------------------- the stream's own tables
def _text(raw: bytes) -> str:
    """DVB text: the first byte can name the character set (ETSI EN 300 468)."""
    if not raw:
        return ""
    first = raw[0]
    enc, body = "latin-1", raw
    if first < 0x20:
        body = raw[1:]
        enc = {0x01: "iso-8859-5", 0x02: "iso-8859-6", 0x03: "iso-8859-7", 0x04: "iso-8859-8",
               0x05: "iso-8859-9", 0x06: "iso-8859-10", 0x09: "iso-8859-13", 0x0A: "iso-8859-14",
               0x0B: "iso-8859-15", 0x11: "utf-16-be", 0x15: "utf-8"}.get(first, "latin-1")
        if first == 0x10 and len(raw) >= 3:          # 0x10 00 xx = ISO-8859-xx
            enc, body = f"iso-8859-{raw[2]}", raw[3:]
    try:
        s = body.decode(enc, "replace")
    except LookupError:
        s = body.decode("latin-1", "replace")
    # control codes used for emphasis in DVB strings
    return "".join(ch for ch in s if ch >= " " or ch == "\n").strip()


def _descriptors(raw: bytes):
    i = 0
    while i + 2 <= len(raw):
        tag, length = raw[i], raw[i + 1]
        yield tag, raw[i + 2:i + 2 + length]
        i += 2 + length


def _sections_body(section: bytes) -> bytes:
    """Strip the section header and the CRC at the end."""
    return section[8:-4] if len(section) > 12 else b""


def parse_pat(section: bytes) -> dict[int, int]:
    """Programme number → PMT pid (programme 0 is the network table)."""
    body, out = _sections_body(section), {}
    for i in range(0, len(body) - 3, 4):
        num = (body[i] << 8) | body[i + 1]
        pid = ((body[i + 2] & 0x1F) << 8) | body[i + 3]
        if num:
            out[num] = pid
    return out


# What a stream type in the PMT means, and whether a browser can play it as it stands. Everything a
# browser can't take has to be re-encoded, so knowing this before starting saves probing the stream.
STREAM_TYPES = {
    0x01: ("video", "mpeg1video", False), 0x02: ("video", "mpeg2video", False),
    0x10: ("video", "mpeg4", False),      0x1B: ("video", "h264", True),
    0x24: ("video", "hevc", False),       0x42: ("video", "cavs", False),
    0x03: ("audio", "mp2", False),        0x04: ("audio", "mp2", False),
    0x0F: ("audio", "aac", True),         0x11: ("audio", "aac_latm", False),
    0x81: ("audio", "ac3", False),        0x06: ("audio", "", False),   # decided by its descriptors
    0x87: ("audio", "eac3", False),
}


def parse_pmt(section: bytes) -> dict:
    """One programme's parts: which pid carries the video, which the audio, and in what format.

    The private-data type 0x06 is whatever its descriptors say — AC-3, E-AC-3, subtitles or teletext —
    so the descriptor tag decides rather than the stream type.
    """
    if len(section) < 13:
        return {}
    out = {"service_id": (section[3] << 8) | section[4], "pcr_pid": ((section[8] & 0x1F) << 8) | section[9],
           "video": [], "audio": [], "other": []}
    info_len = ((section[10] & 0x0F) << 8) | section[11]
    i = 12 + info_len
    end = len(section) - 4                       # the last four bytes are the CRC
    while i + 5 <= end:
        stype = section[i]
        pid = ((section[i + 1] & 0x1F) << 8) | section[i + 2]
        dlen = ((section[i + 3] & 0x0F) << 8) | section[i + 4]
        kind, codec, native = STREAM_TYPES.get(stype, ("other", "", False))
        lang = ""
        for tag, data in _descriptors(section[i + 5:i + 5 + dlen]):
            if tag == 0x0A and len(data) >= 3:                       # ISO 639 language
                lang = data[0:3].decode("ascii", "replace").strip("\x00 ")
            elif tag == 0x6A:                                        # AC-3
                kind, codec = "audio", "ac3"
            elif tag == 0x7A:                                        # enhanced AC-3
                kind, codec = "audio", "eac3"
            elif tag == 0x7B:
                kind, codec = "audio", "dts"
            elif tag in (0x59, 0x56):                                # DVB subtitles, teletext
                kind, codec = "other", "dvb_subtitle" if tag == 0x59 else "teletext"
        if kind in ("video", "audio"):
            out[kind].append({"pid": pid, "codec": codec, "language": lang,
                              "browser_ready": native and bool(codec)})
        else:
            out["other"].append({"pid": pid, "codec": codec, "language": lang})
        i += 5 + dlen
    return out


SERVICE_TYPES = {0x01: "tv", 0x02: "radio", 0x11: "tv", 0x16: "tv", 0x19: "tv", 0x1F: "tv",
                 0x0A: "radio", 0x18: "radio", 0x0C: "data"}


def parse_sdt(section: bytes) -> dict:
    """Service names and who provides them (table 0x42 = the stream we are tuned to)."""
    if len(section) < 15:
        return {}
    tsid = (section[3] << 8) | section[4]
    onid = (section[8] << 8) | section[9]
    body, i, services = section[11:-4], 0, []
    while i + 5 <= len(body):
        sid = (body[i] << 8) | body[i + 1]
        free_ca = (body[i + 3] >> 4) & 0x01
        dlen = ((body[i + 3] & 0x0F) << 8) | body[i + 4]
        svc = {"service_id": sid, "scrambled": bool(free_ca), "name": "", "provider": "", "type": ""}
        for tag, data in _descriptors(body[i + 5:i + 5 + dlen]):
            if tag == 0x48 and len(data) >= 3:              # service_descriptor
                svc["type"] = SERVICE_TYPES.get(data[0], "other")
                plen = data[1]
                svc["provider"] = _text(data[2:2 + plen])
                nlen = data[2 + plen] if len(data) > 2 + plen else 0
                svc["name"] = _text(data[3 + plen:3 + plen + nlen])
        services.append(svc)
        i += 5 + dlen
    return {"tsid": tsid, "onid": onid, "services": services}


def parse_nit(section: bytes) -> dict:
    """The network: its name, the other frequencies it uses, and the channel numbers it asks for."""
    if len(section) < 16:
        return {}
    nid = (section[3] << 8) | section[4]
    net_desc_len = ((section[8] & 0x0F) << 8) | section[9]
    out = {"network_id": nid, "network_name": "", "transports": [], "lcn": {}}
    for tag, data in _descriptors(section[10:10 + net_desc_len]):
        if tag == 0x40:                                      # network_name_descriptor
            out["network_name"] = _text(data)
    i = 10 + net_desc_len
    if i + 2 > len(section):
        return out
    loop_len = ((section[i] & 0x0F) << 8) | section[i + 1]
    i += 2
    end = min(i + loop_len, len(section) - 4)
    while i + 6 <= end:
        tsid = (section[i] << 8) | section[i + 1]
        onid = (section[i + 2] << 8) | section[i + 3]
        dlen = ((section[i + 4] & 0x0F) << 8) | section[i + 5]
        for tag, data in _descriptors(section[i + 6:i + 6 + dlen]):
            if tag == 0x5A and len(data) >= 11:              # terrestrial delivery system
                freq = struct.unpack(">I", data[0:4])[0] * 10      # 10 Hz units
                bw = {0: 8_000_000, 1: 7_000_000, 2: 6_000_000, 3: 5_000_000}.get(data[4] >> 5, 8_000_000)
                out["transports"].append({"delsys": "DVB-T", "frequency": freq, "bandwidth": bw,
                                          "tsid": tsid, "onid": onid})
            elif tag == 0x44 and len(data) >= 11:            # cable delivery system
                freq = int(data[0:4].hex()) * 100            # BCD, 100 Hz units
                sr = int(data[7:11].hex()[:7] or 0) * 100    # BCD, symbols/s
                mod = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}.get(data[6], QAM_AUTO)     # QAM-16 … QAM-256
                out["transports"].append({"delsys": "DVB-C", "frequency": freq, "symbol_rate": sr,
                                          "modulation": mod, "tsid": tsid, "onid": onid})
            elif tag == 0x43 and len(data) >= 11:            # satellite delivery system
                freq = int(data[0:4].hex()) * 10             # BCD, 10 kHz units → kHz
                sr = int(data[7:11].hex()[:7] or 0) * 100
                s2 = bool(data[6] & 0x04)
                out["transports"].append({"delsys": "DVB-S2" if s2 else "DVB-S", "frequency": freq,
                                          "symbol_rate": sr, "tsid": tsid, "onid": onid})
            elif tag in (0x83, 0x87) and len(data) >= 4:     # logical channel numbers (HD simulcast too)
                for j in range(0, len(data) - 3, 4):
                    sid = (data[j] << 8) | data[j + 1]
                    out["lcn"][sid] = ((data[j + 2] & 0x03) << 8) | data[j + 3]
        i += 6 + dlen
    return out


# ---------------------------------------------------------------- the programme guide
EIT_PID = 0x12               # every event table arrives here
EIT_NOW_NEXT = 0x4E          # what is on and what follows, for the stream we are tuned to
EIT_SCHEDULE_FIRST = 0x50    # 0x50..0x5F carry the days ahead, for the same stream
EIT_SCHEDULE_LAST = 0x5F

CONTENT_GENRES = {           # the top nibble of the content descriptor (ETSI EN 300 468 table 28)
    0x1: "Film", 0x2: "News", 0x3: "Entertainment", 0x4: "Sport", 0x5: "Children",
    0x6: "Music", 0x7: "Arts", 0x8: "Social", 0x9: "Education", 0xA: "Leisure",
}


def _mjd_to_date(mjd: int) -> tuple[int, int, int]:
    """Modified Julian Date to year, month, day — the conversion in ETSI EN 300 468 annex C."""
    y = int((mjd - 15078.2) / 365.25)
    m = int((mjd - 14956.1 - int(y * 365.25)) / 30.6001)
    day = mjd - 14956 - int(y * 365.25) - int(m * 30.6001)
    k = 1 if m in (14, 15) else 0
    return 1900 + y + k, m - 1 - k * 12, day


def _dvb_time(raw: bytes) -> int | None:
    """Five bytes — two of Modified Julian Date, three of BCD clock — as a UTC timestamp.

    Broadcast times are UTC; what the viewer should see is their own clock, which is the player's job.
    """
    if len(raw) < 5 or raw[:5] == b"\xff\xff\xff\xff\xff":
        return None
    mjd = (raw[0] << 8) | raw[1]
    try:
        year, month, day = _mjd_to_date(mjd)
        hour, minute, second = (_bcd(raw[2]), _bcd(raw[3]), _bcd(raw[4]))
        return int(calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)))
    except (ValueError, OverflowError):
        return None


def _bcd(b: int) -> int:
    return (b >> 4) * 10 + (b & 0x0F)


def _dvb_duration(raw: bytes) -> int:
    """Three BCD bytes: hours, minutes, seconds."""
    if len(raw) < 3:
        return 0
    return _bcd(raw[0]) * 3600 + _bcd(raw[1]) * 60 + _bcd(raw[2])


def parse_eit(section: bytes) -> dict:
    """One event table: which service it describes and what is on it, with times and titles.

    Tables 0x4E (now and next) and 0x50–0x5F (the days ahead) are the ones worth reading; the
    equivalents for *other* transport streams describe muxes we are not tuned to and are skipped.
    """
    if len(section) < 18:
        return {}
    table_id = section[0]
    if table_id != EIT_NOW_NEXT and not (EIT_SCHEDULE_FIRST <= table_id <= EIT_SCHEDULE_LAST):
        return {}
    out = {"service_id": (section[3] << 8) | section[4],
           "tsid": (section[8] << 8) | section[9],
           "onid": (section[10] << 8) | section[11],
           "now_next": table_id == EIT_NOW_NEXT,
           "events": []}
    i, end = 14, len(section) - 4
    while i + 12 <= end:
        start = _dvb_time(section[i + 2:i + 7])
        seconds = _dvb_duration(section[i + 7:i + 10])
        running = (section[i + 10] >> 5) & 0x07
        dlen = ((section[i + 10] & 0x0F) << 8) | section[i + 11]
        event = {"event_id": (section[i] << 8) | section[i + 1], "start": start,
                 "seconds": seconds, "running": running, "scrambled": bool(section[i + 10] & 0x10),
                 "title": "", "summary": "", "description": "", "genre": "", "language": ""}
        extended = []
        for tag, data in _descriptors(section[i + 12:i + 12 + dlen]):
            if tag == 0x4D and len(data) >= 5:                    # short event: the title
                event["language"] = data[0:3].decode("ascii", "replace").strip("\x00 ")
                name_len = data[3]
                event["title"] = _text(data[4:4 + name_len])
                rest = data[4 + name_len:]
                if rest:
                    event["summary"] = _text(rest[1:1 + rest[0]])
            elif tag == 0x4E and len(data) >= 6:                  # extended event: the long description
                # byte 0 is this descriptor's number in the set, 1..3 the language, 4 the length of the
                # item list, and the text follows it. A long description arrives split across several
                # of these, in order, and is joined back together below.
                items_len = data[4]
                tail = data[5 + items_len:]
                if tail:
                    extended.append(_text(tail[1:1 + tail[0]]))
            elif tag == 0x54 and len(data) >= 2:                  # content: what kind of programme
                event["genre"] = event["genre"] or CONTENT_GENRES.get(data[0] >> 4, "")
        event["description"] = "".join(extended)
        if start and (event["title"] or event["summary"]):
            out["events"].append(event)
        i += 12 + dlen
    return out
