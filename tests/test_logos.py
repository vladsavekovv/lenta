"""Channel artwork: name matching, and the monogram every channel falls back to."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import urllib.request

from lenta import logos, safefetch          # noqa: E402

fails = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{'  — ' + detail if detail and not ok else ''}")
    if not ok:
        fails.append(label)


real_get = logos._get          # before any stub takes its place

print("1. the same channel written two ways shares a key")
for a, b in [("Star Channel", "Star Channel HD"), ("Eurosport 2 SD", "Eurosport 2 HD"),
             ("24 Kitchen", "24Kitchen HD"), ("BNT 1", "BNT 1 HD"), ("Nova TV", "Nova HD"),
             ("AgroTV HD", "Agro TV"), ("СКАТ", "SKAT HD"), ("FEN TV HD", "Fen TV")]:
    check(f"{a!r} / {b!r}", bool(set(logos.keys(a)) & set(logos.keys(b))))

print("\n2. different channels do not collide")
for a, b in [("bTV", "bTV Action"), ("Nova TV", "Nova Sport"), ("Travel TV", "Travel Channel"),
             ("BNT 1", "BNT 2"), ("Diema", "Diema Family"), ("Star Life", "Star Crime")]:
    check(f"{a!r} vs {b!r}", logos.keys(a)[0] != logos.keys(b)[0])

print("\n3. Cyrillic names reduce to what a Latin-named file would be called")
for name, want in [("СКАТ", "skat"), ("Наше Кино", "nashe kino"), ("Детский мир", "detskiy mir"),
                   ("Дом Кино Премиум HD", "dom kino premium")]:
    check(f"{name!r} -> {want!r}", logos.key(name) == want, logos.key(name))

print("\n4. a real directory listing is read, images only, country suffixes allowed for")
listing = json.dumps([
    {"name": "btv-bg.png", "type": "file", "download_url": "https://e/btv-bg.png"},
    {"name": "bnt-1-bg.png", "type": "file", "download_url": "https://e/bnt1.png"},
    {"name": "nova-tv-bg.png", "type": "file", "download_url": "https://e/nova.png"},
    {"name": "diema-sport-2-bg.png", "type": "file", "download_url": "https://e/ds2.png"},
    {"name": "readme.md", "type": "file", "download_url": "https://e/readme.md"},
    {"name": "subdir", "type": "dir"},
])
logos._get = lambda url, binary=False, mine=False: listing
index = logos.fetch_index("https://e/listing")
for name, want in [("bTV", "btv-bg.png"), ("BNT 1 HD", "bnt1.png"), ("Nova TV", "nova.png"),
                   ("Diema Sport 2", "ds2.png"), ("Star Life HD", None)]:
    hit = logos.match(name, index)
    check(f"{name!r}", hit.endswith(want) if want else not hit, hit or "(nothing)")
check("non-images are not offered", not any("readme" in k for k in index))

print("\n5. a GitHub tree listing resolves to real file addresses, the chosen country first")
tree = json.dumps({"tree": [
    {"path": "countries/bulgaria/btv-bg.png", "type": "blob"},
    {"path": "countries/bulgaria/bloomberg-tv-bulgaria-bg.png", "type": "blob"},
    {"path": "countries/turkey/bloomberg-ht-tr.png", "type": "blob"},
    {"path": "countries/italy/btv-it.png", "type": "blob"},
    {"path": "README.md", "type": "blob"},
]})
logos._get = lambda url, binary=False, mine=False: tree
idx = logos.fetch_index(logos.DEFAULT_INDEX, prefer="bulgaria")
check("the raw address is derived from the listing's",
      logos.raw_base(logos.DEFAULT_INDEX) == "https://raw.githubusercontent.com/tv-logo/tv-logos/main/")
check("a plain match takes the chosen country",
      logos.match("bTV", idx).endswith("countries/bulgaria/btv-bg.png"), logos.match("bTV", idx))
check("so does a name matched by its beginning",
      "bulgaria" in logos.match("Bloomberg TV HD", idx), logos.match("Bloomberg TV HD", idx))
check("the README is not offered as a logo", not any("readme" in k for k in idx))

print("\n6. a source that cannot be read leaves everything to monograms")
logos._get = lambda url, binary=False, mine=False: None
check("unreachable source", logos.fetch_index("https://nowhere/x") == {})
logos._get = lambda url, binary=False, mine=False: "<html>not json</html>"
check("a page that is not a listing", logos.fetch_index("https://e/x") == {})

print("\n7. a source that will not answer says why, not just that it did not")
import io, urllib.error                                                              # noqa: E402
logos._get = real_get
logos.last_error = ""


def refuse(code, body):
    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(body.encode()))
    return opener


def stub(opener):
    """Stand in for safefetch's own opener — the fetch stopped going through urlopen in 1.19.0.

    The address guard is stubbed out with it, because it resolves the host before opening anything and
    `https://e/x` does not resolve. What is being checked here is whether logos.py reports a server's
    complaint in words a person can read; the guard's own refusals are checked in test_security.py.
    """
    safefetch._opener = type("O", (), {"open": staticmethod(opener)})()
    safefetch.check = lambda url, **kw: None


stub(refuse(403, '{"message":"API rate limit exceeded for 1.2.3.4."}'))
check("a refusal is not mistaken for an empty collection", logos.fetch_index("https://e/x") == {})
check(f"and the reason is kept: {logos.last_error!r}",
      "403" in logos.last_error and "rate limit exceeded" in logos.last_error)
check("the JSON wrapping is left out of it", "{" not in logos.last_error)

stub(refuse(404, "<html><body>Not Found</body></html>"))
logos.fetch_index("https://e/x")
check(f"a body that is not JSON still reads: {logos.last_error!r}", "404" in logos.last_error)


def unreachable(req, timeout=0):
    raise urllib.error.URLError("Name or service not known")


stub(unreachable)
logos.fetch_index("https://e/x")
check(f"so does a name that does not resolve: {logos.last_error!r}",
      "not known" in logos.last_error)
check("a very long complaint is cut to a sentence", len(logos.last_error) <= 240)

print("\n8. every channel gets a monogram, whatever it is called")
for name in ["BNT 1", "bTV", "СКАТ", "24Kitchen HD", "Мир 24 HD", "", "?", "...", "4"]:
    svg = logos.monogram(name)
    check(f"{name!r} -> {logos.initials(name)!r}", svg.startswith("<svg") and "</svg>" in svg)
check("the same channel always gets the same colours",
      logos.monogram("bTV") == logos.monogram("bTV"))
check("different channels usually do not",
      logos.monogram("bTV") != logos.monogram("Nova TV"))
check("a name with markup in it cannot break the drawing",
      "<script>" not in logos.monogram('<script>alert(1)</script>'))

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("channel artwork ok")
