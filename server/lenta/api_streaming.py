"""Streaming services (Server admin › Settings › Streaming services).

Which services the household pays for, and what they are showing. The reading half is open to anyone
signed in, because Home draws the rows; the changing half is the administrator's, like the TMDB key it
depends on.
"""
from __future__ import annotations

import urllib.error
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import auth, streaming, safefetch
from .api_editor import _store_image
from .db import set_setting
from .metadata import TMDBError

router = APIRouter(prefix="/api/admin/streaming", dependencies=[Depends(auth.admin_user)])
read = APIRouter(prefix="/api/streaming")


class Choice(BaseModel):
    services: list[int] | None = None
    hide_owned: bool | None = None
    languages_off: list[str] | None = None
    genres: list[str] | None = None


class Art(BaseModel):
    tmdb_id: int
    kind: str
    shape: str = "poster"       # which picture: the upright one or the wide one
    ref: str | None = None      # one of the choices TMDB holds for this title
    url: str | None = None      # a picture somewhere on the web
    reset: bool = False         # back to the one TMDB has


@router.get("")
def settings_page(refresh: bool = False) -> dict:
    """Everything the settings page needs: the services available here, and what is chosen.

    The list comes from TMDB for this country, so it is the services someone here could actually
    subscribe to — not a global list with Hulu at the top for a household in Bulgaria.
    """
    try:
        available = streaming.providers(refresh=refresh)
    except TMDBError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**streaming.status(),
            "available": [{"id": p["provider_id"], "name": p["name"],
                           "logo": f"/api/img/tmdb/{p['logo'].lstrip('/')}" if p["logo"] else None}
                          for p in available]}


@router.post("")
def save(body: Choice) -> dict:
    """Choose the services. Picking a new one fetches what it is showing straight away, because a row
    that stays empty until tomorrow looks like a feature that does not work."""
    before = set(streaming.chosen())
    if body.services is not None:
        streaming.set_chosen(body.services)
    if body.hide_owned is not None:
        set_setting("streaming_hide_owned", "1" if body.hide_owned else "0")
    if body.languages_off is not None:
        streaming.set_languages_off(body.languages_off)
    genres_changed = body.genres is not None and set(body.genres) != set(streaming.chosen_genres())
    if body.genres is not None:
        streaming.set_genres(body.genres)
    # Narrowing by genre changes the question asked of TMDB, so what is cached answers the old one.
    if genres_changed:
        streaming.request_refresh(force=True)
    if set(streaming.chosen()) - before:
        streaming.request_refresh()
    return streaming.status()


@router.post("/refresh")
def refresh_now() -> dict:
    streaming.request_refresh(force=True)
    return streaming.status()


@router.get("/art/choices")
def art_choices(tmdb_id: int, kind: str) -> dict:
    """The pictures TMDB holds for this title, to pick from — the same offer the editor makes for a
    title of your own."""
    if kind not in streaming.KINDS:
        raise HTTPException(400, "Unknown kind")
    try:
        return streaming.art_choices(tmdb_id, kind)
    except TMDBError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/art")
def set_art(body: Art) -> dict:
    """Use your own picture for one of these titles, instead of the one TMDB publishes.

    Kept in a table of its own, because the titles themselves are thrown away and re-fetched every day:
    a choice that vanished overnight would be worse than not offering it.
    """
    if body.kind not in streaming.KINDS:
        raise HTTPException(400, "Unknown kind")
    shape = body.shape if body.shape in streaming.ART_SHAPES else "poster"
    if body.reset:
        streaming.clear_art(body.tmdb_id, body.kind, shape)
        return {"ok": True, "own_art": False}
    if body.ref:
        if not body.ref.startswith(("tmdb:/", "local:", "web:")):
            raise HTTPException(400, "Unknown image")
        streaming.set_art(body.tmdb_id, body.kind, body.ref, shape)
        return {"ok": True, "own_art": True}
    if not body.url or not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a link that starts with http:// or https://")
    try:
        data, _ctype = safefetch.get(body.url, allow_private=True, limit=25_000_000, timeout=30)
    except safefetch.Blocked as exc:
        raise HTTPException(400, f"That link can't be fetched: {exc}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HTTPException(400, f"Couldn't download that image: {exc}") from exc
    streaming.set_art(body.tmdb_id, body.kind, _store_image(data), shape)
    return {"ok": True, "own_art": True}


@router.post("/art/upload")
async def upload_art(tmdb_id: int, kind: str, request: Request, shape: str = "poster") -> dict:
    if kind not in streaming.KINDS:
        raise HTTPException(400, "Unknown kind")
    data = await request.body()
    if not data:
        raise HTTPException(400, "No file received")
    streaming.set_art(tmdb_id, kind, _store_image(data),
                      shape if shape in streaming.ART_SHAPES else "poster")
    return {"ok": True, "own_art": True}


@read.get("/status")
def status(user: dict = Depends(auth.current_user)) -> dict:
    return streaming.status()
