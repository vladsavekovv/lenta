"""Settings › Dashboard, General and Network: why they sometimes took seconds, and what was done.

Reported as "sometimes above 5 seconds". Four separate things were making them slow, three of them
provable here and one of them provable only by construction — which is said plainly rather than
glossed over.

**Four requests, one after another.** `settings()` fetched /api/admin/settings, then /overview, then
/artwork-sources, then /network, each waiting for the one before it although none of them needs its
answer. The page could not appear until four round trips had finished in series. They go out together
now.

**The whole Dashboard, for two fields.** The Settings pages asked for /api/admin/overview and read
exactly two things out of it: the encoder and the IMDb figures. The rest of that answer counts every
item and calls disk_usage() on every library folder — and a folder on another machine (a NAS over NFS
or SMB, a disk that has spun down) makes that wait for the mount rather than the disk, for as long as
the mount takes. `?light=1` returns the two fields and touches no storage at all.

**The authority's key, parsed on every request.** /api/admin/network describes the certificate in use,
which in automatic mode goes through `_ensure_ca()`, which loaded and checked an RSA private key off
the disk every single time. Measured here: 86 ms of the 90 ms that endpoint took. It is the same two
files every time, so it is kept, and their mtime and size say when they have been replaced.

**A reverse DNS lookup on every request.** `local_names()` calls `socket.getfqdn()`, and on a network
whose DNS server is slow or away — a domain controller that is off, a VPN that is up — that waits for
the resolver's own timeout. Five seconds is the usual one, per nameserver, and that is the shape of
what was reported. It cannot be reproduced in this sandbox, where there is no such resolver to wait
for: `_local_names()` answers in about 4 ms here. What is checked is therefore the construction — that
the answer is cached, that a request never waits for a new one, and that it is worked out at start-up
rather than while somebody is looking at a page. On his server, the proof is that the page is quick.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
ROOT = Path("/home/claude/lenta")
FIXTURE = Path("/tmp/claude-0/intro")

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# ---------------------------------------------------------------- 1. the four requests
print("1. the page's four requests go out together")
admin_js = (ROOT / "web" / "js" / "admin.js").read_text()
m = re.search(r"async function settings\(body, page = 'general'\) \{(.*?)\n  const artOrder", admin_js, re.S)
head = m.group(1) if m else ""
check("there is a settings() to look at", bool(head))
check("they are asked for at once", "await Promise.all([" in head)
check("and not one after another", len(re.findall(r"^\s*const \w+ = await api\.get", head, re.M)) == 0)
check("the one that may fail still may", "api.get('/api/admin/network').catch(() => null)" in head)
check("the Dashboard's figures are not asked for in full",
      "'/api/admin/overview?light=1'" in head)

print("\n2. light=1 is light")
api_admin = (ROOT / "server" / "lenta" / "api_admin.py").read_text()
check("the endpoint takes it", "def overview(light: bool = False)" in api_admin)
light = re.search(r"if light:\s*\n\s*return \{(.*?)\}\s*\n\s*with db\(\) as con:", api_admin, re.S)
check("and returns early", light is not None)
if light:
    inside = light.group(1)
    check("with the encoder", '"hardware": transcode.hw' in inside)
    check("and the IMDb figures", '"imdb": imdb.status()' in inside)
    check("and no storage walk", "disk_usage" not in inside and "storage" not in inside)
    check("and no counting", "COUNT" not in inside and "counts" not in inside)

# what the page reads out of it must all be in there
settings_body = admin_js[admin_js.index("async function settings(body"):]
ends = re.search(r"\n(async )?function ", settings_body[1:])
settings_body = settings_body[:ends.start()] if ends else settings_body
used = sorted(set(re.findall(r"\bo\.([A-Za-z_]+)", settings_body)))
check(f"the page reads only what light=1 returns ({', '.join(used)})",
      set(used) <= {"hardware", "imdb"})

# ---------------------------------------------------------------- 3. measured
print("\n3. measured, here")
sys.path.insert(0, str(ROOT / "server"))
os.environ["LENTA_DATA_DIR"] = str(FIXTURE / "data") if (FIXTURE / "data" / "lenta.db").exists() \
    else os.environ["LENTA_DATA_DIR"]
from lenta import certs                                                     # noqa: E402


def ms(fn, n=5):
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1000


slow = getattr(certs, "_local_names", None)
check("the slow part is a function of its own, so the quick one can be told from it", slow is not None)
certs.local_names()
certs.info()                                      # warm, as the running server is
cold = ms(slow, 3) if slow else ms(certs.local_names, 3)
warm = ms(certs.local_names, 50)
info = ms(certs.info)
print(f"       asking the system     {cold:7.2f} ms")
print(f"       from the cache        {warm:7.3f} ms")
print(f"       certs.info()          {info:7.2f} ms")
check(f"the names come from a cache, not the system ({warm:.3f} ms)", warm < cold / 10)
check(f"and describing the certificate is no longer the slowest thing on the page ({info:.1f} ms)",
      info < 10)

# a replaced authority is still noticed: the cache is keyed on the files, not on a clock
ca, _key = certs._ensure_ca()
st = certs.CA_CRT.stat()
os.utime(certs.CA_CRT, (st.st_atime, st.st_mtime + 11))
again, _k2 = certs._ensure_ca()
check("a replaced certificate authority is read again", again is not ca)
os.utime(certs.CA_CRT, (st.st_atime, st.st_mtime))

print("\n4. and no request waits for a new answer")
src = (ROOT / "server" / "lenta" / "certs.py").read_text()
check("the slow part has a name of its own", "def _local_names()" in src)
check("the cache has a life", "NAMES_TTL" in src)
check("a stale answer is served while a new one is worked out",
      "threading.Thread(target=refresh" in src and "_names_lock.acquire(blocking=False)" in src)
check("one refresh at a time", "_names_lock" in src and "finally:" in src)
check("the first one is paid for at start-up, not by a page",
      "certs.local_names" in (ROOT / "server" / "lenta" / "main.py").read_text())
main = (ROOT / "server" / "lenta" / "main.py").read_text()
check("in a thread, so start-up does not wait for it either",
      re.search(r"threading\.Thread\(target=certs\.local_names", main) is not None)

# ---------------------------------------------------------------- 5. the pages themselves
print("\n5. the pages, over HTTP")
if not (FIXTURE / "uplive.sh").exists():
    print(f"  skip  no {FIXTURE}/uplive.sh")
else:
    subprocess.run(["bash", str(FIXTURE / "uplive.sh")], check=True, timeout=180)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  skip  playwright not installed here")
        sync_playwright = None
    if sync_playwright:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
            page = b.new_page(viewport={"width": 1440, "height": 900})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto("http://127.0.0.1:8597", wait_until="load")
            page.wait_for_timeout(400)
            page.click('[data-user="admin"]')
            if page.query_selector("#lp"):
                page.fill("#lp", "secret123")
                page.click("#login button.primary")
            page.wait_for_selector(".card", timeout=45000)
            for name in ("dashboard", "general", "network"):
                took = page.evaluate("""async (p) => {
                  location.hash = '#/x';
                  await new Promise(r => setTimeout(r, 300));
                  const t = performance.now();
                  location.hash = '#/settings/' + p;
                  for (let i = 0; i < 200; i++) {
                    await new Promise(r => setTimeout(r, 25));
                    if (document.querySelector('#view .panel, #view .adm-grid, #view .lay-head')) break;
                  }
                  return Math.round(performance.now() - t);
                }""", name)
                print(f"       {name:10s} {took:5d} ms")
                check(f"{name} appears promptly ({took} ms)", took < 2000)
            # and the four really do overlap rather than queue
            overlap = page.evaluate("""async () => {
              const t0 = performance.now();
              performance.clearResourceTimings();
              location.hash = '#/';
              await new Promise(r => setTimeout(r, 400));
              location.hash = '#/settings/general';
              await new Promise(r => setTimeout(r, 2500));
              const want = ['admin/settings', 'admin/overview', 'admin/artwork-sources', 'admin/network'];
              const got = performance.getEntriesByType('resource')
                .filter(e => want.some(w => e.name.includes(w)) && e.startTime > t0)
                .map(e => ({ n: e.name.split('/api/')[1], a: e.startTime, b: e.responseEnd }));
              return got;
            }""")
            names = {g["n"].split("?")[0] for g in overlap}
            check(f"all four requests were made ({len(names)})", len(names) >= 4)
            if len(overlap) >= 4:
                starts = sorted(g["a"] for g in overlap)
                # in series they start one answer apart; together, within a tick of each other. Measured:
                # 0 ms together, 146 ms in series on this machine, where each answer takes about 50 ms.
                check(f"and they started together, not in a queue ({starts[-1] - starts[0]:.0f} ms apart)",
                      starts[-1] - starts[0] < 60)
            check(f"no errors on the page ({errs[:1]})", not errs)
            b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
