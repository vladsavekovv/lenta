"""Subtitle conversion to WebVTT (embedded text tracks and sidecar files), cached on disk."""
import hashlib
import re
import subprocess
from pathlib import Path

from .config import CACHE_DIR, FFMPEG

CYRILLIC_LANGS = {"bg", "bul", "ru", "rus", "uk", "ukr", "sr", "srp", "mk", "mkd", "be", "bel"}
_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _decode(data: bytes, language: str) -> str:
    """UTF-8 if valid; otherwise Windows-1251 or 1252, judged from the bytes, not just the language tag.
    In Cyrillic text almost every letter is a high byte; in Western text only accented letters are."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    letters = sum(1 for b in data if b >= 0xC0 or 0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A)
    high = sum(1 for b in data if b >= 0xC0)
    cyrillic = (high / letters > 0.3) if letters else language in CYRILLIC_LANGS
    return data.decode("cp1251" if cyrillic else "cp1252", errors="replace")


def _srt_to_vtt(text: str) -> str:
    lines = ["WEBVTT", ""]
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if "-->" in line:
            line = _TIME.sub(lambda m: f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3)}.{m.group(4).ljust(3, '0')}",
                             line)
        line = re.sub(r"\{\\[^}]*\}", "", line)  # {\an8} style overrides
        lines.append(line)
    return "\n".join(lines) + "\n"


def to_vtt(file_row: dict, sub: dict) -> str | None:
    ident = f"{file_row['path']}|{file_row.get('mtime')}|{sub['key']}"
    if sub.get("external"):   # sidecar or downloaded file: its own path and timestamp identify the content
        try:
            st = Path(sub["path"]).stat()
            ident += f"|{sub['path']}|{st.st_mtime}|{st.st_size}"
        except OSError:
            ident += f"|{sub['path']}"
    key = hashlib.sha1(ident.encode()).hexdigest()
    cache = CACHE_DIR / "subs" / f"{key}.vtt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    cache.parent.mkdir(parents=True, exist_ok=True)
    text = None
    lang = (sub.get("language") or "und").lower()
    if sub.get("external"):
        path = Path(sub["path"])
        raw = path.read_bytes()
        if sub["codec"] == "srt":
            text = _srt_to_vtt(_decode(raw, lang))
        elif sub["codec"] == "vtt":
            text = _decode(raw, lang)
        else:  # ass / ssa
            charenc = "UTF-8"
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                charenc = "CP1251" if lang in CYRILLIC_LANGS else "CP1252"
            out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-sub_charenc", charenc,
                                  "-i", str(path), "-f", "webvtt", "pipe:1"], capture_output=True, timeout=60)
            text = out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None
    else:
        out = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", file_row["path"],
                              "-map", f"0:{sub['index']}", "-f", "webvtt", "pipe:1"],
                             capture_output=True, timeout=600)
        text = out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None
    if text:
        cache.write_text(text, encoding="utf-8")
    return text


def downloaded(file_id: int) -> list[dict]:
    """Subtitles fetched from online providers for one file, shaped like the other subtitle entries."""
    from .db import db
    with db() as con:
        rows = con.execute("SELECT * FROM subtitle_downloads WHERE file_id = ? ORDER BY id", (file_id,)).fetchall()
    out = []
    for r in rows:
        if not Path(r["path"]).exists():
            continue
        out.append({"key": f"dl{r['id']}", "path": r["path"], "codec": Path(r["path"]).suffix[1:].lower() or "srt",
                    "language": r["language"], "title": r["release"] or "", "forced": False, "default": False,
                    "text": True, "image": False, "external": True, "downloaded": True, "dl_id": r["id"],
                    "hearing_impaired": bool(r["hearing_impaired"]), "provider": r["provider"]})
    return out
