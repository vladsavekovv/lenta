"""Remote control of LENTA's own players: every open LENTA page reports itself, and takes commands.

Asked for by Jarvis (Vlad's voice assistant) on 9 Nov 2026 so that "move it to my tablet" can pause the
film where it was, carry the exact second over, and start it on the next device — on a PC browser, the
phone and tablet app and the Samsung TV app alike, because all of them run the same web client.

**What this answers is LENTA's own ids, states and seconds**, the device names Vlad chose himself and
the language codes of the tracks a file carries. Titles appear only for an assistant key holding the
`titles` scope (assistant.py, 1.26.0); a person's own session and a key without it get ids, as in 1.25.0.
`tests/test_players.py` checks every answer for strings it was not told to expect.

How it works:
  - `POST /api/players/beat` — every open LENTA page, every 2 s while something plays and every 5 s
    while nothing does. It says which device it is and what it is doing, reports the commands it has
    carried out, and takes the ones waiting for it. Held in memory only: nothing is written per beat.
  - `GET /api/now-playing` (find.py) lists the devices of the caller's own account.
  - `POST /api/players/{device_id}/command` — see ACTIONS. A command not collected within
    `COMMAND_TTL` seconds expires, so a stale "pause" cannot reach a tab that wakes an hour later.
    Whoever sent it reads the outcome in the list, under `last_command`, or as a `command` event:
    queued → sent (collected) → running (being carried out, 1.26.2) → done, failed or expired. A page
    gives up on a command after 20 s and reports it failed with `reason: "timeout"`.

The heartbeat runs on its own timer in the client, not the drawing loop that `/api/progress` hangs off:
browsers stop drawing in a hidden tab and in an app sent to the background, which is exactly where a
film keeps playing and where a pause is wanted.
"""
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, events

router = APIRouter(prefix="/api")

DEVICE_TTL = 30          # a device not heard from for this long is no longer listed
COMMAND_TTL = 15         # a command not collected within this long expires
KINDS = ("pc", "android", "tv")
STATES = ("playing", "paused", "buffering", "idle")
# 1.25.0: the first five. 1.26.0: the rest — what the player's own buttons do, for Jarvis.
ACTIONS = ("pause", "play", "seek", "stop", "open",
           "subtitles", "audio", "next", "previous", "skip_intro",
           "volume", "mute", "minimize", "expand", "quality")
QUALITIES = ("original", "1080p", "720p", "480p")       # the same list as player.js and transcode.QUALITIES
DEVICE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")
REASONS = ("timeout",)  # why a command failed, when the page knows (1.26.2)
MAX_DEVICES = 50         # per account; a page that makes up a new id every load cannot grow this forever

_lock = threading.Lock()
_devices: dict[tuple[int, str], dict] = {}       # (user id, device id) → what it last said
_commands: dict[str, dict] = {}                  # command id → the command and what became of it


class Done(BaseModel):
    id: str = Field(max_length=40)
    ok: bool = True
    position_s: float | None = None
    reason: str | None = Field(None, max_length=20)        # 1.26.2: "timeout" — the page gave up waiting


class Beat(BaseModel):
    device_id: str = Field(max_length=64)
    device_kind: str = "pc"
    device_name: str = Field("", max_length=40)
    item_id: int | None = None
    channel_id: int | None = None
    state: str = "idle"
    position_s: float = 0
    duration_s: float = 0
    done: list[Done] = Field(default_factory=list, max_length=20)
    running: list[str] = Field(default_factory=list, max_length=20)    # 1.26.2: commands being carried out now
    # 1.26.0, all optional, so a 1.25.0 page that does not send them is still understood.
    audio: str | None = Field(None, max_length=16)
    audio_options: list[str] = Field(default_factory=list, max_length=40)
    subtitles: str | None = Field(None, max_length=16)
    subtitle_options: list[str] = Field(default_factory=list, max_length=60)
    can_skip_intro: bool = False
    has_next: bool = False
    volume: float | None = None
    muted: bool | None = None
    small: bool | None = None
    quality: str | None = Field(None, max_length=12)
    album_id: int | None = None
    photo_index: int | None = None
    photo_count: int | None = None


class Command(BaseModel):
    action: str
    position_s: float | None = None
    item_id: int | None = None
    channel_id: int | None = None
    language: str | None = Field(None, max_length=16)     # subtitles / audio; "off" switches subtitles off
    level: float | None = None                            # volume, 0 to 1
    muted: bool | None = None
    quality: str | None = Field(None, max_length=12)


def _num(x) -> float:
    return round(max(0.0, float(x or 0)), 1)


def _lang(x) -> str | None:
    return x if isinstance(x, str) and LANG.fullmatch(x) else None


def _langs(xs) -> list[str]:
    out = []
    for x in xs or []:
        x = _lang(x)
        if x and x not in out:
            out.append(x)
    return out


def _summary(did: str, d: dict) -> dict:
    return {"device_id": did, "kind": d["kind"], "name": d["name"], "item_id": d["item_id"],
            "channel_id": d["channel_id"], "album_id": d.get("album_id"), "state": d["state"],
            "position_s": d["position_s"]}


def _command_event(c: dict) -> None:
    events.publish("command", {"command_id": c["id"], "device_id": c["device"], "action": c["action"],
                               "status": c["status"], "reason": c.get("reason"), "position_s": c.get("position_s"),
                               "item_id": c.get("item_id"), "channel_id": c.get("channel_id")},
                   user_id=c["user"])


def _prune(now: float) -> None:
    for k in [k for k, d in _devices.items() if now - d["seen"] > DEVICE_TTL]:
        d = _devices.pop(k, None)
        if d:
            events.publish("player", {**_summary(k[1], d), "state": "gone"}, user_id=k[0])
    for cid, c in list(_commands.items()):
        if c["status"] == "queued" and now - c["at"] > COMMAND_TTL:
            c["status"] = "expired"
            _command_event(c)
        if now - c["at"] > 600:                       # the outcome stays readable for ten minutes
            _commands.pop(cid, None)


def sweep() -> None:
    """Notice devices gone quiet and commands nobody collected (the event stream calls this)."""
    with _lock:
        _prune(time.time())


@router.post("/players/beat")
def beat(body: Beat, user: dict = Depends(auth.current_user)):
    if not DEVICE_ID.fullmatch(body.device_id):
        raise HTTPException(400, "device_id is 8–64 letters, digits, - or _")
    now = time.time()
    key = (user["id"], body.device_id)
    with _lock:
        _prune(now)
        for cid in body.running:
            c = _commands.get(cid)
            if c and c["user"] == user["id"] and c["device"] == body.device_id and c["status"] == "sent":
                c["status"] = "running"
                _command_event(c)
        for d in body.done:
            c = _commands.get(d.id)
            if c and c["user"] == user["id"] and c["device"] == body.device_id and c["status"] in ("sent", "running"):
                c["status"] = "done" if d.ok else "failed"
                c["reason"] = d.reason if (not d.ok and d.reason in REASONS) else None
                c["done_at"] = now
                if d.position_s is not None:
                    c["position_s"] = _num(d.position_s)
                _command_event(c)
        mine = [k for k in _devices if k[0] == user["id"]]
        if key not in _devices and len(mine) >= MAX_DEVICES:
            oldest = min(mine, key=lambda k: _devices[k]["seen"])
            _devices.pop(oldest, None)
        prev = _devices.get(key)
        cur = {
            "kind": body.device_kind if body.device_kind in KINDS else "pc",
            "name": " ".join(body.device_name.split())[:40],
            "item_id": body.item_id, "channel_id": body.channel_id,
            "state": body.state if body.state in STATES else "idle",
            "position_s": _num(body.position_s), "duration_s": _num(body.duration_s),
            "seen": now, "last": (prev or {}).get("last"),
            "audio": _lang(body.audio), "audio_options": _langs(body.audio_options),
            "subtitles": _lang(body.subtitles), "subtitle_options": _langs(body.subtitle_options),
            "can_skip_intro": bool(body.can_skip_intro), "has_next": bool(body.has_next),
            "volume": None if body.volume is None else round(min(1.0, max(0.0, body.volume)), 2),
            "muted": body.muted, "small": body.small,
            "quality": body.quality if body.quality in QUALITIES else None,
            "album_id": body.album_id,
            "photo_index": body.photo_index if body.album_id else None,
            "photo_count": body.photo_count if body.album_id else None,
        }
        _devices[key] = cur
        changed = prev is None or any(prev.get(f) != cur[f] for f in ("state", "item_id", "channel_id",
                                                                       "album_id", "name"))
        out = []
        for cid, c in _commands.items():
            if c["user"] == user["id"] and c["device"] == body.device_id and c["status"] == "queued":
                c["status"], c["sent_at"] = "sent", now
                out.append({k: c[k] for k in ("id", "action", "position_s", "item_id", "channel_id",
                                              "language", "level", "muted", "quality", "target")
                            if c.get(k) is not None})
    if changed:
        events.publish("player", _summary(body.device_id, cur), user_id=user["id"])
    return {"commands": out}


def players_for(user_id: int, user: dict | None = None) -> list[dict]:
    """The caller's own devices, newest first. With a `titles` key (pass `user`), titles as well."""
    now = time.time()
    with _lock:
        _prune(now)
        rows = []
        for (uid, did), d in _devices.items():
            if uid != user_id:
                continue
            last = _commands.get(d.get("last") or "")
            rows.append({
                "device_id": did, "kind": d["kind"], "name": d["name"],
                "item_id": d["item_id"], "channel_id": d["channel_id"], "state": d["state"],
                "position_s": d["position_s"], "duration_s": d["duration_s"],
                "seen_s": round(now - d["seen"], 1),
                "last_command": None if not last else {
                    "id": last["id"], "action": last["action"], "status": last["status"],
                    "reason": last.get("reason"), "position_s": last.get("position_s")},
                # 1.26.0
                "audio": d.get("audio"), "audio_options": d.get("audio_options") or [],
                "subtitles": d.get("subtitles"), "subtitle_options": d.get("subtitle_options") or [],
                "can_skip_intro": bool(d.get("can_skip_intro")), "has_next": bool(d.get("has_next")),
                "volume": d.get("volume"), "muted": d.get("muted"), "small": d.get("small"),
                "quality": d.get("quality"), "album_id": d.get("album_id"),
                "photo_index": d.get("photo_index"), "photo_count": d.get("photo_count"),
            })
    rows.sort(key=lambda r: r["seen_s"])
    if user is not None:
        from .assistant import channel_names, describe, has
        if has(user, "titles"):
            names = describe([r["item_id"] for r in rows] + [r["album_id"] for r in rows], user)
            chans = channel_names([r["channel_id"] for r in rows])
            for r in rows:
                if r["item_id"] in names:
                    r.update(names[r["item_id"]])
                if r["album_id"] in names:
                    r["album_title"] = names[r["album_id"]]["title"]
                if r["channel_id"] in chans:
                    r["channel_name"] = chans[r["channel_id"]]
    return rows


def _album(item_id: int, user: dict) -> int | None:
    """A photo album this account may see, with at least one photo in it."""
    from .db import db
    with db() as con:
        row = con.execute("SELECT id, kind, library_id FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row or row["kind"] != "photoalbum":
            return None
        try:
            auth.check_item_access(user, row["library_id"])
        except HTTPException:
            return None
        if not con.execute("SELECT 1 FROM items WHERE parent_id = ? AND kind = 'photo' LIMIT 1",
                           (item_id,)).fetchone():
            return None
    return row["id"]


def _channel(channel_id: int, user: dict) -> int | None:
    """A live channel this account may watch: it has a live TV library and the channel is not hidden."""
    from . import livetv
    try:
        auth.live_tv_viewer(user)
    except HTTPException:
        return None
    return channel_id if any(c["id"] == channel_id for c in livetv.channels(include_hidden=False)) else None


@router.post("/players/{device_id}/command")
def command(device_id: str, body: Command, user: dict = Depends(auth.current_user)):
    if body.action not in ACTIONS:
        raise HTTPException(400, f"action is one of: {', '.join(ACTIONS)}")
    if body.action == "seek" and (body.position_s is None or body.position_s < 0):
        raise HTTPException(400, "seek needs position_s")
    fields: dict = {"position_s": _num(body.position_s) if body.position_s is not None else None}
    if body.action == "open":
        if (body.item_id is None) == (body.channel_id is None):
            raise HTTPException(400, "open needs item_id or channel_id (one of them)")
        if body.channel_id is not None:
            cid = _channel(body.channel_id, user)
            if cid is None:
                raise HTTPException(404, "Not playable")
            fields.update(channel_id=cid, target="channel", position_s=None)
        elif (album := _album(body.item_id, user)) is not None:
            fields.update(item_id=album, target="photos", position_s=None)
        else:
            # The same question `/playable` answers: may this account play it, and what is played. A show
            # opens its next episode. Missing and forbidden look the same, as they do there.
            from .find import playable
            p = playable(body.item_id, user)
            if not p["playable"]:
                raise HTTPException(404, "Not playable")
            fields.update(item_id=p["item_id"], target="item")
    elif body.action in ("subtitles", "audio"):
        lang = (body.language or "").strip()
        if body.action == "subtitles" and lang.lower() == "off":
            fields["language"] = "off"
        elif _lang(lang):
            fields["language"] = lang
        else:
            raise HTTPException(400, f"{body.action} needs language, a code such as bg or en"
                                     + (", or off" if body.action == "subtitles" else ""))
    elif body.action == "volume":
        if body.level is None or not 0 <= body.level <= 1:
            raise HTTPException(400, "volume needs level, from 0 to 1")
        fields["level"] = round(body.level, 2)
    elif body.action == "mute":
        if body.muted is None:
            raise HTTPException(400, "mute needs muted: true or false")
        fields["muted"] = body.muted
    elif body.action == "quality":
        if body.quality not in QUALITIES:
            raise HTTPException(400, f"quality is one of: {', '.join(QUALITIES)}")
        fields["quality"] = body.quality
    now = time.time()
    with _lock:
        _prune(now)
        dev = _devices.get((user["id"], device_id))
        if not dev:
            raise HTTPException(404, "That device is not connected")
        cid = secrets.token_urlsafe(9)
        _commands[cid] = {"id": cid, "user": user["id"], "device": device_id, "action": body.action,
                          **fields, "status": "queued", "at": now}
        dev["last"] = cid
        queued = dict(_commands[cid])
    # The page listening on the event stream checks in the moment it hears this (remote.js), instead of
    # at its next beat. The beat stays the way the command is delivered: this only says "now".
    _command_event(queued)
    return {"command_id": cid, "device_id": device_id, "action": body.action}
