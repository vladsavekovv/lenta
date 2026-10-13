"""Collecting the guide off a multiplex and reading it back.

The parsing of an event table is test_eit's job. This one is about everything around it: matching a
table to the right channel, keeping programmes without duplicating them, surviving a rescan, and
answering "what is on" quickly enough to put under every tile on a page.
"""
import os, sys, struct, zlib, datetime, tempfile, threading, time

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, dvb, epg, livetv                                     # noqa: E402

db.init_db()
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# ---- building event tables, as a broadcaster sends them ----------------------------------------
EPOCH = datetime.date(1858, 11, 17)
mjd_of = lambda y, m, d: (datetime.date(y, m, d) - EPOCH).days          # noqa: E731
bcd = lambda n: ((n // 10) << 4) | (n % 10)                             # noqa: E731


def event(eid, when, dur_min, title, summary=""):
    lang = b"bul"
    name, sm = title.encode(), summary.encode()
    body = lang + bytes([len(name) + 1, 0x15]) + name + bytes([len(sm) + 1, 0x15]) + sm
    descs = bytes([0x4D, len(body)]) + body
    head = struct.pack(">H", eid)
    head += struct.pack(">H", mjd_of(when.year, when.month, when.day))
    head += bytes([bcd(when.hour), bcd(when.minute), 0])
    head += bytes([bcd(dur_min // 60), bcd(dur_min % 60), 0])
    head += struct.pack(">H", (4 << 13) | len(descs))
    return head + descs


def eit(table_id, service_id, events, tsid=4001, onid=1):
    body = struct.pack(">HHB", tsid, onid, 0) + bytes([table_id]) + b"".join(events)
    head = bytes([table_id]) + b"\x00\x00" + struct.pack(">H", service_id) + bytes([0xC1, 0, 0])
    sec = head + body
    sec = sec[:1] + struct.pack(">H", 0xB000 | (len(sec) - 3 + 4)) + sec[3:]
    return sec + struct.pack(">I", zlib.crc32(sec) & 0xFFFFFFFF)


def at(minutes_from_now):
    """A whole minute, that many minutes from now — broadcast times carry no seconds."""
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes_from_now)
    return t.replace(second=0, microsecond=0)


class FakeTuner:
    """Hands over whatever tables the test wants, the way the demux would."""
    def __init__(self, sections):
        self.given = list(sections)
        self.asked = None

    def sections(self, pid, table_id, mask, seconds, buffer_bytes=0, alive=None):
        self.asked = (pid, table_id, mask)
        for s in self.given:
            if alive is not None and not alive():
                return
            yield s


# ---- a provider with two channels on one multiplex ----------------------------------------------
sid = livetv.add_source("tuner", "dualHD", {"device": "usb:2040:8265:T", "inputs": []})
livetv._save_channels(sid, [
    {"uid": "1:4001:4124", "name": "BNT 1 HD", "number": 1, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4124}},
    {"uid": "1:4001:4125", "name": "bTV", "number": 2, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4125}},
])

# Deliberately straddling the present: ON started a quarter of an hour ago and runs for an hour, so
# "what is on now" has something true to find whatever time the test is run at.
ON, SOON, LATER = at(-15), at(45), at(90)
tables = [
    eit(dvb.EIT_NOW_NEXT, 4124, [event(0x1001, ON, 60, "Дивата природа", "Епизод 4"),
                                 event(0x1002, SOON, 45, "Новините", "Късна емисия")]),
    eit(dvb.EIT_SCHEDULE_FIRST, 4124, [event(0x1003, LATER, 90, "Филмът на вечерта")]),
    eit(dvb.EIT_NOW_NEXT, 4125, [event(0x2001, ON, 30, "Тази сутрин")]),
    eit(0x4F, 4126, [event(0x3001, ON, 30, "Another multiplex")]),       # another TS: not ours to keep
]

print("1. one filter is asked for, catching now/next and the schedule together")
t = FakeTuner(tables)
got = epg.harvest(t, sid, seconds=1)
check(f"the event pid is read: {t.asked}", t.asked[0] == dvb.EIT_PID)
check("the id and mask cover 0x4E and 0x50-0x5F",
      all((tid & t.asked[2]) == t.asked[1] for tid in (0x4E, 0x50, 0x5F)))

print("\n2. what the tables said is kept")
check(f"four programmes stored, one per real event: {got}", got["programmes"] == 4)
with db.db() as con:
    rows = [dict(r) for r in con.execute("SELECT * FROM tv_programmes ORDER BY uid, start")]
check("a table for another multiplex is left alone", len(rows) == 4)
check("Cyrillic survives the round trip", rows[0]["title"] == "Дивата природа")
check("so does the episode line", rows[0]["summary"] == "Епизод 4")
check("the stop time is the start plus the duration", rows[0]["stop"] - rows[0]["start"] == 3600)

print("\n3. what is on now, and what is next")
nn = epg.now_next(sid, "1:4001:4124")
check(f"now: {nn['now'] and nn['now']['title']}", nn["now"]["title"] == "Дивата природа")
check(f"next: {nn['next'] and nn['next']['title']}", nn["next"]["title"] == "Новините")
many = epg.now_next_many(sid, ["1:4001:4124", "1:4001:4125", "1:4001:9999"])
check("a page of channels is answered in one go", len(many) == 3)
check("each gets its own programme", many["1:4001:4125"]["now"]["title"] == "Тази сутрин")
check("a channel with no guide says so rather than borrowing one",
      many["1:4001:9999"]["now"] is None and many["1:4001:9999"]["next"] is None)

print("\n4. a programme that is revised corrects itself instead of appearing twice")
epg.harvest(FakeTuner([eit(dvb.EIT_NOW_NEXT, 4124, [event(0x1001, ON, 60, "Дивата природа (повторение)")])]),
            sid, seconds=1)
with db.db() as con:
    same = [dict(r) for r in con.execute(
        "SELECT * FROM tv_programmes WHERE uid = ? AND event_id = ?", ("1:4001:4124", 0x1001))]
check("still one row for that event", len(same) == 1)
check(f"carrying the newer title: {same[0]['title']!r}", "повторение" in same[0]["title"])

print("\n5. the guide outlives a rescan, which rebuilds every channel row")
before = [r["id"] for r in livetv.channels()]
livetv._save_channels(sid, [
    {"uid": "1:4001:4124", "name": "BNT 1 HD", "number": 1, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4124}},
    {"uid": "1:4001:4125", "name": "bTV", "number": 2, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4125}},
])
check("the channels are still there", len(livetv.channels()) == 2)
check("and so is what is on them", epg.now_next(sid, "1:4001:4124")["now"] is not None)
print(f"   (channel row ids before {before}, after {[r['id'] for r in livetv.channels()]})")

print("\n6. a network that leaves out its network id still finds its channels")
epg.harvest(FakeTuner([eit(dvb.EIT_NOW_NEXT, 4125, [event(0x2002, SOON, 30, "Предаването")], onid=0)]),
            sid, seconds=1)
check("matched on the service id alone, since only one channel answers to it",
      any(p["title"] == "Предаването" for p in epg.window(
          sid, ["1:4001:4125"], time.time(), time.time() + 86400)["1:4001:4125"]))

print("\n7. a guide page asks for a stretch of time")
win = epg.window(sid, ["1:4001:4124", "1:4001:4125"], time.time(), time.time() + 3 * 3600)
check(f"BNT 1 has three in the next three hours: {[p['title'] for p in win['1:4001:4124']]}",
      len(win["1:4001:4124"]) == 3)
check("in time order", win["1:4001:4124"] == sorted(win["1:4001:4124"], key=lambda p: p["start"]))

print("\n8. what has finished and gone cold is dropped")
epg.harvest(FakeTuner([eit(dvb.EIT_NOW_NEXT, 4124, [event(0x1009, at(-9 * 60), 30, "Снощи")])]), sid, seconds=1)
everything = epg.window(sid, ["1:4001:4124"], 0, time.time() + 86400)["1:4001:4124"]
check("a programme that finished hours ago is not even stored",
      not any(p["title"] == "Снощи" for p in everything))
with db.db() as con:
    con.execute("INSERT INTO tv_programmes (source_id, uid, event_id, start, stop, title, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (sid, "1:4001:4124", 0x9999, time.time() - 99999, time.time() - 90000, "Old", time.time()))
swept = epg.expire()
check(f"and one that aged in the table is swept up: {swept} removed", swept == 1)
check("sweeping again finds nothing left to do", epg.expire() == 0)
check("while what is still on is untouched", epg.now_next(sid, "1:4001:4124")["now"] is not None)

print("\n9. the settings page can say how much guide there is")
cov = epg.coverage()
check(f"it counts programmes and channels: {cov['programmes']} on {cov['channels']} channels",
      cov["programmes"] >= 5 and cov["channels"] == 2)

# ---- filling in the multiplexes nobody watches ---------------------------------------------------
print("\n10. the sweep goes where the guide is thinnest")
livetv._save_channels(sid, [
    {"uid": "1:4001:4124", "name": "BNT 1 HD", "number": 1, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4124}},
    {"uid": "1:4001:4125", "name": "bTV", "number": 2, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 626_000_000, "service_id": 4125}},
    # a second multiplex, never watched and with no guide at all
    {"uid": "1:4002:5100", "name": "Diema", "number": 9, "kind": "tv", "provider": "Unics TV",
     "tune": {"delsys": "DVB-C", "frequency": 642_000_000, "service_id": 5100}},
])
muxes = epg._multiplexes()
check(f"both multiplexes are known: {[m['frequency'] // 1_000_000 for m in muxes]}", len(muxes) == 2)
check("the one never heard from comes first", muxes[0]["frequency"] == 642_000_000)
check("and the one with a fresh guide comes last", muxes[1]["frequency"] == 626_000_000)

print("\n11. a viewer always outranks the sweep")
check("nothing is held when it is not sweeping", epg.sweep_inputs() == set())
check("and there is nothing to give up", epg.yield_input() is False)
epg._sweep["using"] = (0, 1)
check("while sweeping, its input is declared so nothing hands it out twice",
      epg.sweep_inputs() == {(0, 1)})


def release_soon():
    time.sleep(0.2)
    epg._sweep["using"] = None


threading.Thread(target=release_soon, daemon=True).start()
asked = time.monotonic()
gave = epg.yield_input()
check(f"asked to stand down, it lets go ({time.monotonic() - asked:.2f}s)", gave and epg.sweep_inputs() == set())
check("and it was told to stop, not merely forgotten", epg._sweep["stop"] is True)

print("\n12. the sweep declines politely when there is nothing to do or nowhere to do it")
from lenta import livestream                                              # noqa: E402
livestream.idle_input = lambda device=None: None
out = epg.sweep_once()
check(f"no spare input, so no sweep: {out.get('why')}", out["swept"] is False)
# Large enough that "now minus this" lands before 1970, so no multiplex can count as stale.
epg.SWEEP_STALE = 10 ** 10
out = epg.sweep_once()
check(f"nothing stale, so no sweep: {out.get('why')}", out["swept"] is False and "recent" in out["why"])

print("\n13. a sweep and a viewer starting at the same moment cannot get the same input")
# The frontends the server can see, and nothing watching anything yet.
from lenta import dvb as _dvb                                             # noqa: E402
livestream._feeds.clear()
epg._sweep["using"] = None
fes = [_dvb.Frontend(adapter=0, index=i, name="Si2168", delsys=["DVB-C"], product="dualHD",
                     usb_id="2040:8265", serial="T") for i in (0, 1)]
_dvb.adapters = lambda skip=None: list(fes)

handed_out = []
barrier = threading.Barrier(2)


def as_a_viewer():
    barrier.wait()
    fe = livestream._free_input("usb:2040:8265:T")
    handed_out.append(("viewer", fe and (fe.adapter, fe.index)))


def as_the_sweep():
    barrier.wait()
    fe = livestream.reserve_input("usb:2040:8265:T", lambda key: epg._sweep.__setitem__("using", key))
    handed_out.append(("sweep", fe and (fe.adapter, fe.index)))


for _ in range(60):                       # one run proves nothing about a race; sixty is a fair try
    handed_out.clear()
    epg._sweep["using"] = None
    livestream._feeds.clear()
    barrier.reset()
    ts = [threading.Thread(target=as_a_viewer), threading.Thread(target=as_the_sweep)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    inputs = [i for _, i in handed_out if i is not None]
    if len(inputs) != len(set(inputs)):
        break
check(f"over sixty races, no input went to two claimants at once: {handed_out}",
      len(inputs) == len(set(inputs)))
epg._sweep["using"] = None

print("\n14. collecting can be switched off, so it can be ruled out of a problem")
from lenta.db import set_setting                                          # noqa: E402
check("on unless said otherwise", epg.wanted("watching") and epg.wanted("sweep"))
set_setting("livetv_guide_watching", "off")
check("switched off, nothing rides along with viewing", epg.wanted("watching") is False)
check("and the sweep is a separate decision", epg.wanted("sweep") is True)
check("a feed asked to collect declines quietly",
      epg.collect_alongside(None, 1, (0, 0), lambda: True) is False)
set_setting("livetv_guide_watching", "on")

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the guide ok")
