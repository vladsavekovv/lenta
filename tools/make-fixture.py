#!/usr/bin/env python3
"""Build the test fixture the Playwright sections of `tests/test_*.py` run against.

**Why this file exists.** The fixture — the accounts, the libraries and their titles — used to be a
thing that had been built by hand in a sandbox and lived only in `/tmp`. Nothing in the repository
described it, so when the data folder was deleted on 23 Oct 2026 it took a reconstruction to get it
back. That is exactly the hazard CLAUDE.md warns about: anything that must survive belongs in this
source tree. So the fixture is now *described* here, and a lost data folder costs one command.

    python3 tools/make-fixture.py                      # against the usual test server on :8597
    python3 tools/make-fixture.py --base http://... --media /path/to/media

**What it does not own.** The media files themselves — a handful of tiny transcoded clips, audio
files and artwork — are too big to keep in the repository and are not created here. They live beside
the test server (`--media`, default `/tmp/claude-0/intro`). What this script guarantees is that given
those folders, the database comes out the shape the tests expect: `admin` and `guest`, both
`secret123`, libraries in a fixed order so the ids are stable, and `guest` restricted to two of them.

**The order of the libraries is part of the fixture.** Ids are assigned in creation order and some
tests reach for "library 1" or "library 4" through a lookup that assumes the usual shape, so do not
reorder `LIBRARIES` — append.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Created in this order, so the ids are 1..n and stable between rebuilds. Append, never reorder.
LIBRARIES = [
    ("Movies",      "movies",     "media/Movies"),
    ("Animation",   "movies",     "media/Movies"),
    ("Boxing",      "movies",     "media/Movies"),
    ("TV",          "shows",      "media/TV"),
    ("Audio Books", "audiobooks", "books"),
    ("Music",       "music",      "music"),
    ("Live TV",     "livetv",     None),            # folderless; channels come from the tuner stub
]
VIEWER_LIBRARIES = (1, 4)       # what `guest` may see: the first movie library and the TV one
PASSWORD = "secret123"
# Enough genres that the commonest few differ, which is what a genre row and a genre tile are ordered
# by. `test_genre_tiles.py` wants at least two distinct ones on the shows.
GENRES = ["Action", "Comedy", "Drama", "Thriller", "Science Fiction", "Documentary"]
# The trailer is a **local** key (`v:/api/items/<id>/trailer-file`), not a YouTube id, and that is
# deliberate: a YouTube trailer is drawn by injecting YouTube's iframe API, which this sandbox cannot
# reach, so no element is ever created and anything measuring "is a trailer playing" reads false. A
# local key gives a plain <video>, which is what the banner tests look for.
PART_WATCHED = 4                # how many titles are left half-played, for Continue watching


class Api:
    def __init__(self, base):
        self.base, self.cookie = base.rstrip("/"), None

    def __call__(self, method, path, body=None, quiet=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}/api{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                for k, v in r.getheaders():
                    if k.lower() == "set-cookie":
                        self.cookie = v.split(";")[0]
                raw = r.read().decode() or "{}"
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            if not quiet:
                print(f"    {method} {path} -> {e.code} {detail}")
            return e.code, {"detail": detail}
        except OSError as e:
            print(f"    {method} {path} -> {e}")
            return 0, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8597")
    ap.add_argument("--media", default="/tmp/claude-0/intro", help="where the fixture's media folders live")
    ap.add_argument("--scan-wait", type=int, default=90, help="seconds to let the scans finish")
    a = ap.parse_args()
    api = Api(a.base)

    status, server = api("GET", "/server")
    if not status:
        sys.exit(f"No server at {a.base}. Start it first (bash /tmp/claude-0/intro/uplive.sh).")
    print(f"LENTA {server.get('version')} at {a.base}")

    # 1. the administrator. `/setup` only works on an empty server, which is the point: running this
    #    against a populated one must not quietly reset it.
    if server.get("setup_required"):
        code, _ = api("POST", "/setup", {"username": "admin", "password": PASSWORD, "server_name": "LENTA test"})
        print(f"  admin created ({code})")
    else:
        print("  admin already exists")
    code, _ = api("POST", "/auth/login", {"username": "admin", "password": PASSWORD})
    if code != 200:
        sys.exit("  could not sign in as admin — is the password still secret123?")

    # 2. the viewer. `all_libraries` off, so the library grants below are what it can see.
    _, users = api("GET", "/admin/users")
    have = {u["username"] for u in (users if isinstance(users, list) else [])}
    if "guest" not in have:
        code, _ = api("POST", "/admin/users",
                      {"username": "guest", "password": PASSWORD, "is_admin": False, "all_libraries": False})
        print(f"  guest created ({code})")
    else:
        print("  guest already exists")

    # 3. the libraries, in order, each pointing at media that is already on disk.
    _, libs = api("GET", "/libraries")
    libs = libs if isinstance(libs, list) else libs.get("libraries", [])
    named = {l["name"] for l in libs}
    for name, kind, rel in LIBRARIES:
        if name in named:
            print(f"  {name} already there")
            continue
        # `paths` is required even for a folderless kind, where it has to be present and empty.
        body = {"name": name, "kind": kind, "paths": [f"{a.media}/{rel}"] if rel else []}
        code, out = api("POST", "/admin/libraries", body)
        print(f"  {name} ({kind}) -> {code}{' ' + str(out.get('detail'))[:60] if code >= 400 else ''}")

    # 4. wait for the scans, which is what actually puts titles in the libraries. Adding a library
    #    starts one, but ask again in case a library was already there from a half-finished run.
    api("POST", "/admin/scan", quiet=True)
    print(f"  scanning (up to {a.scan_wait}s)", end="", flush=True)
    # The count has to stop changing *and* be non-zero: a scan that has not reached the first title
    # yet reads the same as one that has finished, which is how the first version of this declared
    # an empty fixture built.
    end, last, steady = time.time() + a.scan_wait, 0, 0
    while time.time() < end:
        _, libs = api("GET", "/libraries", quiet=True)
        libs = libs if isinstance(libs, list) else libs.get("libraries", [])
        total = sum(l.get("count") or 0 for l in libs)
        steady = steady + 1 if total and total == last else 0
        last = total
        if steady >= 3:
            break
        print(".", end="", flush=True)
        time.sleep(5)
    print(f" {last} titles")

    # 4b. genres and a trailer. The old fixture had these because a real metadata fetch had run
    #     against it; this sandbox has no TMDB key and must never ship one, so they are written
    #     locally instead, through the editor's own endpoint. `locked` is what the editor sets when
    #     someone pastes a link by hand, and it is what makes `trailer_for()` hand the key back
    #     without a provider being consulted. Genre rows, genre tiles and the banner trailer all
    #     need this; without it two test files have nothing to measure.
    _, libs = api("GET", "/libraries")
    libs = libs if isinstance(libs, list) else libs.get("libraries", [])
    tagged = trailers = partly = 0
    for lib in libs:
        if lib.get("kind") not in ("movies", "shows"):
            continue
        _, out = api("GET", f"/libraries/{lib['id']}/items?limit=100", quiet=True)
        items = (out or {}).get("items") or []
        for i, it in enumerate(items):
            # A spread, not one genre on everything: the genre rows and tiles are ordered by how
            # many titles carry a genre, so they need something to order.
            genres = [GENRES[i % len(GENRES)], GENRES[(i + 1 + i // len(GENRES)) % len(GENRES)]]
            values = {"genres": sorted(set(genres))}
            # Every film gets one, which is also what a real fetch produces — most films have a
            # trailer. It matters because the banner picks its own title: give three and the one on
            # screen is usually not among them, which reads as "the trailer stopped working".
            values |= {"trailer": f"v:/api/items/{it['id']}/trailer-file",
                       "trailer_name": "Fixture trailer", "trailer_source": "local"}
            code, _ = api("PUT", f"/admin/items/{it['id']}/metadata",
                          {"values": values, "locked": ["trailer"]}, quiet=True)
            tagged += code == 200
            trailers += code == 200
            if partly < PART_WATCHED and it.get("kind") in ("movie", "show"):
                # A part-watched title, so Continue watching has a row and a card has a progress
                # line on it. Reported through the same endpoint the player uses.
                dur = it.get("duration") or 3600
                if api("POST", "/progress", {"item_id": it["id"], "position": dur * 0.35,
                                             "duration": dur, "state": "paused"}, quiet=True)[0] == 200:
                    partly += 1
    print(f"  genres on {tagged} titles, a trailer on {trailers}, {partly} part-watched")

    # 5. what the viewer may see. Done last, because it names library ids.
    _, users = api("GET", "/admin/users")
    guest = next((u for u in (users if isinstance(users, list) else []) if u["username"] == "guest"), None)
    if guest:
        code, _ = api("PUT", f"/admin/users/{guest['id']}",
                      {"username": "guest", "is_admin": False, "all_libraries": False,
                       "libraries": list(VIEWER_LIBRARIES)})
        print(f"  guest sees libraries {list(VIEWER_LIBRARIES)} ({code})")

    _, libs = api("GET", "/libraries")
    libs = libs if isinstance(libs, list) else libs.get("libraries", [])
    print("\nFixture:")
    for l in libs:
        print(f"  {l['id']:>2}  {l['name']:<12} {l.get('kind'):<11} {l.get("count") or 0:>4} titles")
    if not any((l.get("count") or 0) for l in libs):
        print("\n  Nothing was scanned. Check that the media folders exist under --media.")
        sys.exit(1)


if __name__ == "__main__":
    main()
