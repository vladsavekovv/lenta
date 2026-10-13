import os, sys, struct, zlib, calendar, tempfile
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import dvb

# --- the date conversion, against dates with known Modified Julian Dates -----------------------
import datetime
EPOCH = datetime.date(1858, 11, 17)          # day zero of the Modified Julian Date
# Check every day across forty years rather than a handful of dates picked by hand: the conversion in
# the standard is a pile of magic constants and an off-by-one shows up only on certain days.
first, last = datetime.date(1990, 1, 1), datetime.date(2030, 12, 31)
bad = []
d = first
while d <= last:
    got = dvb._mjd_to_date((d - EPOCH).days)
    if got != (d.year, d.month, d.day):
        bad.append((d, got))
    d += datetime.timedelta(days=1)
assert not bad, f"{len(bad)} wrong, first: {bad[0]}"
print(f"MJD conversion ok on every day from {first} to {last} ({(last - first).days + 1:,} days)")
# the one everybody gets wrong: the leap day, and the day after
for day in (datetime.date(2024, 2, 29), datetime.date(2024, 3, 1), datetime.date(2000, 2, 29)):
    assert dvb._mjd_to_date((day - EPOCH).days) == (day.year, day.month, day.day), day

def mjd_of(y, m, d):
    return (datetime.date(y, m, d) - EPOCH).days

bcd = lambda n: ((n // 10) << 4) | (n % 10)

def event(eid, y, mo, d, hh, mm, dur_h, dur_m, title, summary, desc="", genre=None, lang="bul"):
    short = bytes([0x4D, 0, *lang.encode()])
    name = title.encode("utf-8"); sm = summary.encode("utf-8")
    body = lang.encode() + bytes([len(name) + 1, 0x15]) + name + bytes([len(sm) + 1, 0x15]) + sm
    short = bytes([0x4D, len(body)]) + body
    descs = short
    if desc:
        text = desc.encode("utf-8")
        ext = bytes([0x00]) + lang.encode() + bytes([0, len(text) + 1, 0x15]) + text
        descs += bytes([0x4E, len(ext)]) + ext
    if genre is not None:
        descs += bytes([0x54, 2, genre << 4, 0x00])
    head = struct.pack(">H", eid)
    head += struct.pack(">H", mjd_of(y, mo, d)) + bytes([bcd(hh), bcd(mm), 0])
    head += bytes([bcd(dur_h), bcd(dur_m), 0])
    head += struct.pack(">H", (4 << 13) | len(descs))      # running, not scrambled
    return head + descs

def eit(table_id, service_id, events, tsid=4001, onid=1):
    body = struct.pack(">HHB", tsid, onid, 0) + bytes([table_id]) + b"".join(events)
    head = bytes([table_id]) + b"\x00\x00" + struct.pack(">H", service_id) + bytes([0xC1, 0, 0])
    sec = head + body
    sec = sec[:1] + struct.pack(">H", 0xB000 | (len(sec) - 3 + 4)) + sec[3:]
    return sec + struct.pack(">I", zlib.crc32(sec) & 0xFFFFFFFF)

# --- a real-looking now/next table, in Cyrillic --------------------------------------------------
sec = eit(dvb.EIT_NOW_NEXT, 4124, [
    event(0x1001, 2026, 10, 12, 20, 30, 1, 0, "Дивата природа", "Епизод 4", "Пътуване из Африка", genre=0x9),
    event(0x1002, 2026, 10, 12, 21, 30, 0, 45, "Новините", "Късна емисия", genre=0x2),
])
out = dvb.parse_eit(sec)
print()
print("service", out["service_id"], "· now/next", out["now_next"], "· tsid", out["tsid"])
for e in out["events"]:
    when = datetime.datetime.fromtimestamp(e["start"], datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"  {when} UTC  +{e['seconds']//60:>3}m  {e['title']!r}  [{e['genre']}]  {e['summary']!r}")
    if e["description"]:
        print(f"       {e['description']!r}")
assert out["service_id"] == 4124 and out["now_next"] and out["tsid"] == 4001
assert len(out["events"]) == 2, out["events"]
a, b = out["events"]
assert a["title"] == "Дивата природа" and a["summary"] == "Епизод 4"
assert a["description"] == "Пътуване из Африка" and a["genre"] == "Education"
assert a["start"] == calendar.timegm((2026, 10, 12, 20, 30, 0, 0, 0, 0)) and a["seconds"] == 3600
assert b["title"] == "Новините" and b["genre"] == "News" and b["seconds"] == 45 * 60
assert a["language"] == "bul"

# --- the schedule tables are read, the ones for other muxes are not -----------------------------
assert dvb.parse_eit(eit(0x50, 4124, [event(0x2001, 2026, 10, 13, 9, 0, 1, 0, "Сутрин", "")]))["events"]
assert dvb.parse_eit(eit(0x5F, 4124, [event(0x2002, 2026, 10, 14, 9, 0, 1, 0, "Later", "")]))["events"]
for other in (0x4F, 0x60, 0x6F, 0x42, 0x00):
    assert dvb.parse_eit(eit(other, 4124, [event(0x3001, 2026, 10, 13, 9, 0, 1, 0, "Elsewhere", "")])) == {}, hex(other)
print()
print("schedule tables read; the ones describing other multiplexes ignored")

# --- junk must not raise -------------------------------------------------------------------------
for bad in (b"", b"\x4e", b"\x4e\xb0\x0d" + b"\x00" * 10, b"\x4e" + b"\xff" * 40):
    dvb.parse_eit(bad)
# an event with no start time is not a programme
blank = eit(dvb.EIT_NOW_NEXT, 1, [b"\x40\x01" + b"\xff" * 5 + b"\x00\x00\x00" + b"\x00\x00"])
assert dvb.parse_eit(blank)["events"] == []
print("junk and undated events handled")
print()
print("parse_eit ok")
