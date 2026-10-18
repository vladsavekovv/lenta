"""Settings › About: the page that answers "what is this, exactly?".

Every server has one, and the reason is not vanity. When something is wrong on a television the
first question is always which version is on it and which of the two clients the set was given,
and the person standing in front of it has a remote control and a phone camera. So the page has to
say the version, the engine and the build in one photograph, and it has to draw on an engine from
2017 as well as it draws on a laptop.

The other half is what the page must not do. It is read-only — no draft, no Save button to go grey
— and it must not tell an account about libraries it is not allowed to open: the counts are the
reader's own. Two attributions are not decoration either: TMDB asks for one sentence and JustWatch
withdraws the data without one, so both are checked for here rather than trusted to survive the
next edit of the page.
"""
import json, os, re, subprocess, tempfile, urllib.error, urllib.request
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


print("1. the page is wired into Settings, and it is not a settings page")
shell = (ROOT / "web" / "js" / "settings-shell.js").read_text()      # the one file that must exist
check("About is an item in the Settings menu", "id: 'about'" in shell and "label: 'About'" in shell)
check("it is drawn by the About page, not by renderSettings",
      re.search(r"page === 'about'.*renderAbout", shell, re.S) is not None)
# renderSettings owns the draft and the Save / Discard bar. A read-only page that went through it
# would inherit both, which is how a Save button ends up on a page with nothing to save.
check("and it is not listed among the pages that have a draft", "'account', 'about'" not in shell)

def text(path):
    """Missing file: report it as a failure rather than exploding on the first check."""
    try:
        return path.read_text()
    except OSError:
        check(f"{path.name} exists", False)
        return ""


src = text(ROOT / "web" / "js" / "sysinfo.js")
check("the page reads the server's own account of itself", "'/api/system'" in src)
# The sentence itself is TMDB's and is checked word for word in tests/test_tmdb_terms.py, which owns
# it. Here we only care that the About page is where it ended up, because that is where TMDB ask for it.
check("TMDB's attribution sentence is on the page", "or otherwise approved by TMDB" in src)
check("JustWatch is credited for streaming availability", "provided by JustWatch" in src)
check("the licence is stated as public domain", "public domain" in src and "Unlicense" in src)
check("no draft, no save: the page never writes anything",
      not re.search(r"api\.(put|post|patch|del)\(", src))

print("\n2. the server's account of itself")
server = text(ROOT / "server" / "lenta" / "api_system.py")
check("the counts are restricted to the reader's libraries", "auth.library_filter(user" in server)
check("paths, free space and the service account are administrators only",
      re.search(r'if user\["is_admin"\]:\s*\n\s*info\["admin"\]', server) is not None)
# The one thing that must never leak out of a diagnostics endpoint.
for secret in ("tmdb_key", "opensubtitles", "kinocheck_key", "password", "token", "secret"):
    check(f"it does not read the {secret} setting", f'"{secret}"' not in server.lower())


def call(path, jar=None, body=None):
    req = urllib.request.Request(f"{BASE}{path}", method="POST" if body else "GET")
    if body:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    if jar:
        req.add_header("Cookie", jar)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r, json.loads(r.read() or b"null")


print("\n3. the live endpoint")
try:
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=180)
    live = True
except (subprocess.SubprocessError, OSError, FileNotFoundError) as ex:
    print(f"  skip  no test server here ({ex})")
    live = False

info = {}
if live:
    r, _ = call("/api/auth/login", body={"username": "admin", "password": "secret123"})
    jar = "; ".join(c.split(";")[0] for c in r.headers.get_all("Set-Cookie") or [])
    _, info = call("/api/system", jar)
    for key in ("app", "version", "licence", "project", "uptime", "python", "sqlite", "os", "arch",
                "ffmpeg", "hardware", "counts", "libraries", "media_size"):
        check(f"it reports {key}", key in info and info[key] not in (None, ""))
    check("the version is the one in config.py",
          info.get("version") == re.search(r'VERSION = "([^"]+)"',
                                           (ROOT / "server" / "lenta" / "config.py").read_text()).group(1))
    check("an administrator is told where the data lives", bool(info.get("admin", {}).get("data_dir")))
    flat = json.dumps(info).lower()
    check("and nothing in the answer looks like a key or a password",
          not re.search(r"(api[_-]?key|password|token|secret)", flat))

    # A restricted account: one library, and it should not learn the size of the rest.
    try:
        libs = call("/api/admin/libraries", jar)[1]
        one = [lb["id"] for lb in libs][:1]
        try:
            call("/api/admin/users", jar, {"username": "guest", "password": "secret123",
                                           "is_admin": False, "all_libraries": False, "library_ids": one})
        except urllib.error.HTTPError as ex:
            if ex.code != 409:                       # already created by an earlier run
                raise
        r2, _ = call("/api/auth/login", body={"username": "guest", "password": "secret123"})
        jar2 = "; ".join(c.split(";")[0] for c in r2.headers.get_all("Set-Cookie") or [])
        _, guest = call("/api/system", jar2)
        check("a restricted account sees only its own libraries",
              guest["libraries"] == 1 and guest["libraries"] < info["libraries"])
        check("its counts are smaller than the whole server's",
              sum(guest["counts"].values()) < sum(info["counts"].values()))
        check("and it is told nothing about the installation",
              "admin" not in guest)
    except urllib.error.HTTPError as ex:
        check(f"restricted account could be created ({ex.code})", False)

print("\n4. the page draws — on this engine and on a 2017 one")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    sync_playwright = None

if sync_playwright and live:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for label, url in (("standard build", f"{BASE}/#/settings/about"),
                           ("2017 build", f"{BASE}/?legacy=1#/settings/about")):
            page = b.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded")
            page.click('[data-user="admin"]')
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
            page.wait_for_selector(".about-lenta", timeout=30000)
            page.wait_for_timeout(400)
            got = page.evaluate("""() => {
              const t = document.querySelector('.about-lenta')?.innerText || '';
              return { text: t,
                       panels: document.querySelectorAll('.about-lenta .panel').length,
                       rows: document.querySelectorAll('.about-lenta .facts dt').length,
                       save: !!document.querySelector('.save-bar'),
                       title: document.querySelector('.sset-main .page-title')?.textContent || '' };
            }""")
            ver = info.get("version", "")
            check(f"{label}: the page is there, in blocks ({got['panels']} of them, {got['rows']} facts)",
                  got["panels"] >= 4 and got["rows"] >= 10)
            check(f"{label}: Settings calls the page About", got["title"].strip() == "About")
            check(f"{label}: the version is on it", ver and ver in got["text"])
            check(f"{label}: so is the licence", "public domain" in got["text"].lower())
            check(f"{label}: and both attributions", "TMDB" in got["text"] and "JustWatch" in got["text"])
            # The point of the page on a television: which of the two clients this set was given.
            want = "Compatibility build" if "legacy=1" in url else "Standard build"
            check(f"{label}: it says which client this device is running ({want})", want in got["text"])
            check(f"{label}: nothing to save, so no Save bar", not got["save"])
            page.close()
        b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
