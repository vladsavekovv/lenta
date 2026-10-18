"""Whether the documentation still describes the program that exists.

Documentation does not break loudly. A version badge goes on saying 1.4.7 for four months, a contents
entry points at a heading that was renamed, the same count of themes is given as five in one place, six
in another and seven in the code — and nothing anywhere complains, because none of it is executed. The
only person who finds out is a stranger reading the front page, and they do not report it; they just
leave with the wrong idea of what this is.

So the things that can be checked mechanically are checked here: that the version is one version, that
every link within these files lands somewhere real, that every picture referenced exists and every
picture present is used. Not whether the prose is any good — only whether it still refers to something.
"""
import os, re, sys, tempfile
from pathlib import Path

os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, "/home/claude/lenta/server")
from lenta.config import VERSION                                           # noqa: E402

ROOT = Path("/home/claude/lenta")
DOCS = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "CONTRIBUTING.md"] + sorted((ROOT / "docs").glob("*.md"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def anchor(heading):
    """GitHub's rule for turning a heading into a link target."""
    a = heading.strip().lower()
    a = re.sub(r"[^\w\s-]", "", a, flags=re.U)        # punctuation and emoji go
    # The hyphen an emoji leaves behind is kept: "## 📜 License" really is "#-license" on GitHub, which
    # is why the links in these files are written that way. Trimming it here would fail a correct link.
    return re.sub(r"\s+", "-", a)


print("1. the version is one version, in every place that states it")
readme = (ROOT / "README.md").read_text()
badge = re.search(r"badge/version-([0-9.]+)-", readme)
check(f"the README badge says {badge and badge.group(1)}, the server says {VERSION}",
      bool(badge) and badge.group(1) == VERSION)
top = re.search(r"^## ([0-9.]+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
check(f"the changelog's newest entry is {top and top.group(1)}",
      bool(top) and top.group(1) == VERSION)

print("\n2. every link inside these files lands on a heading that exists")
headings = {}
for p in DOCS:
    headings[p.name] = {anchor(h) for h in re.findall(r"^#{1,6}\s+(.+)$", p.read_text(), re.M)}
def links(text):
    """Every link in the file, markdown and HTML alike, as (file, fragment) pairs.

    The README is half HTML — the screenshot tables, the centred header — so looking only for the
    markdown form checks a part of the file and reports on all of it."""
    out = re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", text)
    out += [(m.group(1), m.group(2)) for m in
            re.finditer(r'href="([^"#\s]*)#([^"\s]+)"', text)]
    return out


bad = []
for p in DOCS:
    for target, frag in links(p.read_text()):
        where = Path(target).name if target else p.name
        if where not in headings:                     # a link out to a file with no headings read here
            continue
        if frag.lower() not in headings[where]:
            bad.append(f"{p.name} → {where}#{frag}")
check(f"{sum(len(h) for h in headings.values())} headings, every link resolves"
      f"{'' if not bad else ' — BROKEN: ' + '; '.join(bad[:6])}", not bad)

print("\n3. every file a link points at is really there")
missing = []
for p in DOCS:
    text = p.read_text()
    paths = re.findall(r"\]\(((?!https?:|#|mailto:)[^)#\s]+)", text)
    paths += re.findall(r'(?:href|src)="((?!https?:|#|mailto:|data:)[^"#\s]+)"', text)
    for target in paths:
        if not (p.parent / target).resolve().exists() and not (ROOT / target).exists():
            missing.append(f"{p.name} → {target}")
check(f"no link points at a file that does not exist{'' if not missing else ': ' + '; '.join(missing[:6])}",
      not missing)

print("\n4. pictures: all used, none missing")
text = "\n".join(p.read_text() for p in DOCS)
refs = {r.split("/")[-1] for r in re.findall(r"(?:docs/)?images/[A-Za-z0-9._-]+", text)}
have = {p.name for p in (ROOT / "docs" / "images").glob("*.jpg")}
check(f"{len(have)} pictures, none referenced but absent{'' if not refs - have else ': ' + ', '.join(sorted(refs - have))}",
      not refs - have)
check(f"none present but unused{'' if not have - refs else ': ' + ', '.join(sorted(have - refs))}",
      not have - refs)

print("\n5. the counts that are easy to get wrong")
# Seven themes exist. Writing the number in prose means it can disagree with the program, and it has.
from lenta.api_prefs import THEMES                                         # noqa: E402
words = {5: "five", 6: "six", 7: "seven", 8: "eight"}
said = re.search(r"Looks: (\w+) dark themes", text)
check(f"the user guide says {said and said.group(1)} themes, there are {len(THEMES)}",
      bool(said) and said.group(1) == words.get(len(THEMES)))
rm = re.search(r"(\w+) dark themes", readme)
check(f"the README says {rm and rm.group(1)}", bool(rm) and rm.group(1).lower() == words.get(len(THEMES)))

print("\n6. the handoff stays at home")
# CLAUDE.md is written about Vlad and his network — employer, vault path, the VM and addresses LENTA
# runs on. It went to GitHub once, in 1.12.0, and should not go again. Two gates, because one of them
# will eventually be edited by somebody who does not know why it is there.
gitignore = (ROOT / ".gitignore").read_text()
check("CLAUDE.md is in .gitignore",
      any(ln.strip() == "CLAUDE.md" for ln in gitignore.splitlines()))
handoff = (ROOT / "CLAUDE.md")
if handoff.exists():
    rules = handoff.read_text()
    check("and the packaging command in it excludes the file itself",
          "--exclude='CLAUDE.md'" in rules)
    check("and says plainly that it is never published",
          "never packaged and never published" in rules)
else:
    print("  ok    CLAUDE.md is not in this copy of the tree (a package, or a clone) — nothing to check")

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("the documentation ok")
