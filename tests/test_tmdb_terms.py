"""The conditions TMDB attach to their API, checked rather than remembered.

LENTA's artwork, descriptions, cast and episode titles come from The Movie Database under a licence
with conditions in it. Three of them are easy to break by accident months later, and none of them
announces itself when broken — nothing fails, no test goes red, the data simply keeps arriving until
somebody notices. So they are written down here as assertions.

  * **The notice is a fixed sentence.** Paragraph 3 of the API Terms of Use gives the exact wording,
    with a bracketed word to choose. Paraphrasing it is the single most common way projects fall out
    of compliance, because the short version reads perfectly well and is wrong.
  * **Six months is the limit on keeping their content.** Paragraph 1.C: nothing obtained from the
    API may be cached for longer than that — the metadata in the database and the posters in the
    cache folder alike. LENTA refreshes both at five months.
  * **The key may not be passed on.** The licence is "non-transferable, non-sublicensable" (1.A) and
    sublicensing access to the APIs is forbidden "whether for direct commercial or monetary gain or
    otherwise" (1.C). Other media servers do ship a key. LENTA must not, and the easiest way for a
    future session to be helpful and wrong is to add one — so this looks for it.
"""
import os, re, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
ROOT = Path("/home/claude/lenta")

# Paragraph 3, with "product" chosen from the bracketed list.
NOTICE = ("This product uses TMDB and the TMDB APIs but is not endorsed, certified, "
          "or otherwise approved by TMDB.")
SIX_MONTHS = 180 * 86400

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def text(rel):
    try:
        return (ROOT / rel).read_text()
    except OSError:
        check(f"{rel} exists", False)
        return ""


print("1. the notice says what TMDB say it must say")
# Everywhere the attribution appears, it must be their sentence and not a shortening of it.
for f in ("web/js/sysinfo.js", "README.md", "THIRD-PARTY-NOTICES.md"):
    body = text(f)
    check(f"{f} carries the notice in full", NOTICE in body)
    # The old short form, and any other paraphrase, is a failure however reasonable it reads.
    stale = re.findall(r"uses the TMDB API but is not endorsed[^.]*\.", body)
    check(f"{f} has no shortened version left behind{'' if not stale else ': ' + stale[0]}", not stale)

about_page = text("web/js/sysinfo.js")
check("the About page is where the notice sits (TMDB ask for About or Credits)",
      "about-lenta" in about_page and NOTICE in about_page)
check("their logo is shown beside it", 'class="about-tmdb"' in about_page)
check("and it takes itself off the page rather than showing a broken image",
      "onerror" in about_page and "about-tmdb" in about_page)
css = text("web/css/app.css")
height = re.search(r"\.about-tmdb\s*\{[^}]*height:\s*(\d+)px", css)
logo_h = int(height.group(1)) if height else 999
own = re.search(r"\.about-logo\s*\{[^}]*width:\s*clamp\((\d+)px", css)
own_w = int(own.group(1)) if own else 0
check(f"their logo is less prominent than LENTA's own ({logo_h}px against {own_w}px)",
      0 < logo_h < own_w)
# The file itself is TMDB's trademark, downloaded from themoviedb.org/about/logos-attribution and not
# ours to draw. It lives in the source tree rather than on a server, because the installer and the
# publish script both rsync --delete: a file put anywhere else is removed by the next install.
logo = ROOT / "web" / "img" / "tmdb.svg"
check("the official logo file is in the source tree", logo.exists())
if logo.exists():
    body = logo.read_text(errors="ignore")
    check("and it is a real SVG rather than a placeholder", body.lstrip().startswith("<svg") and "viewBox" in body)
    check("that fetches nothing from anywhere when it is drawn", "<image" not in body and "xlink:href=\"http" not in body)

print("\n2. nothing of theirs is kept longer than six months")
images = text("server/lenta/images.py")
about = text("server/lenta/about.py")
img_max = re.search(r"TMDB_CACHE_MAX\s*=\s*(\d+)\s*\*\s*86400", images)
det_max = re.search(r"TMDB_REFRESH\s*=\s*(\d+)\s*\*\s*86400", about)
check("cached pictures have a lifetime", img_max is not None)
check("so does cached metadata", det_max is not None)
if img_max:
    check(f"pictures are re-fetched inside the limit ({img_max.group(1)} days)",
          int(img_max.group(1)) * 86400 < SIX_MONTHS)
if det_max:
    check(f"metadata is re-fetched inside the limit ({det_max.group(1)} days)",
          int(det_max.group(1)) * 86400 < SIX_MONTHS)
check("a cached picture's age is actually looked at, not just declared",
      "st_mtime" in images and "TMDB_CACHE_MAX" in images)
check("and the title sweep picks up anything whose details have gone stale",
      "details_at" in about and "TMDB_REFRESH" in about)
check("details are stamped with the time they were fetched",
      '"details_at": time.time()' in text("server/lenta/metadata.py"))

print("\n3. no key of TMDB's is shipped with LENTA")
# A v3 key is 32 hex characters; a v4 read token is a long JWT. Either sitting in the tree would be
# a key being passed on to everyone who downloads LENTA, which the licence does not allow.
suspects = []
for p in list(ROOT.glob("server/lenta/*.py")) + list(ROOT.glob("web/js/*.js")) + \
         [ROOT / "compose.yaml", ROOT / "Dockerfile", ROOT / "docker/entrypoint.sh"]:
    try:
        body = p.read_text()
    except OSError:
        continue
    for m in re.findall(r"[\"'][0-9a-f]{32}[\"']|eyJ[A-Za-z0-9_-]{20,}", body):
        suspects.append(f"{p.relative_to(ROOT)}: {m[:12]}…")
check(f"no API key is embedded anywhere{'' if not suspects else ': ' + '; '.join(suspects[:3])}",
      not suspects)
config = text("server/lenta/config.py")
check("the key comes from the environment or the settings page, never from us",
      'os.environ.get("LENTA_TMDB_KEY"' in config)
check("and the environment only ever fills an empty setting",
      re.search(r"if TMDB_KEY_ENV and not get_setting\(\"tmdb_api_key\"\)", text("server/lenta/main.py")) is not None)
# The comment wraps, so match on a phrase that cannot straddle the line break.
check("why LENTA ships no key is written down where someone would add one",
      "LENTA ships no key of its own" in config and "API Terms of Use" in config)

print("\n4. the person who needs a key is told how to get one")
admin = text("web/js/admin.js")
check("the settings field links straight to the page that makes one",
      "themoviedb.org/settings/api" in admin)
check("so does the banner shown when metadata is off",
      admin.count("themoviedb.org/settings/api") >= 2)
check("and the container route is offered to people editing a compose file",
      "LENTA_TMDB_KEY" in text("compose.yaml") and "LENTA_TMDB_KEY" in text("docs/DOCKER.md"))
check("without a key the rest of LENTA still works, and says so",
      "works without it" in admin or "Everything else works without it" in admin)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("the TMDB conditions are met")
