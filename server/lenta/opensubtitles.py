"""OpenSubtitles.com REST client (API v1).

Needs a free API key from https://www.opensubtitles.com/consumers. A (free) account username and
password raise the daily download quota; without them downloads are anonymous and more limited.
"""
import os
import struct
import threading
import time

import httpx

from . import logs
from .config import VERSION
from .db import get_setting

log = logs.get("subtitles")
API = "https://api.opensubtitles.com/api/v1"
USER_AGENT = f"LENTA v{VERSION}"

_token = {"value": None, "base": None, "user": None, "expires": 0.0}
_token_lock = threading.Lock()


class SubtitleError(Exception):
    pass


def movie_hash(path: str) -> str | None:
    """OpenSubtitles hash: file size + 64-bit sums of the first and last 64 KiB."""
    try:
        size = os.path.getsize(path)
        if size < 131072:
            return None
        h = size
        with open(path, "rb") as f:
            for offset in (0, size - 65536):
                f.seek(offset)
                for (value,) in struct.iter_unpack("<Q", f.read(65536)):
                    h = (h + value) & 0xFFFFFFFFFFFFFFFF
        return f"{h:016x}"
    except OSError:
        return None


class OpenSubtitles:
    def __init__(self):
        self.key = (get_setting("opensubtitles_api_key") or "").strip()
        self.username = (get_setting("opensubtitles_username") or "").strip()
        self.password = get_setting("opensubtitles_password") or ""
        self.client = httpx.Client(timeout=25, follow_redirects=True)

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _headers(self, auth: bool = False) -> dict:
        h = {"Api-Key": self.key, "User-Agent": USER_AGENT, "Accept": "application/json",
             "Content-Type": "application/json"}
        if auth and _token["value"]:
            h["Authorization"] = f"Bearer {_token['value']}"
        return h

    def _base(self) -> str:
        return f"https://{_token['base']}/api/v1" if _token["base"] else API

    @staticmethod
    def _message(r: httpx.Response) -> str | None:
        """OpenSubtitles explains most refusals in a 'message' (sometimes 'errors') field: pass it on."""
        try:
            data = r.json()
        except ValueError:
            return (r.text or "").strip()[:200] or None
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error")
            if not msg and data.get("errors"):
                msg = "; ".join(str(e) for e in data["errors"])
            return msg
        return None

    def _check(self, r: httpx.Response, what: str = "request") -> dict:
        if r.status_code < 400:
            return r.json()
        msg = self._message(r)
        log.warning("OpenSubtitles %s failed with HTTP %s: %s", what, r.status_code, msg)
        if r.status_code in (406, 429):
            raise SubtitleError(msg or "OpenSubtitles download limit reached for today. Try again later.")
        if r.status_code in (401, 403) and what != "login":
            raise SubtitleError(msg or "OpenSubtitles rejected the API key. Check Server admin › Settings.")
        raise SubtitleError(f"OpenSubtitles {what} failed (HTTP {r.status_code})" + (f": {msg}" if msg else ""))

    def login(self) -> None:
        if not self.username or not self.password:
            return
        if "@" in self.username:
            raise SubtitleError("OpenSubtitles needs your username, not your email address. "
                                "Your username is shown on your profile at opensubtitles.com.")
        with _token_lock:
            if _token["value"] and _token["user"] == self.username and _token["expires"] > time.time():
                return
            r = self.client.post(f"{API}/login", headers=self._headers(),
                                 json={"username": self.username, "password": self.password})
            data = self._check(r, "login")
            _token.update(value=data.get("token"), base=data.get("base_url") or None, user=self.username,
                          expires=time.time() + 20 * 3600)

    def test(self) -> dict:
        r = self.client.get(f"{API}/infos/formats", headers=self._headers())
        self._check(r, "API key check")
        if self.username or self.password:
            try:
                self.login()
            except SubtitleError as exc:
                raise SubtitleError(f"The API key works, but the login failed. {exc}")
        if _token["value"]:
            r = self.client.get(f"{self._base()}/infos/user", headers=self._headers(auth=True))
            data = self._check(r).get("data", {})
            return {"user": data.get("username") or self.username, "allowed_downloads": data.get("allowed_downloads"),
                    "remaining": data.get("remaining_downloads")}
        return {"user": None}

    def search(self, *, language: str, tmdb_id: int | None = None, parent_tmdb_id: int | None = None,
               season: int | None = None, episode: int | None = None, query: str | None = None,
               year: int | None = None, moviehash: str | None = None, kind: str = "movie") -> list[dict]:
        params = {"languages": language.lower()}
        if kind == "episode":
            params["type"] = "episode"
            if parent_tmdb_id:
                params["parent_tmdb_id"] = parent_tmdb_id
            elif query:
                params["query"] = query.lower()
            if season is not None:
                params["season_number"] = season
            if episode is not None:
                params["episode_number"] = episode
        else:
            params["type"] = "movie"
            if tmdb_id:
                params["tmdb_id"] = tmdb_id
            elif query:
                params["query"] = query.lower()
                if year:
                    params["year"] = year
        if moviehash:
            params["moviehash"] = moviehash
        # The API asks for alphabetically sorted parameters (otherwise it redirects).
        ordered = dict(sorted(params.items()))
        r = self.client.get(f"{API}/subtitles", params=ordered, headers=self._headers())
        data = self._check(r, "search").get("data", [])
        out = []
        for item in data:
            a = item.get("attributes", {})
            files = a.get("files") or []
            if not files:
                continue
            out.append({
                "provider_id": str(files[0].get("file_id")),
                "release": a.get("release") or files[0].get("file_name") or "",
                "language": a.get("language") or language,
                "downloads": a.get("download_count") or 0,
                "hearing_impaired": bool(a.get("hearing_impaired")),
                "hash_match": bool(a.get("moviehash_match")),
                "machine_translated": bool(a.get("machine_translated") or a.get("ai_translated")),
                "fps": a.get("fps"),
                "uploader": (a.get("uploader") or {}).get("name"),
                "uploaded": (a.get("upload_date") or "")[:10],
                "rating": a.get("ratings"),
            })
        out.sort(key=lambda s: (not s["hash_match"], s["machine_translated"], s["hearing_impaired"], -s["downloads"]))
        return out

    def download(self, provider_id: str) -> tuple[bytes, str]:
        try:
            self.login()
        except SubtitleError as exc:   # a bad login shouldn't block downloads: fall back to anonymous
            log.warning("OpenSubtitles login failed, downloading anonymously: %s", exc)
            _token.update(value=None, base=None)
        r = self.client.post(f"{self._base()}/download", headers=self._headers(auth=True),
                             json={"file_id": int(provider_id)})
        data = self._check(r, "download")
        link = data.get("link")
        if not link:
            raise SubtitleError(data.get("message") or "OpenSubtitles did not return a download link")
        f = self.client.get(link, headers={"User-Agent": USER_AGENT})
        if f.status_code != 200 or not f.content:
            raise SubtitleError("The subtitle file could not be downloaded")
        log.info("Downloaded subtitle %s (%s downloads left today)", provider_id, data.get("remaining"))
        return f.content, data.get("file_name") or f"{provider_id}.srt"
