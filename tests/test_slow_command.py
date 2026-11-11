"""A slow command must not stop a page's check-ins (1.26.2).

Jarvis, 9 Nov 2026 ~20:00, in `Homelab/LENTA media server`: "English subtitles" — the subtitles
appeared on the PC, but the command stayed `sent` for over 8 s, no outcome ever came, and 30 s later
the PC had dropped off `/api/now-playing` while the film played on. Its reading of the code was right:
`remote.js` ran each command *inside* the check-in that collected it, so a command that waited — a
subtitle track the server extracts on demand, or a picture subtitle burned into a new stream — held
every check-in after it.

This test does exactly what Jarvis asked for: a page plays an episode, the `.vtt` of its English
subtitles is made to answer only after 40 s, and Jarvis's command for them is sent. Throughout:
  - the device stays on `/api/now-playing`, checking in every couple of seconds, its position moving;
  - the command goes `sent` → `running` → `failed` with reason `timeout` at about 20 s, with the second
    the film was at — in the list and as `command` events;
  - a pause sent while the subtitles are still loading is carried out at once, not after them.
On 1.26.1 the device drops off the list and the command never ends.
"""
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = os.environ.get("LENTA_TEST_BASE", "http://127.0.0.1:8597")
DB = Path(os.environ.get("LENTA_TEST_DB", "/tmp/claude-0/intro/data/lenta.db"))
MEDIA = Path("/tmp/claude-0/intro/media-slowcmd")
SLOW = 40                 # seconds the .vtt is held back
LIMIT = 20                # the page's own limit for one command

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def http(method, path, body=None, headers=None, opener=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:                                   # noqa: BLE001
        return 0, {}


if BASE.endswith(":8597"):
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

admin = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
signed = http("POST", "/api/auth/login", {"username": "admin", "password": "secret123"}, opener=admin)[0] == 200
check("the administrator signs in", signed)
call = lambda m, p, b=None: http(m, p, b, opener=admin)                 # noqa: E731
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# A key, as Jarvis has one; on an older LENTA there are none, and the session does the same job.
code, made = call("POST", "/api/admin/assistant-keys", {"name": "slow command test", "scopes": ["watch"]})
KEY = made.get("key")
jarvis = (lambda m, p, b=None: http(m, p, b, headers={"Authorization": f"Bearer {KEY}"})) if KEY else call

# ---- a one-episode library with English and Bulgarian subtitles, made here and removed at the end
FF = shutil.which("ffmpeg")
shutil.rmtree(MEDIA, ignore_errors=True)
season = MEDIA / "Slow Command Show/Season 1"
season.mkdir(parents=True)
subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=120", "-f", "lavfi",
                "-i", "sine=f=440:d=120", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(season / "Slow Command Show S01E01.mkv")], check=True)
for lang, line in (("en", "Hello"), ("bg", "Здравей")):
    (season / f"Slow Command Show S01E01.{lang}.srt").write_text(
        f"1\n00:00:01,000 --> 00:01:59,000\n{line}\n", encoding="utf-8")
code, lib = call("POST", "/api/admin/libraries", {"name": "Slow command", "kind": "shows", "paths": [str(MEDIA)]})
lib_id = lib.get("id")
if lib_id:
    call("POST", f"/api/admin/libraries/{lib_id}/scan")
ep = None
for _ in range(90):
    row = con.execute("SELECT id FROM items WHERE kind = 'episode' AND library_id = ?", (lib_id or 0,)).fetchone()
    if row and con.execute("SELECT 1 FROM files WHERE item_id = ?", (row["id"],)).fetchone():
        ep = row["id"]
        break
    time.sleep(1)
check(f"the test episode is scanned ({ep})", bool(ep))


class Stream:
    def __init__(self):
        self.events = []
        headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
        try:
            self.resp = urllib.request.urlopen(urllib.request.Request(BASE + "/api/events", headers=headers), timeout=90)
        except Exception:                               # noqa: BLE001 — an older LENTA has no stream
            self.resp = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.resp:
                line = line.decode().strip()
                if line.startswith("data:"):
                    try:
                        self.events.append(json.loads(line[5:]))
                    except ValueError:
                        pass
        except Exception:                               # noqa: BLE001
            pass


async def run():
    from playwright.async_api import async_playwright
    out = {"samples": []}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await (await b.new_context(viewport={"width": 1280, "height": 720})).new_page()
        await pg.goto(BASE + "/", wait_until="load")
        await pg.click('[data-user="admin"]')
        if await pg.query_selector("#lp"):
            await pg.fill("#lp", "secret123")
            await pg.click("#login button.primary")
        await pg.wait_for_selector(".card", timeout=45000)
        await pg.wait_for_function("!!localStorage.getItem('lenta.device')", timeout=15000)
        did = await pg.evaluate("localStorage.getItem('lenta.device')")

        def me():
            return next((d for d in jarvis("GET", "/api/now-playing")[1].get("players", [])
                         if d.get("device_id") == did), None)

        # Every subtitle file answers only after SLOW seconds, as a slow extraction would (English is the
        # only one asked for).
        async def slow(route):
            await asyncio.sleep(SLOW)
            try:
                await route.continue_()
            except Exception:                           # noqa: BLE001 — the page may have gone
                pass
        await pg.route("**/api/subs/**", slow)

        for _ in range(40):
            if await asyncio.to_thread(me):
                break
            await asyncio.sleep(0.5)
        jarvis("POST", f"/api/players/{did}/command", {"action": "open", "item_id": ep})
        t0 = time.time()
        while time.time() - t0 < 40:
            d = await asyncio.to_thread(me)
            if d and d.get("item_id") == ep and d.get("state") == "playing" and (d.get("position_s") or 0) > 1:
                break
            await asyncio.sleep(0.3)
        out["playing"] = bool(d and d.get("state") == "playing")
        out["offers"] = (d or {}).get("subtitle_options")
        stream = Stream()
        await asyncio.sleep(0.5)

        _, r = jarvis("POST", f"/api/players/{did}/command", {"action": "subtitles", "language": "en"})
        cid = r.get("command_id")
        sent = time.time()
        pause_done = None
        paused_id = None
        while time.time() - sent < SLOW + 6:
            d = await asyncio.to_thread(me)
            t = round(time.time() - sent, 1)
            lc = (d or {}).get("last_command") or {}
            out["samples"].append({"t": t, "here": bool(d), "seen_s": (d or {}).get("seen_s"),
                                   "pos": (d or {}).get("position_s"), "state": (d or {}).get("state"),
                                   "cmd": lc.get("id"), "status": lc.get("status"), "reason": lc.get("reason"),
                                   "cpos": lc.get("position_s")})
            if paused_id is None and t >= 5:
                _, pr = jarvis("POST", f"/api/players/{did}/command", {"action": "pause"})
                paused_id, paused_at = pr.get("command_id"), time.time()
            if paused_id and pause_done is None and lc.get("id") == paused_id and lc.get("status") == "done":
                pause_done = round(time.time() - paused_at, 2)
                jarvis("POST", f"/api/players/{did}/command", {"action": "play"})
            await asyncio.sleep(0.5)
        out["pause_done"] = pause_done
        out["cid"] = cid
        out["events"] = [e for e in (stream.events if stream.resp else []) if e.get("command_id") == cid]
        await b.close()
    return out


if ep:
    try:
        r = asyncio.run(run())
    except Exception as e:                              # noqa: BLE001 — absent machinery is a red check
        print(f"  (the page run could not go on: {str(e).splitlines()[0][:140]})")
        r = {"samples": [], "events": []}
    s = r["samples"]
    check("the episode plays on the page, with English subtitles on offer",
          r.get("playing") and "en" in (r.get("offers") or []))
    gone = [x["t"] for x in s if not x["here"]]
    check(f"the device never leaves the list while the subtitles load ({len(s)} looks over {SLOW + 6} s"
          f"{', gone at ' + str(gone[:3]) if gone else ''})", bool(s) and not gone)
    late = max((x["seen_s"] or 0 for x in s if x["here"]), default=99)
    check(f"and keeps checking in: the longest silence it shows is {late} s", late < 6)
    moved = [x["pos"] for x in s if x["here"] and x["pos"] is not None]
    check(f"the film's position keeps being reported ({moved[0] if moved else '–'} → {moved[-1] if moved else '–'} s)",
          len(moved) > 5 and moved[-1] > moved[0] + 10)
    mine = [x for x in s if x["cmd"] == r.get("cid")]
    running = next((x["t"] for x in mine if x["status"] == "running"), None)
    check(f"the subtitles command says it is running ({running} s after it was sent)", running is not None and running < 4)
    # The pause sent at 5 s takes over last_command; the subtitles' outcome is read from the events.
    ev = r.get("events") or []
    statuses = [e.get("status") for e in ev]
    end = next((e for e in ev if e.get("status") in ("done", "failed")), None)
    end_t = None
    if end:
        sent_at = min((e.get("at") or 0) for e in ev)
        end_t = round((end.get("at") or 0) - sent_at, 1)
    check(f"and ends: failed, reason timeout, about {LIMIT} s after it was sent ({statuses}, {end_t} s)",
          bool(end) and end.get("status") == "failed" and end.get("reason") == "timeout"
          and end_t is not None and LIMIT - 2 <= end_t <= LIMIT + 4)
    check(f"with the second the film was at ({(end or {}).get('position_s')})", (end or {}).get("position_s") is not None)
    check(f"a pause sent while the subtitles load is done at once ({r.get('pause_done')} s), not after them",
          r.get("pause_done") is not None and r["pause_done"] < 3)

# ---- tidy
if lib_id:
    call("DELETE", f"/api/admin/libraries/{lib_id}")
shutil.rmtree(MEDIA, ignore_errors=True)
if made.get("id"):
    call("DELETE", f"/api/admin/assistant-keys/{made['id']}")
print(f"\n{len(fails)} failed" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
