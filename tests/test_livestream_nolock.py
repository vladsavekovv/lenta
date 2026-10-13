import os, sys, tempfile
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, dvb, livestream, livetv
db.init_db()

class T:
    """A tuner that sees the carrier but never decodes it — the real symptom."""
    def __init__(self, fe=None): pass
    def tune(self, delsys, freq, wait=2.5, **kw): return False
    def status(self): return 0x03                      # signal + carrier, no lock
    def tap_mux(self, buffer_bytes=0): return os.open(os.devnull, os.O_RDONLY)
    def section(self, pid, t, timeout=5.0, want=1): return []
    def signal_text(self): return "strength -62.0 dBm"
    def close(self): pass
livestream.dvb.Tuner = T
fe = dvb.Frontend(adapter=0, index=0, name="Si2168", delsys=["DVB-C"], product="dualHD",
                  usb_id="2040:8265", serial="X")
fe.__dict__["_dvr"] = os.devnull
dvb.Frontend.dvr = property(lambda self: self.__dict__["_dvr"])
dvb.adapters = lambda: [fe]

sid = livetv.add_source("tuner", "dualHD", {"device": fe.device, "inputs": []})
livetv._save_channels(sid, [{"uid": "1:1:3", "name": "Nova TV", "number": 3, "kind": "tv", "provider": "U",
                             "tune": {"delsys": "DVB-C", "frequency": 530_000_000,
                                      "symbol_rate": 6_900_000, "modulation": 5, "service_id": 3}}])
cid = livetv.channels()[0]["id"]
try:
    livestream.watch(livetv.channel(cid))
    raise SystemExit("it should not have started")
except livestream.LiveError as ex:
    print("message:", ex)
    # six combinations, plus the longer retry of the one that found the carrier
    assert "attempts" in str(ex), ex          # two passes now, plus the longer retry of each
    assert "best the demodulator managed was: carrier" in str(ex), ex
    assert "too weak or too dirty" in str(ex), ex

fails = livestream.failures()
assert len(fails) == 1, fails
print("\nkept for the page:")
for l in fails[0]["lines"]:
    print("  ", l[:110])
assert any("QAM-256" in l for l in fails[0]["lines"]) and any("auto" in l for l in fails[0]["lines"])
assert not livestream.active(), "the input must be free again"
print("\nok — a lock failure now names every combination it tried and what the demodulator reached")
