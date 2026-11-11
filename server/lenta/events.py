"""What happens in LENTA, as it happens, for a program that wants to know without asking (1.26.0).

`GET /api/events` is a server-sent event stream: one long HTTP answer, one event per message. Chosen
over a webhook because nothing has to go *out* of LENTA — no address of Jarvis's to keep, no secret to
share, nothing for `safefetch` to allow — and because a program that restarts simply connects again.

Every event has an `id`. Reconnecting with `Last-Event-ID` (the header every SSE client sends by itself)
or `?since=` replays what was missed from the last `KEEP` events. A comment line every `PING` seconds
keeps proxies from closing a quiet stream.

Types:
  player    a device appeared, went, or changed what it is doing (players.py)
  command   a command's outcome: done, failed or expired
  finished  a film, episode or book was watched to the end
  added     titles new to a library after a scan (ids; a show's new episodes carry the show's id)
  scan      a scan finished or failed — administrators' keys only

Each event is for one account (`player`, `command`, `finished`) or for whoever may see that library
(`added`, `scan`). A key with the `titles` scope gets titles in them, as everywhere else.
"""
import asyncio
import collections
import json
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from . import auth

router = APIRouter(prefix="/api")

TYPES = ("player", "command", "finished", "added", "scan")
ADMIN_TYPES = ("scan",)
KEEP = 1000            # events kept for replay; at a beat every 2 s that is a long evening of changes
PING = 15              # seconds between keep-alive comments
POLL = 0.25            # seconds between looks at the buffer

_lock = threading.Lock()
_buffer: collections.deque = collections.deque(maxlen=KEEP)
_next = 1


def publish(kind: str, data: dict, user_id: int | None = None, library_id: int | None = None) -> None:
    """Record one event. Safe from any thread; never raises — an event is never worth a failed request."""
    global _next
    try:
        with _lock:
            _buffer.append({"id": _next, "type": kind, "at": round(time.time(), 2),
                            "user": user_id, "library": library_id, "data": data})
            _next += 1
    except Exception:
        pass


def last_id() -> int:
    with _lock:
        return _next - 1


def _since(after: int) -> list[dict]:
    with _lock:
        return [e for e in _buffer if e["id"] > after]


def _visible(e: dict, user: dict, allowed: list[int] | None, admin_ok: bool) -> bool:
    if e["type"] in ADMIN_TYPES and not admin_ok:
        return False
    if e["user"] is not None and e["user"] != user["id"]:
        return False
    if e["library"] is not None and allowed is not None and e["library"] not in allowed:
        return False
    return True


def _with_titles(e: dict, user: dict) -> dict:
    from .assistant import channel_names, describe, has
    data = dict(e["data"])
    if not has(user, "titles"):
        return data
    ids = ([data.get("item_id")] + list(data.get("item_ids") or []) + list(data.get("show_ids") or [])
           + list(data.get("episode_ids") or []))
    names = describe(ids, user)
    if data.get("item_id") in names:
        data.update(names[data["item_id"]])
    if data.get("item_ids"):
        data["titles"] = {str(i): names[i] for i in data["item_ids"] if i in names}
    if data.get("episode_ids"):
        data["episode_titles"] = {str(i): names[i] for i in data["episode_ids"] if i in names}
    if data.get("show_ids"):
        data["show_titles"] = {str(i): names[i]["title"] for i in data["show_ids"] if i in names}
    if data.get("channel_id"):
        data["channel_name"] = channel_names([data["channel_id"]]).get(data["channel_id"])
    return data


def _frame(e: dict, data: dict) -> str:
    body = json.dumps({"type": e["type"], "at": e["at"], **data}, separators=(",", ":"))
    return f"id: {e['id']}\nevent: {e['type']}\ndata: {body}\n\n"


@router.get("/events")
async def stream(request: Request, since: int | None = None, user: dict = Depends(auth.current_user)):
    from .assistant import has
    key = user.get("key")
    admin_ok = bool(user["is_admin"]) and (not key or has(user, "admin"))
    allowed = auth.allowed_libraries(user)
    try:
        after = int(request.headers.get("last-event-id") or (since if since is not None else last_id()))
    except ValueError:
        after = last_id()

    async def gen():
        nonlocal after
        from . import players
        yield f"retry: 3000\n: LENTA events, from id {after}\n\n"
        quiet = swept = time.time()
        while True:
            if await request.is_disconnected():
                return
            if time.time() - swept >= 1:                       # a device that went silent is an event too
                swept = time.time()
                await asyncio.to_thread(players.sweep)
            for e in _since(after):
                after = e["id"]
                if _visible(e, user, allowed, admin_ok):
                    data = await asyncio.to_thread(_with_titles, e, user)
                    yield _frame(e, data)
                    quiet = time.time()
            if time.time() - quiet >= PING:
                yield ": keep-alive\n\n"
                quiet = time.time()
            await asyncio.sleep(POLL)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
