"""Channel artwork: a logo for every channel, one way or another.

A broadcast carries no logo. The name is all there is, so a logo has to be found by matching that name
against a collection of them — and names on air are not tidy: "bTV Comedy HD", "БНТ 1", "Nova Sport",
"24Kitchen HD", "Diema Sport 3 4K". They differ in case, in quality suffixes, in punctuation, and some
are in Cyrillic while the files are named in Latin.

So there are three ways a channel gets its artwork, in this order:

    1. one the person set by hand, or one a playlist supplied — never overridden
    2. one matched from a logo collection and cached here
    3. a monogram drawn from the channel's own name, which always works and needs nothing

The third matters most. A collection will never have every regional channel, and a wall of blank tiles
looks broken; a wall of clean monograms looks deliberate.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request

from . import logs
from .config import IMAGES_DIR

log = logs.get("logos")

# Why the last fetch failed, in words a person can act on. Only ever a hint: by the time it is read
# another fetch may have overwritten it, which is why nothing depends on it being right.
last_error = ""

# A collection that covers most of Europe, asked for its own file list so nothing is guessed at. The
# address is only a default: Settings lets it be pointed anywhere that answers with a list of names.
DEFAULT_INDEX = "https://api.github.com/repos/tv-logo/tv-logos/git/trees/main?recursive=1"
GH_TREE = re.compile(r"https://api\.github\.com/repos/([^/]+)/([^/]+)/git/trees/([^/?]+)")

LOGO_DIR = IMAGES_DIR / "channels"
INDEX_NAME = "logo-index.json"
FETCH_TIMEOUT = 20

# Bulgarian and Russian Cyrillic, as a logo collection would spell it in Latin letters.
CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht",
    "ъ": "a", "ь": "", "ю": "yu", "я": "ya", "ы": "y", "э": "e", "ё": "e", "і": "i", "ї": "yi",
    "є": "e", "ґ": "g",
}

# Said after a channel's name rather than part of it: the same channel in another form.
QUALITY = re.compile(r"\b(?:u?hd|sd|fhd|4k|8k|hevc|h265|h\.265|plus|\+|raw|backup|tv)\b", re.I)


def transliterate(name: str) -> str:
    out = []
    for ch in name:
        low = ch.lower()
        if low in CYRILLIC:
            mapped = CYRILLIC[low]
            out.append(mapped.upper() if ch.isupper() and mapped else mapped)
        else:
            out.append(ch)
    return "".join(out)


def key(name: str) -> str:
    """A channel name reduced to something two spellings of it can agree on."""
    s = transliterate(name or "").lower()
    s = s.replace("&", " and ")
    s = QUALITY.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def keys(name: str) -> list[str]:
    """The ways this channel might be filed, best first — "bTV Action" also answers to "btvaction"."""
    base = key(name)
    if not base:
        return []
    out = [base, base.replace(" ", "")]
    # "Agro TV" and "AgroTV" are the same channel, but only the first has "TV" as a separate word for
    # the rule above to drop. Offer the glued spelling without it too — never for a short name, where
    # those two letters are the name ("bTV").
    glued = base.replace(" ", "")
    if glued.endswith("tv") and len(glued) > 5:
        out.append(glued[:-2])
    # A trailing number is often glued on, or dropped entirely: "Diema Sport 2", "diemasport2", "diema"
    stem = re.sub(r"\s*\d+$", "", base).strip()
    if stem and stem != base:
        out += [stem, stem.replace(" ", "")]
    return list(dict.fromkeys(k for k in out if k))


# ---------------------------------------------------------------- a logo drawn from the name
PALETTE = [  # picked to stay legible on a dark grid, and to look unrelated to each other
    ("#1E3A8A", "#3B82F6"), ("#7C2D12", "#F97316"), ("#134E4A", "#14B8A6"),
    ("#4C1D95", "#A78BFA"), ("#7F1D1D", "#F87171"), ("#064E3B", "#34D399"),
    ("#78350F", "#FBBF24"), ("#1E293B", "#94A3B8"), ("#831843", "#F472B6"),
    ("#0C4A6E", "#38BDF8"),
]


def initials(name: str) -> str:
    """One or two characters that stand for this channel, in the name's own alphabet."""
    # "24Kitchen" is two words written as one; splitting at the digit boundary reads better than "24".
    spaced = re.sub(r"(?<=\d)(?=[^\W\d])", " ", (name or "").strip())
    words = [w for w in re.split(r"[^\w]+", spaced) if w]
    words = [w for w in words if not QUALITY.fullmatch(w)] or words
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    if words[0].isdigit() and len(words) > 1:
        return (words[0][:2] + words[1][:1]).upper()[:3]
    return (words[0][:1] + words[1][:1]).upper()


def monogram(name: str) -> str:
    """A small SVG standing in for a logo. Stable: the same channel always gets the same colours."""
    text = initials(name)
    seed = int(hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:8], 16)
    dark, light = PALETTE[seed % len(PALETTE)]
    size = 20 if len(text) > 2 else 26
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" '
        f'aria-label="{_xml(name)}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{dark}"/><stop offset="1" stop-color="{light}"/>'
        "</linearGradient></defs>"
        '<rect width="64" height="64" rx="12" fill="url(#g)"/>'
        f'<text x="32" y="32" text-anchor="middle" dominant-baseline="central" '
        f'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
        f'font-size="{size}" font-weight="700" fill="#fff" letter-spacing="0.5">{_xml(text)}</text>'
        "</svg>"
    )


def _xml(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ---------------------------------------------------------------- a logo from a collection
def raw_base(index_url: str) -> str:
    """Where the files themselves live, given the address of a listing that only names them."""
    m = GH_TREE.match(index_url or "")
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/"
    return ""


def fetch_index(url: str, prefer: str = "") -> dict[str, str]:
    """Ask a logo collection what it holds, and file it by the same key channel names reduce to.

    Collections differ, so rather than guessing at filenames this reads whatever index the source
    offers — a GitHub tree listing, or a plain JSON mapping — and matches against what is really there.
    """
    raw = _get(url)
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        log.info("the logo index at %s was not JSON", url)
        return {}
    files: dict[str, str] = {}
    if isinstance(data, dict) and isinstance(data.get("tree"), list):      # a GitHub tree listing
        base = raw_base(url)
        paths = [e.get("path", "") for e in data["tree"] if e.get("type") == "blob"]
        paths = [p for p in paths if p.lower().endswith((".png", ".svg", ".webp", ".jpg"))]
        # A channel's own country first: many names repeat across countries, and the local one is the
        # one meant. Then the shortest path, which tends to be the plain version rather than a variant.
        want = f"/{prefer.strip().lower()}/" if prefer.strip() else ""
        paths.sort(key=lambda p: (0 if want and want in p.lower() else 1, len(p)))
        for path in paths:
            files[path] = base + path
    elif isinstance(data, list):                                           # a GitHub directory listing
        for entry in data:
            name = entry.get("name", "")
            if entry.get("type") == "file" and name.lower().endswith((".png", ".svg", ".webp", ".jpg")):
                files[name] = entry.get("download_url") or ""
    elif isinstance(data, dict):                                           # a plain name -> url mapping
        files = {k: v for k, v in data.items() if isinstance(v, str)}
    out: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for path, link in files.items():
        if not link:
            continue
        stem = re.sub(r"\.[a-z0-9]+$", "", path.rsplit("/", 1)[-1], flags=re.I)
        k = key(stem)
        if not k:
            continue
        out.setdefault(k, link)
        out.setdefault(k.replace(" ", ""), link)
        # Collections tag files with the country — "btv-bg", "bnt-1-bg" — which no channel name
        # carries. Offer the name without that tail as well, but only as a second choice, so a file
        # named for the channel itself always wins over one that merely ends the same way.
        words = k.split()
        if len(words) > 1 and len(words[-1]) <= 3:
            short = " ".join(words[:-1])
            aliases.setdefault(short, link)
            aliases.setdefault(short.replace(" ", ""), link)
    for k, link in aliases.items():
        out.setdefault(k, link)
    log.info("the logo collection offers %d names", len(out))
    return out


def match(name: str, index: dict[str, str]) -> str:
    """The best link this collection has for this channel, or empty."""
    for k in keys(name):
        if k in index:
            return index[k]
    # Nothing named for the channel exactly. A file is often the channel's name plus a qualifier —
    # "bloomberg-tv-bulgaria" for Bloomberg TV, "balkanika-music-television" for Balkanika Music — so
    # accept a name that begins with the channel's, shortest first. Only for a name long enough to be
    # distinctive: "fen" must not walk off with "fen folk", and "bnt" must not take "bnt 1".
    base = key(name)
    if len(base) >= 6:
        # The index is built in order of preference — the chosen country first, then shortest path —
        # so the first name that begins with this one is the best of them. Sorting by length here
        # instead would hand Bloomberg TV the Turkish logo over the Bulgarian one.
        for k in index:
            if k.startswith(base + " "):
                return index[k]
    return ""


def save(name: str, data: bytes, suffix: str) -> str:
    """Keep a fetched logo where LENTA serves its own artwork from, and return the path to it."""
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", key(name)).strip("-") or hashlib.sha256(name.encode()).hexdigest()[:12]
    path = LOGO_DIR / f"{safe}{suffix}"
    path.write_bytes(data)
    return f"/images/channels/{path.name}"


def download(link: str) -> tuple[bytes, str] | None:
    raw = _get(link, binary=True)
    if not raw or len(raw) > 2_000_000:
        return None
    suffix = ".svg" if raw[:200].lstrip()[:5].lower().startswith(b"<svg") or link.lower().endswith(".svg") \
        else ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".webp" if raw[8:12] == b"WEBP" \
        else ".jpg" if raw[:3] == b"\xff\xd8\xff" else ""
    return (raw, suffix) if suffix else None


def _get(url: str, binary: bool = False) -> bytes | str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "LENTA", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            raw = r.read(8_000_000)
    except urllib.error.HTTPError as ex:
        # The body usually says what the server objected to — a rate limit, a moved repository, a
        # private one. Worth keeping: "it did not answer" sends someone looking in the wrong place.
        try:
            body = ex.read(1200).decode("utf-8", "replace").strip()
        except Exception:                                          # noqa: BLE001 - nothing to add
            body = ""
        _fail(url, f"HTTP {ex.code}" + (f" - {_oneline(body)}" if body else ""))
        return None
    except (urllib.error.URLError, OSError, ValueError) as ex:
        _fail(url, _oneline(str(getattr(ex, "reason", None) or ex)) or ex.__class__.__name__)
        return None
    return raw if binary else raw.decode("utf-8", "replace")


def _fail(url: str, reason: str) -> None:
    """Remember the last thing that went wrong, so a page can say it rather than only a log file."""
    global last_error
    last_error = reason
    log.info("could not fetch %s: %s", url, reason)


def _oneline(text: str) -> str:
    """A server's complaint, trimmed to something that fits in a sentence."""
    flat = " ".join((text or "").split())
    # A JSON error body is nearly all punctuation; the message inside it is the readable part.
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.){3,400})"', flat)
    if m:
        flat = m.group(1)
    return flat[:200]
