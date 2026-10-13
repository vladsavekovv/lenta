"""The scan must try a frequency on every band that uses it, not only the first band to reach it.

The aerial UHF channels and the European cable raster share the same 40 frequencies from 474 to
786 MHz. Remembering a frequency without its band made the aerial pass swallow the middle of the
cable band — which, on a cable feed, is where every channel lives.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from lenta import livetv, db, dvb          # noqa: E402
db.init_db()

# The real shape of the problem: a cable network whose muxes sit on frequencies the aerial plan also
# uses. Nothing is on the aerial band at all, exactly like a flat in Gabrovo on a Unics cable feed.
UNICS = {f * 1_000_000: n for f, n in [(530, 9), (546, 19), (610, 7), (754, 5)]}
tried_aerial, tried_cable = [], []

class FakeTuner:
    def __init__(self, fe): self.freq, self.delsys = None, None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def tune(self, delsys, freq, **kw):
        self.freq, self.delsys = freq, delsys
        (tried_cable if delsys.startswith("DVB-C") else tried_aerial).append(freq)
        return delsys.startswith("DVB-C") and freq in UNICS
    def status(self): return 0
    def signal_text(self): return "strength -47.0 dBm"

livetv.dvb.Tuner = FakeTuner
livetv._read_stream = lambda tuner, entry: {
    "services": [{"service_id": 1000 + i, "name": f"Ch {tuner.freq//10**6}-{i}", "type": "tv",
                  "provider": "Unics", "scrambled": False} for i in range(UNICS[tuner.freq])],
    "network_name": "Unics TV", "transports": [], "lcn": {}, "tsid": tuner.freq // 10**6, "onid": 1}
livetv._tuner_inputs = lambda cfg: [dvb.Frontend(adapter=0, index=0, name="Silicon Labs Si2168",
                                                 delsys=["DVB-T", "DVB-T2", "DVB-C"], error="")]
sid = livetv.add_source("tuner", "dualHD", {"inputs": []})
res = livetv.scan_tuner(sid)

overlap = {e["frequency"] for e in livetv.aerial_plan()} & {e["frequency"] for e in livetv.cable_plan()}
missed = sorted(f for f in overlap if f not in set(tried_cable))
print(f"aerial frequencies tried: {len(set(tried_aerial))}")
print(f"cable  frequencies tried: {len(set(tried_cable))} of {len(livetv.cable_plan())}")
print(f"shared with the aerial plan and skipped on cable: {len(missed)}")
assert not missed, f"{len(missed)} cable frequencies skipped, first {missed[0]//10**6} MHz"
assert res["found"] == sum(UNICS.values()) == 40, res
assert res["network"] == "Unics TV", res
print(f"\nfound {res['found']} channels on {res['network']} — every shared frequency was tried on both bands")
