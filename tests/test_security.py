"""The security review of 18 Oct 2026, turned into checks that stay checked.

Three reviews went through LENTA looking for one thing each — who may call what, what reaches a
dangerous sink, and what the browser and the phone are trusted with. They found thirty-odd things
between them. Most of the code came out well: every one of the sixty-six administrator endpoints
inherits its guard from its router, every file-serving path validates its filename, every SQL statement
binds its parameters, nothing shells out through a shell, passwords are PBKDF2 with a per-user salt and
a constant-time compare, and no API key or signing secret is ever sent to a browser.

What this file covers is the part that did not come out well, so that it cannot come back:

  **Live television had no permissions at all.** `/api/livetv/*` asked for a signed-in user and nothing
  more, so a viewer granted one film library could list every channel, read the whole guide, and take a
  tuner input from whoever was watching. The product means to control this — there is a `livetv` library
  kind, the menu filters on it, and Home goes out of its way to drop the channels row for a viewer who
  has none — so this was a decision made in four places and kept in three.

  **A feed's id was a password, and it was published.** `/api/livetv/live` handed every running feed's
  id to any signed-in user, and the per-viewer token was advisory: `touch()` updates a token it knows and
  does nothing otherwise, which reads like a check and is not one. So one viewer could watch another's
  stream. The same two endpoints also returned FFmpeg's command line and the tuner's device path.

  **A token could not be taken back.** Sessions are signed statements rather than rows, so signing out
  deleted the cookie and left the token itself good for the rest of its thirty days; changing the
  password because you thought it had leaked did nothing at all.

  **`?token=` authenticated everything**, including `/api/admin`. The comment said it was so VLC could
  open a stream URL. A full-privilege credential that travels in URLs is one that ends up in history,
  in bookmarks, in a `Referer` header and in any access log somebody turns up to INFO.

  **Signing in had no limit**, so a password guesser ran as fast as the server would answer — and each
  answer cost 240,000 rounds of PBKDF2, which made it a way to spend the server's processor too.

  **Three queries forgot the library filter** and one write was unscoped: a viewer could read cards for
  films in libraries he cannot open, and could rewrite trailer metadata server-wide.

  **The server would fetch any address it was handed**, including `file:///etc/passwd` — and some of
  those addresses come out of a third party's answer rather than out of LENTA.

  **A link from TMDB went into an `href` unchecked**, so `javascript:` survived the escaping, and the
  client's key receiver accepted a message from any window at all.

What is NOT claimed here, and is in CLAUDE.md as the next job: the Android app's certificate check
compares a name, not a key, so anyone already on the LAN can still intercept HTTPS with a self-signed
CA named exactly "LENTA local CA". Pinning the key is the fix; it needs an API the app's minimum
Android does not have and an exception handler the dex writer cannot emit, and it is not something to
guess at in hand-written Dalvik that cannot be run here.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(ROOT / "server"))
from lenta import auth, db                                                 # noqa: E402
try:
    from lenta import safefetch                                            # noqa: E402
except ImportError:                     # it does not exist before 1.19.0, which section 7 then says
    safefetch = None

db.init_db()
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------------------------
print("1. live television is a library, and only people who have it may watch")

livetv = src("server/lenta/api_livetv.py")
check("the viewer-facing router demands a live TV library, not merely a signed-in user",
      "dependencies=[Depends(auth.live_tv_viewer)]" in livetv)
check("the guard is on the router, so the next endpoint added here inherits it",
      re.search(r'read = APIRouter\(prefix="/api/livetv",\s*dependencies=', livetv) is not None)
check("it refuses with 404 rather than 403, which would confirm what exists",
      '404, "There is no live television here"' in src("server/lenta/auth.py"))

# Run it: three users against the real dependency.
with db.db() as con:
    con.execute("INSERT INTO users(id, username, password_hash, is_admin, all_libraries) "
                "VALUES(1, 'owner', 'x', 1, 1)")
    con.execute("INSERT INTO users(id, username, password_hash, is_admin, all_libraries) "
                "VALUES(2, 'films-only', 'x', 0, 0)")
    con.execute("INSERT INTO users(id, username, password_hash, is_admin, all_libraries) "
                "VALUES(3, 'has-tv', 'x', 0, 0)")
    con.execute("INSERT INTO libraries(id, name, kind, paths) VALUES(1, 'Films', 'movies', '[]')")
    con.execute("INSERT INTO libraries(id, name, kind, paths) VALUES(2, 'Television', 'livetv', '[]')")
    con.execute("INSERT INTO user_libraries(user_id, library_id) VALUES(2, 1)")
    con.execute("INSERT INTO user_libraries(user_id, library_id) VALUES(3, 2)")


def as_user(uid):
    with db.db() as con:
        row = con.execute("SELECT id, username, is_admin, all_libraries FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row)


def refused(uid):
    """True when the live TV guard turns this user away. No guard at all counts as 'let in'."""
    guard = getattr(auth, "live_tv_viewer", None)
    if guard is None:
        return False
    try:
        guard(as_user(uid))
        return False
    except Exception:
        return True


check("a viewer with only a film library is refused", refused(2))
check("a viewer who has the television library is allowed", not refused(3))
check("the owner is allowed", not refused(1))

# ---------------------------------------------------------------------------------------------------
print("\n2. a live feed's id is not a permission")

check("the playlist and the segments require the token the viewer was given",
      "def _watching(" in livetv and livetv.count("_watching(fid, token)") >= 2)
check("and say so, rather than quietly serving somebody else's stream",
      "not yours to watch" in livetv)
check("livestream can be asked whether a token is one it handed out",
      "def known_viewer(" in src("server/lenta/livestream.py"))
check("the token is written into the playlist, because a player drops the query string",
      "def _with_token(" in livetv and "#EXT-X-MAP:" in livetv)
check("the list of running feeds is an administrator's, since it names the ids",
      re.search(r'@router\.get\("/live"\)\s*\ndef live_now\(user: dict = Depends\(auth\.admin_user\)', livetv)
      is not None)
check("so is FFmpeg's own log, which quotes the command line and the device path",
      re.search(r'@router\.get\("/live/\{fid\}/log"\)\s*\ndef live_log\(fid: str, user: dict = Depends\(auth\.admin_user\)',
                livetv) is not None)
check("and the failure list, which carries the same lines",
      re.search(r'@router\.get\("/live-failures"\)\s*\ndef live_failures\(user: dict = Depends\(auth\.admin_user\)',
                livetv) is not None)
check("a feed described to a viewer leaves out the id and the device path",
      "def info(self, *, admin: bool = True)" in src("server/lenta/livestream.py"))
check("and the answer to 'watch this channel' uses that",
      'feed.info(admin=bool(user["is_admin"]))' in livetv)
check("the administrator's own Live TV page was moved to the new addresses",
      "/api/admin/livetv/live'" in src("web/js/livetv.js")
      and "/api/admin/livetv/live-failures'" in src("web/js/livetv.js"))

# ---------------------------------------------------------------------------------------------------
print("\n3. a session token can be withdrawn")

authsrc = src("server/lenta/auth.py")
check("accounts carry a token epoch", "token_epoch" in src("server/lenta/db.py"))
check("which an upgrade adds without signing anybody out",
      'ALTER TABLE users ADD COLUMN token_epoch INTEGER NOT NULL DEFAULT 0' in src("server/lenta/db.py"))
check("a token records the epoch it was minted under", '"ep": _epoch(user_id)' in authsrc)
check("and is refused once the two disagree", 'int(payload.get("ep", 0)) != int(row["token_epoch"])' in authsrc)
check("there is one call that withdraws every token an account has", "def revoke_tokens(" in authsrc)

apiauth = src("server/lenta/api_auth.py")
check("signing out withdraws them, not just the cookie", "auth.revoke_tokens(user[\"id\"])" in apiauth)
check("changing the password withdraws them", apiauth.count("auth.revoke_tokens") >= 2)
check("and hands this browser a fresh one, so you are not signed out of the page you are on",
      re.search(r"auth\.revoke_tokens\(user\[\"id\"\]\)\s*\n\s*_set_cookie\(response, request", apiauth) is not None)
check("an administrator resetting someone's password withdraws theirs too",
      "auth.revoke_tokens(user_id)" in src("server/lenta/api_admin.py"))

# Run it: issue a token, use it, withdraw it, use it again.
class Bearer:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"}
        self.cookies = {}
        self.method = "GET"
        self.url = type("U", (), {"scheme": "http", "path": "/api/home"})()
        self.query_params = {}


tok = auth.issue_token(2)
check("a token works", auth.user_from_request(Bearer(tok)) is not None)
revoke = getattr(auth, "revoke_tokens", None)
if revoke:
    revoke(2)
check("and stops working the moment the account's tokens are withdrawn",
      bool(revoke) and auth.user_from_request(Bearer(tok)) is None)
check("a token issued afterwards works again, so signing back in is all it takes",
      bool(revoke) and auth.user_from_request(Bearer(auth.issue_token(2))) is not None)

# ---------------------------------------------------------------------------------------------------
print("\n4. a token in the query string reaches only the things that cannot carry a header")


class Req:
    def __init__(self, path, method="GET", token="t"):
        self.headers = {}
        self.cookies = {}
        self.method = method
        self.url = type("U", (), {"scheme": "http", "path": path})()
        self.query_params = {"token": token}


check("a stream URL may carry one — a television's decoder and VLC cannot send a header",
      auth._token_from_request(Req("/api/stream/file/3")) == "t")
check("so may an HLS segment", auth._token_from_request(Req("/api/hls/abc/seg00001.ts")) == "t")
check("so may a live segment", auth._token_from_request(Req("/api/livetv/live/abc/seg00001.ts")) == "t")
check("an administrator endpoint may NOT — this used to authenticate",
      auth._token_from_request(Req("/api/admin/users")) is None)
check("nor may the ordinary API", auth._token_from_request(Req("/api/home")) is None)
check("nor may anything that changes state, even on a permitted path",
      auth._token_from_request(Req("/api/stream/file/3", method="POST")) is None)
check("a header and a cookie still work everywhere", "authorization" in authsrc and "cookies.get" in authsrc)

# ---------------------------------------------------------------------------------------------------
print("\n5. signing in has a speed limit")

check("failures from one address are counted", "_wrong_guess" in apiauth and "_too_many" in apiauth)
check("a correct password forgets them, so the household never meets it", "_right_guess" in apiauth)
check("the refusal happens before the password is hashed, so guessing costs the server nothing",
      "wait = _too_many(request)" in apiauth
      and apiauth.index("wait = _too_many(request)") < apiauth.index("auth.verify_password(body.password"))
check("it answers 429 with Retry-After", '429' in apiauth and '"Retry-After"' in apiauth)
check("the table of addresses is pruned and capped, so it cannot be grown without bound",
      "LOGIN_TABLE_MAX" in apiauth and "del _tries[addr]" in apiauth)

import importlib                                                            # noqa: E402
ia = importlib.import_module("lenta.api_auth")


class Caller:
    def __init__(self, host):
        self.client = type("C", (), {"host": host})()


one, two = Caller("10.0.0.5"), Caller("10.0.0.6")
if hasattr(ia, "_too_many"):            # run the limiter, rather than only reading it
    check("an address is free to try at first", ia._too_many(one) == 0)
    for _ in range(ia.LOGIN_FAILURES):
        ia._wrong_guess(one)
    check(f"and is made to wait after {ia.LOGIN_FAILURES} wrong guesses", ia._too_many(one) > 0)
    check("while a different address is unaffected", ia._too_many(two) == 0)
    ia._right_guess(one)
    check("getting it right clears the count at once", ia._too_many(one) == 0)
else:
    for name in ("an address is free to try at first", "and is made to wait after wrong guesses",
                 "while a different address is unaffected", "getting it right clears the count at once"):
        check(name, False)

# ---------------------------------------------------------------------------------------------------
print("\n6. every query and every write is scoped to the libraries the caller may open")

check("the rest of a film's collection is filtered (it was the one query that wasn't)",
      "auth.library_filter(user)" in src("server/lenta/about.py"))
check("so is the actor headshot lookup in /api/people",
      src("server/lenta/api_library.py").count("WHERE {where} AND json_extract(c.value, '$.name')") >= 1)
check("reporting an unplayable trailer rewrites only titles the reporter can see",
      "auth.library_filter(user)" in src("server/lenta/api_extras.py"))

playback = src("server/lenta/api_playback.py")
check("replacing a transcode session checks whose it is",
      'victim.user_id == user["id"] or user["is_admin"]' in playback)
check("a now-playing entry is namespaced to its owner, so one viewer cannot delete another's",
      "f\"{user['id']}:{body.play_id}\"" in playback)

# ---------------------------------------------------------------------------------------------------
print("\n7. the server will not fetch an address aimed back at itself")

def guarded(url, **kw):
    """True when LENTA refuses this address."""
    if safefetch is None:
        return False                    # there is no guard at all
    try:
        safefetch.check(url, **kw)
        return False
    except safefetch.Blocked:
        return True


for url, why in [("file:///etc/passwd", "a local file — urllib's default opener really does fetch these"),
                 ("ftp://host/x", "another scheme nobody asked for"),
                 ("http://127.0.0.1:8484/api/admin/users", "the server itself"),
                 ("http://169.254.169.254/latest/meta-data/", "a cloud instance's credentials"),
                 ("http://[::1]/x", "the server itself, written the other way"),
                 ("http://192.168.1.50/x", "a machine on the network, named by a stranger")]:
    check(f"refused: {why}", guarded(url))

check("but an address HE typed may be on his own network — an IPTV box is a real setup",
      safefetch is not None and not guarded("http://192.168.1.50/playlist.m3u", allow_private=True))
for url in ("http://127.0.0.1/x", "http://169.254.169.254/x", "file:///etc/passwd"):
    check(f"and even then not {url}", guarded(url, allow_private=True))

sf = src("server/lenta/safefetch.py") if (ROOT / "server/lenta/safefetch.py").exists() else ""
check("no handler for file: or ftp: is installed at all, guard or no guard",
      "build_opener(urllib.request.HTTPHandler, urllib.request.HTTPSHandler)" in sf)
check("every redirect hop is checked, which is the usual way round a guard like this",
      "MAX_REDIRECTS" in sf and sf.count("check(seen") >= 1)
check("and every body has a limit, because three of them had none",
      "limit: int = MAX_BYTES" in sf and "r.read(limit)" in sf)

check("the channel-logo fetcher goes through it", "safefetch.get(" in src("server/lenta/logos.py"))
check("so does the metadata image fetcher", "safefetch.get(" in src("server/lenta/images.py"))
check("so does the playlist reader, which had no size limit at all",
      "safefetch.get(" in src("server/lenta/livetv.py") and "PLAYLIST_MAX" in src("server/lenta/livetv.py"))
check("so do both pasted-artwork endpoints",
      "safefetch.get(" in src("server/lenta/api_editor.py") and "safefetch.get(" in src("server/lenta/api_streaming.py"))
check("a channel logo is no longer a redirect to wherever the playlist said",
      "safefetch.check(logo)" in livetv)
check("a subtitle id is digits, so it cannot become a path",
      r're.fullmatch(r"[0-9]{1,20}"' in src("server/lenta/api_subtitles.py"))
check("an image name with a NUL in it is a 404, not a traceback",
      r'"\x00" in name' in src("server/lenta/images.py"))

# ---------------------------------------------------------------------------------------------------
print("\n8. the browser is told what to enforce, and what it is told is honest")

main = src("server/lenta/main.py")
for header in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy"):
    check(f"{header} is sent", f'"{header}"' in main)
check("nothing may frame LENTA, which is what closes clickjacking",
      "frame-ancestors 'none'" in main and '"X-Frame-Options": "DENY"' in main)
check("an injected script cannot be loaded from another host", "script-src 'self' 'unsafe-inline'" in main)
check("and 'unsafe-eval' is present on purpose — the three-client switch probes syntax with new Function",
      "'unsafe-eval'" in main and "new Function" in main)
check("and cannot post what it reads to one", "connect-src 'self'" in main)
check("YouTube is allowed to be framed, because trailers are",
      "frame-src 'self' https://www.youtube-nocookie.com" in main)
# The policy has to allow exactly what the server is willing to hand over, and no more. Leaving Apple
# out of media-src is what silently killed theme music in 1.19.0: the audio element was refused and the
# only symptom was nothing playing. These two lists are derived from `trailer_sources._APPLE_HOST`, the
# same test the server applies before giving the client a media address.
import importlib                                                            # noqa: E402
ts = importlib.import_module("lenta.trailer_sources")
check("Apple's media hosts are allowed, because that is where theme music and Apple trailers come from",
      "https://*.apple.com" in main and "https://*.mzstatic.com" in main)
check("and they are the same hosts the server itself accepts, so the two cannot drift apart",
      all(ts._APPLE_HOST.match(f"https://x.{h}/y") for h in ("apple.com", "mzstatic.com")))
check("a theme preview from anywhere else is refused by the server before the browser sees it",
      "_APPLE_HOST.match" in src("server/lenta/api_extras.py"))
check("the YouTube thumbnail strip in the trailer picker can load its images",
      "https://i.ytimg.com" in main and "i.ytimg.com" in src("web/js/editor.js"))
check("no address LENTA serves travels to another site in a Referer",
      '"Referrer-Policy": "no-referrer"' in main)
check("the schema and the documentation page are off unless asked for",
      'os.environ.get("LENTA_API_DOCS") == "1"' in main)
check("forwarded headers are believed only from a proxy on this machine",
      'LENTA_TRUSTED_PROXIES' in src("server/lenta/listeners.py"))
check("the wildcard CORS answer no longer carries the household's account names",
      "cross_site" in apiauth)

# ---------------------------------------------------------------------------------------------------
print("\n9. and the client does not turn somebody else's text into somebody else's script")

ui = src("web/js/ui.js")
check("there is one place that decides whether a link may be used", "export function safeUrl" in ui)
check("it allows http and https and nothing else", r"/^https?:\/\//i.test(s)" in ui)
views, extras = src("web/js/views.js"), src("web/js/extras.js")
check("TMDB's homepage link goes through it", "links.filter(([, u]) => safeUrl(u))" in views)
check("so does Wikipedia's", "safeUrl(a.wiki.url)" in views)
check("so does a streaming provider's watch link", ui.count("safeUrl(item.link)") >= 2)
check("so does Apple Music's preview link", "safeUrl(this.t.link)" in extras)
check("no untrusted href is left unchecked",
      not re.search(r'href="\$\{(?!safeUrl)(item\.link|u|a\.wiki\.url|this\.t\.link)\}', ui + views + extras))

tv = src("web/js/tv.js")
check("the forwarded-key listener only exists on a television",
      "listenForForwardedKeys()" in tv and tv.index("function listenForForwardedKeys") < tv.index("export function initTv"))
check("and only believes the window hosting this page",
      "e.source !== window.parent" in tv and tv.count("e.source !== window.parent") >= 2)
check("the dormant AVPlay bridge checks the sender too, before it is ever switched on",
      "e.source !== window.parent" in src("web/js/avplay.js"))
check("the two attributes built by hand are escaped like every other",
      'data-tmdb="${esc(r.tmdb_id)}"' in src("web/js/admin.js")
      and 'width="${esc(p.width)}"' in views)

# ---------------------------------------------------------------------------------------------------
print("\n10. the phone and the machine it runs on")

app = src("android/app_code.py")
# Precisely: the comparison applied to the issuer's common name. The other `startsWith` calls in this
# file are prefix tests on a URL scheme, which is what startsWith is for.
# The first mention is in the module docstring; the one that matters is the Dalvik constant.
cn_test = app[app.index('("const-string", "v2", "LENTA local CA")'):][:900]
cn_code = "\n".join(ln for ln in cn_test.splitlines() if not ln.lstrip().startswith("#"))
check("the certificate's issuer must match exactly, not merely start with the right words",
      "->equals(" in cn_code and "startsWith" not in cn_code.split("->equals(")[0])
check("and the comment says plainly that this is hardening, not the fix",
      "Nothing\n            # here checks the key" in app or "checks the key" in app)
for setting in ("setAllowFileAccess", "setAllowFileAccessFromFileURLs", "setAllowUniversalAccessFromFileURLs",
                "setJavaScriptCanOpenWindowsAutomatically", "setMixedContentMode"):
    check(f"WebSettings.{setting} is set out loud, not left to the framework's default", setting in app)

unit = src("deploy/lenta.service")
for directive in ("CapabilityBoundingSet=", "RestrictAddressFamilies=", "RestrictNamespaces=true",
                  "LockPersonality=true", "ProtectKernelTunables=true", "ProtectProc=invisible",
                  "SystemCallFilter=@system-service"):
    check(f"the unit sets {directive.rstrip('=')}", directive in unit)
check("and says why PrivateDevices and MemoryDenyWriteExecute are deliberately absent",
      "PrivateDevices" in unit and "MemoryDenyWriteExecute" in unit)
# This asserted the opposite until 1.19.2, and the assertion was wrong: narrowing `packages:` with an
# expression makes the workflow file invalid, because `permissions:` is read before expressions are
# evaluated. Every container build failed for two releases. Publishing is gated at the steps that
# publish, which is where a gate belongs. test_docker.py section 9 now guards the shape of the line.
check("the registry token is a literal permission, not an expression that invalidates the workflow",
      "packages: write" in src(".github/workflows/docker.yml")
      and "${{" not in src(".github/workflows/docker.yml").split("permissions:")[1].split("steps:")[0])

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
