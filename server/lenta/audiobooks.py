"""Audiobook libraries: which files make one book, and the book's details from Google Books / Open Library.

Audiobooks come in many layouts, so a folder is looked at as a whole (all its audio files) before a file
is filed:
  * one file per book in a series folder:   Alien/01.Alien - Out of the Shadows.mp3, 02.Alien - ...
  * a folder of chapter files = one book:    The Hobbit/01 Chapter One.mp3, 02 Chapter Two.mp3 ...
  * Author/Book folders:                     Tolkien/The Hobbit/part 1.mp3 ...
  * one .m4b (or .mp3) per book, with its chapters inside.
Files that share one real album tag are one book; files whose names differ only by numbers and words like
"chapter", "part", "track", "disc" are one book; otherwise each file is its own book and the folder is the
series. Tags win over names (album = book title, album artist/artist = author, composer = narrator).

Online details need no key: Google Books (description, authors, year, genres, cover) and Open Library
(larger covers). Matching runs after a scan, like TMDB for films; Fix match and Refresh work the same.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from . import images
from .config import AUDIO_EXT, FFPROBE
from .db import db, jloads, now

log = logging.getLogger("lenta.audiobooks")

PARSER_VERSION = "2"          # raise when the rules change: every audiobook library is read again once

GENERIC_ALBUMS = {"", "unknown", "unknown album", "audiobook", "audiobooks", "audio book", "audio books", "untitled",
                  "various", "chapters", "book", "books"}
GENERIC_PEOPLE = {"", "unknown", "unknown artist", "various", "various artists", "audiobook", "narrator", "author"}
_PARTWORDS = re.compile(r"\b(chapter|chap|ch|part|pt|track|trk|disc|disk|cd|section|sect|file|kapitel|teil|"
                        r"глава|част|chapitre|capitulo|capítulo|parte)\b", re.I)


def natural(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def _tags(path: str) -> tuple[dict, float]:
    import mutagen
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    tags = (f.tags if f is not None and f.tags is not None else {}) or {}

    def tag(name):
        try:
            v = tags.get(name)
        except Exception:
            return None
        v = str(v[0]).strip() if v else None
        return v or None
    out = {k: tag(k) for k in ("album", "albumartist", "artist", "title", "composer", "date", "genre", "tracknumber")}
    return out, float(getattr(getattr(f, "info", None), "length", 0) or 0)


def _core(stem: str) -> str:
    """A file name without its numbers and part words: equal for the chapters of one book."""
    s = stem.lower()
    s = _PARTWORDS.sub(" ", s)
    s = re.sub(r"\d+", " ", s)
    return re.sub(r"[\W_]+", "", s)


_layouts: dict = {}


def layout(directory: str) -> str:
    """'book' (the folder's files are one book) or 'each' (every file is its own book)."""
    try:
        st = os.stat(directory)
        names = sorted((n for n in os.listdir(directory) if Path(n).suffix.lower() in AUDIO_EXT), key=natural)
    except OSError:
        return "each"
    key = (directory, st.st_mtime_ns, len(names))
    if key in _layouts:
        return _layouts[key]
    mode = "each"
    if len(names) > 1:
        albums = [(_tags(os.path.join(directory, n))[0].get("album") or "").strip() for n in names[:40]]
        real = {a.lower() for a in albums if a.lower() not in GENERIC_ALBUMS}
        cores = {_core(Path(n).stem) for n in names}
        if len(real) == 1 and all(a.lower() not in GENERIC_ALBUMS for a in albums):
            mode = "book"                                  # one real album tag on every file
        elif len(real) > 1:
            mode = "each"                                  # different books, each with its own album tag
        elif len(cores) == 1:
            mode = "book"                                  # "Chapter 01", "Part 2", "03"...: one book
        else:
            # names that share all but a short tail ("Hobbit 01 Riddles", "Hobbit 02 ...") still differ: separate
            mode = "each"
    _layouts[key] = mode
    if len(_layouts) > 2000:
        _layouts.clear()
    return mode


def _clean(text: str) -> str:
    text = re.sub(r"[_]+", " ", text)
    text = re.sub(r"\s*\((unabridged|abridged|audiobook|audio book)\)\s*", " ", text, flags=re.I)
    text = re.sub(r"\s*\[(unabridged|abridged|audiobook|audio book|mp3|m4b|\d+k(bps)?)\]\s*", " ", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip(" -–._")


def parse_name(stem: str, series: str | None) -> tuple[str, int | None]:
    """'01.Alien - Out of the Shadows' (in the folder 'Alien') -> ('Out of the Shadows', 1)."""
    index = None
    m = re.match(r"^\s*(?:book|vol(?:ume)?|#)?\s*(\d{1,3})(?:\s*[.)\-_:]\s*|\s+)(.+)$", stem, re.I)
    if m:
        index, stem = int(m.group(1)), m.group(2)
    stem = _clean(stem)
    if series:
        s = re.escape(series.strip())
        stem2 = re.sub(rf"^{s}\s*(?:[-–:.]|book\s*\d+\s*[-–:]?)\s*", "", stem, flags=re.I).strip()
        if stem2 and stem2.lower() != stem.lower():
            stem = stem2
        m = re.match(rf"^(?:book|vol(?:ume)?|#)?\s*(\d{{1,3}})\s*[-–:.]\s*(.+)$", stem, re.I)
        if m and index is None:
            index, stem = int(m.group(1)), m.group(2)
    return _clean(stem) or stem, index


def _chapters(path: str) -> list[dict]:
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_chapters", path],
                             capture_output=True, timeout=30)
        chs = json.loads(out.stdout or b"{}").get("chapters", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    res = []
    for i, c in enumerate(chs, 1):
        try:
            start = float(c.get("start_time") or 0)
        except ValueError:
            continue
        title = ((c.get("tags") or {}).get("title") or "").strip() or f"Chapter {i}"
        res.append({"title": title, "start": round(start, 3)})
    return res if len(res) > 1 else []


def _year(text: str | None) -> int | None:
    m = re.match(r"(\d{4})", text or "")
    return int(m.group(1)) if m else None


def add_book(lib: dict, root: str, path: str, st) -> None:
    """Scanner handler for audiobook libraries (one file at a time; the folder decides the grouping)."""
    from .scanner import ALBUM_ART_NAMES, _embedded_cover, _find_art, _insert_file, _upsert_item
    from . import metadata
    directory = os.path.dirname(path)
    rel = Path(os.path.relpath(path, root)).parts
    folder = rel[-2] if len(rel) >= 2 else None             # the folder the file is in (None: the library root)
    parent = rel[-3] if len(rel) >= 3 else None             # the folder above it
    tags, duration = _tags(path)
    album = tags.get("album") if (tags.get("album") or "").lower() not in GENERIC_ALBUMS else None
    person = next((tags.get(k) for k in ("albumartist", "artist") if (tags.get(k) or "").lower() not in GENERIC_PEOPLE), None)
    narrator = tags.get("composer") if (tags.get("composer") or "").lower() not in GENERIC_PEOPLE else None
    stem = Path(path).stem
    mode = layout(directory) if folder else "each"
    series, index = None, None
    if mode == "book":                                      # the folder is the book
        title = album or (folder and _clean(folder))
        author = person or (parent and _clean(parent))
        key = f"book:{directory}"
        m = re.match(r"^(.+?)\s*(?:[-–]\s*)?(?:book|#|vol\.?)\s*(\d{1,3})\s*[-–:]\s*(.+)$", title or "", re.I)
        if m and not album:                                 # "Series Book 2 - Title" folder names
            series, index, title = m.group(1).strip(), int(m.group(2)), m.group(3).strip()
    else:                                                   # every file is its own book
        single = layout_has_single(directory)            # the only audio file in its folder
        series = _clean(folder) if folder and not single else None
        if not single and (not series or os.path.normpath(directory) == os.path.normpath(root)):
            series = _shared_series(directory) or series   # "01.Alien - Out of the Shadows" at the library's top
        name_title, index = parse_name(stem, series)
        tag_title = tags.get("title")
        title = album if album and single else None
        title = title or (tag_title if tag_title and tag_title.lower() not in GENERIC_ALBUMS and not re.fullmatch(r"(track\s*)?\d+", tag_title, re.I) else None)
        title = _clean(title) if title else name_title
        if series and title.lower().startswith(series.lower() + " - "):
            title = title[len(series) + 3:]
        author = person or (parent and folder and _clean(parent))
        if folder and single:
            # Book/book.m4b: the folder names the book unless the file has a real name of its own
            # (Author/Book/part1.mp3, Dune/Dune.m4b); otherwise a top folder is the author (Tolkien/The Hobbit.mp3)
            generic = not _core(stem) or _core(stem) in ("audiobook", "book", "unabridged", "abridged", "full")
            same = _core(stem) and (_core(stem) in _core(folder) or _core(folder) in _core(stem))
            if generic or same or parent:
                title = album or (_clean(folder) if generic else title) or title
                key = f"book:{directory}"
            else:
                author = person or _clean(folder)
                key = f"book:{path}"
        else:
            key = f"book:{path}"
        try:
            index = index or int((tags.get("tracknumber") or "").split("/")[0]) if series else index
        except ValueError:
            pass
    title = title or _clean(stem) or "Untitled"
    year = _year(tags.get("date"))
    extra = {"parsed_title": title, "parsed_author": author, "parsed_series": series}
    if series:
        extra.update(series=series, series_index=index)
    if narrator:
        extra["narrator"] = narrator
    with db() as con:
        book_id, new = _upsert_item(con, lib["id"], "book", key, None, {
            "title": title, "artist": author, "year": year,
            "genres": json.dumps([tags["genre"]] if tags.get("genre") else []),
            "sort_title": metadata.sort_title(f"{series} {index:04d}" if series and index else title),
            "extra": json.dumps(extra),
        })
        if not new:                                         # read again: newer name rules, keep hand edits
            row = con.execute("SELECT title, extra, matched FROM items WHERE id = ?", (book_id,)).fetchone()
            ex = json.loads(row["extra"] or "{}")
            locks = metadata.locked_fields(ex)
            ex.update(parsed_title=title, parsed_author=author, parsed_series=series)
            for k, v in (("series", series), ("series_index", index), ("narrator", narrator)):
                if k not in locks and v:
                    ex[k] = v
            sets = {"extra": json.dumps(ex)}
            if author and not row["matched"] and "artist" not in locks:     # online details win once found
                sets["artist"] = author
            if "title" not in locks:
                sets["title"] = title
                if "sort_title" not in locks:
                    s_ser, s_idx = ex.get("series"), ex.get("series_index")
                    sets["sort_title"] = metadata.sort_title(f"{s_ser} {int(s_idx):04d}" if s_ser and s_idx else title)
            con.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), book_id))
        info = {"container": Path(path).suffix.lower()[1:], "duration": duration, "bitrate": 0,
                "audio": [{"index": 0, "codec": Path(path).suffix.lower()[1:], "channels": 2}], "subtitles": []}
        _insert_file(con, book_id, lib["id"], path, st, info)
        row = con.execute("SELECT poster, extra FROM items WHERE id = ?", (book_id,)).fetchone()
        ex = json.loads(row["extra"] or "{}")
        chs = _chapters(path) if duration > 600 else []
        fc = ex.get("file_chapters") or {}
        if chs:
            fc[path] = chs
        else:
            fc.pop(path, None)
        ex["file_chapters"] = fc
        ft = ex.get("file_titles") or {}
        t = tags.get("title")
        if t and not re.fullmatch(r"(track\s*)?\d+", t, re.I):
            ft[path] = t
        ex["file_titles"] = ft
        if not row["poster"]:
            art = (_find_art(directory, ALBUM_ART_NAMES, stem) if mode == "each" else _find_art(directory, ALBUM_ART_NAMES))
            ref = images.save_local_file(art) if art else None
            if not ref:
                data = _embedded_cover(path)
                ref = images.save_local(data, ".jpg") if data else None
            if ref:
                con.execute("UPDATE items SET poster = ? WHERE id = ?", (ref, book_id))
                ex["local_cover"] = True
        con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(ex), book_id))


_SERIES_NAME = re.compile(r"^\s*(?:book|vol(?:ume)?|#)?\s*\d{1,3}\s*[.)\-_:]?\s*(.+?)\s+[-–:]\s+.+$", re.I)


def _shared_series(directory: str) -> str | None:
    """The series name that two or more files in the folder start with: '01.Alien - …', '02.Alien - …' -> 'Alien'."""
    try:
        names = [Path(n).stem for n in os.listdir(directory) if Path(n).suffix.lower() in AUDIO_EXT]
    except OSError:
        return None
    counts: dict = {}
    for n in names:
        m = _SERIES_NAME.match(n)
        if m:
            key = _clean(m.group(1))
            counts[key.lower()] = (counts.get(key.lower(), (0, key))[0] + 1, key)
    best = max(counts.values(), default=(0, None))
    return best[1] if best[0] >= 2 else None


def layout_has_single(directory: str) -> bool:
    try:
        return sum(1 for n in os.listdir(directory) if Path(n).suffix.lower() in AUDIO_EXT) == 1
    except OSError:
        return True


def parts(con, book_id: int) -> list[dict]:
    """The book's files in reading order, with their place in the whole book."""
    rows = [dict(r) for r in con.execute("SELECT id, path, duration FROM files WHERE item_id = ?", (book_id,))]
    rows.sort(key=lambda r: natural(r["path"]))
    at = 0.0
    for r in rows:
        r["start"] = round(at, 3)
        at += r["duration"] or 0
    return rows


def refresh_book(con, book_id: int) -> None:
    """Total length and the chapter list (each file, or the chapters inside a single file)."""
    row = con.execute("SELECT extra FROM items WHERE id = ?", (book_id,)).fetchone()
    if not row:
        return
    ex = json.loads(row["extra"] or "{}")
    fc = ex.get("file_chapters") or {}
    ps = parts(con, book_id)
    chapters = []
    for i, p in enumerate(ps, 1):
        inside = fc.get(p["path"])
        if inside:
            chapters += [{"title": c["title"], "start": round(p["start"] + c["start"], 3)} for c in inside]
        else:
            t = (ex.get("file_titles") or {}).get(p["path"]) if len(ps) > 1 else None
            chapters.append({"title": t or (f"Part {i}" if len(ps) > 1 else "Start"), "start": p["start"]})
    have = {p["path"] for p in ps}               # forget files that are gone
    ex["file_chapters"] = {k: v for k, v in fc.items() if k in have}
    ex["file_titles"] = {k: v for k, v in (ex.get("file_titles") or {}).items() if k in have}
    total = sum(p["duration"] or 0 for p in ps)
    ex["chapters"] = chapters if len(chapters) > 1 else []
    con.execute("UPDATE items SET runtime = ?, extra = ? WHERE id = ?",
                (round(total / 60) if total else None, json.dumps(ex), book_id))


# ---- online details ----------------------------------------------------------------------------------

UA = {"User-Agent": "LENTA media server (self-hosted)"}


class SourceError(Exception):
    """Neither Google Books nor Open Library answered (no internet, blocked, or too many requests)."""


_failed: list = []          # requests that failed during the current search


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").lower()).strip()


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<\s*br\s*/?>|</p>", "\n", text, flags=re.I)
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _genres(cats) -> list[str]:
    """'Fiction / Science Fiction / General' -> 'Science Fiction'."""
    out = []
    for c in cats or []:
        parts = [p.strip() for p in str(c).split("/") if p.strip() and p.strip().lower() != "general"]
        g = parts[-1] if parts else None
        if g and g not in out:
            out.append(g)
    return out


def _google(q: str, n: int = 8) -> list[dict]:
    try:
        r = httpx.get("https://www.googleapis.com/books/v1/volumes",
                      params={"q": q, "maxResults": n, "printType": "books"}, headers=UA, timeout=15)
        r.raise_for_status()
        items = r.json().get("items") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Google Books: %s", exc)
        _failed.append(f"Google Books: {exc}")
        return []
    out = []
    for it in items:
        v = it.get("volumeInfo") or {}
        title = v.get("title") or ""
        if v.get("subtitle"):
            title_full = f"{title}: {v['subtitle']}"
        else:
            title_full = title
        links = v.get("imageLinks") or {}
        cover = links.get("extraLarge") or links.get("large") or links.get("medium") or links.get("thumbnail") or links.get("smallThumbnail")
        if cover:
            cover = cover.replace("http://", "https://").replace("&edge=curl", "")
            if "books.google" in cover and "fife=" not in cover:
                cover += "&fife=w800-h1200"
        isbn = next((x.get("identifier") for x in v.get("industryIdentifiers") or [] if x.get("type") == "ISBN_13"), None)
        out.append({"source": "google", "id": it.get("id"), "title": title_full, "short_title": title,
                    "authors": v.get("authors") or [], "year": _year(v.get("publishedDate")),
                    "description": _strip_html(v.get("description")), "genres": _genres(v.get("categories")),
                    "cover": cover, "isbn": isbn, "language": v.get("language")})
    return out


def _openlibrary(title: str, author: str | None) -> list[dict]:
    params = {"title": title, "limit": 6, "fields": "key,title,author_name,first_publish_year,cover_i,subject,isbn"}
    if author:
        params["author"] = author
    try:
        r = httpx.get("https://openlibrary.org/search.json", params=params, headers=UA, timeout=15)
        r.raise_for_status()
        docs = r.json().get("docs") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Open Library: %s", exc)
        _failed.append(f"Open Library: {exc}")
        return []
    return [{"source": "openlibrary", "id": d.get("key"), "title": d.get("title") or "", "short_title": d.get("title") or "",
             "authors": d.get("author_name") or [], "year": d.get("first_publish_year"),
             "description": None, "genres": (d.get("subject") or [])[:4],
             "cover": f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get("cover_i") else None,
             "isbn": next((i for i in d.get("isbn") or [] if len(i) == 13), None)} for d in docs]


def _score(c: dict, title: str, author: str | None, series: str | None) -> float:
    t = _norm(c["title"]); st = _norm(c.get("short_title") or "")
    want = [_norm(title)] + ([_norm(f"{series} {title}")] if series else [])
    s = max(SequenceMatcher(None, w, x).ratio() for w in want for x in (t, st) if x) if (t or st) else 0
    if any(w and (w in t) for w in want):
        s = max(s, 0.86)
    if author:
        a = _norm(author)
        if any(a and (a in _norm(x) or _norm(x) in a) for x in c.get("authors") or []):
            s += 0.15
    if c.get("description"):
        s += 0.03
    if c.get("cover"):
        s += 0.02
    return s


def search(title: str, author: str | None = None, series: str | None = None) -> list[dict]:
    """Candidates for a book, best first (Google Books, with Open Library's covers when they match).
    Raises SourceError when nothing was found because the sites didn't answer."""
    _failed.clear()
    queries = []
    for t in ([f"{series}: {title}", title] if series else [title]):
        queries.append(f'intitle:"{t}"' + (f' inauthor:"{author}"' if author else ""))
    queries.append(" ".join(x for x in (series, title, author) if x))
    seen, found = set(), []
    for q in queries:
        for c in _google(q):
            if c["id"] not in seen:
                seen.add(c["id"]); found.append(c)
        if len(found) >= 6:
            break
    ol = _openlibrary(f"{series} {title}" if series else title, author) or (_openlibrary(title, None) if author else [])
    for c in found:                                         # a bigger cover from Open Library for the same book
        for o in ol:
            if o["cover"] and max(SequenceMatcher(None, _norm(c[k]), _norm(o["title"])).ratio()
                                  for k in ("short_title", "title")) > 0.85:
                c["cover_large"] = o["cover"]
                break
    if not found:
        found = ol
    if not found and _failed:
        raise SourceError("; ".join(dict.fromkeys(_failed)))
    for c in found:
        c["score"] = round(_score(c, title, author, series), 3)
    return sorted(found, key=lambda c: -c["score"])


def apply(item_id: int, c: dict) -> None:
    from . import metadata
    with db() as con:
        row = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return
        ex = json.loads(row["extra"] or "{}")
        values = {"matched": 1}
        if c.get("description"):
            values["overview"] = c["description"]
        if c.get("year"):
            values["year"] = c["year"]
        if c.get("genres"):
            values["genres"] = json.dumps(c["genres"][:4])
        if c.get("authors"):
            values["artist"] = ", ".join(c["authors"][:3])
        cover = c.get("cover_large") or c.get("cover")
        if cover and not ex.get("local_cover"):            # a cover in the folder or the file always wins
            values["poster"] = images.web_ref(cover)
        ex.update(book_source=c.get("source"), book_id=c.get("id"), isbn=c.get("isbn") or ex.get("isbn"),
                  online_title=c.get("title"))
        ex.pop("match_attempts", None)
        ex.pop("match_tries", None)
        metadata._update(con, item_id, values, ex)


def match_book(item_id: int, candidate_id: str | None = None, source: str = "google",
               query: str | None = None) -> bool:
    """Find the book online and fill in its details. With candidate_id: the result picked in Fix match."""
    with db() as con:
        row = con.execute("SELECT * FROM items WHERE id = ? AND kind = 'book'", (item_id,)).fetchone()
    if not row:
        return False
    ex = json.loads(row["extra"] or "{}")
    title = ex.get("parsed_title") or row["title"]
    author = ex.get("parsed_author") or row["artist"]
    series = ex.get("parsed_series") or ex.get("series")
    if candidate_id:
        found = []
        if query:
            t, _, a = query.partition(" by ")
            try:
                found = search(t.strip(), a.strip() or None)
            except SourceError:
                found = []
        cands = [c for c in found if c["id"] == candidate_id][:1] or [c for c in _by_id(candidate_id, source)]
    else:
        try:
            cands = search(title, author, series)
        except SourceError as exc:
            log.info("Book details for '%s' not fetched: %s", title, exc)
            return False
        cands = [c for c in cands if c["score"] >= 0.8][:1]
    if not cands:
        if not candidate_id:
            tried = [f"{series}: {title}" if series else title] + ([f"{title} by {author}"] if author else [])
            with db() as con:
                ex["match_attempts"] = tried
                ex["match_tries"] = int(ex.get("match_tries") or 0) + 1
                con.execute("UPDATE items SET extra = ? WHERE id = ?", (json.dumps(ex), item_id))
        return False
    apply(item_id, cands[0])
    return True


def _by_id(cid: str, source: str) -> list[dict]:
    if source == "openlibrary":
        try:
            r = httpx.get(f"https://openlibrary.org{cid}.json", headers=UA, timeout=15)
            r.raise_for_status()
            w = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        desc = w.get("description")
        desc = desc.get("value") if isinstance(desc, dict) else desc
        covers = [c for c in w.get("covers") or [] if c and c > 0]
        return [{"source": "openlibrary", "id": cid, "title": w.get("title") or "", "short_title": w.get("title") or "",
                 "authors": [], "year": _year(w.get("first_publish_date")), "description": _strip_html(desc),
                 "genres": (w.get("subjects") or [])[:4],
                 "cover": f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else None}]
    if not re.fullmatch(r"[\w-]{4,40}", cid or ""):
        return []
    try:
        r = httpx.get(f"https://www.googleapis.com/books/v1/volumes/{cid}", headers=UA, timeout=15)
        r.raise_for_status()
        it = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    v = it.get("volumeInfo") or {}
    links = v.get("imageLinks") or {}
    cover = links.get("extraLarge") or links.get("large") or links.get("medium") or links.get("thumbnail")
    if cover:
        cover = cover.replace("http://", "https://").replace("&edge=curl", "")
    return [{"source": "google", "id": it.get("id"), "title": v.get("title") or "", "short_title": v.get("title") or "",
             "authors": v.get("authors") or [], "year": _year(v.get("publishedDate")),
             "description": _strip_html(v.get("description")), "genres": _genres(v.get("categories")), "cover": cover}]


def match_library(library_id: int, refresh: bool = False, state: dict | None = None, ids: list[int] | None = None) -> None:
    cond = "match_locked = 0" if refresh else "matched = 0 AND match_locked = 0 AND COALESCE(json_extract(extra, '$.match_tries'), 0) < 3"
    with db() as con:
        pending = [r["id"] for r in con.execute(
            f"SELECT id FROM items WHERE library_id = ? AND kind = 'book' AND {cond} ORDER BY added_at DESC", (library_id,))]
    if ids is not None:
        pending = [i for i in pending if i in ids]
    if state is not None:
        state.update(phase="fetching book details", total=len(pending), done=0)
    for n, item_id in enumerate(pending, 1):
        try:
            match_book(item_id)
        except Exception as exc:
            log.warning("Book details for item %s failed: %s", item_id, exc)
        if state is not None:
            state["done"] = n
