"""An idle library should look idle.

New media does not arrive by scanning. The watcher notices a folder whose modification time moved,
waits for the copy to finish and scans that one folder — a minute or two after the file lands. The
hourly scan exists for what that cannot see: a file replaced in place, a folder whose timestamp a
network share never updated. A safety net.

The trouble was what the safety net did on the way past. The file-reading half has always been
incremental and does nothing when nothing changed, but everything after it ran regardless: matching
titles that have never matched, asking again for trailers, and running FFmpeg over every film with no
picture — including the ones that have never given a usable frame and never will. On three libraries
every hour, for ever, on a server nobody had touched. That is what Vlad was looking at: "artwork,
13 of 22", the same 22 films, hour after hour.

So two rules, checked here. A periodic scan that finds no change stops at the walk. And a film that
gives up no frame is written down as tried and left alone for a fortnight rather than being asked
again every time. Neither applies to a scan a person asked for: that still does everything.
"""
import os, re, sys, tempfile, time
from pathlib import Path

ROOT = Path("/home/claude/lenta")
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "server"))
from lenta import scanner                                                  # noqa: E402

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. the periodic scan is marked as one, and only it")
src = (ROOT / "server" / "lenta" / "scanner.py").read_text()
main = (ROOT / "server" / "lenta" / "main.py").read_text()
watcher = (ROOT / "server" / "lenta" / "watcher.py").read_text()
admin = (ROOT / "server" / "lenta" / "api_admin.py").read_text()
check("the hourly timer asks for a periodic scan", "request_scan(None, periodic=True)" in main)
check("the watcher never does — it only scans what really changed",
      "periodic" not in watcher)
check("and neither does anything a person presses", "periodic" not in admin)

print("\n2. a periodic scan that finds nothing stops before the expensive half")
# The gate has to sit above the file-reading phase: everything below it costs work.
gate = re.search(r"if periodic and not todo and not gone and not report\.get\(\"renamed\"\):(.{0,400})",
                 src, re.S)
check("the gate exists and tests all three kinds of change", gate is not None)
if gate:
    body = gate.group(1)
    check("it returns rather than carrying on", "return" in body)
    check("and records that nothing changed", "unchanged=True" in body)
    before_gate = src[:src.index("if periodic and not todo")]
    after_gate = src[src.index("if periodic and not todo"):]
    for phase, what in (('phase="reading files"', "reading files"),
                        ('phase="fetching metadata"', "fetching metadata"),
                        # the call, not the definition: the definition sits earlier in the file
                        ("_fill_frames(library_id, force=refresh_metadata)", "grabbing frames"),
                        ("trailers.request_index()", "indexing trailers"),
                        ("trickplay.request()", "seek-bar thumbnails"),
                        ("intros.request()", "intro detection")):
        check(f"{what} happens only past the gate", phase in after_gate and phase not in before_gate)

print("\n3. a film that gives up no frame is not asked again every scan")
check("the frame grabber skips what it has already tried",
      "'$.thumb_tried'" in src and "FRAME_RECHECK" in src)
window = re.search(r"FRAME_RECHECK = (\d+) \* 86400", src)
check(f"it waits a fortnight before trying again ({window.group(1) if window else '?'} days)",
      window is not None and int(window.group(1)) >= 7)
check("a failure is written down, not only a success",
      re.search(r"if not data:.{0,200}thumb_tried", src, re.S) is not None)
check("and a scan asked for with refresh tries them all again",
      "_fill_frames(library_id, force=refresh_metadata)" in src)
# A cutoff of 0 reads as "ignore the record" and does the exact opposite: the stamps are past times,
# so every one of them fails `stamp < 0` and the whole point of "refresh" is lost. It must be ahead.
check("forcing uses a cutoff ahead of now, not behind it",
      re.search(r"cutoff = time\.time\(\) \+ \d+ if force", src) is not None)
check("an empty list does not even show a progress bar",
      re.search(r"if not rows:\s*\n\s*return\s*\n\s*state\.update\(phase=\"artwork\"", src) is not None)

print("\n3b. and the same thing proved by running it")
# Reading the source says the filter is written; this says it works. A title whose file FFmpeg can
# never open is exactly the case that was being retried every hour — so: try, be skipped, then be
# tried again when somebody asks for a refresh.
from lenta.db import db, init_db                                           # noqa: E402
init_db()
with db() as con:
    con.execute("INSERT INTO libraries(name, kind, paths, position) VALUES('Proof', 'movies', '[]', 99)")
    lib = con.execute("SELECT id FROM libraries WHERE name = 'Proof'").fetchone()["id"]
    cur = con.execute("INSERT INTO items(library_id, kind, key, title, sort_title, thumb, added_at) "
                      "VALUES(?, 'movie', 'broken', 'Broken', 'broken', NULL, ?)", (lib, time.time()))
    item = cur.lastrowid
    con.execute("INSERT INTO files(item_id, library_id, path, size, mtime, video_codec, duration) "
                "VALUES(?, ?, '/nonexistent/broken.mkv', 1, 1, 'h264', 100)", (item, lib))

attempts = []
scanner._grab_frame = lambda path, at, hdr: attempts.append(path) or None      # a file that never yields
scanner._fill_frames(lib);              first = len(attempts)
scanner._fill_frames(lib);              again = len(attempts) - first
scanner._fill_frames(lib, force=True);  forced = len(attempts) - first - again

check(f"a file that yields no frame is tried once ({first})", first == 1)
check(f"and not again on the next scan ({again})", again == 0)
check(f"but a refresh does try it again ({forced})", forced == 1)
with db() as con:
    stamp = con.execute("SELECT json_extract(extra, '$.thumb_tried') AS t FROM items WHERE id = ?",
                        (item,)).fetchone()["t"]
check("the attempt is written against the title, not kept in memory", bool(stamp))

print("\n4. the queue keeps the flag, and a person's request outranks the timer's")
scanner._queues.clear()
scanner._workers.clear()
started = []
scanner._start_worker = lambda lid: started.append(lid)     # don't actually scan anything here

scanner.request_scan(7, periodic=True)
q = scanner._queues[7]
check(f"a periodic scan is queued as one ({q})", q == [[False, None, True]])

scanner.request_scan(7)                                      # the same library, asked for by hand
check(f"asking by hand upgrades the queued one rather than adding a second ({scanner._queues[7]})",
      scanner._queues[7] == [[False, None, False]])

scanner.request_scan(7, refresh_metadata=True, periodic=True)
check("and a refresh is not lost either", scanner._queues[7] == [[True, None, False]])

scanner._queues.clear()
scanner.request_scan_folders(9, ["/media/films/New"])
check(f"a folder scan from the watcher is never periodic ({scanner._queues[9]})",
      scanner._queues[9] == [[False, ["/media/films/New"], False]])

scanner.request_scan(9, periodic=True)
check("a full scan replaces the folder scans it covers",
      scanner._queues[9] == [[False, None, True]])

print("\n5. the worker unpacks what the queue holds")
check("three fields in, three fields out",
      "refresh, folders, periodic = q.pop(0)" in src and
      "scan_library(library_id, refresh, folders, periodic)" in src)
check("and the phase says what an idle check is doing",
      '"looking for changes" if periodic else "discovering"' in src)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("idle libraries stay quiet")
