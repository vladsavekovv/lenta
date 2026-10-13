import os, sys, tempfile, json
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, dvb, livestream, livetv
db.init_db()

REAL_RATE = 6_875_000          # what this network actually uses
tried = []

class T:
    """Locks only on the network's real symbol rate, like the cable mux does."""
    def __init__(self, fe=None): pass
    def tune(self, delsys, freq, wait=2.5, **kw):
        tried.append((delsys, kw.get("symbol_rate")))
        return delsys == "DVB-C" and kw.get("symbol_rate") == REAL_RATE
    def tap_mux(self, buffer_bytes=0): return os.open(os.devnull, os.O_RDONLY)
    def section(self, pid, table_id, timeout=5.0, want=1): return []
    def status(self): return 0x03          # signal and carrier, but no lock
    def signal_text(self): return "strength -62.0 dBm"
    def close(self): pass

# --- 1. the scan must save the rate that worked, not the one it started with -------------------
plan_entry = {"delsys": "DVB-C", "frequency": 530_000_000, "symbol_rate": 6_900_000,
              "modulation": dvb.QAM_AUTO, "label": "cable 530 MHz"}
assert livetv._tune_entry(T(), plan_entry) is True
print("scan tried:", tried)
assert plan_entry["symbol_rate"] == REAL_RATE, plan_entry
print(f"scan saves  : symbol_rate {plan_entry['symbol_rate']}  (started from 6900000)")

# --- 2. a channel saved with the wrong rate still plays, and is corrected ----------------------
tried.clear()
livestream.dvb.Tuner = T
fe = dvb.Frontend(adapter=0, index=0, name="Si2168", delsys=["DVB-C"], product="dualHD",
                  usb_id="2040:8265", serial="X")
fe.__dict__["_dvr"] = os.devnull
dvb.Frontend.dvr = property(lambda self: self.__dict__["_dvr"])
dvb.adapters = lambda: [fe]

sid = livetv.add_source("tuner", "dualHD", {"device": fe.device, "inputs": []})
livetv._save_channels(sid, [{"uid": "1:1:3", "name": "Nova TV", "number": 3, "kind": "tv", "provider": "Unics",
                             "tune": {"delsys": "DVB-C", "frequency": 530_000_000,
                                      "symbol_rate": 6_900_000, "modulation": dvb.QAM_AUTO,
                                      "service_id": 3}}])
cid = livetv.channels()[0]["id"]
feed, token = livestream.watch(livetv.channel(cid))
print("watch tried :", tried)
saved = livetv.channel(cid)["tune"]
print(f"saved now   : symbol_rate {saved['symbol_rate']}")
assert saved["symbol_rate"] == REAL_RATE, saved
assert any("correcting the channel" in n for n in feed.notes), feed.notes
livestream.stop(feed.id)
print("\nok — a channel saved with the wrong rate plays anyway, and the right one is written back")
