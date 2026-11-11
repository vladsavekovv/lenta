"""Remote control of LENTA's players: every open page reports itself and takes commands (1.25.0).

Asked for by Jarvis (Vlad's voice assistant) on 9 Nov 2026, with Vlad's go: "move it to my tablet" left
the film playing on the phone, because nothing could reach a LENTA player to pause it. Read
`server/lenta/players.py` for the design. What this file holds:

  1. the interface itself, over HTTP: a page checks in, a command waits for it, the outcome — with the
     second the film was at — comes back under `last_command`;
  2. the rules: one account never sees or commands another's devices; `open` refuses a title the
     account may not play; nothing in an answer is a title; a stale command expires; a silent device
     drops off the list;
  3. **two real pages acting as two devices**, driven only through the interface, the way Jarvis will:
     pause the film on one (and the position is saved), open it on the other at that second, seek,
     stop, resume — **and the time each one took, measured**;
  4. how a page says which device it is: Android app, TV mode, PC, and the name set in Settings;
  5. that what existed before still behaves the same: `/api/now-playing` keeps `item_id` and `state`.

Needs the test server (/tmp/claude-0/intro/uplive.sh; `tools/make-test-env.py` recreates it). Set
`LENTA_TEST_BASE` to point it at another server.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("LENTA_TEST_BASE", "http://127.0.0.1:8597")
DB = Path(os.environ.get("LENTA_TEST_DB", "/tmp/claude-0/intro/data/lenta.db"))

fails = []
timings = {}


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


class Client:
    """A signed-in account, the way Jarvis talks to LENTA. Every answer read defensively: on an older
    server these endpoints do not exist, and that must show as red checks, not a traceback."""

    def __init__(self, user):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.ok = self.call("POST", "/api/auth/login", {"username": user, "password": "secret123"})[0] == 200

    def call(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=30) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            return e.code, {}
        except Exception:                                    # noqa: BLE001 — a dead server is a red check
            return 0, {}

    def players(self):
        return self.call("GET", "/api/now-playing")[1].get("players") or []

    def device(self, device_id):
        return next((p for p in self.players() if p.get("device_id") == device_id), None)


if BASE.endswith(":8597"):
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

admin, guest = Client("admin"), Client("guest")
print("1. a page checks in, a command waits for it, the outcome comes back")
check("both accounts sign in", admin.ok and guest.ok)
code, _ = admin.call("POST", "/api/players/beat", {"device_id": "bad id!", "state": "idle"})
check(f"a device id that is not letters and digits is refused ({code})", code in (400, 422))
code, r = admin.call("POST", "/api/players/beat", {"device_id": "testpc000001", "device_kind": "pc",
                                                   "device_name": "Test PC", "state": "idle"})
check(f"a page checks in ({code})", code == 200 and r.get("commands") == [])
me = admin.device("testpc000001")
check(f"it is in the account's list ({me})", bool(me) and me["kind"] == "pc" and me["name"] == "Test PC"
      and me["state"] == "idle")
code, _ = admin.call("POST", "/api/players/nosuchdevice01/command", {"action": "pause"})
check(f"a command to a device that is not connected is refused ({code})", code == 404)
code, _ = admin.call("POST", "/api/players/testpc000001/command", {"action": "jump"})
check(f"an action that does not exist is refused ({code})", code in (400, 422))
code, _ = admin.call("POST", "/api/players/testpc000001/command", {"action": "seek"})
check(f"seek without a position is refused ({code})", code in (400, 422))
code, sent = admin.call("POST", "/api/players/testpc000001/command", {"action": "pause"})
cid = sent.get("command_id")
check(f"a pause is queued ({code})", code == 200 and bool(cid))
check("and the list says it is waiting",
      ((admin.device("testpc000001") or {}).get("last_command") or {}).get("status") == "queued")
_, r = admin.call("POST", "/api/players/beat", {"device_id": "testpc000001", "state": "playing", "item_id": 1,
                                                "position_s": 100})
check(f"the page collects it on its next check-in ({r.get('commands')})",
      [c.get("id") for c in r.get("commands", [])] == [cid] and r["commands"][0].get("action") == "pause")
_, r = admin.call("POST", "/api/players/beat", {"device_id": "testpc000001", "state": "playing", "item_id": 1,
                                                "position_s": 101})
check("and only once", r.get("commands") == [])
admin.call("POST", "/api/players/beat", {"device_id": "testpc000001", "state": "paused", "item_id": 1,
                                         "position_s": 101.37, "done": [{"id": cid, "ok": True, "position_s": 101.37}]})
last = (admin.device("testpc000001") or {}).get("last_command") or {}
check(f"the outcome is in the list, with the second it was done at ({last})",
      last.get("status") == "done" and last.get("action") == "pause" and last.get("position_s") == 101.4)

print("\n2. the rules")
QUICK = os.environ.get("QUICK") == "1"
check("another account does not see this device", guest.device("testpc000001") is None)
code, _ = guest.call("POST", "/api/players/testpc000001/command", {"action": "pause"})
check(f"and cannot command it ({code})", code == 404)
code, _ = admin.call("POST", "/api/players/beat", {"device_id": "x" * 65, "state": "idle"})
check(f"a device id longer than 64 is refused ({code})", code in (400, 422))
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
guest_id = con.execute("SELECT id FROM users WHERE username = 'guest'").fetchone()["id"]
hidden = con.execute(
    "SELECT i.id FROM items i JOIN files f ON f.item_id = i.id WHERE i.kind = 'movie' AND i.library_id NOT IN "
    "(SELECT library_id FROM user_libraries WHERE user_id = ?) LIMIT 1", (guest_id,)).fetchone()
guest.call("POST", "/api/players/beat", {"device_id": "testguest001", "state": "idle"})
if hidden:
    code, _ = guest.call("POST", "/api/players/testguest001/command", {"action": "open", "item_id": hidden["id"]})
    check(f"open refuses a title this account may not play ({code})", code == 404)
code, _ = admin.call("POST", "/api/players/testpc000001/command", {"action": "open", "item_id": 987654321})
check(f"open refuses a title that does not exist ({code})", code == 404)
show = con.execute("SELECT id FROM items WHERE kind = 'show' LIMIT 1").fetchone()
if show:
    code, r = admin.call("POST", "/api/players/testpc000001/command", {"action": "open", "item_id": show["id"]})
    _, got = admin.call("POST", "/api/players/beat", {"device_id": "testpc000001", "state": "idle"})
    ep = (got.get("commands") or [{}])[0].get("item_id")
    kind = con.execute("SELECT kind FROM items WHERE id = ?", (ep or 0,)).fetchone()
    check(f"open on a show opens its next episode ({ep})", code == 200 and kind and kind["kind"] == "episode")
titles = {r[0] for r in con.execute("SELECT title FROM items WHERE title IS NOT NULL AND length(title) > 3")}
answer = admin.call("GET", "/api/now-playing")[1]
text = json.dumps(answer, ensure_ascii=False)
check("no title of anything in the library appears in the list", not any(t in text for t in titles))
# 1.26.0 adds, on purpose: the language codes of the tracks on offer and in use, whether an intro can be
# skipped now, whether there is a next episode, volume, size and quality, and which photo is shown. No
# titles — those come only to an assistant key with the `titles` scope (test_assistant.py).
KEYS = {"device_id", "kind", "name", "item_id", "channel_id", "state", "position_s", "duration_s", "seen_s",
        "last_command", "audio", "audio_options", "subtitles", "subtitle_options", "can_skip_intro", "has_next",
        "volume", "muted", "small", "quality", "album_id", "photo_index", "photo_count"}
check(f"each device carries only the agreed keys", all(set(p) == KEYS for p in answer.get("players", [])))
check("the top of the answer keeps its meaning (item_id, state)", "item_id" in answer and "state" in answer)
# A stale command expires, and a silent device drops off. One wait covers both.
if QUICK:
    print("  skip  the 47 s of waiting (QUICK=1)")
admin.call("POST", "/api/players/beat", {"device_id": "testquiet001", "state": "idle"})
_, stale = admin.call("POST", "/api/players/testquiet001/command", {"action": "pause"})
print("  …  waiting 16 s for a command nobody collects")
time.sleep(0 if QUICK else 16)
last = (admin.device("testquiet001") or {}).get("last_command") or {}
check(f"a command not collected within 15 s expires ({last.get('status')})", last.get("status") == "expired")
_, r = admin.call("POST", "/api/players/beat", {"device_id": "testquiet001", "state": "idle"})
check("and is never delivered after that", r.get("commands") == [])
print("  …  waiting 31 s with the device silent")
time.sleep(0 if QUICK else 31)
check("a device silent for 30 s is no longer listed", admin.device("testquiet001") is None)

print("\n3. two real pages as two devices, driven only through the interface")
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
    print("  skip  playwright is not installed here")

film = con.execute(
    "SELECT i.id FROM items i JOIN files f ON f.item_id = i.id WHERE i.kind = 'movie' "
    "ORDER BY i.id LIMIT 1").fetchone()


async def wait_for(cond, limit=20.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < limit:
        if await cond():
            return time.time() - t0
        await asyncio.sleep(step)
    return None


async def two_devices():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"])
        pages = {}
        for name, ua in (("PC test", None), ("Tablet test", None)):
            ctx = await b.new_context(user_agent=ua) if ua else await b.new_context()
            await ctx.add_init_script(f"try {{ localStorage.setItem('lenta.deviceName', {json.dumps(name)}) }} catch (e) {{}}")
            pg = await ctx.new_page()
            await pg.goto(BASE, wait_until="load")
            await pg.click('[data-user="admin"]')
            if await pg.query_selector("#lp"):
                await pg.fill("#lp", "secret123")
                await pg.click("#login button.primary")
            await pg.wait_for_selector(".card", timeout=45000)
            pages[name] = pg
        pc, tab = pages["PC test"], pages["Tablet test"]
        ids = {}
        for name, pg in pages.items():
            await pg.wait_for_function("!!localStorage.getItem('lenta.device')", timeout=10000)
            ids[name] = await pg.evaluate("localStorage.getItem('lenta.device')")

        async def dev(name):
            return admin.device(ids[name]) or {}

        listed = await wait_for(lambda: _async(lambda: bool(admin.device(ids["PC test"])) and bool(admin.device(ids["Tablet test"]))), 12)
        out = {"listed": listed, "ids": ids}
        out["names"] = (admin.device(ids["PC test"]) or {}).get("name"), (admin.device(ids["Tablet test"]) or {}).get("name")
        # The film plays on the PC.
        await pc.evaluate(f"location.hash = '#/play/{film['id']}'")
        playing = await wait_for(lambda: pc.evaluate("(() => { const v = document.querySelector('#pv'); return !!v && !v.paused && v.currentTime > 18 })()"), 60)
        out["pc_playing"] = playing is not None
        out["reported_playing"] = await wait_for(lambda: _async(lambda: (admin.device(ids["PC test"]) or {}).get("state") == "playing"
                                                                and (admin.device(ids["PC test"]) or {}).get("item_id") == film["id"]), 8)
        # Pause it there, the way Jarvis will.
        t0 = time.time()
        _, c = admin.call("POST", f"/api/players/{ids['PC test']}/command", {"action": "pause"})
        out["pause_acted"] = await wait_for(lambda: pc.evaluate("(() => { const v = document.querySelector('#pv'); return !!v && v.paused })()"), 15)
        out["pause_confirmed"] = await wait_for(lambda: _async(lambda: ((admin.device(ids["PC test"]) or {}).get("last_command") or {}).get("status") == "done"), 15)
        last = (admin.device(ids["PC test"]) or {}).get("last_command") or {}
        out["pause_last"] = last
        out["pc_time"] = await pc.evaluate("(() => { const v = document.querySelector('#pv'); return v ? v.currentTime : null })()")
        await asyncio.sleep(1.5)
        row = sqlite3.connect(DB).execute("SELECT position FROM watch_state ws JOIN users u ON u.id = ws.user_id "
                                          "WHERE u.username = 'admin' AND ws.item_id = ?", (film["id"],)).fetchone()
        out["saved"] = row[0] if row else None
        # Carry it to the tablet at that second.
        at = last.get("position_s") or 0
        _, c = admin.call("POST", f"/api/players/{ids['Tablet test']}/command",
                          {"action": "open", "item_id": film["id"], "position_s": at})
        out["open_code"] = bool(c.get("command_id"))
        out["open_acted"] = await wait_for(lambda: tab.evaluate("(() => { const v = document.querySelector('#pv'); return !!v && !v.paused && v.currentTime > 0 })()"), 40)
        out["tab_reported"] = await wait_for(lambda: _async(lambda: (admin.device(ids["Tablet test"]) or {}).get("item_id") == film["id"]
                                                            and (admin.device(ids["Tablet test"]) or {}).get("state") == "playing"), 10)
        out["tab_at"] = (admin.device(ids["Tablet test"]) or {}).get("position_s")
        out["carried_from"] = at
        # Seek on the tablet.
        _, c = admin.call("POST", f"/api/players/{ids['Tablet test']}/command", {"action": "seek", "position_s": 40})
        out["seek_acted"] = await wait_for(lambda: _async(lambda: abs(((admin.device(ids["Tablet test"]) or {}).get("position_s") or 0) - 40) < 6), 25)
        # Stop it there.
        _, c = admin.call("POST", f"/api/players/{ids['Tablet test']}/command", {"action": "stop"})
        out["stop_acted"] = await wait_for(lambda: tab.evaluate("!document.querySelector('#pv')"), 15)
        out["stop_last"] = await wait_for(lambda: _async(lambda: ((admin.device(ids["Tablet test"]) or {}).get("last_command") or {}).get("status") == "done"), 15)
        out["stop_position"] = ((admin.device(ids["Tablet test"]) or {}).get("last_command") or {}).get("position_s")
        out["tab_idle"] = await wait_for(lambda: _async(lambda: (admin.device(ids["Tablet test"]) or {}).get("state") == "idle"), 8)
        # Resume on the PC.
        _, c = admin.call("POST", f"/api/players/{ids['PC test']}/command", {"action": "play"})
        out["play_acted"] = await wait_for(lambda: pc.evaluate("(() => { const v = document.querySelector('#pv'); return !!v && !v.paused })()"), 15)
        # The page has a hidden tab's timers: the heartbeat must not depend on drawing. A page whose
        # drawing is stopped (requestAnimationFrame never fires) must still check in and obey.
        await pc.evaluate("window.requestAnimationFrame = () => 0")
        _, c = admin.call("POST", f"/api/players/{ids['PC test']}/command", {"action": "pause"})
        out["pause_without_drawing"] = await wait_for(lambda: pc.evaluate("(() => { const v = document.querySelector('#pv'); return !!v && v.paused })()"), 15)
        out["errors"] = []
        await b.close()
        return out


async def _async(fn):
    return fn()


async def device_kinds():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--disable-dev-shm-usage"])
        found = {}
        for label, url, ua in (
                ("android", BASE, "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/130.0 Mobile Safari/537.36 LENTA-Android/1.2.0"),
                ("tablet", BASE, "Mozilla/5.0 (Linux; Android 14; SM-X200) AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/130.0 Safari/537.36 LENTA-Android/1.2.0"),
                ("tv", BASE + "/?tv=1", None), ("pc", BASE, None)):
            ctx = await b.new_context(user_agent=ua) if ua else await b.new_context()
            pg = await ctx.new_page()
            await pg.goto(url, wait_until="load")
            await pg.click('[data-user="admin"]')
            if await pg.query_selector("#lp"):
                await pg.fill("#lp", "secret123")
                await pg.click("#login button.primary")
            await pg.wait_for_function("!!localStorage.getItem('lenta.device')", timeout=15000)
            did = await pg.evaluate("localStorage.getItem('lenta.device')")
            d = None
            for _ in range(20):
                d = admin.device(did or "")
                if d:
                    break
                await asyncio.sleep(0.5)
            found[label] = (d or {}).get("kind"), (d or {}).get("name")
            if label == "pc":
                # The name set in Settings › Media pages › This device reaches the list.
                await pg.evaluate("location.hash = '#/settings/media-pages'")
                await pg.wait_for_timeout(2000)
                box = await pg.query_selector("#p-devname")
                if box:
                    await box.fill("Study PC")
                    for _ in range(16):
                        if (admin.device(did) or {}).get("name") == "Study PC":
                            break
                        await asyncio.sleep(0.5)
                found["named"] = (admin.device(did) or {}).get("name"), bool(box)
                await pg.reload(wait_until="load")
                await pg.wait_for_timeout(1500)
                found["after_reload"] = await pg.evaluate("localStorage.getItem('lenta.deviceName')"), \
                    await pg.evaluate("localStorage.getItem('lenta.device')") == did
            await ctx.close()
        await b.close()
        return found


if async_playwright and film:
    # A page with no remote control (1.24.1 and older) never makes a device id, and the browser half
    # would end in a Playwright timeout. Report that as the red checks it is, not as a traceback.
    try:
        r = asyncio.run(two_devices())
    except Exception as e:
        print(f"  (the two-device run could not go on: {str(e).splitlines()[0][:120]})")
        import collections
        r = collections.defaultdict(lambda: None, pause_last={})
    check(f"both pages are listed as devices ({r['listed'] and round(r['listed'], 1)} s)", r["listed"] is not None)
    check(f"each under the name set for it ({r['names']})", r["names"] == ("PC test", "Tablet test"))
    check("the film plays on the PC", r["pc_playing"])
    check("and the list says so, with the film's id", r["reported_playing"] is not None)
    check(f"pause: the PC's film stops ({r['pause_acted'] and round(r['pause_acted'], 2)} s after the command)",
          r["pause_acted"] is not None and r["pause_acted"] < 3)
    check(f"and the list confirms it ({r['pause_confirmed'] and round(r['pause_confirmed'], 2)} s)",
          r["pause_confirmed"] is not None)
    lp = r["pause_last"].get("position_s")
    check(f"with the second it stopped at ({lp} s; the page itself stopped at {r['pc_time']})",
          lp is not None and r["pc_time"] is not None and abs(lp - r["pc_time"]) < 1.5)
    check(f"and the position is saved for the account ({r['saved']})",
          r["saved"] is not None and lp is not None and abs(r["saved"] - lp) < 2)
    check("open on the tablet is accepted", r["open_code"])
    check(f"the film starts on the tablet ({r['open_acted'] and round(r['open_acted'], 2)} s, incl. the server starting the stream)",
          r["open_acted"] is not None)
    check(f"at the second it left the PC ({r['tab_at']} against {r['carried_from']})",
          r["tab_at"] is not None and abs(r["tab_at"] - r["carried_from"]) < 8)
    check(f"seek on the tablet moves it ({r['seek_acted'] and round(r['seek_acted'], 2)} s)", r["seek_acted"] is not None)
    check(f"stop closes the tablet's player ({r['stop_acted'] and round(r['stop_acted'], 2)} s)", r["stop_acted"] is not None)
    check(f"and reports the second it stopped at ({r['stop_position']})", r["stop_last"] is not None and r["stop_position"])
    check("the tablet then reads as idle", r["tab_idle"] is not None)
    check(f"play resumes the PC ({r['play_acted'] and round(r['play_acted'], 2)} s)", r["play_acted"] is not None)
    check(f"a page that has stopped drawing still obeys ({r['pause_without_drawing'] and round(r['pause_without_drawing'], 2)} s)",
          r["pause_without_drawing"] is not None)
    timings.update({k: round(v, 2) for k, v in r.items() if k in ("pause_acted", "pause_confirmed", "open_acted",
                                                                   "seek_acted", "stop_acted", "play_acted",
                                                                   "pause_without_drawing") and v is not None})

    print("\n4. how a page says which device it is")
    try:
        k = asyncio.run(device_kinds())
    except Exception as e:
        print(f"  (the device-kind run could not go on: {str(e).splitlines()[0][:120]})")
        k = {}
    check(f"LENTA's Android app on a phone ({k.get('android')})", k.get("android") == ("android", "Android phone"))
    check(f"LENTA's Android app on a tablet ({k.get('tablet')})", k.get("tablet") == ("android", "Android tablet"))
    check(f"a television ({k.get('tv')})", (k.get("tv") or (None,))[0] == "tv")
    check(f"a browser ({k.get('pc')})", k.get("pc") == ("pc", "PC"))
    check(f"a name typed in Settings reaches the list ({k.get('named')})", (k.get("named") or (None,))[0] == "Study PC")
    check(f"and it and the device id survive a reload ({k.get('after_reload')})", k.get("after_reload") == ("Study PC", True))

print("\n5. what existed before behaves the same")
code, r = admin.call("GET", "/api/now-playing")
check("now-playing still answers item_id and state", code == 200 and "item_id" in r and "state" in r)
src = (ROOT / "web/js/remote.js").read_text() if (ROOT / "web/js/remote.js").exists() else ""
check("the heartbeat runs on its own timer, not the drawing loop",
      "setTimeout(beat" in src and "requestAnimationFrame" not in src)
check("a failed check-in never throws into the page", "catch {" in src)

if timings:
    print("\nmeasured, command to effect (s):", json.dumps(timings))
print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
