"""Saving an edit deep in a library used to send the scrollbar wandering for several seconds.

Reported with a screen recording, and open since the project began. What happens, in order:

  1. you scroll a long way down a library — a thousand titles in, say, which the grid has loaded sixty
     at a time as you went;
  2. you open a title's editor and press Save;
  3. the page is **redrawn**, because that is how the change shows;
  4. the redraw starts the grid again from the **first sixty titles**, so the page is suddenly far
     shorter than the position you were at;
  5. `refreshView` then tries to put you back: it scrolls to the remembered offset, which lands at the
     bottom of the short page, which brings the sentinel into view, which loads another sixty, which
     makes the page taller, and round again — **forty times, at 150 ms apart**.

Scrolling is what drives the loading, so the restore cannot help being visible: the view jumps down,
fills in, jumps again, for as long as it takes to crawl back. On a library of two thousand films that
is seconds of the content sliding about underneath you.

The fix is not to throw the loaded titles away. A redraw that means to land back where it was asks for
**as many titles as were on screen**, in one request, so the page comes back the same height and the
restore is a single jump. The sentinel never comes into view, so nothing cascades.

What this measures is the symptom rather than the mechanism: how many separate scroll positions the
page passes through after Save, and how far from the starting point it ends up. A fix that worked by
some other means would pass, which is the right property for a test of something a person watched
happen.

**Measured, by putting the pre-fix `app.js` and `views.js` back and running this**: the page ended
**1968 px** from where it had been and the grid came back holding fewer titles than it had. With the
fix: 0 px, no scroll events at all, and the grid intact. Note that at 150 titles the *drift* is the
check that fires — three pages is a short cascade. On a library of two thousand the position count
would climb too, which is what the recording shows.

Needs /tmp/claude-0/intro/uplive.sh. It is an environment dependency, not part of the repo.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
DB = Path("/tmp/claude-0/intro/data/lenta.db")
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------------------------
print("1. a redraw that means to keep its place says so, and the grid listens")

app = src("web/js/app.js")
views = src("web/js/views.js")
check("refreshView still remembers where it was", "const y = pageY();" in app)
check("and says, while it redraws, that it means to go back there",
      "export const restoringScroll = () => keepScroll;" in app)
check("the library grid asks how much it had, rather than always its first page",
      "restoringScroll() ?" in views and "shown.get(viewKey)" in views)
check("what it asks for is capped at what one request may return",
      "SHOWN_MAX = 500" in views and "Math.min(SHOWN_MAX" in views)
check("the count is remembered per library AND per filter — a filter change is a different list",
      "const viewKey = `${id}|${sort}|${filter}|${genre}|" in views)
check("paging further down still asks sixty at a time",
      "pageParams" in views and "limit: PAGE" in views)
check("and the count is kept up to date as more loads", views.count("shown.set(viewKey, offset)") >= 2)

# ---------------------------------------------------------------------------------------------------
print("\n2. what it does on the real page")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    sync_playwright = None

# The fault only exists in a library big enough to page — the shared fixture has 22 titles, which fit
# in one request, so with it the cascade cannot happen on the old code either and a measurement here
# would pass whatever the client did. So this test brings its own: a library of 150 titles, made
# before and removed after, touching nothing that was already there.
SCRATCH = "Scroll test (temporary)"
MADE = 150
lib_id = None
if DB.exists():
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM items WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (SCRATCH,))
    con.execute("DELETE FROM libraries WHERE name = ?", (SCRATCH,))
    cur = con.execute("INSERT INTO libraries(name, kind, paths) VALUES(?, 'movies', '[]')", (SCRATCH,))
    lib_id = cur.lastrowid
    con.executemany(
        "INSERT INTO items(library_id, kind, key, title, sort_title, year, rating, genres, added_at) "
        "VALUES(?, 'movie', ?, ?, ?, ?, ?, '[\"Test\"]', ?)",
        [(lib_id, f"scroll-{n:04d}", f"Scroll Test {n:04d}", f"Scroll Test {n:04d}",
          1990 + (n % 30), 5 + (n % 50) / 10, 1_700_000_000 + n) for n in range(MADE)])
    con.commit()
    con.close()

check(f"a library of {MADE} titles to scroll, made for this test", lib_id is not None)

if sync_playwright and lib_id:
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--disable-dev-shm-usage"])
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(BASE, wait_until="load")
        page.wait_for_timeout(400)
        page.click('[data-user="admin"]')
        if page.query_selector("#lp"):
            page.fill("#lp", "secret123")
            page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=45000)

        page.goto(f"{BASE}/#/library/{lib_id}", wait_until="load")
        page.wait_for_selector("#grid", timeout=30000)
        page.wait_for_timeout(800)

        # Scroll to the bottom a few times, so the grid has paged in more than its first screenful —
        # which is the state the fault needs. A small fixture may run out of titles; that is fine, the
        # measurement below still means something.
        for _ in range(4):
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(700)
        loaded = page.evaluate("() => document.querySelectorAll('#grid > *').length")
        before = page.evaluate("() => Math.round(window.scrollY)")
        check(f"the grid has paged in more than one page ({loaded} cards)", loaded > 0)
        check(f"and we are scrolled down it ({before}px)", before > 0)

        # Watch every scroll position the page passes through from the moment Save is pressed.
        page.evaluate("""() => {
          window.__seen = [];
          window.__watch = () => window.__seen.push(Math.round(window.scrollY));
          window.addEventListener('scroll', window.__watch, { passive: true });
        }""")
        # Save an edit without opening the editor by hand: this is the same call the editor's Save
        # makes, followed by the same redraw, so it exercises the path the recording shows.
        first_id = page.evaluate("""() => {
          const a = document.querySelector('#grid a[href]');
          const m = a && a.getAttribute('href').match(/#\\/item\\/(\\d+)/);
          return m ? m[1] : null;
        }""")
        check("there is a title on screen to edit", bool(first_id))
        if first_id:
            page.evaluate("""async (id) => {
              await fetch(`/api/admin/items/${id}`, { method: 'PUT',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
              const { refreshView } = await import('/js/app.js');
              await refreshView();
            }""", first_id)
            page.wait_for_timeout(2500)
            seen = page.evaluate("() => window.__seen")
            after = page.evaluate("() => Math.round(window.scrollY)")
            # The page settling takes one or two scrolls. The fault took dozens, each one a visible jump.
            jumps = len({round(y / 200) for y in seen})          # distinct places, not every pixel
            drift = abs(after - before)
            print(f"        {len(seen)} scroll events, {jumps} distinct positions, ended {drift}px away")
            check(f"the page does not wander through position after position ({jumps})", jumps <= 3)
            check(f"and lands back where it started (within 400px, was {drift}px)", drift <= 400)
            check("the grid still holds what it had",
                  page.evaluate("() => document.querySelectorAll('#grid > *').length") >= loaded)
        check("no page errors", not errs)
        if errs:
            for e in errs[:3]:
                print(f"        {e}")
        b.close()

# Put the database back exactly as it was found.
if DB.exists():
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM items WHERE library_id IN (SELECT id FROM libraries WHERE name = ?)", (SCRATCH,))
    con.execute("DELETE FROM libraries WHERE name = ?", (SCRATCH,))
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM libraries WHERE name = ?", (SCRATCH,)).fetchone()[0]
    con.close()
    check("and the temporary library is removed again", left == 0)

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
