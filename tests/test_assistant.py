"""Working with an assistant program: keys, scopes, titles, capabilities, events, health and the new
player commands (1.26.0).

Vlad, 9 Nov 2026: "Jarvis and LENTA must be able to work with each other extremely well and easy."
Read `server/lenta/assistant.py`, `events.py` and `players.py` for the design. What this file holds:

  1. the key: made by an administrator, shown once, stored as a hash, read only from the header,
     **not ended by signing out or a password change**, ended by removing it or by its expiry date;
  2. scopes, enforced by LENTA: every scope against the endpoints it opens and the ones it does not,
     and the things no key may ever do;
  3. titles: an ids-only key gets exactly 1.24.0's answers; a `titles` key gets titles and years, but
     never for a library its account may not see;
  4. find: the faster `_collapse` gives the same answer as the old one on random data, and is quick
     on a library of Vlad's size; channels and photo albums by name;
  5. the new commands: what each needs, and what is refused;
  6. events: a live stream with a key — devices, commands, a finished film, replay after a reconnect,
     and that one account never sees another's;
  7. /api/health;
  8. **a real page driven through the interface only**, on a small library this test makes and removes
     again (two audio languages, Bulgarian subtitles, an intro, a second episode, three photos):
     subtitles, audio, skip intro, next, volume, mute, small and full, quality, a channel, photos;
  9. the Settings › Assistants page: make a key, see it once, remove it;
 10. the contract: every answer above validated against `server/lenta/contract/assistant.json`.

Needs the test server (/tmp/claude-0/intro/uplive.sh; `tools/make-test-env.py` recreates it).
"""
import asyncio
import json
import os
import random
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
BASE = os.environ.get("LENTA_TEST_BASE", "http://127.0.0.1:8597")
DB = Path(os.environ.get("LENTA_TEST_DB", "/tmp/claude-0/intro/data/lenta.db"))
MEDIA = Path("/tmp/claude-0/intro/media-assistant")
CONTRACT = ROOT / "server/lenta/contract/assistant.json"

fails = []
answers = {}          # endpoint id → answers seen, for section 10


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def keep(eid, body):
    answers.setdefault(eid, []).append(body)


def http(method, path, body=None, headers=None, opener=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, (json.loads(raw) if raw else {})
            except ValueError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:                               # noqa: BLE001 — a dead server is a red check
        return 0, {}


class Person:
    """A signed-in account, through its session cookie, as a browser is."""

    def __init__(self, user, password="secret123"):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.ok = self.call("POST", "/api/auth/login", {"username": user, "password": password})[0] == 200

    def call(self, method, path, body=None):
        return http(method, path, body, opener=self.op)


class Key:
    """An assistant key, the way Jarvis will talk to LENTA."""

    def __init__(self, key):
        self.key = key or ""

    def call(self, method, path, body=None):
        return http(method, path, body, headers={"Authorization": f"Bearer {self.key}"})


if BASE.endswith(":8597"):
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], timeout=180, stdout=subprocess.DEVNULL)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
admin, guest = Person("admin"), Person("guest")
made_keys = []


def make(scopes, user_id=None, name="test key", by=None):
    body = {"name": name, "scopes": scopes}
    if user_id:
        body["user_id"] = user_id
    code, r = (by or admin).call("POST", "/api/admin/assistant-keys", body)
    if code == 200 and r.get("id"):
        made_keys.append(r["id"])
    return code, r


# ---------------------------------------------------------------- 1. the key
print("1. the key")
check("both accounts sign in", admin.ok and guest.ok)
guest_id = (con.execute("SELECT id FROM users WHERE username = 'guest'").fetchone() or {"id": 0})["id"]
code, r = make(["watch", "titles", "library", "admin"], name="Jarvis test")
full = Key(r.get("key"))
check(f"an administrator makes a key ({code})", code == 200 and full.key.startswith("lk_") and len(full.key) > 40)
check("it is made with the scopes asked for", r.get("scopes") == ["watch", "titles", "library", "admin"])
code, _ = make(["watch"], by=guest)
check(f"a viewer cannot make one ({code})", code == 403)
code, lst = admin.call("GET", "/api/admin/assistant-keys")
text = json.dumps(lst)
check(f"the list shows the key without the key itself ({code})", code == 200 and full.key not in text
      and any(k.get("name") == "Jarvis test" for k in lst.get("keys", [])))
row = con.execute("SELECT * FROM assistant_keys WHERE name = 'Jarvis test'").fetchone() if \
    con.execute("SELECT 1 FROM sqlite_master WHERE name = 'assistant_keys'").fetchone() else None
check("only a hash is kept in the database", bool(row) and full.key not in json.dumps(dict(row)))
code, me = full.call("GET", "/api/auth/me")
check(f"the key acts as its account ({code})", code == 200 and json.dumps(me).find("admin") >= 0)
code, _ = http("GET", f"/api/find?q=fighter&token={full.key}")
check(f"a key in the address is not accepted ({code})", code == 401)
code, _ = http("GET", "/api/find?q=fighter", headers={"Cookie": f"lenta_token={full.key}"})
check(f"nor a key in a cookie ({code})", code == 401)
code, _ = http("GET", "/api/find?q=fighter", headers={"Authorization": "Bearer lk_notarealkey000000000000000000"})
check(f"an unknown key is refused ({code})", code == 401)
code, _ = full.call("POST", "/api/auth/logout")
check(f"a key cannot sign its account out ({code})", code == 403)
# Signing out on a browser ends every session of the account — that is what cost Jarvis its sign-in.
other = Person("admin")
other.call("POST", "/api/auth/logout")
code, _ = admin.call("GET", "/api/auth/me")
check(f"signing out on a browser ends the account's sessions, as before ({code})", code == 401)
code, _ = full.call("GET", "/api/find?q=fighter")
check(f"but not the key ({code})", code == 200)
admin = Person("admin")
code, _ = admin.call("POST", "/api/auth/password", {"current": "secret123", "new": "secret123"})
code2, _ = full.call("GET", "/api/now-playing")
check(f"nor does a password change end it ({code}, {code2})", code == 200 and code2 == 200)
code, k2 = make(["watch"], name="soon gone")
gone = Key(k2.get("key"))
check("a second key works", gone.call("GET", "/api/find?q=fighter")[0] == 200)
admin.call("DELETE", f"/api/admin/assistant-keys/{k2.get('id')}")
check("removing it ends it at once", gone.call("GET", "/api/find?q=fighter")[0] == 401)
code, k3 = make(["watch"], name="expired")
if k3.get("id"):
    con.execute("UPDATE assistant_keys SET expires_at = ? WHERE id = ?", (time.time() - 1, k3["id"]))
    con.commit()
check("a key past its expiry date is refused", Key(k3.get("key")).call("GET", "/api/find?q=fighter")[0] == 401)
last = con.execute("SELECT last_used FROM assistant_keys WHERE name = 'Jarvis test'").fetchone() if row else None
check("the list says when a key was last used", bool(last) and (last["last_used"] or 0) > time.time() - 120)
code, _ = make(["watch", "everything"])
check(f"a scope that does not exist is refused ({code})", code in (400, 422))
code, _ = admin.call("POST", "/api/admin/assistant-keys", {"name": "   ", "scopes": ["watch"]})
check(f"a key needs a name ({code})", code in (400, 422))

# ---------------------------------------------------------------- 2. scopes
print("\n2. scopes, enforced by LENTA")
_, ids_only_r = make(["watch"], name="ids only")
_, titles_r = make(["watch", "titles"], name="titles")
_, library_r = make(["library"], name="library only")
_, admin_r = make(["watch", "admin"], name="admin")
_, guest_admin_r = make(["watch", "titles", "admin"], user_id=guest_id, name="guest admin")
ids_only, titled, libonly, adminkey, guestkey = (Key(x.get("key")) for x in
                                                  (ids_only_r, titles_r, library_r, admin_r, guest_admin_r))
film = con.execute("SELECT i.id FROM items i JOIN files f ON f.item_id = i.id WHERE i.kind = 'movie' "
                   "AND i.library_id = 1 ORDER BY i.id LIMIT 1").fetchone()
film_id = film["id"] if film else 0
cases = [  # (key, method, path, body, allowed)
    (ids_only, "GET", "/api/find?q=fighter", None, True),
    (ids_only, "GET", "/api/now-playing", None, True),
    (ids_only, "GET", f"/api/items/{film_id}/playable", None, True),
    (ids_only, "GET", "/api/libraries", None, True),
    (ids_only, "GET", "/api/capabilities", None, True),
    (ids_only, "GET", "/api/search?q=fight", None, False),
    (ids_only, "GET", f"/api/items/{film_id}", None, False),
    (ids_only, "GET", "/api/home", None, False),
    (ids_only, "POST", f"/api/items/{film_id}/watchlist", {"on": False}, False),
    (ids_only, "GET", "/api/health", None, False),
    (ids_only, "POST", "/api/playback", {"item_id": film_id}, False),
    (titled, "GET", "/api/search?q=fight", None, True),
    (titled, "GET", f"/api/items/{film_id}", None, True),
    (titled, "GET", "/api/libraries/1/items?limit=2", None, True),
    (titled, "GET", "/api/people?name=Nobody", None, True),
    (titled, "POST", f"/api/items/{film_id}/watched", {"watched": False}, False),
    (libonly, "POST", f"/api/items/{film_id}/watchlist", {"on": False}, True),
    (libonly, "GET", "/api/find?q=fighter", None, False),
    (adminkey, "GET", "/api/health", None, True),
    (adminkey, "GET", "/api/admin/overview", None, False),
    (guestkey, "GET", "/api/health", None, False),          # an admin scope on a viewer's account is nothing
]
for key, method, path, body, ok in cases:
    code, _ = key.call(method, path, body)
    want = "allowed" if ok else "refused"
    check(f"{method} {path.split('?')[0]} — {want} ({code})", (code == 200) if ok else code in (403, 401))
never = [("DELETE", "/api/admin/libraries/999"), ("GET", "/api/admin/users"), ("POST", "/api/admin/users"),
         ("PUT", "/api/admin/settings"), ("DELETE", "/api/admin/users/999"), ("PUT", "/api/me/prefs"),
         ("POST", "/api/auth/password"), ("GET", "/api/admin/assistant-keys"),
         ("POST", "/api/admin/assistant-keys"), ("DELETE", "/api/subtitles/1"), ("PUT", "/api/admin/items/1")]
check("no key, even with every scope, may delete, change users, settings or keys",
      all(full.call(m, p, {})[0] == 403 for m, p in never))

# ---------------------------------------------------------------- 3. titles
print("\n3. titles only to a key with the titles scope")
_, a = ids_only.call("GET", "/api/find?q=fighter")
_, b = titled.call("GET", "/api/find?q=fighter")
keep("find", a); keep("find", b)
check(f"an ids-only key gets no title ({sorted(a)})", "title" not in a and "candidates" not in a and a.get("item_id"))
check(f"a titles key gets the title and year ({b.get('title')}, {b.get('year')})",
      bool(b.get("title")) and bool(b.get("year")) and b.get("item_id") == a.get("item_id"))
_, p = titled.call("GET", f"/api/items/{film_id}/playable")
keep("playable", p)
check(f"/playable carries it too ({p.get('title')})", p.get("playable") and bool(p.get("title")))
_, p0 = ids_only.call("GET", f"/api/items/{film_id}/playable")
keep("playable", p0)
check("and not for an ids-only key", "title" not in p0)
_, s = admin.call("GET", "/api/find?q=fighter")
check("a person's own session gets ids only, as in 1.24.0", "title" not in s)
ep = con.execute("SELECT e.id FROM items e JOIN files f ON f.item_id = e.id WHERE e.kind = 'episode' "
                 "LIMIT 1").fetchone()
if ep:
    admin.call("POST", "/api/players/beat", {"device_id": "assistanttv01", "device_kind": "tv", "state": "playing",
                                              "item_id": ep["id"], "position_s": 12})
_, np_t = titled.call("GET", "/api/now-playing")
keep("now_playing", np_t)
dev = next((d for d in np_t.get("players", []) if d.get("device_id") == "assistanttv01"), {})
check(f"an episode on a device carries its show, season and number ({dev.get('title')} "
      f"S{dev.get('season')}E{dev.get('episode')})",
      bool(dev.get("title")) and dev.get("season") is not None and dev.get("episode") is not None)
_, np_i = ids_only.call("GET", "/api/now-playing")
keep("now_playing", np_i)
dev_i = next((d for d in np_i.get("players", []) if d.get("device_id") == "assistanttv01"), {})
check("the same device to an ids-only key: no title", bool(dev_i) and "title" not in dev_i)
hidden = con.execute("SELECT i.id FROM items i WHERE i.kind = 'movie' AND i.library_id NOT IN "
                     "(SELECT library_id FROM user_libraries WHERE user_id = ?) LIMIT 1", (guest_id,)).fetchone()
if hidden:
    guest.call("POST", "/api/players/beat", {"device_id": "guestpage001", "state": "playing",
                                                "item_id": hidden["id"], "position_s": 3})
    _, gnp = guestkey.call("GET", "/api/now-playing")
    gd = next((d for d in gnp.get("players", []) if d.get("device_id") == "guestpage001"), {})
    check("a page claiming a title its account may not see is not given that title",
          bool(gd) and "title" not in gd)

# ---------------------------------------------------------------- 4. find
print("\n4. find: the same answers, sooner; channels and albums by name")
os.environ.setdefault("LENTA_DATA_DIR", str(DB.parent))
from lenta import find as F  # noqa: E402


def old_collapse(rows):
    groups = []
    for r in rows:
        for g in groups:
            if F._same_title(g[0], r):
                g.append(r)
                break
        else:
            groups.append([r])
    return [max(g, key=lambda r: (r["touched"] or 0, -r["id"])) for g in groups]


rnd = random.Random(1109)
same = True
for trial in range(300):
    rows = []
    for i in range(rnd.randint(0, 40)):
        rows.append({"id": rnd.randint(1, 60), "kind": rnd.choice(["movie", "show"]),
                     "tmdb_id": rnd.choice([None, 0, 1, 2, 3]), "title": rnd.choice(["Rocky", "rocky", "Rocky 2", "It", "Up"]),
                     "year": rnd.choice([None, 1976, 1979, 2017]), "touched": rnd.choice([None, 1, 2, 3])})
    if [r["id"] for r in F._collapse(rows)] != [r["id"] for r in old_collapse(rows)]:
        same = False
        break
check("on 300 random lists the new grouping picks exactly what the old one did", same)
big = [{"id": i, "kind": "movie", "tmdb_id": 10000 + i, "title": f"Film number {i}", "year": 1950 + i % 70,
        "touched": None} for i in range(2000)]
t0 = time.time()
F._collapse(big)
fast = time.time() - t0
check(f"2,000 films are grouped in {fast * 1000:.0f} ms (the old way took seconds)", fast < 0.5)
code, ch = ids_only.call("GET", "/api/find?kind=channel&q=nova")
keep("find", ch)
check(f"a channel by its name: 'nova' is Nova TV, not Kino Nova ({ch.get('channel_id')})",
      code == 200 and ch.get("kind") == "channel" and ch.get("channel_id") and not ch.get("ambiguous"))
_, ch_t = titled.call("GET", "/api/find?kind=channel&q=nova")
keep("find", ch_t)
check(f"and a titles key hears its name ({ch_t.get('channel_name')})", bool(ch_t.get("channel_name")))
_, ch0 = ids_only.call("GET", "/api/find?kind=channel&q=zzzzzz")
check("a channel that is not there is no answer", ch0.get("channel_id") is None and ch0.get("matches") == 0)
code, _ = ids_only.call("GET", "/api/find?kind=everything&q=x")
check(f"a kind that does not exist is refused ({code})", code == 400)

# ---------------------------------------------------------------- 5. commands
print("\n5. the new commands: what each one needs")
admin.call("POST", "/api/players/beat", {"device_id": "assistantpc01", "state": "playing", "item_id": film_id,
                                          "position_s": 30})
bad = [({"action": "subtitles"}, "subtitles without a language"), ({"action": "audio", "language": "!!"}, "audio with nonsense"),
       ({"action": "volume", "level": 2}, "volume above 1"), ({"action": "volume"}, "volume without a level"),
       ({"action": "mute"}, "mute without true or false"), ({"action": "quality", "quality": "8k"}, "a quality that does not exist"),
       ({"action": "open"}, "open with nothing"), ({"action": "open", "item_id": film_id, "channel_id": 1}, "open with both"),
       ({"action": "open", "channel_id": 987654}, "a channel that is not there")]
for body, what in bad:
    code, _ = titled.call("POST", "/api/players/assistantpc01/command", body)
    check(f"{what} is refused ({code})", code in (400, 404, 422))
good = [{"action": "subtitles", "language": "bg"}, {"action": "subtitles", "language": "off"},
        {"action": "audio", "language": "en"}, {"action": "next"}, {"action": "previous"}, {"action": "skip_intro"},
        {"action": "volume", "level": 0.4}, {"action": "mute", "muted": True}, {"action": "minimize"},
        {"action": "expand"}, {"action": "quality", "quality": "720p"}]
oks = [titled.call("POST", "/api/players/assistantpc01/command", b) for b in good]
for code, r in oks:
    keep("command", r)
check("each of the eleven new commands is queued", all(code == 200 and r.get("command_id") for code, r in oks))
_, got = admin.call("POST", "/api/players/beat", {"device_id": "assistantpc01", "state": "playing",
                                                   "item_id": film_id, "position_s": 31})
sent = got.get("commands", [])
check(f"and the page collects them with what each needs ({len(sent)})",
      len(sent) == 11 and sent[0].get("language") == "bg" and sent[6].get("level") == 0.4
      and sent[7].get("muted") is True and sent[10].get("quality") == "720p")
chan = con.execute("SELECT id FROM tv_channels WHERE hidden = 0 ORDER BY id LIMIT 1").fetchone()
if chan:
    code, _ = titled.call("POST", "/api/players/assistantpc01/command", {"action": "open", "channel_id": chan["id"]})
    _, got = admin.call("POST", "/api/players/beat", {"device_id": "assistantpc01", "state": "idle"})
    c = (got.get("commands") or [{}])[0]
    check(f"open a channel goes to the page as a channel ({c})", code == 200 and c.get("target") == "channel"
          and c.get("channel_id") == chan["id"])

# ---------------------------------------------------------------- 6. events
print("\n6. events")


class Stream:
    """A live /api/events connection read on a thread, as Jarvis would hold one."""

    def __init__(self, key, since=None):
        self.events, self.raw, self.ok = [], [], False
        path = "/api/events" + (f"?since={since}" if since is not None else "")
        req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {key.key}"})
        try:
            self.resp = urllib.request.urlopen(req, timeout=60)
            self.ok = self.resp.status == 200 and "text/event-stream" in self.resp.headers.get("content-type", "")
        except Exception:                                   # noqa: BLE001
            self.resp = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        cur = {}
        try:
            for line in self.resp:
                line = line.decode().rstrip("\n")
                self.raw.append(line)
                if not line:
                    if "data" in cur:
                        try:
                            self.events.append({"id": int(cur.get("id", 0)), **json.loads(cur["data"])})
                        except ValueError:
                            pass
                    cur = {}
                elif not line.startswith(":") and ":" in line:
                    k, v = line.split(":", 1)
                    cur[k] = v.strip()
        except Exception:                                   # noqa: BLE001 — closed
            pass

    def wait(self, pred, limit=6.0):
        t0 = time.time()
        while time.time() - t0 < limit:
            hit = next((e for e in self.events if pred(e)), None)
            if hit:
                return hit
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.resp.close()
        except Exception:                                   # noqa: BLE001
            pass


st = Stream(titled)
check("a key opens the event stream", st.ok)
time.sleep(0.5)
t0 = time.time()
admin.call("POST", "/api/players/beat", {"device_id": "eventsdev0001", "device_name": "Events test",
                                          "state": "playing", "item_id": film_id, "position_s": 5})
e = st.wait(lambda e: e.get("type") == "player" and e.get("device_id") == "eventsdev0001")
check(f"a new device is an event, {(time.time() - t0) * 1000:.0f} ms later", bool(e) and e.get("state") == "playing")
check(f"with its title, for a titles key ({(e or {}).get('title')})", bool((e or {}).get("title")))
_, cmd = titled.call("POST", "/api/players/eventsdev0001/command", {"action": "pause"})
_, got = admin.call("POST", "/api/players/beat", {"device_id": "eventsdev0001", "state": "playing",
                                                   "item_id": film_id, "position_s": 6})
admin.call("POST", "/api/players/beat", {"device_id": "eventsdev0001", "state": "paused", "item_id": film_id,
                                          "position_s": 6.4, "done": [{"id": cmd.get("command_id"), "ok": True,
                                                                       "position_s": 6.4}]})
e = st.wait(lambda e: e.get("type") == "command" and e.get("command_id") == cmd.get("command_id")
            and e.get("status") == "done")
check(f"a command's outcome is an event, with its second ({(e or {}).get('position_s')})",
      bool(e) and e.get("position_s") == 6.4)
e = st.wait(lambda e: e.get("type") == "player" and e.get("device_id") == "eventsdev0001" and e.get("state") == "paused")
check("and the device's change to paused", bool(e))
guest.call("POST", "/api/players/beat", {"device_id": "guestevents01", "state": "playing", "item_id": None})
time.sleep(1)
check("another account's devices never appear", not any(e.get("device_id") == "guestevents01" for e in st.events))
if film_id:
    play = f"assist-{int(time.time())}"
    admin.call("POST", "/api/progress", {"item_id": film_id, "play_id": play, "position": 99, "duration": 100,
                                         "state": "playing", "mode": "direct", "quality": "original"})
    e = st.wait(lambda e: e.get("type") == "finished" and e.get("item_id") == film_id)
    check("a film watched to the end is an event", bool(e))
    admin.call("POST", f"/api/items/{film_id}/watched", {"watched": False})
mark = max((e["id"] for e in st.events), default=0)
st.close()
admin.call("POST", "/api/players/beat", {"device_id": "eventsdev0001", "state": "idle", "item_id": None})
st2 = Stream(titled, since=mark)
e = st2.wait(lambda e: e.get("type") == "player" and e.get("device_id") == "eventsdev0001" and e.get("state") == "idle")
check("reconnecting from the last id it saw replays what it missed", bool(e) and st2.ok)
check("each event has an id and a keep-alive comment opens the stream",
      bool(e) and e.get("id", 0) > mark and any(r.startswith(":") for r in st2.raw))
st2.close()
st3 = Stream(ids_only)
time.sleep(0.4)
admin.call("POST", "/api/players/beat", {"device_id": "eventsdev0002", "state": "playing", "item_id": film_id,
                                            "position_s": 2})
e = st3.wait(lambda e: e.get("device_id") == "eventsdev0002")
check("an ids-only key's events carry no title", bool(e) and "title" not in e)
st3.close()
code, _ = http("GET", "/api/events", timeout=5)
check(f"the stream needs a key or a sign-in ({code})", code == 401)

# ---------------------------------------------------------------- 7. health
print("\n7. health")
code, h = adminkey.call("GET", "/api/health")
keep("health", h)
check(f"an admin key reads it ({code})", code == 200 and isinstance(h.get("problems"), list) and "ok" in h)
check("every library is in it, by its own name", len(h.get("libraries", [])) >= 1)
code, _ = guest.call("GET", "/api/health")
check(f"a viewer's own session cannot ({code})", code == 403)
code, caps = adminkey.call("GET", "/api/capabilities")
keep("capabilities", caps)
check("capabilities name health and the admin actions for that key", "health" in caps.get("features", {}))
for k in (ids_only, titled, libonly, guestkey):
    _, c = k.call("GET", "/api/capabilities")
    keep("capabilities", c)
_, c = ids_only.call("GET", "/api/capabilities")
check(f"and leave out what a key cannot use ({sorted(c.get('features', {}))})",
      "titles" not in c.get("features", {}) and "health" not in c.get("features", {}) and "find" in c.get("features", {}))
_, c = guestkey.call("GET", "/api/capabilities")
check("an admin scope on a viewer's account gives no admin features", "health" not in c.get("features", {}))
_, c = admin.call("GET", "/api/capabilities")
check("a person's session can ask too", c.get("key") is None and "find" in c.get("features", {}))

# ---------------------------------------------------------------- 8. a real page
print("\n8. a real page, driven through the interface only")
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None
    print("  skip  playwright is not installed here")
FF = shutil.which("ffmpeg")


def make_media():
    shutil.rmtree(MEDIA, ignore_errors=True)
    show = MEDIA / "shows/Assist Test Show/Season 1"
    photos = MEDIA / "photos/Greece 2026"
    show.mkdir(parents=True)
    photos.mkdir(parents=True)
    for n in (1, 2):
        subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=60",
                        "-f", "lavfi", "-i", "sine=f=440:d=60", "-f", "lavfi", "-i", "sine=f=660:d=60",
                        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=bul",
                        str(show / f"Assist Test Show S01E0{n}.mkv")], check=True)
        (show / f"Assist Test Show S01E0{n}.bg.srt").write_text(
            "1\n00:00:01,000 --> 00:00:59,000\nЗдравей\n", encoding="utf-8")
    for n in (1, 2, 3):
        subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=640x360:d=1", "-frames:v", "1",
                        str(photos / f"IMG_000{n}.jpg")], check=True)


libs_made = []
delays = {}           # command → seconds from sending it to the page confirming it was done
watch_scan = watch_ids = None
if async_playwright and FF:
    make_media()
    watch_scan, watch_ids = Stream(adminkey), Stream(ids_only)        # who hears what a scan brings
    time.sleep(0.4)
    for name, kind, path in (("Assist shows", "shows", MEDIA / "shows"), ("Assist photos", "photos", MEDIA / "photos")):
        code, r = admin.call("POST", "/api/admin/libraries", {"name": name, "kind": kind, "paths": [str(path)]})
        if r.get("id"):
            libs_made.append(r["id"])
            admin.call("POST", f"/api/admin/libraries/{r['id']}/scan")
    t0 = time.time()
    while time.time() - t0 < 90:
        n = con.execute("SELECT COUNT(*) FROM items WHERE library_id IN (%s) AND kind IN ('episode','photo')"
                        % ",".join(str(i) for i in libs_made or [0])).fetchone()[0]
        if n >= 5:
            break
        time.sleep(1)
    eps = [r["id"] for r in con.execute(
        "SELECT e.id FROM items e WHERE e.kind = 'episode' AND e.library_id = ? ORDER BY e.index_number",
        (libs_made[0] if libs_made else 0,))]
    album = con.execute("SELECT id FROM items WHERE kind = 'photoalbum' AND library_id = ?",
                        (libs_made[1] if len(libs_made) > 1 else 0,)).fetchone()
    check(f"the test library is scanned ({len(eps)} episodes, album {album and album['id']})", len(eps) == 2 and album)
    show_id = (con.execute("SELECT id FROM items WHERE kind = 'show' AND library_id = ?",
                           (libs_made[0] if libs_made else 0,)).fetchone() or {"id": None})["id"]
    e = watch_scan.wait(lambda e: e.get("type") == "added" and show_id in (e.get("item_ids") or []), 20)
    check("the new show is an `added` event", bool(e))
    e = watch_scan.wait(lambda e: e.get("type") == "added" and album and album["id"] in (e.get("item_ids") or []), 20)
    check("and so is the new album", bool(e))
    e = watch_scan.wait(lambda e: e.get("type") == "scan" and e.get("library_id") in libs_made and e.get("ok"), 20)
    check("an admin key hears that the scan finished", bool(e))
    check("a key without the admin scope does not", not any(x.get("type") == "scan" for x in watch_ids.events))
    check("but hears what was added", any(x.get("type") == "added" for x in watch_ids.events))
    watch_scan.close(); watch_ids.close()
    if eps:
        fid = con.execute("SELECT id FROM files WHERE item_id = ?", (eps[0],)).fetchone()["id"]
        con.execute("INSERT OR REPLACE INTO intros(file_id, start, end, source, sig, checked) "
                    "VALUES(?, 5, 25, 'manual', '', ?)", (fid, time.time()))
        con.commit()


async def page_run():
    out = {}
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

        def dev():
            return next((d for d in titled.call("GET", "/api/now-playing")[1].get("players", [])
                         if d.get("device_id") == did), {})

        async def until(pred, limit=20.0):
            t0 = time.time()
            while time.time() - t0 < limit:
                d = await asyncio.to_thread(dev)
                if pred(d):
                    return time.time() - t0, d
                await asyncio.sleep(0.2)
            return None, await asyncio.to_thread(dev)

        sent_at = {}

        def send(body):
            code, r = titled.call("POST", f"/api/players/{did}/command", body)
            sent_at[r.get("command_id")] = (time.time(), body["action"] + (f" {body['language']}" if body.get("language") else ""))
            return code, r

        async def done(cid, limit=15.0):
            def st():
                return (dev().get("last_command") or {})
            while time.time() - sent_at.get(cid, (time.time(), ""))[0] < limit:
                lc = await asyncio.to_thread(st)
                if lc.get("id") == cid and lc.get("status") in ("done", "failed"):
                    t, what = sent_at[cid]
                    delays.setdefault(what, round(time.time() - t, 2))
                    return lc
                await asyncio.sleep(0.05)
            return {}

        await until(lambda d: bool(d))
        code, r = send({"action": "open", "item_id": eps[0]})
        t, d = await until(lambda d: d.get("item_id") == eps[0] and d.get("state") == "playing", 40)
        out["opened"] = t
        out["options"] = (d.get("audio_options"), d.get("subtitle_options"))
        out["audio_now"] = d.get("audio")
        out["has_next"] = d.get("has_next")
        keep("now_playing", titled.call("GET", "/api/now-playing")[1])
        # subtitles
        _, r = send({"action": "subtitles", "language": "bg"})
        out["subs_cmd"] = (await done(r.get("command_id"))).get("status")
        t, d = await until(lambda d: d.get("subtitles") == "bg")
        out["subs"] = t
        out["cue"] = await pg.evaluate("(document.querySelector('.player') || document.body).innerText.includes('Здравей')")
        _, r = send({"action": "subtitles", "language": "off"})
        await done(r.get("command_id"))
        t, d = await until(lambda d: d.get("subtitles") is None)
        out["subs_off"] = t
        # skip intro: the intro is 5–25 s
        _, r = send({"action": "seek", "position_s": 8})
        await done(r.get("command_id"))
        t, d = await until(lambda d: d.get("can_skip_intro") is True, 15)
        out["can_skip"] = t
        _, r = send({"action": "skip_intro"})
        lc = await done(r.get("command_id"))
        t, d = await until(lambda d: (d.get("position_s") or 0) >= 24.5)
        out["skipped"] = (lc.get("status"), t, d.get("position_s"))
        # volume and mute
        _, r = send({"action": "volume", "level": 0.3})
        await done(r.get("command_id"))
        out["volume"] = await pg.evaluate("document.querySelector('video') && document.querySelector('video').volume")
        _, r = send({"action": "mute", "muted": True})
        await done(r.get("command_id"))
        out["muted"] = await pg.evaluate("document.querySelector('video') && document.querySelector('video').muted")
        send({"action": "mute", "muted": False})
        # small and full
        _, r = send({"action": "minimize"})
        await done(r.get("command_id"))
        t, d = await until(lambda d: d.get("small") is True)
        out["small"] = t
        _, r = send({"action": "expand"})
        await done(r.get("command_id"))
        t, d = await until(lambda d: d.get("small") is False)
        out["full"] = t
        # audio: change the track; the page reloads the stream at the same second
        pos = (await asyncio.to_thread(dev)).get("position_s") or 0
        _, r = send({"action": "audio", "language": "bg"})
        lc = await done(r.get("command_id"), 30)
        t, d = await until(lambda d: d.get("audio") == "bg" and d.get("state") in ("playing", "paused"), 30)
        out["audio"] = (lc.get("status"), t, pos, d.get("position_s"))
        # quality
        _, r = send({"action": "quality", "quality": "480p"})
        lc = await done(r.get("command_id"), 30)
        t, d = await until(lambda d: d.get("quality") == "480p", 30)
        out["quality"] = (lc.get("status"), t)
        # next episode
        _, r = send({"action": "next"})
        await done(r.get("command_id"))
        t, d = await until(lambda d: d.get("item_id") == eps[1], 40)
        out["next"] = t
        # a live channel
        if chan:
            _, r = send({"action": "open", "channel_id": chan["id"]})
            await done(r.get("command_id"))
            t, d = await until(lambda d: d.get("channel_id") == chan["id"], 30)
            out["channel"] = t
        # photos
        if album:
            _, r = send({"action": "open", "item_id": album["id"]})
            await done(r.get("command_id"))
            t, d = await until(lambda d: d.get("album_id") == album["id"] and d.get("photo_index") == 0, 20)
            out["photos"] = (t, d.get("photo_count"))
            _, r = send({"action": "next"})
            await done(r.get("command_id"))
            t, d = await until(lambda d: d.get("photo_index") == 1)
            out["photo_next"] = t
            _, r = send({"action": "previous"})
            await done(r.get("command_id"))
            t, d = await until(lambda d: d.get("photo_index") == 0)
            out["photo_prev"] = t
            _, np = titled.call("GET", "/api/now-playing")
            keep("now_playing", np)
            me = next((x for x in np.get("players", []) if x.get("device_id") == did), {})
            out["album_title"] = me.get("album_title")
        # 9. the Settings page
        await pg.goto(BASE + "/#/settings/assistants", wait_until="load")
        await pg.wait_for_selector("#add-key", timeout=15000)
        await pg.click("#add-key")
        await pg.fill("#kname", "Made in the page")
        await pg.click("#kform .foot .primary")
        await pg.wait_for_selector("#kshow", timeout=10000)
        out["shown"] = await pg.input_value("#kshow")
        await pg.click(".modal [data-close], [data-close]")
        await pg.wait_for_timeout(600)
        out["listed"] = await pg.evaluate("document.body.innerText.includes('Made in the page')")
        out["listed_without_key"] = not await pg.evaluate(f"document.body.innerText.includes({json.dumps(out['shown'])})")
        await b.close()
    return out


if async_playwright and FF and libs_made and eps:
    try:
        r = asyncio.run(page_run())
    except Exception as e:                                  # noqa: BLE001 — absent machinery is a red check
        print(f"  (the page run could not go on: {str(e).splitlines()[0][:140]})")
        import collections
        r = collections.defaultdict(lambda: None)
    fmt = lambda t: "–" if t is None else f"{t:.2f} s"     # noqa: E731
    check(f"open the episode on the page ({fmt(r['opened'])})", r["opened"] is not None)
    opts = r["options"] or (None, None)
    check(f"the page reports the tracks on offer ({opts})", opts[0] and set(opts[0]) >= {"en", "bg"}
          and opts[1] and "bg" in opts[1])
    check("and that there is a next episode", r["has_next"] is True)
    check(f"subtitles bg ({fmt(r['subs'])}), and the Bulgarian line is on screen",
          r["subs_cmd"] == "done" and r["subs"] is not None and r["cue"])
    check(f"subtitles off ({fmt(r['subs_off'])})", r["subs_off"] is not None)
    check(f"inside the intro, the page says it can be skipped ({fmt(r['can_skip'])})", r["can_skip"] is not None)
    sk = r["skipped"] or (None, None, None)
    check(f"skip intro lands at its end ({sk[2]} s)", sk[0] == "done" and sk[1] is not None)
    check(f"volume 0.3 ({r['volume']})", r["volume"] is not None and abs(r["volume"] - 0.3) < 0.01)
    check(f"mute ({r['muted']})", r["muted"] is True)
    check(f"small ({fmt(r['small'])}) and full again ({fmt(r['full'])})", r["small"] is not None and r["full"] is not None)
    au = r["audio"] or (None, None, 0, 0)
    check(f"audio bg ({fmt(au[1])}), staying near the second it was at ({au[2]} → {au[3]})",
          au[0] == "done" and au[1] is not None and abs((au[3] or 0) - (au[2] or 0)) < 12)
    q = r["quality"] or (None, None)
    check(f"quality 480p ({fmt(q[1])})", q[0] == "done" and q[1] is not None)
    check(f"next: the second episode plays ({fmt(r['next'])})", r["next"] is not None)
    if chan:
        check(f"open a live channel ({fmt(r['channel'])})", r["channel"] is not None)
    ph = r["photos"] or (None, None)
    check(f"open the photo album: the first photo, of three ({fmt(ph[0])})", ph[0] is not None and ph[1] == 3)
    check(f"next photo ({fmt(r['photo_next'])}) and back ({fmt(r['photo_prev'])})",
          r["photo_next"] is not None and r["photo_prev"] is not None)
    check(f"a titles key hears the album's name ({r['album_title']})", r["album_title"] == "Greece 2026")
    print("  …  from sending a command to the page confirming it: "
          + ", ".join(f"{k} {v:.2f} s" for k, v in delays.items()))
    print("\n9. Settings › Assistants")
    check("making a key in the page shows it once", (r["shown"] or "").startswith("lk_"))
    check("it is in the list afterwards, without the key itself", r["listed"] and r["listed_without_key"])
    con.execute("DELETE FROM assistant_keys WHERE name = 'Made in the page'")
    con.commit()

for lid in libs_made:
    admin.call("DELETE", f"/api/admin/libraries/{lid}")
shutil.rmtree(MEDIA, ignore_errors=True)

# ---------------------------------------------------------------- 10. the contract
print("\n10. every answer above, against the contract")
try:
    import jsonschema
except ImportError:
    jsonschema = None
    print("  skip  jsonschema is not installed")
contract = json.loads(CONTRACT.read_text(encoding="utf-8")) if CONTRACT.exists() else {}
check("the contract file exists and names this version",
      contract.get("lenta") == (ROOT / "server/lenta/config.py").read_text().split('VERSION = "')[1].split('"')[0])
code, served = ids_only.call("GET", "/api/contract")
check(f"LENTA serves the same file at /api/contract ({code})", served == contract)
if jsonschema and contract:
    eps_by_id = {e["id"]: e for e in contract.get("endpoints", [])}
    for eid, bodies in sorted(answers.items()):
        schema = eps_by_id.get(eid, {}).get("response")
        if not isinstance(schema, dict):
            check(f"{eid}: in the contract", False)
            continue
        full_schema = {**schema, "$defs": contract["$defs"]}
        bad = []
        for body in bodies:
            try:
                jsonschema.validate(body, full_schema)
            except jsonschema.ValidationError as e:
                bad.append(e.message[:120])
        check(f"{eid}: {len(bodies)} answer(s) match the contract{(' — ' + bad[0]) if bad else ''}", not bad)
    from lenta import players as P  # noqa: E402
    check("the contract lists exactly the actions the server takes",
          contract["$defs"]["action"]["enum"] == list(P.ACTIONS))
    try:
        from lenta import assistant as A  # noqa: E402
    except ImportError:                    # an older server: no keys at all, which is a red check
        A = None
    check("and exactly its scopes", bool(A) and contract["$defs"]["scope"]["enum"] == list(A.SCOPES))
    paths = {(e["method"], e["path"]) for e in contract["endpoints"]} if A else set()
    import re as _re
    sample = lambda p: (p.replace("{id}", "1").replace("{device_id}", "abcdefgh12")    # noqa: E731
                        .replace("{kind}/{name}", "poster/x.jpg"))
    missing = ["the assistant module"] if not A else [f"{m} {rx}" for rules in A.ALLOW.values() for m, rx in rules
               if not any(m == cm and _re.fullmatch(rx, sample(cp)) for cm, cp in paths)]
    check(f"every endpoint a key can reach is described in it ({missing[:3]})", not missing)
    extra = ["the assistant module"] if not A else [f"{cm} {cp}" for cm, cp in paths
                                                    if not A.allowed(cm, sample(cp), list(A.SCOPES), True)]
    check(f"and it describes nothing a key cannot reach ({extra[:3]})", not extra)

# ---------------------------------------------------------------- tidy
try:
    for kid in made_keys:
        con.execute("DELETE FROM assistant_keys WHERE id = ?", (kid,))
    con.commit()
except sqlite3.OperationalError:          # an older server has no keys table: nothing to tidy
    pass
print(f"\n{len(fails)} failed" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
