"""Live TV settings: the tuners LENTA found, the playlists you added, and the channels they carry."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from . import auth, dvb, epg, livestream, livetv, logos, safefetch
from .db import db, get_setting, set_setting

router = APIRouter(prefix="/api/admin/livetv", dependencies=[Depends(auth.admin_user)])
# Anyone with a live TV library — not merely anyone signed in, which is what it used to be. The guard
# is on the router so that the next endpoint added here inherits it (see auth.live_tv_viewer).
read = APIRouter(prefix="/api/livetv", dependencies=[Depends(auth.live_tv_viewer)])


class PlaylistBody(BaseModel):
    kind: str = "m3u"                # m3u | xtream
    name: str = ""
    url: str = ""
    username: str = ""
    password: str = ""


class SourceBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class ChannelBody(BaseModel):
    hidden: bool | None = None
    number: int | None = None
    name: str | None = None


class SettingsBody(BaseModel):
    auto_scan: bool


@router.get("")
def overview() -> dict:
    """Everything the Live TV page shows: what is plugged in, what was found, what is running."""
    tuners = livetv.detect()
    srcs = livetv.sources()
    counts = {}
    with db() as con:
        for r in con.execute("SELECT source_id, COUNT(*) n FROM tv_channels GROUP BY source_id"):
            counts[r["source_id"]] = r["n"]
    for s in srcs:
        s["channels"] = counts.get(s["id"], 0)
    return {"dvb_present": dvb.present(), "tuners": tuners, "sources": srcs,
            "auto_scan": (get_setting("livetv_auto_scan") or "1") != "0",
            "guide": {**epg.coverage(), "collecting": epg.collecting(), **epg.sweep_state(),
                      "watching_on": epg.wanted("watching"), "sweep_on": epg.wanted("sweep")},
            "logo_index": get_setting("livetv_logo_index") or logos.DEFAULT_INDEX,
            "logo_country": get_setting("livetv_logo_country") or "",
            "channels": livetv.channels()}


@router.patch("/settings")
def settings(body: SettingsBody) -> dict:
    set_setting("livetv_auto_scan", "1" if body.auto_scan else "0")
    return {"auto_scan": body.auto_scan}


@router.post("/sources/{sid}/stop")
def stop(sid: int) -> dict:
    """Let go of the tuner now — what was found so far is kept."""
    if not livetv.stop_scan(sid):
        raise HTTPException(409, "Nothing is scanning on that source.")
    return {"stopping": True}


@router.get("/sources/{sid}/trace")
def scan_trace(sid: int) -> dict:
    """What the last scan actually saw. The answer when a scan and the check tool disagree."""
    if not livetv.source(sid):
        raise HTTPException(404, "That source is gone.")
    return {"lines": livetv.trace(sid)}


@router.post("/detect")
def detect_now() -> dict:
    return {"tuners": livetv.detect()}


@router.post("/sources/{sid}/scan")
def scan(sid: int, deep: bool = False) -> dict:
    src = livetv.source(sid)
    if not src:
        raise HTTPException(404, "That source is gone.")
    if (livetv.sources() and any((s.get("scan") or {}).get("running") for s in livetv.sources())):
        raise HTTPException(409, "A scan is already running.")
    livetv.scan_async(sid, **({"deep": deep} if src["kind"] == "tuner" else {}))
    return {"started": True}


@router.patch("/sources/{sid}")
def update_source(sid: int, body: SourceBody) -> dict:
    src = livetv.source(sid)
    if not src:
        raise HTTPException(404, "That source is gone.")
    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip() or src["name"]
    if body.enabled is not None:
        fields["enabled"] = 1 if body.enabled else 0
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db() as con:
            con.execute(f"UPDATE tv_sources SET {cols} WHERE id = ?", (*fields.values(), sid))
    return livetv.source(sid)


@router.delete("/sources/{sid}")
def delete_source(sid: int) -> dict:
    src = livetv.source(sid)
    if not src:
        raise HTTPException(404, "That source is gone.")
    if src["kind"] == "tuner":
        raise HTTPException(400, "A tuner is found automatically; unplug it or switch it off instead.")
    with db() as con:
        con.execute("DELETE FROM tv_sources WHERE id = ?", (sid,))
    return {"deleted": True}


@router.post("/playlists")
def add_playlist(body: PlaylistBody) -> dict:
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "The address must start with http:// or https://")
    kind = "xtream" if body.kind == "xtream" else "m3u"
    if kind == "xtream" and not (body.username and body.password):
        raise HTTPException(400, "An Xtream login needs a user name and a password.")
    name = body.name.strip() or ("Xtream" if kind == "xtream" else url.rsplit("/", 1)[-1] or "Playlist")
    cfg = {"url": url}
    if kind == "xtream":
        cfg.update(username=body.username, password=body.password)
    sid = livetv.add_source(kind, name, cfg)
    livetv.scan_async(sid)
    return {"id": sid}


@router.patch("/channels/{cid}")
def update_channel(cid: int, body: ChannelBody) -> dict:
    fields = {}
    if body.hidden is not None:
        fields["hidden"] = 1 if body.hidden else 0
    if body.number is not None:
        fields["number"] = body.number
    if body.name is not None and body.name.strip():
        fields["name"] = body.name.strip()
    if not fields:
        return {"ok": True}
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db() as con:
        n = con.execute(f"UPDATE tv_channels SET {cols} WHERE id = ?", (*fields.values(), cid)).rowcount
    if not n:
        raise HTTPException(404, "That channel is gone.")
    return {"ok": True}


@read.get("/channels")
def channel_list(user: dict = Depends(auth.current_user)) -> dict:
    """The channels people can watch, in the order the provider asks for."""
    return {"channels": [
        {k: v for k, v in c.items() if k not in ("tune",)} | {"source": c["source_name"]}
        for c in livetv.channels(include_hidden=False)]}


# ---- what is on -----------------------------------------------------------------
def _guide_keys(include_hidden: bool = False) -> tuple[list[dict], dict[int, tuple[int, str]]]:
    """Every channel with the pair the guide is filed under: which source, and the channel's uid."""
    rows = livetv.channels(include_hidden=include_hidden)
    return rows, {c["id"]: (c["source_id"], c["uid"]) for c in rows}


@read.get("/epg/now")
def epg_now(user: dict = Depends(auth.current_user)) -> dict:
    """What is on every channel at this moment, and what follows — one call for a whole page of tiles."""
    rows, keys = _guide_keys()
    by_source: dict[int, list[str]] = {}
    for sid, uid in keys.values():
        by_source.setdefault(sid, []).append(uid)
    found: dict[int, dict] = {}
    for sid, uids in by_source.items():
        found[sid] = epg.now_next_many(sid, uids)
    out = {}
    for cid, (sid, uid) in keys.items():
        slot = found.get(sid, {}).get(uid)
        if slot and (slot["now"] or slot["next"]):
            out[str(cid)] = slot
    return {"at": time.time(), "channels": out}


@read.get("/epg")
def epg_window(start: float = 0, hours: float = 4, channel: int = 0,
               user: dict = Depends(auth.current_user)) -> dict:
    """Everything on in a stretch of time — one channel's evening, or the whole guide page."""
    hours = max(0.5, min(hours, 24))
    start = start or time.time()
    rows, keys = _guide_keys()
    if channel:
        rows = [c for c in rows if c["id"] == channel]
        if not rows:
            raise HTTPException(404, "That channel is gone.")
    by_source: dict[int, list[str]] = {}
    for c in rows:
        by_source.setdefault(c["source_id"], []).append(c["uid"])
    got: dict[int, dict] = {sid: epg.window(sid, uids, start, start + hours * 3600)
                            for sid, uids in by_source.items()}
    return {"start": start, "stop": start + hours * 3600,
            "channels": [{"id": c["id"], "name": c["name"], "number": c.get("number"),
                          "programmes": got.get(c["source_id"], {}).get(c["uid"], [])} for c in rows]}


# ---- watching a channel ---------------------------------------------------------

def _feed_for(fid: str) -> livestream.Feed:
    f = livestream.feed(fid)
    if not f:
        raise HTTPException(404, "That stream has ended.")
    return f


class LogoBody(BaseModel):
    index: str = ""          # where to ask what logos exist; empty uses the stored one, or the default
    country: str = ""        # prefer this country's folder when a name appears in several
    replace: bool = False    # also reconsider channels that already have one


@read.get("/channels/{cid}/logo")
def channel_logo(cid: int, request: Request, user: dict = Depends(auth.current_user)):
    """This channel's artwork — whatever it has, and a monogram when it has nothing.

    Always answering means nothing on a page has to decide between a logo and a placeholder, and a
    channel that gains a real logo later simply starts looking different at the same address.
    """
    ch = livetv.channel(cid)
    if not ch:
        raise HTTPException(404, "That channel is gone.")
    logo = (ch.get("logo") or "").strip()
    if logo.startswith(("http://", "https://")):
        # A 302 to an address **the playlist provider chose** made this an open redirector on LENTA's
        # own origin, and sent every viewer's browser to a third party to fetch a picture. Checked
        # against the same policy as any other link out of somebody else's answer, so at minimum it
        # cannot be used to aim a browser at this network; an ordinary external logo still redirects.
        try:
            safefetch.check(logo)
            return RedirectResponse(logo, status_code=302)
        except safefetch.Blocked:
            pass                    # falls through to the monogram below, as a channel with no logo does
    # The address of a channel's artwork never changes, but what it answers with does: a monogram today
    # and the station's real logo the moment one is found. Cached outright, that first monogram would
    # outlive the logo that replaced it and no amount of reloading would shift it. So the browser is
    # asked to check back every time, and gets a 304 and no picture whenever nothing has changed.
    fresh = {"Cache-Control": "no-cache"}
    if logo.startswith("/images/channels/"):
        path = logos.LOGO_DIR / logo.rsplit("/", 1)[-1]
        if path.is_file():
            return FileResponse(path, headers=fresh)
    svg = logos.monogram(ch["name"])
    tag = hashlib.sha256(svg.encode("utf-8")).hexdigest()[:16]
    if request.headers.get("if-none-match") == f'W/"{tag}"':
        return Response(status_code=304, headers={**fresh, "ETag": f'W/"{tag}"'})
    return Response(svg, media_type="image/svg+xml", headers={**fresh, "ETag": f'W/"{tag}"'})


class GuideBody(BaseModel):
    watching: bool = True        # read the guide off the multiplex you are watching
    sweep: bool = True           # tune the multiplexes nobody is watching, when an input is spare


@router.post("/guide")
def guide_settings(body: GuideBody) -> dict:
    """Turn either way of collecting the guide on or off — one at a time, to rule one out."""
    set_setting("livetv_guide_watching", "on" if body.watching else "off")
    set_setting("livetv_guide_sweep", "on" if body.sweep else "off")
    return {"watching": body.watching, "sweep": body.sweep}


@router.post("/logos")
def find_logos(body: LogoBody) -> dict:
    """Look up artwork for every channel that has none, from a collection of logos.

    Nothing is guessed at: the collection is asked what it holds and the names are matched against
    that, so a source whose files are named differently simply matches fewer channels rather than
    producing a page of broken images.
    """
    url = body.index.strip() or (get_setting("livetv_logo_index") or "").strip() or logos.DEFAULT_INDEX
    country = body.country.strip() or (get_setting("livetv_logo_country") or "").strip()
    index = logos.fetch_index(url, prefer=country)
    if not index:
        why = (logos.last_error or "").strip()
        raise HTTPException(502, "That address did not answer with a list of logos LENTA could read."
                                 + (f" The server said: {why}" if why else
                                    " Check the server can reach it."))
    set_setting("livetv_logo_index", url)
    set_setting("livetv_logo_country", country)
    skipped = 0
    wanted: list[tuple[dict, str]] = []
    for ch in livetv.channels():
        if ch.get("logo") and not body.replace:
            skipped += 1
            continue
        link = logos.match(ch["name"], index)
        if link:
            wanted.append((ch, link))

    # A hundred channels means a hundred small files from the far side of the world, each one its own
    # connection. Fetched one after another that is a minute of a person watching a button say
    # "Looking…" and wondering whether it is broken; fetched a few at a time it is a few seconds.
    # The writing stays here on one thread, so SQLite only ever sees the one writer it expects.
    found, failed = 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (ch, _), got in zip(wanted, pool.map(lambda w: logos.download(w[1]), wanted)):
            if not got:
                failed += 1
                continue
            raw, suffix = got
            path = logos.save(ch["name"], raw, suffix)
            with db() as con:
                con.execute("UPDATE tv_channels SET logo = ? WHERE id = ?", (path, ch["id"]))
            found += 1
    log_line = f"{found} matched, {failed} could not be downloaded, {skipped} already had one"
    return {"found": found, "failed": failed, "skipped": skipped, "offered": len(index),
            "summary": log_line}


@read.post("/channels/{cid}/watch")
def watch_channel(cid: int, user: dict = Depends(auth.current_user)) -> dict:
    """Start watching, or join whoever is already on this channel. Returns where to read the stream."""
    ch = livetv.channel(cid)
    if not ch:
        raise HTTPException(404, "That channel is gone.")
    if ch["source_kind"] != "tuner":
        raise HTTPException(400, "Only a tuner channel can be watched this way yet.")
    scanning = {(s.get("config") or {}).get("device") for s in livetv.sources()
                if (s.get("scan") or {}).get("running")}
    try:
        feed, token = livestream.watch(ch, scanning=scanning)
    except livestream.LiveError as ex:
        raise HTTPException(409, str(ex)) from ex
    if not livestream.wait_for_start(feed):
        # Say what FFmpeg said. "Did not start in time" on its own tells nobody anything, and the feed
        # is about to be thrown away along with its account of what went wrong.
        said = [n for n in feed.notes if "ffmpeg -hide_banner" not in n][-4:]
        detail = feed.error or "The channel did not start in time."
        if said:
            detail += "  —  " + " / ".join(n.split("  ", 1)[-1] for n in said)
        livestream.stop(feed.id)
        raise HTTPException(504, detail[-600:])
    return {"id": feed.id, "token": token, "url": f"/api/livetv/live/{feed.id}/index.m3u8",
            "channel": {"id": ch["id"], "name": ch["name"], "number": ch.get("number"),
                        "logo": ch.get("logo")},
            "info": feed.info(admin=bool(user["is_admin"]))}


def _watching(fid: str, token: str):
    """The feed this viewer is actually watching. A feed's id is not a permission (see known_viewer)."""
    f = _feed_for(fid)
    if not livestream.known_viewer(f, token):
        raise HTTPException(404, "That stream is not yours to watch. Choose the channel again.")
    return f


@read.get("/live/{fid}/index.m3u8")
def live_playlist(fid: str, token: str = "", user: dict = Depends(auth.current_user)):
    f = _watching(fid, token)
    livestream.touch(f, token)
    path = f.directory / "index.m3u8"
    if not path.exists():
        raise HTTPException(404, f.error[-400:] if f.error else "The stream is not ready.")
    return PlainTextResponse(_with_token(path.read_text(), token), media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-cache"})


def _with_token(playlist: str, token: str) -> str:
    """Carry the viewer's token onto every segment the playlist names.

    FFmpeg writes bare filenames — `seg00001.ts` — which a player resolves against the playlist's own
    address, **dropping its query string**. So now that a segment actually requires the token (it did
    not before 1.19.0), the token has to be written into the playlist. This is how token-authenticated
    HLS is normally done, and it works for hls.js and for a television's own decoder alike, neither of
    which can be asked to add a header.
    """
    if not token:
        return playlist
    q = f"?token={urllib.parse.quote(token, safe='')}"
    out = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MAP:"):
            out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{m.group(1)}{q}"', line))
        elif line and not line.startswith("#"):
            out.append(line + q)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@router.get("/live/{fid}/log")
def live_log(fid: str, user: dict = Depends(auth.admin_user)) -> dict:
    """What this stream is doing, in FFmpeg's own words. The answer when a channel stutters."""
    f = _feed_for(fid)
    return {"info": f.info(), "lines": list(f.notes)}


@read.get("/live/{fid}/{segment}")
def live_segment(fid: str, segment: str, token: str = "", user: dict = Depends(auth.current_user)):
    if not re.fullmatch(r"seg\d{5}\.(ts|m4s)|init\.mp4", segment):
        raise HTTPException(404, "Unknown segment")
    f = _watching(fid, token)
    livestream.touch(f, token)
    path = f.directory / segment
    if not path.exists():                  # live segments are deleted as the window slides past them
        raise HTTPException(404, "That part of the stream has gone by.")
    media = "video/mp2t" if segment.endswith(".ts") else "video/mp4"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


@read.delete("/live/{fid}")
def live_leave(fid: str, token: str = "", user: dict = Depends(auth.current_user)) -> dict:
    """Stop watching. The tuner is kept for a moment in case this is channel flipping."""
    f = livestream.feed(fid)
    if f:
        livestream.leave(f, token)
    return {"ok": True}


@router.get("/live-failures")
def live_failures(user: dict = Depends(auth.admin_user)) -> dict:
    """The last few channels that would not start, with what FFmpeg said about each."""
    return {"failures": livestream.failures()}


@router.get("/live")
def live_now(user: dict = Depends(auth.admin_user)) -> dict:
    """What is on and which input each one holds — the page shows this before offering to scan."""
    return {"feeds": [f.info() for f in livestream.active()]}


@router.delete("/live/{fid}")
def live_stop(fid: str) -> dict:
    """An admin ending a stream outright, to free the tuner."""
    return {"stopped": livestream.stop(fid)}
