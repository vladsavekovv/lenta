#!/usr/bin/env python3
"""Check a USB TV tuner at the operating-system level, outside LENTA.

It uses the very code LENTA uses (server/lenta/dvb.py), so whatever it reports here is what LENTA
sees. Run it on the server:

    sudo /opt/lenta/deploy/tv-check.py                 # look at everything, sweep the aerial band
    sudo /opt/lenta/deploy/tv-check.py --freq 522      # try one frequency (MHz)
    sudo /opt/lenta/deploy/tv-check.py --band cable    # sweep the cable band instead
    sudo /opt/lenta/deploy/tv-check.py --quick         # every second channel, for a fast look
    sudo -u lenta /opt/lenta/deploy/tv-check.py        # as the service account, to prove its access

It prints, in order: what the system shows, what the driver said while loading, what each tuner
reports about itself, and then every frequency it tries with the lock, signal strength and quality.
Where it locks, it reads the stream and lists the channels it carries.
"""
from __future__ import annotations

import argparse
import glob
import grp
import os
import pwd
import re
import stat
import subprocess
import sys
import time

for candidate in ("/opt/lenta/server", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server")):
    if os.path.isdir(os.path.join(candidate, "lenta")):
        sys.path.insert(0, os.path.abspath(candidate))
        break
try:
    from lenta import dvb
except ImportError:
    sys.exit("Can't find LENTA's code. Run this from the LENTA folder, or use /opt/lenta/deploy/tv-check.py")

OK, BAD, WARN = "  ok ", " --- ", "  ? "


def head(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "-" * len(title))


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- 1. what the system shows
def system_check() -> list[str]:
    head("1. The system")
    notes = []
    who = pwd.getpwuid(os.geteuid()).pw_name
    groups = sorted(g.gr_name for g in grp.getgrall() if who in g.gr_mem)
    print(f"running as        {who} (groups: {', '.join(groups) or 'none'})")
    print(f"kernel            {os.uname().release}")

    if not os.path.isdir(dvb.DVB_ROOT):
        print(f"{BAD} /dev/dvb does not exist — no tuner, or its driver did not load")
        notes.append("There is no /dev/dvb. Either no tuner is plugged in, or the kernel has no driver for it. "
                     "Check the driver messages below and `lsusb`.")
        return notes

    for path in sorted(glob.glob(f"{dvb.DVB_ROOT}/adapter*/*")):
        st = os.stat(path)
        mode = stat.filemode(st.st_mode)
        owner = pwd.getpwuid(st.st_uid).pw_name
        group = grp.getgrgid(st.st_gid).gr_name
        readable = os.access(path, os.R_OK | os.W_OK)
        print(f"{OK if readable else BAD} {path:<34} {mode} {owner}:{group}"
              + ("" if readable else "   <- this account cannot open it"))
        if not readable:
            notes.append(f"{who} cannot open {path}. On Ubuntu these belong to the 'video' group: "
                         f"run `sudo usermod -aG video {who}` and restart LENTA.")
    return notes


# ---------------------------------------------------------------- 1b. the USB side
def _controller(path: str) -> tuple[str, str]:
    """Walk up to the root hub and name the controller driving it (xhci_hcd, ehci-pci, uhci_hcd…)."""
    is_root_hub = re.compile(r"^usb\d+$").match
    real = os.path.realpath(path)
    while real and real != "/" and not is_root_hub(os.path.basename(real)):
        parent = os.path.dirname(real)
        if parent == real:
            break
        real = parent
    root = os.path.basename(real) if real and is_root_hub(os.path.basename(real)) else ""
    if not root:
        return "", ""
    driver = ""
    try:
        driver = os.path.basename(os.path.realpath(os.path.join(os.path.dirname(real), "driver")))
    except OSError:
        pass
    return root, driver


def _usb2_sockets() -> list[str]:
    """Root hubs run by a plain USB 2 controller — the sockets worth moving a tuner to."""
    out = []
    for hub in sorted(glob.glob("/sys/bus/usb/devices/usb*")):
        _root, driver = _controller(hub)
        if driver.startswith(("ehci", "ohci")):
            out.append(os.path.basename(hub))
    return out


def _socket_is_usb3(path: str) -> bool | None:
    """Whether the receptacle itself is a USB 3 one, read from the kernel's port pairing.

    The controller alone does not settle it: plenty of boards wire their plain black sockets to the xHCI
    controller as well. What settles it is that a USB 3 receptacle carries two ports in one hole — a USB 2
    one and a SuperSpeed one — and the kernel links the pair with a 'peer' symlink. A peer means a USB 3
    socket; no peer means a USB-2-only socket whatever drives it. None means the kernel did not say.
    """
    name = os.path.basename(path)                       # 2-6  or  2-6.3
    hub_dir = os.path.dirname(os.path.realpath(path))
    hub = os.path.basename(hub_dir)                     # usb2 (a root hub) or 2-6 (a hub)
    port = name.rsplit(".", 1)[-1] if "." in name else name.split("-", 1)[-1]
    hits = glob.glob(os.path.join(hub_dir, "*", f"{hub}-port{port}"))
    if not hits:
        return None
    return os.path.lexists(os.path.join(hits[0], "peer"))


def _virt() -> str:
    """The hypervisor this machine runs under, or "" on bare metal.

    This decides whether anything below is worth reading. Inside a virtual machine the USB controllers
    are emulated ones the hypervisor made up, so they say nothing at all about the socket the stick is
    really in — that question can only be answered on the host.
    """
    name = (run(["systemd-detect-virt"]) or "").strip().splitlines()
    name = name[0].strip() if name else ""
    if name and name != "none":
        return name
    for f in ("sys_vendor", "product_name"):
        try:
            with open(f"/sys/class/dmi/id/{f}") as fh:
                v = fh.read().strip()
        except OSError:
            continue
        for needle, label in (("QEMU", "qemu"), ("Bochs", "qemu"), ("KVM", "kvm"), ("VMware", "vmware"),
                              ("VirtualBox", "oracle"), ("innotek", "oracle"), ("Xen", "xen"),
                              ("Virtual Machine", "microsoft")):
            if needle.lower() in v.lower():
                return label
    return ""


def usb_check(dev_ids: set[str]) -> list[str]:
    """How the stick is attached: the socket's controller, its speed, what it hangs off, and any trouble.

    The controller tells us what the socket is. A USB 3 socket is driven by xhci_hcd and offers two root
    hubs — one at 480 Mbit/s for USB 2 devices and one at 5000 — so a tuner that negotiates 480 under an
    xhci controller is still sitting in a USB 3 socket. That matters: USB 3 sockets and their leads throw
    out broadband noise right across the band a tuner listens to.
    """
    head("1b. How the tuner is attached")
    notes = []
    found = False
    for path in sorted(glob.glob("/sys/bus/usb/devices/*")):
        def rd(name, where=path):
            try:
                with open(os.path.join(where, name)) as fh:
                    return fh.read().strip()
            except OSError:
                return ""
        ident = f"{rd('idVendor')}:{rd('idProduct')}"
        if ident not in dev_ids:
            continue
        found = True
        speed, power = rd("speed"), rd("bMaxPower")
        parent = os.path.dirname(os.path.realpath(path))
        parent_name = os.path.basename(parent)
        parent_is_root = bool(glob.glob(os.path.join(parent, "usb*")) ) or parent_name.startswith("usb")
        hub_depth = os.path.basename(path).count(".")
        print(f"   device         {rd('product') or ident} at {os.path.basename(path)}")
        print(f"   speed          {speed} Mbit/s" + ("  (USB 2 high speed — normal for this stick)"
                                                     if speed == "480" else ""))
        print(f"   draws          {power or '?'}   (the port itself: {rd('bMaxPower', parent) or '?'})")
        print(f"   hangs off      {rd('product', parent) or parent_name}"
              + ("  — a root port on the machine" if parent_is_root else "  — an external hub"))
        root, driver = _controller(path)
        virt = _virt()
        superspeed = _socket_is_usb3(path)
        if superspeed is True:
            kind, noisy = "a USB 3 socket", True
        elif superspeed is False:
            kind, noisy = "a USB 2 socket", False
        else:                                   # the kernel did not pair the ports — judge by the controller
            noisy = driver.startswith("xhci")
            kind = ("probably a USB 3 socket" if noisy
                    else "a USB 2 socket" if driver.startswith(("ehci", "ohci"))
                    else "a USB 1.1 socket" if driver.startswith("uhci") else "an unknown socket")
        if virt:
            print(f"   socket         cannot be told from in here — this is a {virt} virtual machine")
            print(f"                  (the {driver or '?'} controller at {root} is an emulated one)")
            notes.append(f"This machine is a {virt} virtual machine and the tuner reaches it through USB "
                         "passthrough, so every USB controller above is one the hypervisor invented. Which "
                         "physical socket the stick sits in — and whether that socket is a noisy USB 3 one — "
                         "can only be read on the host. On the host, `lsusb -t` gives the stick's bus and "
                         "port, and this lists every socket and what it really is:\n"
                         "       for p in /sys/bus/usb/devices/usb*/*/usb*-port*; do\n"
                         "         [ -e \"$p/peer\" ] && echo \"$(basename $p)  USB 3 socket\" \\\n"
                         "                            || echo \"$(basename $p)  USB 2 socket\"\n"
                         "       done | sort")
        else:
            why = ", no SuperSpeed wiring in the socket" if superspeed is False else ""
            print(f"   socket         {kind}   (root hub {root}, controller {driver or '?'}{why})")
        if noisy and not virt:
            spare = [h for h in _usb2_sockets()]
            notes.append("The tuner is in a USB 3 socket (" + ("the kernel pairs that port with a SuperSpeed one"
                         if superspeed else "its root hub is run by " + driver) + "). USB 3 sockets "
                         "and their leads radiate broadband noise across the very band a tuner listens to, and a "
                         "stick sitting in one can be deafened by its own machine. This is the first thing to "
                         "change when nothing is received: move it to a USB 2 socket"
                         + (f" — this machine has plain USB 2 root hubs at {', '.join(spare)}" if spare else "")
                         + ", or put it on a short extension lead a foot away from the case, and scan again.")
        if speed and speed.isdigit() and int(speed) < 480:
            notes.append(f"The tuner negotiated only {speed} Mbit/s. It needs USB 2 high speed (480). "
                         "A worn lead or a tired hub does this; try a port straight on the machine.")
        if not parent_is_root or hub_depth > 1:
            notes.append("The tuner is behind an external hub. Hubs share bandwidth and often supply less "
                         "current than a port on the machine — plug it straight into the back of the machine "
                         "and scan again.")
    if not found:
        print(f"{WARN} could not find the stick under /sys/bus/usb — skipping")
        return notes

    text = run(["dmesg", "-T"]) or run(["dmesg"])
    # A plain "USB disconnect" is what unplugging the stick looks like, so it is not trouble on its own —
    # reporting it as such sent someone hunting a port fault they had caused themselves a minute earlier.
    bad = [l for l in text.splitlines()
           if any(w in l.lower() for w in ("reset high-speed usb", "device descriptor read",
                                           "cannot enumerate", "over-current", "babble", "-71", "-110"))]
    if bad:
        print("\n   the kernel complained about USB:")
        for line in bad[-6:]:
            print("     " + line.strip())
        notes.append("The kernel logged USB trouble (resets, enumeration errors or over-current). That points at "
                     "the port, the lead or the power rather than at reception. Try another port, preferably one "
                     "on the back of the machine, and avoid an extension lead.")
    else:
        print("   usb errors     none in the kernel messages")
    spare = _usb2_sockets()
    if spare and not _virt():
        print(f"   usb 2 sockets  {', '.join(spare)} are run by a plain USB 2 controller")
    return notes


# ---------------------------------------------------------------- 2. what the driver said
def driver_check() -> list[str]:
    head("2. The driver and its firmware")
    notes = []
    # which driver each input belongs to, straight from sysfs
    mods = set()
    for link in sorted(glob.glob("/sys/class/dvb/dvb*.frontend*/device/driver")):
        mods.add(os.path.basename(os.path.realpath(link)))
    for link in sorted(glob.glob("/sys/class/dvb/dvb*.frontend*/device/../driver")):
        mods.add(os.path.basename(os.path.realpath(link)))
    print(f"drivers in use    {', '.join(sorted(mods)) or '(none found in sysfs)'}")
    loaded = run(["lsmod"])
    for mod in ("si2168", "si2157", "em28xx", "cx231xx", "rtl2832", "dvb_usb_v2", "dvb_core"):
        if mod.replace("-", "_") in loaded.replace("-", "_"):
            print(f"   loaded         {mod}")
    # a demodulator without its firmware is listed but never locks, so check the files exist
    fw = sorted(glob.glob("/lib/firmware/dvb-demod-si21*") + glob.glob("/lib/firmware/dvb-tuner-si21*")
                + glob.glob("/lib/firmware/dvb-demod-*") + glob.glob("/lib/firmware/dvb-fe-*"))
    if fw:
        print(f"firmware files    {len(fw)} in /lib/firmware, including "
              + ", ".join(os.path.basename(f) for f in fw[:4]) + ("…" if len(fw) > 4 else ""))
    elif "si2168" in " ".join(mods) or "si2168" in loaded:
        print(f"{WARN} no dvb firmware files in /lib/firmware")
        notes.append("There are no DVB firmware files in /lib/firmware. A Silicon Labs demodulator needs one "
                     "(the 'linux-firmware' package carries them). Without it the tuner is listed but never locks.")
    else:
        print(f"{WARN} no dvb firmware files in /lib/firmware (this tuner may not need any)")
    print()
    text = run(["dmesg", "-T"]) or run(["dmesg"]) or run(["journalctl", "-k", "-b", "--no-pager", "-n", "2000"])
    if not text:
        print(f"{WARN} can't read the kernel messages — run this with sudo to see them")
        return notes
    wanted = ("dvb", "si216", "si2157", "si2168", "cx231", "em28xx", "rtl28", "lgdt", "mn88", "firmware",
              "hauppauge", "tuner")
    lines = [l for l in text.splitlines() if any(w in l.lower() for w in wanted)]
    if not lines:
        print(f"{WARN} nothing about a TV tuner in the kernel messages — on a machine that has been up a long time\n"
              "     these have usually scrolled away. To see them again: unplug the tuner, plug it back in,\n"
              "     then run `sudo dmesg -T | tail -30`.")
        return notes
    for line in lines[-30:]:
        print("   " + line.strip())
    missing = [l for l in lines if "firmware" in l.lower() and ("fail" in l.lower() or "not found" in l.lower())]
    if missing:
        name = ""
        for l in missing:
            for word in l.split():
                if word.endswith(".fw") or word.endswith(".fw'"):
                    name = word.strip("'\"")
        notes.append("The driver asked for a firmware file and did not get it"
                     + (f" ({name})" if name else "")
                     + ". The tuner will be listed but will never lock. Put that file in /lib/firmware "
                       "(Hauppauge publish theirs; Ubuntu's 'linux-firmware' package has many), then "
                       "unplug and replug the tuner.")
    return notes


# ---------------------------------------------------------------- 3. what each tuner says
def tuner_check() -> tuple[list[dvb.Frontend], list[str]]:
    head("3. What each tuner says about itself")
    notes, found = [], dvb.adapters()
    if not found:
        print(f"{BAD} no tuner inputs found")
        return found, notes
    sticks: dict[str, list] = {}
    for fe in found:
        sticks.setdefault(fe.device, []).append(fe)
    print(f"{len(sticks)} stick{'' if len(sticks) == 1 else 's'}, {len(found)} input{'' if len(found) == 1 else 's'} "
          f"in all (a twin tuner is one stick with two inputs)\n")
    for fe in found:
        mark = BAD if fe.error else OK
        print(f"{mark} adapter{fe.adapter}/frontend{fe.index}")
        print(f"       name        {fe.name or '(the driver did not say)'}")
        print(f"       standards   {', '.join(fe.delsys) or '(none reported)'}")
        print(f"       as a person {', '.join(fe.families) or 'unknown'}")
        print(f"       usb         {fe.product or '?'} {f'({fe.usb_id})' if fe.usb_id else ''}"
              + (f" serial {fe.serial}" if fe.serial else " (no serial)"))
        print(f"       stick       {fe.device}")
        if fe.error:
            print(f"       problem     {fe.error}")
            notes.append(f"adapter{fe.adapter}/frontend{fe.index} could not be opened: {fe.error}."
                         + (" Something else is using it — stop LENTA and try again." if fe.error == "busy" else ""))
    return found, notes


# ---------------------------------------------------------------- 4. does it lock?
flags = dvb.flag_text          # the scan and this tool say the same thing


def one_frequency(fe: dvb.Frontend, bands: list[str], mhz: int, wait: float) -> list[str]:
    """Everything this one frequency could be: each band, each modulation, each symbol rate."""
    print(f"trying {mhz} MHz every way this tuner could carry it — {', '.join(bands or ['aerial'])}\n")
    notes, locked, seen = [], None, []
    with dvb.Tuner(fe) as tuner:
        for b in bands or ["aerial"]:
            params = ({"delsys": "DVB-T", "frequency": mhz * 1_000_000, "bandwidth": 8_000_000}
                      if b == "aerial" else
                      {"delsys": "DVB-C", "frequency": mhz * 1_000_000, "symbol_rate": 6_900_000,
                       "modulation": dvb.QAM_AUTO})
            tried: list[str] = []
            won = dvb.lock(tuner, params, wait=wait, tried=tried)
            seen += tried
            for line in tried:
                mark = OK if line.endswith("LOCK") or ", LOCK" in line else BAD
                print(f"{mark} {line}")
            print(f"       signal now  {tuner.signal_text()}")
            if won:
                locked = won
                print(f"\n{OK} locked: {won['delsys']}"
                      + (f", {won['symbol_rate'] // 1000} kBd" if won.get("symbol_rate") else "")
                      + f"   {tuner.signal_text()}")
                if read_stream(tuner, mhz):
                    print(f"{OK} and it carries a channel table — this frequency is good.")
                else:
                    notes.append(f"{mhz} MHz locked but carried no channel table in the time allowed. "
                                 "A lock on noise does that; so does a mux that has been emptied.")
                break
    if not locked:
        carrier = any("carrier" in t for t in seen)
        if carrier:
            notes.append(
                f"At {mhz} MHz one combination found the carrier but never finished locking, even given "
                "longer. That is a weak or dirty signal rather than wrong settings: the multiplex is "
                "there, but not clean enough to decode. Compare the strength above with what the scan "
                "saw (it is printed with every lock) — a drop of ten decibels or more means something "
                "has changed on the line. Check the aerial lead and the wall socket, and any splitter "
                "or amplifier between them.")
        else:
            notes.append(
                f"Nothing at all arrived at {mhz} MHz on any band — not even a carrier. Either the lead "
                "is not connected to the stick or the socket, or the network has moved its multiplexes. "
                "Try `--band cable` with no --freq to sweep the whole band and see what is still there. "
                "If the whole band is silent and it worked earlier, the stick itself may have wedged: "
                "unplug it, plug it back in, and look for a fresh firmware line in `sudo dmesg -T`.")
    return notes


def sweep(fe: dvb.Frontend, band: str, only: int | None, quick: bool, wait: float) -> list[str]:
    head(f"4. Trying frequencies on adapter{fe.adapter}/frontend{fe.index}")
    notes = []
    bands = [band] if band in ("aerial", "cable") else [b for b in ("aerial", "cable") if b in fe.families]
    if only:
        # One frequency, tried every way it could be carried. Picking a single delivery system here —
        # which this used to do, and always chose the aerial one — makes the answer meaningless on a
        # cable feed, which is precisely when someone reaches for --freq.
        return one_frequency(fe, bands, only, wait)
    else:
        from lenta import livetv
        plan = []
        for b in bands:
            plan += livetv.aerial_plan() if b == "aerial" else livetv.cable_plan()
        if quick:
            plan = plan[::2]
        print("bands to try:      " + ", ".join(bands or ["aerial"]))
    print(f"{len(plan)} frequenc{'y' if len(plan) == 1 else 'ies'} to try, up to {wait:.1f}s each."
          " A lock is printed in full; the rest show what the demod managed.\n")
    locks, best, real_locks = 0, (0, None), []
    try:
        with dvb.Tuner(fe) as tuner:
            for entry in plan:
                mhz = entry["frequency"] // 1_000_000
                tried = []
                for delsys in ([entry["delsys"], "DVB-T2"] if entry["delsys"] == "DVB-T" else [entry["delsys"]]):
                    kw = {k: v for k, v in entry.items() if k in ("bandwidth", "symbol_rate", "modulation")}
                    locked = tuner.tune(delsys, entry["frequency"], wait=wait, **kw)
                    st, sig = tuner.status(), tuner.signal()
                    reading = tuner.signal_text()
                    # something to compare frequencies by, whichever way the driver answers
                    score = sig["strength_db"] if sig["strength_db"] is not None else (sig["strength"] or 0) / 655.0
                    tried.append(f"{delsys}: {flags(st)}")
                    if sig["strength_db"] is not None or sig["strength"]:
                        if best[1] is None or score > best[0]:
                            best = (score, f"{mhz} MHz {delsys} ({reading})")
                    if locked:
                        locks += 1
                        print(f"{OK} {mhz:>4} MHz  {delsys:<7} LOCK   {reading}")
                        if read_stream(tuner, mhz):
                            real_locks.append(mhz)
                        break
                else:
                    detail = " | ".join(tried)
                    print(f"{BAD} {mhz:>4} MHz  no lock   ({detail})")
    except OSError as ex:
        print(f"\n{BAD} the tuner stopped responding: {ex}")
        if getattr(ex, "errno", None) == 16:
            notes.append("The tuner is in use by something else — almost always LENTA's own scan. In Settings › "
                         "Live TV press Stop, or switch 'Scan a newly found tuner by itself' off, or run "
                         "`sudo systemctl stop lenta`; then run this again.")
        else:
            notes.append(f"The tuner stopped responding while tuning ({ex.strerror or ex}). That is usually a power "
                         "or cable problem, or a driver that needs firmware.")
        return notes

    print()
    if real_locks:
        print(f"{OK} locked and read the channel tables on {len(real_locks)} frequenc"
              f"{'y' if len(real_locks) == 1 else 'ies'} — the tuner works.")
    elif locks:
        print(f"{WARN} the demodulator claimed a lock {locks} time{'' if locks == 1 else 's'}, but no frequency "
              "carried any channel table.")
        notes.append("A claimed lock with no channel table behind it is not a real one — demodulators do that on "
                     "noise. Treat this as nothing received. The aerial side is what to look at: whether a lead is "
                     "in the stick at all, and whether it carries aerial or cable.")
    else:
        print(f"{BAD} nothing locked anywhere.")
    if not real_locks:
        if best[1]:
            print(f"     The strongest reading was at {best[1]}, so the demodulator itself is alive.")
        else:
            print("     No frequency gave any reading at all, which is what an unplugged aerial socket looks like.")
            notes.append("The tuner reports no signal on any frequency. The usual causes, in order: no lead in the "
                         "stick's aerial socket; an indoor aerial out of range; or the lead carries cable rather "
                         "than aerial — run again with --band cable.")
    return notes


def read_stream(tuner: dvb.Tuner, mhz: int) -> bool:
    """What this frequency carries, as LENTA would read it. True when it really carried something."""
    sdt = tuner.section(0x11, 0x42, timeout=4.0, want=4)
    nit = tuner.section(0x10, 0x40, timeout=4.0, want=4)
    services, network, lcn = [], "", {}
    for raw in sdt:
        parsed = dvb.parse_sdt(raw)
        services += parsed.get("services", [])
    for raw in nit:
        parsed = dvb.parse_nit(raw)
        network = network or parsed.get("network_name", "")
        lcn.update(parsed.get("lcn", {}))
    if network:
        print(f"       network     {network}")
    if not services:
        print("       channels    none — it locked but carried no channel table, so there was nothing there")
        return False
    print(f"       channels    {len(services)}")
    for s in services[:40]:
        no = lcn.get(s["service_id"])
        print(f"         {str(no or '-'):>4}  {s['name'] or '(no name)':<28} {s.get('type', '?'):<6}"
              f" {s.get('provider', '')}" + ("  [scrambled]" if s.get("scrambled") else ""))
    return True


# ---------------------------------------------------------------- run it
def main() -> int:
    ap = argparse.ArgumentParser(description="Check a USB TV tuner at the OS level, the way LENTA sees it.")
    ap.add_argument("--band", choices=("aerial", "cable", "both"), default="both",
                    help="which band to sweep (default: every band the tuner supports)")
    ap.add_argument("--freq", type=int, help="try only this frequency, in MHz")
    ap.add_argument("--adapter", type=int, help="only this adapter number")
    ap.add_argument("--frontend", type=int, help="only this frontend number")
    ap.add_argument("--quick", action="store_true", help="every second frequency, for a faster look")
    ap.add_argument("--wait", type=float, default=2.5, help="seconds to wait for a lock (default 2.5)")
    ap.add_argument("--no-sweep", action="store_true", help="only look, don't tune")
    args = ap.parse_args()

    print(f"LENTA tuner check — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    notes = system_check()
    ids = {fe.usb_id for fe in dvb.adapters() if fe.usb_id}
    notes += usb_check(ids)
    notes += driver_check()
    found, tnotes = tuner_check()
    notes += tnotes

    usable = [fe for fe in found if not fe.error]
    if args.adapter is not None:
        usable = [fe for fe in usable if fe.adapter == args.adapter]
    if args.frontend is not None:
        usable = [fe for fe in usable if fe.index == args.frontend]
    swept = False
    if usable and not args.no_sweep:
        for n, fe in enumerate(usable):        # an input LENTA is scanning on is busy: take the next one
            try:
                dvb.Tuner(fe).close()
            except OSError as ex:
                if getattr(ex, "errno", None) == 16 and n + 1 < len(usable):
                    print(f"\n(adapter{fe.adapter}/frontend{fe.index} is in use — trying the stick's other input)")
                    continue
                notes.append(f"adapter{fe.adapter}/frontend{fe.index} could not be opened: {ex.strerror or ex}."
                             + (" Something else is using it — in LENTA, press Stop on the scan, or switch"
                                " 'Scan a newly found tuner by itself' off in Settings › Live TV, or run"
                                " `sudo systemctl stop lenta` — then try again." if getattr(ex, "errno", None) == 16 else ""))
                fe = None
                break
            break
        if fe is not None:
            notes += sweep(fe, args.band, args.freq, args.quick, args.wait)
            swept = True
        if len(usable) > 1:
            print(f"\n(There are {len(usable)} inputs; only the first was tried. Use --adapter/--frontend for another.)")

    head("What to do")
    if notes:
        for i, n in enumerate(notes, 1):
            print(f"{i}. {n}\n")
    elif swept:
        print("Nothing to fix — the tuner is found, it can be opened, and it locks. LENTA's own scan will find the\n"
              "same channels; if it doesn't, send this output along.")
    elif usable:
        print("So far so good: the tuner is found and this account can open it. Nothing has been tuned yet —\n"
              "run it again without --no-sweep to find out whether it locks anything:\n"
              f"    sudo {sys.argv[0]}                  # sweep the aerial band\n"
              f"    sudo {sys.argv[0]} --band cable     # sweep the cable band")
    else:
        print("No usable tuner input, so nothing was tried.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
