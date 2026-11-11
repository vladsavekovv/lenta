#!/usr/bin/env python3
"""Recreate the test environment the browser tests run against, from nothing.

**Why this file exists.** The tests hard-code a test server at `/tmp/claude-0/intro/uplive.sh` on port
8597. That launcher, the small media clips it serves and the data folder lived only in a sandbox's
`/tmp` — and on 6 Nov 2026 the sandbox was replaced and all of it went, so no browser test could run.
`tools/make-fixture.py` already described the *database*; this describes everything under it:

    python3 tools/make-test-env.py          # media clips + launcher; then:
    bash /tmp/claude-0/intro/uplive.sh      # start the test server on :8597
    python3 tools/make-fixture.py           # accounts, libraries, genres, trailers

What it makes, all under `/tmp/claude-0/intro` (override with `--root`):
  - `media/Movies/<Title (Year)>/<Title (Year)>.mp4` with a `-trailer.mp4` beside each, so the local
    trailer keys `make-fixture.py` writes have a file to serve;
  - `media/TV/<Show>/Season 01/<Show> - S01E0n.mp4`;
  - `books/<Author> - <Title>/` and `music/<Artist>/<Album>/` with short audio files;
  - `runlive.py`, which runs the server with test settings and seeds live TV channels from a stub
    tuner (nothing is ever tuned here); and `uplive.sh`, which starts it once and waits until it answers.

The clips are generated with ffmpeg: a test pattern with a tone, H.264/AAC like a real library, at a
size that keeps the whole set about 55 MB. The titles are real-sounding on purpose — the scanner
parses names and years out of them — but nothing here is fetched from anywhere.

**This is not the same fixture as before 6 Nov**, only the same shape; ids and which film is "first on
Home" can differ. Compare a change against the previous version *on this same environment*.
"""
import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

MOVIES = [
    ("Rocky", 1976), ("Rocky II", 1979), ("Ali", 2001), ("Inception", 2010), ("Southpaw", 2015),
    ("Hands of Stone", 2016), ("Raging Bull", 1980), ("Cinderella Man", 2005), ("The Fighter", 2010),
    ("Creed", 2015), ("Million Dollar Baby", 2004), ("Real Steel", 2011), ("The Hurricane", 1999),
    ("Warrior", 2011), ("Bleed for This", 2016), ("Jawbreaker Road", 2019), ("Night Harbour", 2021),
    ("The Long Corner", 2018), ("Paper Lanterns", 2020), ("Copper Sky", 2017), ("Glass Orchard", 2022),
    ("Iron Meadow", 2014),
]
SHOWS = [("Theme Test", 3), ("Harbour Lights", 4), ("Northern Line", 2)]
BOOKS = [("Ada Wren", "The Quiet Tide"), ("Milo Hart", "Lanterns at Dusk")]
ALBUMS = [("The Low Fields", "First Light", 3), ("Mara Ilieva", "Rila", 2)]
CLIP_SECONDS = 60           # long enough that "past 15 s" and "35 % watched" mean something
TRAILER_SECONDS = 12

RUNLIVE = '''\
"""The LENTA test server on :8597. Made by tools/make-test-env.py; not part of the product."""
import os, sys
os.environ.setdefault("LENTA_DATA_DIR", "{root}/data")
os.environ.setdefault("LENTA_PORT", "8597")
os.environ.setdefault("LENTA_HOST", "127.0.0.1")
os.environ.setdefault("LENTA_DEV_VP9", "1")      # headless Chromium has no H.264 decoder
os.environ.setdefault("LENTA_ALLOW_ROOT", "1")   # the sandbox runs as root; the product refuses to
sys.path.insert(0, "{server}")


def seed():
    """Live TV channels from a stub tuner, once. Nothing is tuned: the rows only give the guide, the
    channel rows and the permission tests something to show."""
    from lenta import db, livetv
    db.init_db()
    if livetv.channels():
        return
    sid = livetv.add_source("tuner", "Test tuner", {{"device": "usb:2040:8265:T", "inputs": []}})
    livetv._save_channels(sid, [
        {{"uid": f"1:1:{{n}}", "name": name, "number": n, "provider": "Test", "kind": "tv",
          "tune": {{"delsys": "DVB-C", "frequency": 474000000, "service_id": n}}}}
        for n, name in enumerate(["BNT 1 HD", "bTV HD", "Nova TV", "BNT 2", "Kino Nova", "Diema"], 1)])


if __name__ == "__main__":
    seed()
    from lenta.__main__ import main
    main()
'''

UPLIVE = '''\
#!/bin/bash
# Start the LENTA test server on :8597 unless it is already answering, then wait until it does.
# Made by tools/make-test-env.py. Stop it with: pkill -f 'runliv[e].py'
up() {{ curl -s -o /dev/null -m 2 http://127.0.0.1:8597/api/server; }}
up && exit 0
mkdir -p {root}/data
setsid nohup python3 {root}/runlive.py > {root}/live.log 2>&1 < /dev/null &
for i in $(seq 1 60); do up && exit 0; sleep 1; done
echo "test server did not start; see {root}/live.log" >&2
exit 1
'''


def clip(path: Path, seconds: int, freq: int, video=True, label=""):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    src = ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}"]
    if video:
        src = ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={seconds}"] + src
        enc = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-b:v", "150k",
               "-c:a", "aac", "-b:a", "48k", "-shortest", "-movflags", "+faststart"]
    else:
        enc = ["-c:a", "libmp3lame", "-b:a", "48k"]
    meta = ["-metadata", f"title={label}"] if label else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, *enc, *meta, str(path)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/tmp/claude-0/intro")
    a = ap.parse_args()
    root = Path(a.root)
    server = Path(__file__).resolve().parent.parent / "server"
    for n, (title, year) in enumerate(MOVIES):
        name = f"{title} ({year})"
        clip(root / "media/Movies" / name / f"{name}.mp4", CLIP_SECONDS, 300 + 20 * n)
        clip(root / "media/Movies" / name / f"{name}-trailer.mp4", TRAILER_SECONDS, 900 + 20 * n)
    for title, episodes in SHOWS:
        for e in range(1, episodes + 1):
            clip(root / "media/TV" / title / "Season 01" / f"{title} - S01E{e:02d}.mp4", CLIP_SECONDS, 500 + 40 * e)
    for author, title in BOOKS:
        for c in range(1, 3):
            clip(root / "books" / f"{author} - {title}" / f"{c:02d} Chapter {c}.mp3", 30, 220 + 30 * c,
                 video=False, label=f"Chapter {c}")
    for artist, album, tracks in ALBUMS:
        for t in range(1, tracks + 1):
            clip(root / "music" / artist / album / f"{t:02d} Track {t}.mp3", 30, 330 + 50 * t,
                 video=False, label=f"Track {t}")
    (root / "runlive.py").write_text(RUNLIVE.format(root=root, server=server))
    up = root / "uplive.sh"
    up.write_text(UPLIVE.format(root=root))
    up.chmod(0o755)
    size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and "data" not in f.parts)
    print(f"test environment in {root}: {len(MOVIES)} films, {sum(e for _, e in SHOWS)} episodes, "
          f"{len(BOOKS)} books, {len(ALBUMS)} albums, {size / 1e6:.1f} MB")
    print(f"next: bash {up} && python3 {Path(__file__).parent / 'make-fixture.py'}")


if __name__ == "__main__":
    sys.exit(main())
