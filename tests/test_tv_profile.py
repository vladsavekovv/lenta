"""What a Samsung set can decode, worked out from which set it is — and why it is not in use yet.

`web/js/avplay.js` holds two things. One is a lookup: given the model and panel a television reports,
what Samsung publish that it decodes, and the ceilings above which they say it stalls. The other is a
player driven over a bridge to the app's own page. Neither is switched on, and this test says what is
true of them today rather than pretending otherwise.

**Why it is dormant.** 1.17.0 moved the TV app off `location.replace()` so it would keep its own origin
and with it the device APIs, showing LENTA in an iframe instead. On a real 2017 set that does not work:
the splash appears, and then **the television opens Samsung Internet on LENTA's address**. A Tizen TV
web app does not load a remote origin into a frame; the runtime hands it to the browser. Restoring
`<tizen:allow-navigation>` made no difference, which is how we know the navigation permission was never
the cause. 1.17.2 put the app back to what works.

**And the real blocker underneath it**, found on the way: LENTA authenticates with a **session cookie**
and `api.js` sends `credentials: 'same-origin'`. Any arrangement that puts the client on the app's own
origin — a frame, a packaged bundle, Samsung's documented "load the scripts from your server" — leaves
it talking to the server cross-origin, where that cookie is not sent and nothing authenticates. This is
why Plex and Jellyfin can package their clients and LENTA cannot: they use token auth. `auth.py`
already accepts `?token=` as a fallback, so the mechanism exists; the client has no idea about it.

So: a real television app needs token auth before it needs AVPlay. What is kept here is the research
that was expensive to get and is still correct — do not delete it to tidy up.
"""
import re
from pathlib import Path

ROOT = Path("/home/claude/lenta")

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. the app is the one that works")
host = (ROOT / "tizen" / "index.html").read_text()
conf = (ROOT / "tizen" / "config.xml").read_text()
check("it opens LENTA directly, which is what a Tizen set allows", "location.replace(addr" in host)
check("and does not frame it, which a Tizen set hands to its browser", "createElement('iframe')" not in host)
check("allow-navigation is present, because that navigation needs permitting",
      "<tizen:allow-navigation" in conf)
check("and access, for the request that checks the address", '<access origin="*"' in conf)

print("\n2. what a television can decode, by which television it is")
avplay = (ROOT / "web" / "js" / "avplay.js").read_text()
# Block comments with DOTALL, line comments without — one pass over both would let `//.*` run to the
# end of the file, because DOTALL makes `.` cross newlines for every alternative in the pattern.
code = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", avplay, flags=re.S))
check("the client never calls webapis itself — on the server's origin it cannot", "webapis." not in code)
check("the profile is looked up from the set's own model and panel, not asserted for every Samsung",
      "tvProfile" in avplay and "p.panel" in avplay)
check("DTS only for a set old enough to have it (gone from Tizen 4, 2018)", "ver < 4" in code)
check("AV1 only from Tizen 5.5", "ver >= 5.5" in code)
check("the level ceiling differs by panel — 4.1 on FHD, 5.1 on UHD", "uhd ? 51 : 41" in code)
check("so does the bitrate — Samsung say a file above it stalls", "20_000_000" in code and "60_000_000" in code)
check("and the 32-stream limit below Tizen 6.5", "maxStreams" in code and "6.5" in code)
check("MKV, MPEG-TS, VC-1 and AC-3 are all in the list a <video> element refuses",
      all(x in code for x in ("'mkv'", "'ts'", "'vc1'", "'ac3'")))

print("\n3. and it stays switched off until something can authenticate")
player = (ROOT / "web" / "js" / "player.js").read_text()
check("the set's player needs a preference that defaults to off", "state.prefs?.tv_player === '1'" in player)
check("which defaults to off", '"tv_player": "0"' in (ROOT / "server" / "lenta" / "api_prefs.py").read_text())
check("and is unreachable anyway without the app's page", "avplayOffered()" in player)
check("the client still authenticates with a cookie, same-origin only",
      "credentials: 'same-origin'" in (ROOT / "web" / "js" / "api.js").read_text())
check("while the server would already take a token in the query string",
      'request.query_params.get("token")' in (ROOT / "server" / "lenta" / "auth.py").read_text())

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
