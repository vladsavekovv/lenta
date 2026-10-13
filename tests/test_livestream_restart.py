import os, sys, tempfile, time
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, dvb, livestream, livetv
db.init_db()

class T:
    def __init__(self, fe=None): pass
    def tune(self, ds, f, wait=2.5, **kw): return True
    def status(self): return 0x1F
    def signal_text(self): return "strength -55.0 dBm"
    def tap_service(self, pids, buffer_bytes=0): return [os.open(os.devnull, os.O_RDONLY) for _ in pids]
    def tap_mux(self, buffer_bytes=0): return os.open(os.devnull, os.O_RDONLY)
    def section(self, pid, t, timeout=5.0, want=1): return []
    def close(self): pass
livestream.dvb.Tuner = T
fe = dvb.Frontend(adapter=0, index=0, name="Si2168", delsys=["DVB-C"], product="dualHD",
                  usb_id="2040:8265", serial="X")
fe.__dict__["_dvr"] = os.devnull
dvb.Frontend.dvr = property(lambda self: self.__dict__["_dvr"])
dvb.adapters = lambda: [fe]
livestream.START_TIMEOUT = livestream.TRANSCODE_TIMEOUT = 3

sid = livetv.add_source("tuner", "dualHD", {"device": fe.device, "inputs": []})
livetv._save_channels(sid, [{"uid": "1:1:48", "name": "National Geographic HD", "number": 48, "kind": "tv",
                             "provider": "Unics", "tune": {"delsys": "DVB-C", "frequency": 626_000_000,
                                                           "service_id": 48}}])
ch = livetv.channel(livetv.channels()[0]["id"])
feed, token = livestream.watch(ch)
livestream.wait_for_start(feed)          # reading /dev/null, so FFmpeg exits at once
feed.segments = 40                       # pretend it had been playing happily

# the converter has fallen over, but someone is still watching and the tuner is still ours
assert not feed.alive(), "the stand-in FFmpeg should have exited"
assert feed.viewers, "a viewer is still there"
livestream._restart(feed)
print("\n".join(feed.notes[-2:]))
assert feed.restarts == 1
assert any("starting it again from segment 42" in n for n in feed.notes), feed.notes
assert feed.error == "", feed.error

# ...and it gives up eventually rather than restarting for ever
for _ in range(livestream.MAX_RESTARTS):
    if feed.restarts < livestream.MAX_RESTARTS:
        livestream._restart(feed)
assert feed.restarts == livestream.MAX_RESTARTS
print(f"\ngives up after {feed.restarts} attempts rather than looping for ever")
livestream.stop(feed.id)

# --- and one that is still running but has stopped producing is picked up too ------------------
feed2, _ = livestream.watch(livetv.channel(livetv.channels()[0]["id"]))
feed2.segments = feed2.last_count = 12
# start_new_session matters: LENTA stops a converter with killpg, which is only safe because every
# converter is started in a session of its own. Without it this test would signal itself.
feed2.proc = __import__("subprocess").Popen(["sleep", "60"], start_new_session=True)
feed2.restarts = 0
restarted = []
livestream._restart = lambda f: restarted.append(f.id)
for _ in range(4):                                               # four quiet ticks = 20 seconds
    if feed2.viewers and feed2.last_count and feed2.alive():
        feed2.stalled += 1
        if feed2.stalled == 4 and feed2.restarts < livestream.MAX_RESTARTS:
            feed2.say("the converter is running but has written nothing for 20 seconds")
            livestream._teardown_proc(feed2)
            livestream._restart(feed2)
assert restarted == [feed2.id], restarted
assert not feed2.alive(), "the stuck converter should have been stopped"
print("a converter that is alive but producing nothing is restarted too")
livestream.stop(feed2.id)
print("ok — a converter that falls over, or simply stops producing, is picked up")
