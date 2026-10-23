"""A library's genre tiles: what they show, where they go, and who decides.

A library grid is two thousand films in the order they arrived. Finding something in a mood — a
thriller, a comedy — means knowing what you want before you start, and the genre dropdown in the
toolbar asks you to. The row of tiles turns that round: **what is this shelf mostly made of**, as
pictures, with the best of each genre one press away.

Four decisions in it, and each is checked here:

  **Commonest first, not alphabetical.** The row exists to describe the library. A genre with two
  titles in a collection of two thousand is noise, so `MIN_TITLES` keeps it off — unless the library
  is small, where anything beats an empty row. That is the *automatic* row, and it is the default.

  **Or exactly the genres he named, in his order** (1.20.2). A curated shelf is not the same thing as
  a description of one, so an administrator can choose the genres and arrange them, and then no floor
  and no count applies — the row is the list. A genre the library has since lost is dropped from the
  row rather than shown opening on nothing, and the choice is kept either way.

  **A tile borrows the artwork of its best-rated title**, and the drill-down opens that same genre
  sorted by rating. So the face on the tile is the first thing behind it, not a random still.

  **It is the administrator's**, server-wide, like the shape of Home (see `api_prefs.OWNER_HOME`): a
  library is one shelf for the whole house. Settings › Libraries › Libraries sections.

  **Off unless switched on**, per library, and the endpoint itself answers with nothing when it is off
  — so the client needs to know nothing about the setting, and a viewer cannot read it.

Needs /tmp/claude-0/intro/uplive.sh. It is an environment dependency, not part of the repo.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path("/home/claude/lenta")
BASE = "http://127.0.0.1:8597"
FIXTURE = Path("/tmp/claude-0/intro")
DB = FIXTURE / "data" / "lenta.db"
os.environ.setdefault("LENTA_DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, str(ROOT / "server"))

fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


def src(rel):
    return (ROOT / rel).read_text()


# ---------------------------------------------------------------------------------------------------
print("1. the configuration: whose it is, and what it will accept")

prefs_admin = src("server/lenta/api_admin.py")
check("the setting exists and is server-wide, not per person", '"library_sections": ""' in src("server/lenta/db.py"))
check("it is editable by an administrator", 'k != "jwt_secret"' in prefs_admin and "EDITABLE" in prefs_admin)
check("and what it accepts is validated rather than trusted", '"library_sections"' in prefs_admin
      and "Unknown library sections" in prefs_admin)

from lenta import api_library, db as lentadb                                # noqa: E402
lentadb.init_db()
# Absent before 1.20.0, which is what section 1 then reports rather than crashing out of the run.
GENRE_TILES_MAX = getattr(api_library, "GENRE_TILES_MAX", 0)
genre_tile_count = getattr(api_library, "genre_tile_count", lambda _id: -1)
check("the server can say how many tiles a library shows", GENRE_TILES_MAX > 0)

lentadb.set_setting("library_sections", json.dumps({"1": 12, "2": 999, "3": "nonsense", "4": 0}))
check("a configured library reports its tile count", genre_tile_count(1) == 12)
check("an absurd count is clamped rather than obeyed", genre_tile_count(2) == GENRE_TILES_MAX)
check("rubbish reads as off, not as a crash", genre_tile_count(3) == 0)
check("zero means off", genre_tile_count(4) == 0)
check("a library nobody configured is off", genre_tile_count(77) == 0)
lentadb.set_setting("library_sections", "")
check("no configuration at all means every library is off", genre_tile_count(1) == 0)

# The other stored shape: the genres named, in order. A plain number is 1.20.0's and still read, so
# an upgrade changes nothing until he opens the page and saves.
genre_tile_choice = getattr(api_library, "genre_tile_choice", lambda _id: (-1, []))
lentadb.set_setting("library_sections", json.dumps({
    "1": {"count": 12, "genres": ["Drama", "Action"]},
    "2": {"count": 8},
    "3": {"genres": ["Action", "Action", " ", "Drama"]},
    "4": 6,
}))
check("a named list is read in the order it was given", genre_tile_choice(1) == (2, ["Drama", "Action"]))
check("and sets the length itself, whatever count says beside it", genre_tile_count(1) == 2)
check("an object with no list falls back to its count", genre_tile_choice(2) == (8, []))
check("a repeated or empty name is dropped rather than tiled twice",
      genre_tile_choice(3) == (2, ["Action", "Drama"]))
check("and the older bare number still means the automatic row", genre_tile_choice(4) == (6, []))
lentadb.set_setting("library_sections", "")

# ---------------------------------------------------------------------------------------------------
print("\n2. the drill-down is the promise the tile makes")

ui = src("web/js/ui.js")
check("a tile links into its own genre", "genre=${encodeURIComponent(g.name)}" in ui)
check("sorted by rating, which is what makes it a shortcut and not just a filter",
      "&sort=rating" in ui)
check("the row is nothing at all when there are no tiles", "if (!genres || !genres.length) return ''" in ui)
check("and the server already supports both halves of that address",
      '"rating": "i.rating DESC NULLS LAST, i.sort_title"' in src("server/lenta/api_library.py"))

views = src("web/js/views.js")
check("the tiles are fetched beside the grid, not before it",
      "Promise.all([" in views and "genre-tiles" in views)
check("a failure to fetch them leaves the page working",
      ".catch(() => ({ genres: [] }))" in views)
check("the row is hidden once you are inside a genre — it is a way in, not a way back",
      "${genre || filter || q.get('q') ? '' : genreRow(tiles.genres, id)}" in views)

# ---------------------------------------------------------------------------------------------------
print("\n3. against the real server")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip  playwright not installed here")
    sync_playwright = None

lib_id = show_id = livetv_id = None
show_genres = []
if DB.exists():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT l.id, COUNT(*) n FROM libraries l JOIN items i ON i.library_id = l.id "
                      "WHERE l.kind = 'movies' GROUP BY l.id ORDER BY n DESC LIMIT 1").fetchone()
    lib_id = row["id"] if row else None
    # A television library, for section 4 — the shape of library the row was broken on.
    row = con.execute("SELECT l.id, COUNT(*) n FROM libraries l JOIN items i ON i.library_id = l.id "
                      "WHERE l.kind = 'shows' AND i.kind = 'show' GROUP BY l.id ORDER BY n DESC LIMIT 1").fetchone()
    show_id = row["id"] if row else None
    if show_id:
        show_genres = [r["genre"] for r in con.execute(
            "SELECT DISTINCT g.value AS genre FROM items i, json_each(i.genres) g "
            "WHERE i.library_id = ? AND i.kind = 'show' ORDER BY g.value", (show_id,))]
    row = con.execute("SELECT id FROM libraries WHERE kind = 'livetv' LIMIT 1").fetchone()
    livetv_id = row["id"] if row else None
    con.close()
check("the fixture has a movie library to work with", lib_id is not None)
check(f"and a television library with genres on its shows ({show_genres})", len(show_genres) >= 2)

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
        page.wait_for_timeout(600)

        def tiles():
            return page.evaluate(
                "async (id) => (await (await fetch(`/api/libraries/${id}/genre-tiles`)).json()).genres", lib_id)

        # --- off by default
        page.evaluate("""async () => { await fetch('/api/admin/settings', { method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library_sections: '{}' }) }); }""")
        check("with nothing switched on, the endpoint answers with no genres", tiles() == [])
        page.goto(f"{BASE}/#/library/{lib_id}", wait_until="load")
        page.wait_for_selector("#grid", timeout=30000)
        page.wait_for_timeout(600)
        check("and the library page draws no row", page.query_selector(".genre-row") is None)

        # --- switched on
        page.evaluate("""async (id) => { await fetch('/api/admin/settings', { method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library_sections: JSON.stringify({ [id]: 8 }) }) }); }""", lib_id)
        got = tiles()
        check(f"switched on, the endpoint returns tiles ({len(got)})", len(got) > 0)
        check("no more than the number configured", len(got) <= 8)
        check("every tile names a genre and how many titles carry it",
              all(g.get("name") and isinstance(g.get("count"), int) and g["count"] > 0 for g in got))
        check("commonest first — the row describes the shelf",
              [g["count"] for g in got] == sorted([g["count"] for g in got], reverse=True))
        check("each tile carries artwork to show, or honestly carries none",
              all("poster" in g and "backdrop" in g for g in got))

        # The title lent to a genre must be the best-rated one in it — the same title the drill-down
        # puts first. Checked against the library's own answer rather than against the database, so it
        # is the two endpoints agreeing, which is what a person actually experiences.
        if got:
            g0 = got[0]
            first_in = page.evaluate(
                "async ([id, name]) => (await (await fetch(`/api/libraries/${id}/items?genre=`"
                "+ encodeURIComponent(name) + `&sort=rating&limit=1`)).json()).items[0]",
                [lib_id, g0["name"]])
            check(f"the tile for '{g0['name']}' shows the title the drill-down opens with",
                  bool(first_in) and first_in.get("title") == g0.get("top_title"))

        # --- the page draws it, and a tile goes where it says
        # `goto` to an address that differs only in its hash does not reload, and the page was already
        # on this library for the check above — so reload rather than navigate, or the row that was
        # correctly absent a moment ago stays absent and the test reads as a fault in the client.
        page.reload(wait_until="load")
        page.wait_for_selector(".genre-row", timeout=30000)
        page.wait_for_timeout(500)
        n = len(page.query_selector_all(".genre-row .genre-tile"))
        check(f"the library page draws the row ({n} tiles)", n > 0)
        check("the row sits above the grid",
              page.evaluate("""() => {
                const r = document.querySelector('.genre-row'), g = document.querySelector('#grid');
                return !!(r && g) && (r.compareDocumentPosition(g) & Node.DOCUMENT_POSITION_FOLLOWING) > 0;
              }"""))
        first_tile = page.query_selector(".genre-row .genre-tile")
        name = first_tile.inner_text().strip().split("\n")[0]
        first_tile.click()
        page.wait_for_timeout(1200)
        check(f"clicking '{name}' filters the library to it, best rated first",
              "genre=" in page.url and "sort=rating" in page.url)
        page.wait_for_selector("#grid", timeout=30000)
        page.wait_for_timeout(500)
        check("and inside a genre the row is gone", page.query_selector(".genre-row") is None)
        check("the page says which genre you are in",
              name.lower() in (page.query_selector(".page-sub").inner_text() or "").lower())

        # --- the Settings page
        page.goto(f"{BASE}/#/settings/library-sections", wait_until="load")
        page.wait_for_timeout(1500)
        check("Settings has the page, under Libraries",
              "library-sections" in src("web/js/settings-shell.js")
              and page.query_selector("#ls-save") is not None)
        check("it lists the libraries with a switch for each",
              len(page.query_selector_all("[data-on]")) > 0)
        nav = page.query_selector(".sset-nav")
        check("and it is reachable from the sidebar by its name, under Libraries",
              bool(nav) and "Libraries sections" in nav.inner_text())

        # -------------------------------------------------------------------------------------------
        print("\n4. a television library — the fault 1.20.1 shipped")
        # `_top_columns("show")` carries UNWATCHED_SHOW, which binds `:uid`. The tile query supplied
        # only the library, the kind and the genre, so **every TV library raised** — and because the
        # client catches a failed fetch so that the page keeps working, the row was simply absent with
        # nothing said anywhere. He reported it as "Browse by genre not visible in TV Shows".
        api_src = src("server/lenta/api_library.py")
        check("the tile's artwork lookup supplies a user id, as the show columns require",
              '"genre": name, "uid": user["id"]' in api_src)
        check("and the kinds that have no genres to tile are named rather than assumed",
              'GENRE_TILE_KINDS = ("movies", "shows", "music", "audiobooks")' in api_src)

        if show_id:
            page.evaluate("""async (id) => { await fetch('/api/admin/settings', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_sections: JSON.stringify({ [id]: 8 }) }) }); }""", show_id)
            # `genres` comes back missing when the request fails, which is the whole point of this
            # section — so it is read defensively, or the test dies here instead of reporting.
            r = page.evaluate("async (id) => { const x = await fetch(`/api/libraries/${id}/genre-tiles`);"
                              " let b = {}; try { b = await x.json(); } catch (e) { }"
                              " return { status: x.status, genres: b.genres || [] }; }", show_id)
            check(f"a TV library answers at all (HTTP {r['status']}, not a 500)", r["status"] == 200)
            check(f"and with tiles on it ({[g['name'] for g in r['genres']]})", len(r["genres"]) > 0)
            check("each one carrying a count", bool(r["genres"]) and all(g["count"] > 0 for g in r["genres"]))
            page.goto(f"{BASE}/#/library/{show_id}", wait_until="load")
            page.wait_for_selector("#grid", timeout=30000)
            page.wait_for_timeout(700)
            check("and the TV library's own page draws the row",
                  len(page.query_selector_all(".genre-row .genre-tile")) > 0)

        if livetv_id:
            # A live TV library has channels rather than titles. It was offered a row until 1.20.2 and
            # could never have shown one; now it is answered with nothing even if something asks.
            page.evaluate("""async (id) => { await fetch('/api/admin/settings', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_sections: JSON.stringify({ [id]: 8 }) }) }); }""", livetv_id)
            r = page.evaluate("async (id) => { const x = await fetch(`/api/libraries/${id}/genre-tiles`);"
                              " let b = {}; try { b = await x.json(); } catch (e) { }"
                              " return { status: x.status, genres: b.genres || null }; }", livetv_id)
            check(f"a live TV library is answered with no genres rather than raising (HTTP {r['status']})",
                  r["status"] == 200 and r["genres"] == [])

        # -------------------------------------------------------------------------------------------
        print("\n5. the genres he chose, in the order he chose them")
        # The automatic row describes the library. This is the other half: a row that is exactly what
        # was asked for, in the given order, whatever the counts say.
        if show_id and len(show_genres) >= 2:
            a, bb = show_genres[0], show_genres[1]

            def chosen(names):
                # Both halves read defensively: a server that does not know the object shape rejects
                # the write, and this section is exactly the one that must report that rather than die.
                return page.evaluate("""async ([id, names]) => {
                    await fetch('/api/admin/settings', { method: 'PUT',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ library_sections: JSON.stringify({ [id]: { genres: names } }) }) });
                    const x = await fetch(`/api/libraries/${id}/genre-tiles`);
                    let b = {}; try { b = await x.json(); } catch (e) { }
                    return (b.genres || []).map(g => g.name);
                  }""", [show_id, names])

            check(f"the row is the list given, in that order ({a} then {bb})", chosen([a, bb]) == [a, bb])
            check("and reversing the list reverses the row — it is his order, not a sort",
                  chosen([bb, a]) == [bb, a])
            check("a genre the library no longer has is left out rather than shown empty",
                  chosen([a, "No Such Genre At All"]) == [a])
            check("a chosen list sets the length itself, with no floor applied",
                  chosen([a]) == [a])
            got = page.evaluate("async () => (await (await fetch('/api/admin/settings')).json()).library_sections")
            check("what is stored is the object shape, normalised by the server",
                  json.loads(got or "{}").get(str(show_id)) == {"count": 1, "genres": [a]})
            # The older shape must keep working: it is what 1.20.0 wrote and what his server holds.
            page.evaluate("""async (id) => { await fetch('/api/admin/settings', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_sections: JSON.stringify({ [id]: 6 }) }) }); }""", show_id)
            check("a bare number still means the automatic row",
                  len(page.evaluate("async (id) => { const x = await fetch(`/api/libraries/${id}/genre-tiles`);"
                                    " let b = {}; try { b = await x.json(); } catch (e) { }"
                                    " return b.genres || []; }", show_id)) > 0)

        # -------------------------------------------------------------------------------------------
        print("\n6. choosing and ordering them from Settings")
        page.evaluate("""async () => { await fetch('/api/admin/settings', { method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library_sections: '' }) }); }""")
        page.goto(f"{BASE}/#/settings/library-sections", wait_until="load")
        page.wait_for_selector("#ls-save", timeout=30000)
        page.wait_for_timeout(1800)
        names = page.evaluate("""() => [...document.querySelectorAll('.table tbody tr')]
            .map(tr => tr.querySelector('td').innerText.split('\\n')[0])""")
        check(f"the page offers the libraries that have genres ({len(names)})", len(names) > 0)
        check("and does not offer a live TV library, which has channels rather than titles",
              not any("Live TV" in n for n in names))

        # Save threw once it had awaited, because `e.currentTarget` is null by then: the write went
        # through, nothing was said, and **Save stayed greyed until you left the page and came back**.
        # Pressed here with nothing changed, which is the plainest form of it.
        page.click("#ls-save")
        page.wait_for_timeout(1000)
        check("pressing Save leaves the button usable rather than greyed for good",
              not page.is_disabled("#ls-save"))

        # Everything below needs the Choose button, which only 1.20.2 has — gated rather than clicked
        # blind, so an older page reports one clear failure instead of a Playwright timeout.
        can_pick = bool(show_id) and len(show_genres) >= 2 and page.query_selector(f'[data-pick="{show_id}"]') is not None
        check("each library has a Choose button to pick and order its genres with", can_pick)
        if can_pick:
            page.click(f'[data-on="{show_id}"]')
            check("Choose is unavailable until the row is switched on, and available after",
                  not page.is_disabled(f'[data-pick="{show_id}"]'))
            page.click(f'[data-pick="{show_id}"]')
            page.wait_for_selector("#gt-done", timeout=15000)
            check("it opens on the automatic row, which is the default",
                  page.evaluate("() => !document.querySelector('#gt-common-f').hidden"))
            page.check('input[name="gt-mode"][value="chosen"]')
            page.wait_for_timeout(200)
            check("choosing 'the ones I choose' shows the list and hides the count",
                  page.evaluate("""() => !document.querySelector('#gt-chosen-f').hidden
                                   && document.querySelector('#gt-common-f').hidden"""))
            check("every genre in the library is offered to add",
                  sorted(page.evaluate("() => [...document.querySelectorAll('#gt-add option')].map(o => o.value)"))
                  == sorted(show_genres))
            page.select_option("#gt-add", show_genres[0])
            page.click("#gt-add-btn")
            page.select_option("#gt-add", show_genres[1])
            page.click("#gt-add-btn")
            listed = page.evaluate("() => [...document.querySelectorAll('#gt-list li[data-genre]')].map(li => li.dataset.genre)")
            check(f"both are added, in the order added ({listed})", listed == show_genres[:2])
            check("a genre already chosen is not offered again",
                  page.evaluate("() => document.querySelector('#gt-add').disabled") or
                  show_genres[0] not in page.evaluate("() => [...document.querySelectorAll('#gt-add option')].map(o => o.value)"))
            page.click(f'#gt-list li[data-genre="{show_genres[1]}"] [data-move="-1"]')
            moved = page.evaluate("() => [...document.querySelectorAll('#gt-list li[data-genre]')].map(li => li.dataset.genre)")
            check(f"and one can be moved up the list ({moved})", moved == [show_genres[1], show_genres[0]])
            check("the first in the list cannot be moved above itself",
                  page.is_disabled('#gt-list li:first-child [data-move="-1"]'))
            page.click('#gt-list li:first-child [data-remove]')
            check("and one can be taken out again",
                  page.evaluate("() => document.querySelectorAll('#gt-list li[data-genre]').length") == 1)
            page.select_option("#gt-add", show_genres[1])
            page.click("#gt-add-btn")
            page.click("#gt-done")
            page.wait_for_timeout(300)
            check("the table then says what was chosen",
                  show_genres[0] in page.evaluate(f"""() => document.querySelector('[data-choice="{show_id}"]').textContent"""))
            page.click("#ls-save")
            page.wait_for_timeout(1000)
            stored = page.evaluate("async () => (await (await fetch('/api/admin/settings')).json()).library_sections")
            check(f"saving writes the chosen genres in order ({stored})",
                  json.loads(stored or "{}").get(str(show_id), {}).get("genres") == [show_genres[0], show_genres[1]])
            # Save used to throw after the save went through — `e.currentTarget` is null once the
            # handler has awaited — so the row was written, nothing was said, and **Save stayed greyed
            # until you left the page and came back**. That is a symptom he has reported before.
            check("and Save is usable again afterwards, not left greyed", not page.is_disabled("#ls-save"))
            page.reload(wait_until="load")
            page.wait_for_selector("#ls-save", timeout=30000)
            page.wait_for_timeout(1800)
            page.click(f'[data-pick="{show_id}"]')
            page.wait_for_selector("#gt-done", timeout=15000)
            check("and reopening it shows the choice as saved, in order",
                  page.evaluate("""() => document.querySelector('input[name="gt-mode"]:checked').value""") == "chosen"
                  and page.evaluate("() => [...document.querySelectorAll('#gt-list li[data-genre]')].map(li => li.dataset.genre)")
                  == [show_genres[0], show_genres[1]])
            page.click("[data-close]")
            page.wait_for_timeout(200)

        # -------------------------------------------------------------------------------------------
        print("\n7. where the row sits on the page")
        # Both reported by eye, from a screenshot: the tiles started a whole gutter in from the grid
        # underneath them, and the grid sat right up against them. The row lives inside `.page`, which
        # has already applied `--gutter`, and `.row h2` and `.track` each applied it again.
        css = src("web/css/app.css")
        check("the row simply does not apply the gutter a second time",
              ".page .row.genre-row h2 { margin-left: 0" in css
              and ".page .row.genre-row .track { padding-left: 0" in css)
        check("**and does not try to subtract it instead** — `--gutter` is a percentage in the Netflix "
              "theme, and two percentages of different boxes do not cancel (1.21.0)",
              "margin-left: calc(-1 * var(--gutter))" not in css)
        check("the heading carries a class more than the themes' own `.row h2`, or it loses to them",
              ".page .row.genre-row h2" in css)
        check("and given room before whatever comes next", "margin-bottom: 52px" in css)
        # The name wears the "Recently added" badge's style, asked for by name in 1.20.3. The numbers
        # are copied from `[data-theme="hbo"] .new-badge` rather than referred to, because that badge
        # belongs to a theme and the tile belongs to every theme — so this checks the two agree today,
        # and is the thing that notices if one is restyled without the other.
        hbo = src("web/css/theme-hbo.css")
        badge = hbo[hbo.index('[data-theme="hbo"] .new-badge'):][:460]
        # Scoped to the `.genre-name` rule itself, not searched for anywhere in the stylesheet:
        # `border-radius: 100px` and `text-transform: uppercase` both appear elsewhere in app.css, so
        # a whole-file search for them passes on 1.20.2, where the pill did not exist at all.
        pill = css[css.index(".genre-name {"):].split("}")[0]
        for prop in ["rgba(0, 0, 0, .62)", "rgba(255, 255, 255, .28)", "border-radius: 100px",
                     "letter-spacing: .1em", "text-transform: uppercase"]:
            # the theme writes its colours in the modern slash form; the tile cannot, see below
            want = prop.replace("rgba(", "rgb(").replace(", ", " ").replace(" .62)", " / .62)").replace(" .28)", " / .28)")
            check(f"the badge still uses {prop} — if not, the tile has drifted from it",
                  prop in badge or want in badge)
            check(f"and so does the genre pill ({prop})", prop in pill)
        check("the pill is centred on the tile rather than sitting in a corner",
              "left: 50%; top: 50%; transform: translate(-50%, -50%)" in pill)
        check("its colours are written out in rgba(), which the 2017 engine can read",
              "rgb(0 0 0 /" not in pill and "rgb(255 255 255 /" not in pill)
        check("and the scrim behind it is flat, not a gradient aimed at the bottom edge",
              "background: rgba(0, 0, 0, .28)" in css)
        check("a television gets the same pill at a size readable across a room",
              "html.tv .genre-name { font-size: 20px !important" in src("web/css/tv.css"))
        check("the built television stylesheets carry it too",
              ".row.genre-row .track{padding-left:0" in src("web/legacy/app.css")
              and ".row.genre-row .track{padding-left:0" in src("web/legacy/mid.css"))

        if lib_id:
            page.evaluate("""async (id) => { await fetch('/api/admin/settings', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ library_sections: JSON.stringify({ [id]: 6 }) }) }); }""", lib_id)
            # The theme is pinned, and **the Netflix theme is the one measured**, because its `--gutter`
            # is a percentage (4%) where every other theme's is a clamp in px. 1.20.2's alignment was
            # a negative margin that cancelled the gutter — which works in px and does not in per cent,
            # since each percentage resolves against a different box. This section passed for two
            # releases only because whatever theme the suite happened to leave behind used px; it went
            # red the day another test left the Netflix theme on. Measure the awkward one on purpose.
            theme_was = page.evaluate("async () => (await (await fetch('/api/me/prefs')).json()).theme")
            page.evaluate("""async () => { await fetch('/api/me/prefs', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: 'netflix' }) }); }""")
            page.goto(f"{BASE}/#/library/{lib_id}", wait_until="load")
            page.reload(wait_until="load")
            page.wait_for_selector(".genre-row .genre-tile", timeout=30000)
            page.wait_for_timeout(900)
            check("the theme being measured is the one whose gutter is a percentage",
                  "%" in page.evaluate("() => getComputedStyle(document.documentElement)"
                                       ".getPropertyValue('--gutter').trim()"))
            m = page.evaluate("""() => {
              const tile = document.querySelector('.genre-row .genre-tile');
              const card = document.querySelector('#grid > *');
              const h2 = document.querySelector('.genre-row h2');
              const title = document.querySelector('.page-title');
              const row = document.querySelector('.genre-row');
              if (!tile || !card || !h2 || !title) return null;
              const r = e => e.getBoundingClientRect();
              const n = tile.querySelector('.genre-name');
              const t = r(tile), nb = n && r(n);
              return { tile: Math.round(r(tile).left), card: Math.round(r(card).left),
                       h2: Math.round(r(h2).left), title: Math.round(r(title).left),
                       gap: Math.round(r(card).top - r(row).bottom),
                       dx: nb ? Math.round((nb.left + nb.width / 2) - (t.left + t.width / 2)) : null,
                       dy: nb ? Math.round((nb.top + nb.height / 2) - (t.top + t.height / 2)) : null,
                       fits: nb ? nb.width <= t.width : null };
            }""")
            check("there is a row and a grid to measure", m is not None)
            if m:
                check(f"the first tile starts where the grid's first card does ({m['tile']} vs {m['card']})",
                      abs(m["tile"] - m["card"]) <= 1)
                check(f"and the heading where the page's title does ({m['h2']} vs {m['title']})",
                      abs(m["h2"] - m["title"]) <= 1)
                check(f"with room between the tiles and the grid ({m['gap']}px)", m["gap"] >= 44)
                # Measured on the page rather than read off the stylesheet, for the same reason the
                # alignment above is: it is what he is looking at.
                check(f"the name sits dead centre of its tile ({m['dx']}, {m['dy']} px off)",
                      m["dx"] is not None and abs(m["dx"]) <= 1 and abs(m["dy"]) <= 1)
                check("and a long genre name stays inside the tile rather than overhanging it",
                      m["fits"] is True)
            # Every theme, not only the two awkward ones: the row is in every library page, and a
            # gutter that is a clamp, a percentage or a plain length must all land on the same edge.
            off = page.evaluate("""async () => {
              const bad = [];
              for (const t of ['netflix', 'hbo', 'projector', 'noir', 'abyss', 'velvet', 'aurora']) {
                await fetch('/api/me/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ theme: t }) });
                document.documentElement.dataset.theme = t;
                await new Promise(r => setTimeout(r, 120));
                const tile = document.querySelector('.genre-row .genre-tile');
                const card = document.querySelector('#grid > *');
                const h2 = document.querySelector('.genre-row h2');
                const title = document.querySelector('.page-title');
                if (!tile || !card || !h2 || !title) { bad.push(t + ':missing'); continue; }
                const L = e => Math.round(e.getBoundingClientRect().left);
                if (L(tile) !== L(card) || L(h2) !== L(title))
                  bad.push(`${t} tile ${L(tile)}/${L(card)} h2 ${L(h2)}/${L(title)}`);
              }
              return bad;
            }""")
            check(f"the row lines up in every theme, not just the one that was on ({off or 'all seven'})",
                  off == [])
            page.evaluate("""async (t) => { await fetch('/api/me/prefs', { method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: t || '' }) }); }""", theme_was)

        # -------------------------------------------------------------------------------------------
        print("\n8. the row on a television")
        # Not a fix — it worked on arrival — but the row is new, he watches on televisions, and the
        # focus ring finds it by geometry rather than by name: a tile is reachable because the row is
        # one of `BANDS` in `tv.js` and the tile is a plain `a[href]`. Change either and this is the
        # only thing that would notice. One press of ▲ from where the page settles, on each of the
        # three clients, then ▶ along the row and OK into the genre.
        if lib_id:
            for label, qs in [("2023+", "?tv=1"), ("2017-18", "?tv=1&legacy=1"), ("2019-22", "?tv=1&legacy=2")]:
                tv = b.new_page(viewport={"width": 1920, "height": 1080})
                tverrs = []
                tv.on("pageerror", lambda e: tverrs.append(str(e)))
                try:
                    tv.goto(f"{BASE}/{qs}", wait_until="load")
                    tv.wait_for_timeout(600)
                    tv.click('[data-user="admin"]')
                    if tv.query_selector("#lp"):
                        tv.fill("#lp", "secret123")
                        tv.click("#login button.primary")
                    tv.wait_for_selector(".card", timeout=45000)
                    tv.goto(f"{BASE}/{qs}#/library/{lib_id}", wait_until="load")
                    tv.wait_for_selector(".genre-row .genre-tile", timeout=30000)
                    tv.wait_for_timeout(1200)
                    tier = tv.evaluate("() => window.LENTA_TIER")
                    geo = tv.evaluate("""() => {
                      const t = document.querySelector('.genre-row .genre-tile');
                      const c = document.querySelector('#grid > *');
                      const r = e => e.getBoundingClientRect();
                      return { left: Math.round(r(t).left), card: Math.round(r(c).left),
                               w: Math.round(r(t).width), h: Math.round(r(t).height) };
                    }""")
                    check(f"[{label}] the row draws, as tier '{tier}', aligned with the grid "
                          f"({geo['left']} vs {geo['card']})", geo["left"] == geo["card"])
                    # 16/9 is an aspect-ratio rule, which the 2017 engine has not got — so this also
                    # checks the fallback the legacy build puts in its place.
                    check(f"[{label}] and a tile keeps its 16/9 shape ({geo['w']}x{geo['h']})",
                          abs(geo["w"] / max(1, geo["h"]) - 16 / 9) < 0.06)
                    on = False
                    for _ in range(3):
                        tv.keyboard.press("ArrowUp")
                        tv.wait_for_timeout(350)
                        if tv.evaluate("() => !!(document.activeElement && document.activeElement.closest('.genre-row'))"):
                            on = True
                            break
                    check(f"[{label}] the remote reaches the row from where the page settles", on)
                    if on:
                        was = tv.evaluate("() => document.activeElement.getAttribute('href')")
                        tv.keyboard.press("ArrowRight")
                        tv.wait_for_timeout(350)
                        now = tv.evaluate("() => document.activeElement.getAttribute('href')")
                        check(f"[{label}] and walks along it", bool(now) and now != was)
                        tv.keyboard.press("Enter")
                        tv.wait_for_timeout(1500)
                        check(f"[{label}] OK opens the genre, best rated first",
                              "genre=" in tv.url and "sort=rating" in tv.url)
                    check(f"[{label}] no page errors", not tverrs)
                    if tverrs:
                        print(f"        {tverrs[0][:140]}")
                finally:
                    tv.close()

        check("no page errors anywhere in all that", not errs)
        if errs:
            for e in errs[:4]:
                print(f"        {e}")
        # Put the fixture back as it was found.
        page.evaluate("""async () => { await fetch('/api/admin/settings', { method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ library_sections: '' }) }); }""")
        b.close()

print()
if fails:
    print(f"{len(fails)} failed:")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("all good")
