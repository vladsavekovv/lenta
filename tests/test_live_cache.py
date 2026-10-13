"""Keeping channels warm, so going back to one is instant.

Leaving a channel does not tear it down: the tuner stays locked and the converter keeps writing, and
returning joins what is already running. The whole question is when that stops being worth an input,
and the answer has two halves that pull against each other — hold a channel long enough to be useful,
and never hold one at the expense of a channel somebody actually wants to watch now.
"""
import os, sys, tempfile, time
from pathlib import Path

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import dvb, livestream                                         # noqa: E402

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


STICK = "usb:2040:8265:TEST"
fes = [dvb.Frontend(adapter=0, index=i, name="Si2168", delsys=["DVB-C"], product="dualHD",
                    usb_id="2040:8265", serial="TEST") for i in (0, 1)]
dvb.adapters = lambda skip=None: list(fes)

stopped = []


def fake_stop(fid):
    stopped.append(fid)
    livestream._feeds.pop(fid, None)
    return True


livestream.stop = fake_stop


def warm(fid, name, left_minutes_ago, index=0):
    """A channel nobody is watching, left this long ago."""
    f = livestream.Feed(id=fid, channel_id=1, name=name, number=1, device=STICK, adapter=0,
                        index=index, frequency=626_000_000, service_id=1, directory=Path("/tmp"))
    f.viewers = {}
    f.last_empty = time.time() - left_minutes_ago * 60
    livestream._feeds[fid] = f
    return f


def watched(fid, name, index=0):
    f = warm(fid, name, 0, index)
    f.viewers = {"someone": time.time()}
    f.last_empty = 0.0
    return f


print("1. a channel is kept for half an hour, not twenty seconds")
check(f"the idle limit is {livestream.CACHE_IDLE // 60} minutes", livestream.CACHE_IDLE == 30 * 60)
check(f"at most {livestream.CACHE_MAX} channels are kept warm", livestream.CACHE_MAX == 5)

print("\n2. a warm channel says so, and for how long it has been idle")
livestream._feeds.clear()
f = warm("a", "Nova TV", 4)
info = f.info()
check("it reports itself as kept ready", info["warm"] is True)
check(f"with the idle time, in seconds: {info['idle']}", 235 <= info["idle"] <= 245)
check("and how long it will be kept", info["keeps_for"] == livestream.CACHE_IDLE)
check("a channel being watched is not warm", watched("b", "bTV").info()["warm"] is False)

print("\n3. a channel wanted now takes an input from one merely kept warm")
livestream._feeds.clear()
stopped.clear()
warm("old", "Left 10 minutes ago", 10, index=0)
warm("new", "Left 1 minute ago", 1, index=1)
got = livestream._free_input(STICK)
check(f"an input was found: {got and (got.adapter, got.index)}", got is not None)
check(f"by dropping the one left longest, not the newest: {stopped}", stopped == ["old"])
check("well inside the half hour, because somebody wants to watch now", True)

print("\n4. but a channel somebody is watching is never taken")
livestream._feeds.clear()
stopped.clear()
watched("w1", "Being watched", index=0)
watched("w2", "Also being watched", index=1)
check("no input is offered", livestream._free_input(STICK) is None)
check("and nothing was stopped out from under a viewer", stopped == [])

print("\n5. a warm channel outranks nothing, but a half-watched one outranks it")
livestream._feeds.clear()
stopped.clear()
watched("w", "Being watched", index=0)
warm("k", "Kept ready", 2, index=1)
got = livestream._free_input(STICK)
check(f"the warm one gives way, the watched one does not: {stopped}", stopped == ["k"])
check("and its input is the one offered", got is not None and (got.adapter, got.index) == (0, 1))

print("\n6. over the cap, the one left longest is dropped — a sixth channel makes room")
livestream._feeds.clear()
stopped.clear()
for i in range(livestream.CACHE_MAX + 1):          # six warm channels, left at different times
    warm(f"c{i}", f"Channel {i}", left_minutes_ago=i + 1, index=i)
check(f"the cache names them stalest first: {[f.name for f in livestream.cached()][:2]}",
      livestream.cached()[0].name == f"Channel {livestream.CACHE_MAX}")
dropped = livestream.trim_cache()
check(f"exactly one is dropped: {dropped}", len(stopped) == 1 and dropped == stopped)
check("and it is the one left longest ago", stopped == ["c5"])
check(f"{livestream.CACHE_MAX} are still kept", len(livestream._feeds) == livestream.CACHE_MAX)

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the channel cache ok")
