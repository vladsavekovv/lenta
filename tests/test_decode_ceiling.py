"""The ceiling above which a television stops playing — and what LENTA does about it.

A film that will not start on his 2017 set has been open since 1.11. It produces no error: the picture
simply never arrives. That is Samsung's own documented behaviour, in their words — "Media with higher
bit or frame rate than specifications can cause video or audio playback to **stall**" — and a stall is
not an event. Nothing fires, so nothing recovers, and until 1.18.0 nothing even looked.

Samsung publish the specifications: a **Full HD** panel decodes H.264 to **level 4.1 at 20 Mbps**, a
**4K** one to level 5.1 at 50–60 Mbps. LENTA could not compare a file against either, because the
`files` table recorded height and bitrate and nothing about how hard the stream is to decode. ffprobe
has always returned `profile`, `level`, `refs` and `r_frame_rate` in the same call we already make.

So, four things, and the fourth is the one that works whatever the cause:

  1. **The numbers are stored.** `probe.summarize()` returns them and the table has columns for them,
     added by migration so an existing library is not rescanned to get them.
  2. **`decide()` has a ceiling.** A file above what the screen states is converted instead of handed
     over — on the direct path and on the remux path, because a remux copies the picture through
     untouched and stalls identically.
  3. **The conversion lands inside the ceiling too.** "original" keeps the source height, so a 4K film
     sent to a Full HD set was being re-encoded at 4K and stalled exactly as the original would have.
     And `libx264` declared `-level:v 5.1` on every encode, including 1080p ones: a decoder reads the
     number in the stream, not the stream.
  4. **A direct play that produces no picture falls back by itself.** The `error` listener has always
     repackaged the file and tried again. The watchdog, which is what actually notices a stall, only
     wrote a diagnostic line and gave up. It now does what the error listener does.

Unknown is never treated as too much, in either direction: a ceiling the client did not state (0) and a
number the file never recorded (NULL, every row scanned before 1.18.0) are both skipped. The failure
this guards against is a library silently converting everything, which would be worse than the fault.

**What cannot be checked here**: whether his television actually plays the converted file. Nothing in
this sandbox decodes H.264 — the Chromium here is the codec-stripped open-source build and reports an
empty string for H.264 *and* for AAC — and no television is reachable. What is checked is that the
right comparison is made with the right numbers, and that the fallback runs. His set is the proof.
"""
import json, os, re, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "server"))
from lenta import api_playback, db, probe, transcode                       # noqa: E402

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def caps(**kw):
    """A client's capabilities: everything a modern browser claims, plus whatever ceiling is asked for."""
    base = dict(video=["h264", "hevc"], audio=["aac", "mp3", "ac3"], containers=["mp4", "mkv"], hdr=True)
    return api_playback.Caps(**{**base, **kw})


def a_file(**kw):
    """A row from the files table, as `_file_row` hands it to `decide()`."""
    base = dict(id=1, container="mp4", duration=5400.0, bitrate=8_000_000, video_codec="h264",
                width=1920, height=1080, pix_fmt="yuv420p", profile="High", level=40, refs=4, fps=23.976,
                hdr=0, audio=[{"index": 1, "codec": "aac", "channels": 2}], subtitles=[], path="/x.mp4")
    return {**base, **kw}


def body(**kw):
    return api_playback.PlaybackBody(**{"item_id": 1, "quality": "original", **kw})


# ---------------------------------------------------------------------------------------------------
print("1. the numbers that say how hard a file is to decode are stored")

src = (ROOT / "server" / "lenta" / "probe.py").read_text()
check("ffprobe's level is read, and it is the codec's own number (H.264 4.1 is 41)",
      '"level": int(video["level"])' in src)
check("so is the profile", '"profile":' in src)
check("so are the reference frames", '"refs": int(video["refs"])' in src)
check("and the frame rate, which ffprobe gives as a fraction", 'r_frame_rate' in src)
check("a level of 0 or a missing one is stored as NULL rather than as 'easy'",
      'video["level"] > 0' in src and 'isinstance(video.get("level"), int)' in src)

db.init_db()
with db.db() as con:
    cols = {r[1]: r[2] for r in con.execute("PRAGMA table_info(files)")}
for col, kind in (("profile", "TEXT"), ("level", "INTEGER"), ("refs", "INTEGER"), ("fps", "REAL")):
    check(f"the files table has {col} {kind}", cols.get(col) == kind)

# The migration, on a database that predates the columns: an existing library must not need a rescan.
old = Path(tempfile.mkdtemp()) / "old.db"
schema = re.search(r"CREATE TABLE IF NOT EXISTS files\s*\((.*?)\n\s*\)", (ROOT / "server" / "lenta" / "db.py")
                   .read_text(), re.S).group(1)
legacy = "\n".join(ln for ln in schema.splitlines()
                   if not re.match(r"\s*(profile|level|refs|fps)\s", ln))
con = sqlite3.connect(old)
con.executescript(f"CREATE TABLE files ({legacy});")
con.execute("INSERT INTO files(id, item_id, library_id, path) VALUES(7, 1, 1, '/old.mkv')")
con.commit()
con.close()
before = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(files)")}
check("the test's own pre-1.18.0 table really is missing them (or this proves nothing)",
      not ({"profile", "level", "refs", "fps"} & before))
os.environ["LENTA_DATA_DIR"] = str(old.parent)
db.DB_PATH = old
db.init_db()
after = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(files)")}
try:
    row = sqlite3.connect(old).execute("SELECT path, level FROM files WHERE id = 7").fetchone()
except sqlite3.OperationalError:
    row = None                      # no such column: the migration did not run (checked just below)
check("init_db adds the four columns to a database that predates them",
      {"profile", "level", "refs", "fps"} <= after)
check("and the rows already there survive it, with the new numbers left unknown",
      row == ("/old.mkv", None))

scan = (ROOT / "server" / "lenta" / "scanner.py").read_text()
check("the scanner writes them when it inserts a file",
      all(c in scan for c in ("profile", "level", "refs", "fps")) and "ON CONFLICT" in scan)

# ---------------------------------------------------------------------------------------------------
print("\n2. decide() refuses to hand over a file the screen would stall on")

fhd = caps(max_height=1080, max_bitrate=20_000_000, max_level=41)
uhd = caps(max_height=2160, max_bitrate=60_000_000, max_level=51)

mode, why = api_playback.decide(a_file(), body(caps=fhd))
check("an ordinary 1080p film is still played as it is on a Full HD set", mode == "direct")

mode, why = api_playback.decide(a_file(level=51), body(caps=fhd))
check("a level 5.1 film is not, on a set that stops at 4.1", mode == "transcode")
check("and the reason names the number, because 'it does not play' explains nothing",
      "4.1" in why and "5.1" in why)

mode, why = api_playback.decide(a_file(level=51), body(caps=uhd))
check("the same film is handed straight over to a 4K set", mode == "direct")

mode, why = api_playback.decide(a_file(height=2160, width=3840), body(caps=fhd))
check("a 4K film is converted for a Full HD set", mode == "transcode" and "1080p" in why)

mode, why = api_playback.decide(a_file(bitrate=45_000_000), body(caps=fhd))
check("so is one above the bitrate Samsung publish for the panel",
      mode == "transcode" and "20 Mbps" in why)

mode, why = api_playback.decide(a_file(bitrate=45_000_000), body(caps=uhd))
check("and it is fine on a 4K set, whose ceiling is 60", mode == "direct")

# The remux path copies the picture through untouched, so it stalls in exactly the same way.
mkv = a_file(container="mkv", level=51)
mode, why = api_playback.decide(mkv, body(caps=caps(containers=["mp4"], max_height=1080, max_level=41)))
check("an MKV above the ceiling is re-encoded, not repackaged — a remux would stall identically",
      mode == "transcode" and "4.1" in why)
mode, why = api_playback.decide(a_file(container="mkv"), body(caps=caps(containers=["mp4"], max_height=1080,
                                                                       max_level=41)))
check("while an MKV inside the ceiling is still only repackaged", mode == "remux")

# Unknown is never too much — in either direction.
mode, why = api_playback.decide(a_file(level=None, refs=None, fps=None), body(caps=fhd))
check("a file scanned before 1.18.0, with no level recorded, is not held back by the level ceiling",
      mode == "direct")
mode, why = api_playback.decide(a_file(level=51, height=2160, bitrate=90_000_000), body(caps=caps()))
check("and a client that stated no ceiling gets what it always got", mode == "direct")

# HEVC counts its levels in thirtieths: its level 4.1 is 123. An H.264 ceiling must not touch it.
mode, why = api_playback.decide(a_file(video_codec="hevc", level=123), body(caps=fhd))
check("an HEVC level is never compared with an H.264 ceiling — the two count differently",
      mode == "direct")

beyond = getattr(api_playback, "_beyond", None)
check("the reference-frame ceiling exists but is only applied when a client states one",
      bool(beyond) and beyond(a_file(refs=16), caps(max_refs=4)) is not None
      and beyond(a_file(refs=16), fhd) is None)

# ---------------------------------------------------------------------------------------------------
print("\n3. the conversion lands inside the ceiling as well")

clamp = getattr(api_playback, "clamp_quality", None) or (lambda *a: "(no clamp)")
check('"original" on a 4K film becomes 1080p for a Full HD set, not the source height',
      clamp(fhd, "original", 2160) == "1080p")
check("while a 1080p film keeps its own size on the same set, rather than dropping a step",
      clamp(fhd, "original", 1080) == "original")
check('"original" on a 4K set stays original', clamp(uhd, "original", 2160) == "original")
check("a quality that already fits is left exactly as asked",
      clamp(fhd, "720p", 2160) == "720p")
check("and one above the ceiling comes down to the largest that fits",
      clamp(caps(max_height=720), "1080p", 1080) == "720p")
check("a client with no ceiling is never clamped",
      clamp(caps(), "original", 2160) == "original")

api = (ROOT / "server" / "lenta" / "api_playback.py").read_text()
check("the clamped quality is what the encoder is given", "quality=quality, start=start" in api)
check("and what the answer reports, so the player is not told something else",
      '"quality": quality,' in api)
check("only a conversion is clamped — a direct play is the file and a remux copies the picture",
      'if mode == "transcode" else body.quality' in api)
check("and the clamp is told the source height, so it does not push an easy film down a step",
      'body.quality, f["height"] or 0' in api)

tc = (ROOT / "server" / "lenta" / "transcode.py").read_text()
check("libx264 no longer declares level 5.1 on a 1080p encode",
      '"-level:v", "5.1", "-sc_threshold"' not in tc)
check("it declares the level the picture it is making actually needs",
      '"4.1" if height <= 1080 else "5.1"' in tc)

# ---------------------------------------------------------------------------------------------------
print("\n4. and a direct play that produces no picture recovers by itself")

player = (ROOT / "web" / "js" / "player.js").read_text()
watch = player[player.index("function watchStart"):]
watch = watch[:watch.index("\nfunction ")] if "\nfunction " in watch else watch
check("the watchdog is still there and still looks twice", "look(6)" in watch and "look(20)" in watch)
check("after twenty seconds of nothing it asks the server to repackage the file",
      "P.forceHls = true" in watch and "load(position())" in watch)
check("only for a direct play, and only once — an HLS attempt that fails must not loop",
      "P.mode === 'direct' && !P.forceHls" in watch)
check("the second look no longer falls through to the first's retry",
      "P.startTimer = look(20); return; }" in watch)
check("the error listener, which handles a refusal rather than a stall, still does its half",
      player.count("P.forceHls = true") >= 2)

check("the client sends the ceiling Samsung publish for the panel it was told about",
      "function screenCeiling" in player and "max_level: 41" in player and "max_level: 51" in player)
check("it sends no reference-frame ceiling, because Samsung publish no such number",
      "max_refs" not in player)
check("and it sends nothing at all until someone says which screen it is",
      "state.prefs?.tv_panel" in player and "return {};" in player)

prefs = (ROOT / "server" / "lenta" / "api_prefs.py").read_text()
check("the panel is a preference of its own, unset by default", '"tv_panel": ""' in prefs)
check("and only the two Samsung publish ceilings for are accepted",
      '("", "fhd", "uhd")' in prefs)
settings = (ROOT / "web" / "js" / "settings.js").read_text()
check("Settings offers it on a television, whether or not the set's own player is on",
      "p-tvpanel" in settings and "tvMode()" in settings)
check("it is saved like any other preference", "bind('#p-tvpanel', 'tv_panel'" in settings)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
