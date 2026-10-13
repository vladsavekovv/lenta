import os, sys, tempfile, time
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, dvb, livestream, livetv
db.init_db()

class T:
    def __init__(self, fe): self.fe = fe
    def tune(self, delsys, freq, wait=5.0, **kw): return True
    def tap_mux(self, buffer_bytes=0): return os.open(os.devnull, os.O_RDONLY)
    def section(self, pid, table_id, timeout=5.0, want=1): return []
    def status(self): return 0x03          # signal and carrier, but no lock
    def signal_text(self): return "strength -62.0 dBm"
    def close(self): pass
livestream.dvb.Tuner = T
fe = dvb.Frontend(adapter=0, index=0, name="Si2168", delsys=["DVB-C"], product="dualHD",
                  usb_id="2040:8265", serial="X")
fe.__dict__["_dvr"] = "/definitely/not/a/device"
dvb.Frontend.dvr = property(lambda self: self.__dict__["_dvr"])
dvb.adapters = lambda: [fe]
livestream.START_TIMEOUT = livestream.TRANSCODE_TIMEOUT = 8

sid = livetv.add_source("tuner", "dualHD", {"device": fe.device, "inputs": []})
livetv._save_channels(sid, [{"uid": "1:1:7", "name": "Broken", "number": 7, "kind": "tv",
                             "provider": "T", "tune": {"delsys": "DVB-C", "frequency": 530_000_000,
                                                       "service_id": 7}}])
ch = livetv.channel(livetv.channels()[0]["id"])
t0 = time.monotonic()
feed, token = livestream.watch(ch)
ok = livestream.wait_for_start(feed)
took = time.monotonic() - t0
print(f"started: {ok}   after {took:.1f}s")
print("error  :", feed.error)
print("notes  :")
for n in feed.notes[-4:]:
    print("   ", n[:150])
assert not ok, "it should not have started"
assert took < 6, f"it waited {took:.0f}s for a process that died at once"
assert feed.error and "did not start in time" not in feed.error, feed.error
assert any("No such file" in n or "Error" in n or "exit code" in n for n in feed.notes), feed.notes

livestream.stop(feed.id)
fails = livestream.failures()
assert len(fails) == 1 and fails[0]["name"] == "Broken" and fails[0]["lines"], fails
print("\nkept for the page:", fails[0]["name"], "-", (fails[0]["error"] or "")[:90])
print("\nok — a channel that will not start now says why, at once")
