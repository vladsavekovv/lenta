"""Which input a new channel gets, when every one of them is already spoken for.

A tuner is kept for a few seconds after the last viewer leaves, so that flipping back to the channel
you just left is instant. The trap is that those warm feeds still hold an input: flip through more
channels than the stick has inputs and every one is held by a feed nobody is watching, while the
channel actually being asked for is turned away. Someone watching now outranks someone who might.
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


# A dual tuner: two inputs on one stick.
STICK = "usb:2040:8265:TEST"
fes = [dvb.Frontend(adapter=0, index=i, name="Si2168", delsys=["DVB-C"], product="dualHD",
                    usb_id="2040:8265", serial="TEST") for i in (0, 1)]
dvb.adapters = lambda skip=None: list(fes)

stopped = []


def fake_stop(fid):
    """What the real stop does, as far as this test cares: the feed lets go of its input."""
    stopped.append(fid)
    livestream._feeds.pop(fid, None)
    return True


livestream.stop = fake_stop


def feed(fid, index, name, viewers, empty_at):
    f = livestream.Feed(id=fid, channel_id=1, name=name, number=1, device=STICK, adapter=0,
                        index=index, frequency=626_000_000, service_id=1, directory=Path("/tmp"))
    f.viewers = dict.fromkeys(viewers, time.time())
    f.last_empty = empty_at
    livestream._feeds[fid] = f
    return f


print("1. an input nobody has taken is simply used")
check("the first free input is offered", livestream._free_input(STICK) is fes[0])

print("\n2. a watched feed never loses its input")
feed("a", 0, "Nova TV", ["viewer-1"], 0.0)
check("the other input is offered instead", livestream._free_input(STICK) is fes[1])
check("nothing was stopped", stopped == [])

print("\n3. both inputs busy and both being watched: the honest refusal stands")
feed("b", 1, "bTV", ["viewer-2"], 0.0)
check("no input is offered", livestream._free_input(STICK) is None)
check("and nothing was stopped out from under a viewer", stopped == [])

print("\n4. an input held only to keep a channel warm is given up")
livestream._feeds["b"].viewers = {}
livestream._feeds["b"].last_empty = time.time() - 5
got = livestream._free_input(STICK)
check("the warm feed was stopped", stopped == ["b"])
check("and its input is now the one offered", got is fes[1])

print("\n5. the one empty longest goes first, not whichever comes to hand")
stopped.clear()
livestream._feeds.clear()
feed("old", 0, "Left a while ago", [], time.time() - 60)
feed("new", 1, "Only just left", [], time.time() - 2)
livestream._free_input(STICK)
check(f"the stalest gave way, not the freshest: {stopped}", stopped == ["old"])

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("tuner inputs ok")
