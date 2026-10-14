"""What the streaming services are showing, and the three ways that can go wrong.

This is the one part of LENTA that depends on a service we cannot reach from a test — and on data whose
shape is TMDB's, not ours. So TMDB is stood in for here with recorded answers in its real shape, and
what gets checked is our half: that the right question is asked, that the answer is stored and comes
back as a row, and that the three rules this feature exists under actually hold.

Those rules, in the order they matter:

  Credit JustWatch.  TMDB gives this data on one condition and says access is revoked without it. A
  missing line of text is not a cosmetic bug here; it is the feature being switched off.

  Never offer what is already downstairs.  Being sent to Netflix for a film on your own disk is the
  exact opposite of what a media server is for.

  Never let Home wait on a network.  Rows come from SQLite. If TMDB is slow, unreachable, or has never
  been asked, Home draws at the same speed and simply has less on it.
"""
import json, os, sys, tempfile, time

os.environ["LENTA_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, "/home/claude/lenta/server")
from lenta import db, metadata, streaming                                  # noqa: E402

db.init_db()
fails = []


def check(name, ok):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(name)


# ---- TMDB, as it really answers --------------------------------------------------------------------
asked = []


def fake_get(self, path, **params):
    asked.append((path, params))
    if path == "/watch/providers/movie":
        return {"results": [
            {"provider_id": 8, "provider_name": "Netflix", "logo_path": "/nflx.jpg",
             "display_priority": 5, "display_priorities": {"BG": 1, "US": 7}},
            {"provider_id": 1899, "provider_name": "Max", "logo_path": "/max.jpg", "display_priority": 3},
            {"provider_id": 337, "provider_name": "Disney Plus", "logo_path": "/dis.jpg", "display_priority": 2},
        ]}
    if path == "/discover/movie":
        return {"results": [
            {"id": 101, "title": "A Film You Do Not Have", "release_date": "2026-02-01",
             "poster_path": "/a.jpg", "overview": "One", "vote_average": 7.5, "original_language": "en"},
            {"id": 202, "title": "A Film On Your Own Disk", "release_date": "2024-06-01",
             "poster_path": "/b.jpg", "overview": "Two", "vote_average": 8.1, "original_language": "en"},
            {"id": 303, "title": "Another One", "release_date": "2025-01-01",
             "poster_path": "/c.jpg", "overview": "Three", "vote_average": 6.0, "original_language": "ko"},
        ]}
    if path == "/discover/tv":
        return {"results": [{"id": 404, "name": "A Series", "first_air_date": "2026-03-03",
                             "poster_path": "/d.jpg", "overview": "Four", "vote_average": 9.0,
                             "original_language": "hi"}]}
    if path == "/genre/movie/list":
        return {"genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"},
                           {"id": 27, "name": "Horror"}, {"id": 878, "name": "Science Fiction"},
                           {"id": 14, "name": "Fantasy"}, {"id": 35, "name": "Comedy"}]}
    if path == "/genre/tv/list":
        # TMDB files series differently: no Science Fiction, no Fantasy, no Horror at all
        return {"genres": [{"id": 10759, "name": "Action & Adventure"}, {"id": 35, "name": "Comedy"},
                           {"id": 10765, "name": "Sci-Fi & Fantasy"}]}
    if path.endswith("/images"):
        return {"posters": [{"file_path": "/p1.jpg", "width": 2000, "height": 3000, "vote_count": 9,
                             "iso_639_1": "en"},
                            {"file_path": "/p2.jpg", "width": 1000, "height": 1500, "vote_count": 2,
                             "iso_639_1": None}],
                "backdrops": [{"file_path": "/b1.jpg", "width": 3840, "height": 2160, "vote_count": 7,
                               "iso_639_1": None}],
                "logos": []}
    if path.endswith("/external_ids"):
        tid = int(path.split("/")[2])
        return {"wikidata_id": f"Q{tid}"} if tid != 303 else {}      # not everything is in Wikidata
    if "/watch/providers" in path:
        tid = int(path.split("/")[2])
        if tid == 303:                       # TMDB does not always have a link for every country
            return {"results": {}}
        return {"results": {"BG": {"link": f"https://www.themoviedb.org/movie/{tid}/watch?locale=BG"}}}
    raise AssertionError(f"unexpected call: {path}")


metadata.TMDB._get = fake_get
metadata.TMDB.enabled = property(lambda self: True)
import httpx                                                               # noqa: E402

wiki_calls = []


class FakeWiki:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        wiki_calls.append(params or {})
        ids = (params or {}).get("ids", "").split("|")
        ents = {}
        for q in ids:
            n = q.lstrip("Q")
            # 404 has no Netflix id in this fixture: plenty of real titles do not either
            ents[q] = {"claims": {} if n == "404" else
                       {"P1874": [{"mainsnak": {"datavalue": {"value": f"8175{n}"}}}]}}

        class R:
            status_code = 200

            @staticmethod
            def raise_for_status():
                pass

            @staticmethod
            def json():
                return {"entities": ents}
        return R()


httpx.Client = FakeWiki

db.set_setting("tmdb_api_key", "x" * 32)
db.set_setting("metadata_region", "BG")


print("1. the services on offer are the ones available here, in the order this country uses")
got = streaming.providers()
check(f"{len(got)} services found", len(got) == 3)
check("Netflix is first, because JustWatch ranks it first in Bulgaria — not because we hardcoded it",
      got[0]["name"] == "Netflix" and got[0]["priority"] == 1)
check("the country asked about was Bulgaria, not the default",
      ("/watch/providers/movie", {"watch_region": "BG"}) in asked)
check("they are remembered, so the list is not re-fetched on every visit",
      (len(asked), streaming.providers() and len(asked)) [0] == len(asked))

print("\n2. what a service is showing, asked for correctly")
streaming.set_chosen([8])
asked.clear()
n = streaming.refresh(force=True)
check(f"{n} titles stored", n == 4)
disc = next(params for path, params in asked if path == "/discover/movie")
check(f"filtered to this service and this country: {disc.get('with_watch_providers')}, {disc.get('watch_region')}",
      disc.get("with_watch_providers") == "8" and disc.get("watch_region") == "BG")
check("and to subscription only — without this the row fills up with things you must rent",
      disc.get("with_watch_monetization_types") == "flatrate")
check("nothing used a search call, which is the quota that is actually scarce (100 a day)",
      not [a for a in asked if a[0].startswith("/search")])

print("\n3. a row comes out of it")
rows = streaming.cards(8, "movie")
check(f"{len(rows)} cards", len(rows) == 3)
check("each one says which service it is on", all(c["service"] == "Netflix" for c in rows))
check("in TMDB's popularity order", [c["title"] for c in rows][0] == "A Film You Do Not Have")
check("pictures go through LENTA's own image store, not a link to TMDB's servers",
      rows[0]["poster"] == "/api/img/tmdb/a.jpg")
check("the service's logo likewise", rows[0]["service_logo"] == "/api/img/tmdb/nflx.jpg")
check(f"each card goes into the service itself: {rows[0]['link']}",
      rows[0]["link"].startswith("https://www.netflix.com/"))
check("and never to a page about the film on somebody else's site",
      not any("themoviedb" in c["link"] or "justwatch" in c["link"] for c in rows))
check("the title is carried across exactly, so the service finds it",
      [c for c in rows if c["tmdb_id"] == 303][0]["link"] == "https://www.netflix.com/search?q=Another+One")
check("the rows are named for what the data is, not a chart TMDB cannot see",
      [r["title"] for r in streaming.rows_for_home()] == ["Popular on Netflix", "Popular series on Netflix"])

print("\n4. nothing you already own is advertised back at you")
with db.db() as con:
    con.execute("INSERT INTO libraries (id, name, kind, paths) VALUES (1, 'Movies', 'movies', '[\"/m\"]')")
    con.execute("INSERT INTO items (id, library_id, kind, key, title, tmdb_id)"
                " VALUES (1, 1, 'movie', 'mine', 'Mine', 202)")
rows = streaming.cards(8, "movie")
check(f"the film on your own disk is gone: {[c['title'] for c in rows]}",
      202 not in [c["tmdb_id"] for c in rows] and len(rows) == 2)
check("and silently — no card saying 'you have this', which would be worse than leaving it out",
      not any("own" in json.dumps(c).lower() and c["tmdb_id"] == 202 for c in rows))
db.set_setting("streaming_hide_owned", "0")
check("unless you ask to see everything", len(streaming.cards(8, "movie")) == 3)
db.set_setting("streaming_hide_owned", "1")

print("\n5. Home never waits on TMDB")
calls = len(asked)
for _ in range(5):
    streaming.cards(8, "movie")
    streaming.rows_for_home()
check(f"drawing rows made no API calls at all ({len(asked) - calls})", len(asked) == calls)


def explode(self, path, **params):
    raise metadata.TMDBError("TMDB unreachable")


metadata.TMDB._get = explode
check("and with TMDB unreachable the rows still draw, from what was fetched before",
      len(streaming.cards(8, "movie")) == 2)
try:
    streaming.refresh(force=True)
    unreachable_ok = True
except metadata.TMDBError:
    unreachable_ok = False
check("a refresh that cannot reach TMDB gives up quietly rather than throwing into a thread",
      unreachable_ok)
metadata.TMDB._get = fake_get

print("\n6. the condition the data comes with")
check("JustWatch is credited in what the server reports — which is what the settings page shows",
      "justwatch" in json.dumps(streaming.status()).lower())
check("and in the notices that ship with LENTA",
      "JustWatch" in open("/home/claude/lenta/THIRD-PARTY-NOTICES.md").read())
src = open("/home/claude/lenta/server/lenta/streaming.py").read()
check("and the requirement is written down where the next person will see it",
      "JustWatch" in src and "revoked" in src.lower())

print("\n5b. narrowing the rows to the genres you actually watch")
check(f"fifteen to choose from: {len(streaming.GENRE_NAMES)}", len(streaming.GENRE_NAMES) == 15)
check("the famous ones are there",
      {"Action", "Horror", "Science Fiction", "Fantasy", "Comedy"} <= set(streaming.GENRE_NAMES))
streaming.set_genres(["Science Fiction", "Action"])
check(f"films use the film numbers: {streaming.genre_ids('movie')}",
      streaming.genre_ids("movie") == [28, 878])
check(f"series use their own, which are different: {streaming.genre_ids('show')}",
      streaming.genre_ids("show") == [10759, 10765])
streaming.set_genres(["Horror"])
check("a genre TMDB keeps only for films narrows the films", streaming.genre_ids("movie") == [27])
check("and leaves the series alone rather than emptying the row", streaming.genre_ids("show") == [])
state = streaming.genre_state()
check("the settings page is told which ones are films only",
      {g["name"] for g in state["all"] if g["films_only"]} == {"Horror", "Romance", "Thriller"})
streaming.set_genres(["Action", "Comedy"])
asked.clear()
streaming.refresh(force=True)
disc = next(p for path, p in asked if path == "/discover/movie")
check(f"the search is narrowed with them: with_genres={disc.get('with_genres')}",
      disc.get("with_genres") in ("28|35", "35|28"))
check("joined with a pipe, which means any of them — a comma would mean all at once, and find nothing",
      "|" in (disc.get("with_genres") or ""))
streaming.set_genres([])
asked.clear()
streaming.refresh(force=True)
disc = next(p for path, p in asked if path == "/discover/movie")
check("and with none ticked the rows are not narrowed at all", not disc.get("with_genres"))

print("\n6a. the title's own number on Netflix, so a click opens the film")
# Wikidata stands in here the way TMDB does. What is being checked is our half: that the number is asked
# for once, remembered, and turned into the address Netflix actually uses.
streaming.set_languages_off([])
with db.db() as con:
    con.execute("DELETE FROM stream_ids")
asked.clear()
wiki_calls.clear()
streaming.refresh(force=True)
check(f"Wikidata was asked once for the whole row, not once per title ({len(wiki_calls)})",
      len(wiki_calls) == 2)               # one for the films, one for the series
rows = streaming.cards(8, "movie")
first = [c for c in rows if c["tmdb_id"] == 101][0]
check(f"a film opens on its own Netflix address: {first['link']}",
      first["link"] == "https://www.netflix.com/watch/8175101")
check("a title Wikidata has no number for still works, through the service's search",
      [c for c in rows if c["tmdb_id"] == 303][0]["link"].startswith("https://www.netflix.com/search?"))
shows = streaming.cards(8, "show")
check(f"a series opens on its page, not mid-episode: {shows[0]['link']}",
      shows[0]["link"] == "https://www.netflix.com/title/8175404" or shows[0]["link"].startswith(
          "https://www.netflix.com/search?"))
before = len(wiki_calls)
ext_before = len([a for a in asked if a[0].endswith("/external_ids")])
asked.clear()
wiki_calls.clear()
streaming.refresh(force=True)
check(f"a second refresh asks nobody again — the numbers are kept ({len(wiki_calls)} calls)",
      not wiki_calls and not [a for a in asked if a[0].endswith("/external_ids")])
check(f"and the first refresh did ask ({ext_before} external_ids, {before} Wikidata)", ext_before > 0)

print("\n6b. leaving out languages you would rather not be offered")
check(f"the languages actually in the rows are listed, with counts: "
      f"{[(l['code'], l['count']) for l in streaming.languages()]}",
      {l["code"] for l in streaming.languages()} == {"en", "ko", "hi"})
streaming.set_languages_off(["ko"])
titles = [c["title"] for c in streaming.cards(8, "movie")]
# One of the three English titles is on this server and was dropped back in section 4, so what should
# remain is exactly the one English film that is neither owned nor filtered.
check(f"a ticked language is gone: {titles}", "Another One" not in titles)
check("and the others are untouched", titles == ["A Film You Do Not Have"])
check("the row says which languages are off", streaming.status()["languages_off"] == ["ko"])
streaming.set_languages_off([])
check("untick it and it comes back", "Another One" in [c["title"] for c in streaming.cards(8, "movie")])

# A row of twenty goes short once a filter removes some of it, so a second page is fetched to refill —
# but only when the first page was full. A short page means the service has no more to give, and asking
# again would spend a call to be told so.
def pages_asked(first_page_size, filter_on):
    streaming.set_languages_off(["ko"] if filter_on else [])

    def paged(self, path, **params):
        asked.append((path, params))
        if path.startswith("/discover"):
            n = first_page_size if params.get("page") == 1 else 2
            return {"results": [{"id": 5000 + i + 100 * params.get("page", 1), "title": f"F{i}",
                                 "release_date": "2026-01-01", "poster_path": "/f.jpg", "overview": "",
                                 "vote_average": 5, "original_language": "en"} for i in range(n)]}
        return fake_get(self, path, **params)

    metadata.TMDB._get = paged
    asked.clear()
    streaming._discover(metadata.TMDB(), 8, "movie")
    metadata.TMDB._get = fake_get
    return sorted(p.get("page") for path, p in asked if path.startswith("/discover"))


check("with a filter on and a full first page, a second is fetched", pages_asked(20, True) == [1, 2])
check("with no filter, one page is enough for a row of twenty", pages_asked(20, False) == [1])
check("and a short first page is never followed by a second — there is nothing there",
      pages_asked(3, True) == [1])
streaming.set_languages_off([])

print("\n6c-pick. choosing from the pictures TMDB already has")
got = streaming.art_choices(101, "movie")
check(f"both shapes are offered: {sorted(got)}", sorted(got) == ["backdrop", "poster"])
check(f"{len(got['poster'])} upright and {len(got['backdrop'])} wide, best first",
      got["poster"][0]["ref"] == "tmdb:/p1.jpg" and got["backdrop"][0]["ref"] == "tmdb:/b1.jpg")
check("each one comes with somewhere to show it from", got["poster"][0]["url"] == "/api/img/tmdb/p1.jpg")
streaming.set_art(101, "movie", "tmdb:/b1.jpg", "backdrop")
card = [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]
check(f"a wide pick becomes the wide picture: {card['backdrop']}", card["backdrop"] == "/api/img/tmdb/b1.jpg")
check("and leaves the upright one alone — a wide picture in a poster's place is a cropped strip",
      card["poster"] == "/api/img/tmdb/a.jpg")
streaming.set_art(101, "movie", "tmdb:/p2.jpg", "poster")
card = [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]
check(f"both can be chosen, and both are kept: {card['poster']} / {card['backdrop']}",
      card["poster"] == "/api/img/tmdb/p2.jpg" and card["backdrop"] == "/api/img/tmdb/b1.jpg")
streaming.clear_art(101, "movie", "poster")
card = [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]
check("one can be put back without losing the other",
      card["poster"] == "/api/img/tmdb/a.jpg" and card["backdrop"] == "/api/img/tmdb/b1.jpg")
streaming.clear_art(101, "movie")

print("\n6c. a picture of your own, which outlives the nightly refresh")
before = streaming.cards(8, "movie")[0]
check(f"by default the picture is TMDB's: {before['poster']}", before["poster"] == "/api/img/tmdb/a.jpg"
      and before["own_art"] is False)
streaming.set_art(101, "movie", "local:mine.jpg", "poster")
after = [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]
check(f"after choosing one, yours is used: {after['poster']}", after["poster"] == "/api/img/local/mine.jpg")
check("and the card says so, so the client can offer to put it back", after["own_art"] is True)
streaming.refresh(force=True)                      # the overnight refresh throws the titles away
kept = [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]
check("a refresh replaces every title — and your picture is still there", kept["poster"] == "/api/img/local/mine.jpg")
streaming.clear_art(101, "movie")
check("until you put it back to the original",
      [c for c in streaming.cards(8, "movie") if c["tmdb_id"] == 101][0]["poster"] == "/api/img/tmdb/a.jpg")

print("\n7. the row as it really reaches a browser")
# Everything above is this module in isolation. This is the whole chain: settings in the database, a
# section built by the library code, a card drawn by the client. It runs against the test server with
# rows put straight into its database, because TMDB cannot be reached from here — but nothing else is
# stood in for, so a mistake anywhere between SQLite and the page shows up.
import sqlite3, subprocess                                                 # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    print("  skip  playwright not installed here")

if sync_playwright:
    subprocess.run(["bash", "/tmp/claude-0/intro/uplive.sh"], check=True, timeout=120)
    live = sqlite3.connect("/tmp/claude-0/intro/data/lenta.db")
    live.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('streaming_services', '[8]')")
    live.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('metadata_region', 'BG')")
    live.execute("INSERT OR REPLACE INTO stream_providers (provider_id, name, logo, priority, region, fetched_at)"
                 " VALUES (8, 'Netflix', '/nflx.jpg', 1, 'BG', ?)", (time.time(),))
    live.executemany(
        "INSERT OR REPLACE INTO stream_titles (provider_id, kind, tmdb_id, rank, title, year, poster,"
        " overview, rating, link, fetched_at) VALUES (8, 'movie', ?, ?, ?, 2026, '/p.jpg', 'x', 7.0, ?, ?)",
        [(999001, 0, "Something On Netflix", "https://www.netflix.com/title/999001", time.time()),
         (999002, 1, "Another Thing", "https://www.netflix.com/title/999002", time.time())])
    live.commit()
    live.close()

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1000})
        page.goto("http://127.0.0.1:8597/", wait_until="domcontentloaded")
        page.click('[data-user="admin"]')
        page.fill("#lp", "secret123")
        page.click("#login button.primary")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1200)

        found = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('.row')];
          const r = rows.find(x => (x.querySelector('h2')?.textContent || '').includes('Popular on Netflix'));
          if (!r) return { row: false, titles: rows.map(x => x.querySelector('h2')?.textContent?.trim()) };
          const a = r.querySelector('.card.streaming a.frame-link');
          return { row: true, credit: (r.querySelector('.row-credit')?.textContent || ''),
                   cards: r.querySelectorAll('.card.streaming').length,
                   href: a?.getAttribute('href'), target: a?.getAttribute('target'),
                   rel: a?.getAttribute('rel'), logo: !!r.querySelector('.sv-logo') };
        }""")
        check(f"the row is on Home: {found.get('titles') if not found['row'] else 'Popular on Netflix'}", found["row"])
        if found["row"]:
            check(f"with {found['cards']} cards", found["cards"] == 2)
            check(f"each links out to the service: {found['href']}",
                  (found["href"] or "").startswith("https://www.netflix.com/"))
            check("in a new tab, as asked — nothing plays in LENTA", found["target"] == "_blank")
            check(f"with rel={found['rel']}, so the opened page cannot reach back into this one",
                  "noopener" in (found["rel"] or ""))
            check("the service's mark is on the picture, so nobody mistakes it for a file they have",
                  found["logo"])
            # The credit is required, but not required to be on every row. It belongs where someone
            # looking for it would go, rather than as small print above the pictures.
            check(f"the row itself carries no small print: {found['credit']!r}", not found["credit"])
        # The service's mark beside the name. Beside it: every img in LENTA is a block by default, so
        # one dropped into a heading takes a line of its own and sits above the words instead.
        mark = page.evaluate("""() => {
          const h = document.querySelector('.row h2 .row-logo')?.closest('h2');
          if (!h) return { there: false };
          const l = h.querySelector('.row-logo'), lr = l.getBoundingClientRect(), hr = h.getBoundingClientRect();
          return { there: true, display: getComputedStyle(l).display, w: Math.round(lr.width),
                   sameLine: Math.abs((lr.top + lr.height / 2) - (hr.top + hr.height / 2)) < 8,
                   beforeText: lr.left < hr.left + 40 };
        }""")
        check(f"the service's mark is in the row's heading: {mark.get('w')}px", mark.get("there"))
        check("on the same line as the name, not above it", mark.get("sameLine"))
        check("and before the words", mark.get("beforeText"))

        # The tile takes the shape of the tiles around it. Whichever artwork style this server is set
        # to, a streaming tile and one of your own must come out the same — a row of portraits between
        # two rows of landscapes is exactly what looks bolted on.
        shape = page.evaluate("""() => {
          const s = document.querySelector('.card.streaming');
          const mine = [...document.querySelectorAll('.card:not(.streaming)')]
            .find(c => c.classList.contains('landscape') === s.classList.contains('landscape')
                    && c.querySelector('.frame') && c.dataset.kind !== 'channel');
          if (!mine) return { pair: false };
          const g = e => { const r = e.querySelector('.frame').getBoundingClientRect();
                           return [Math.round(r.width), Math.round(r.height)]; };
          return { pair: true, wide: s.classList.contains('landscape'),
                   same: JSON.stringify(g(s)) === JSON.stringify(g(mine)), mine: g(s), theirs: g(mine) };
        }""")
        check(f"it is drawn in the same style and size as your own tiles "
              f"({'landscape' if shape.get('wide') else 'poster'}: {shape.get('mine')})",
              shape.get("pair") and shape.get("same"))

        # The tile must be one of the tiles. The Netflix look on a phone draws its cards through a
        # renderer of its own, so a streaming card that missed it would be the one odd thing on the
        # page — a different size and different corners from everything beside it.
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method:'PUT',
            credentials:'same-origin', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({theme:'netflix'}) }); }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".card.streaming", timeout=30000)
        page.wait_for_timeout(1500)
        shape = page.evaluate("""() => {
          const s = document.querySelector('.card.streaming');
          const n = [...document.querySelectorAll('.card.nfx-card:not(.streaming)')][0];
          if (!n) return { pair: false };
          const g = e => { const f = e.querySelector('.frame'), r = f.getBoundingClientRect();
                           return [Math.round(r.width), Math.round(r.height), getComputedStyle(f).borderRadius]; };
          return { pair: true, same: JSON.stringify(g(s)) === JSON.stringify(g(n)), mine: g(s), theirs: g(n) };
        }""")
        check(f"in the phone Netflix look it is the same shape as your own tiles: {shape.get('mine')}",
              shape.get("pair") and shape.get("same"))
        page.evaluate("""async () => { await fetch('/api/me/prefs', { method:'PUT',
            credentials:'same-origin', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({theme:'projector'}) }); }""")
        page.set_viewport_size({"width": 1600, "height": 1000})

        # The pencil: the same affordance as on a title you own, in the same corner.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".card.streaming", timeout=30000)
        page.wait_for_timeout(1200)
        # Present in the markup is not the same as visible: the whole fault last time was a stylesheet
        # hiding card tools on any desktop with a mouse, so this asks the browser what it actually drew.
        page.hover(".card.streaming")
        page.wait_for_timeout(400)
        pencil = page.evaluate("""() => {
          const el = document.querySelector('.card.streaming [data-stream-art]');
          if (!el) return { there: false };
          const cs = getComputedStyle(el), box = el.getBoundingClientRect();
          const tools = el.closest('.card-tools');
          return { there: true, display: cs.display, toolsDisplay: tools && getComputedStyle(tools).display,
                   opacity: tools && getComputedStyle(tools).opacity,
                   w: Math.round(box.width), h: Math.round(box.height) };
        }""")
        check(f"an administrator gets a pencil on the tile, and it is actually drawn: {pencil}",
              pencil.get("there") and pencil.get("toolsDisplay") != "none"
              and float(pencil.get("opacity") or 0) > 0.5 and pencil.get("w", 0) > 8)
        page.evaluate("document.querySelector('.card.streaming [data-stream-art]').click()")
        page.wait_for_timeout(700)
        check("which opens somewhere to put one",
              "picture" in (page.evaluate("document.querySelector('.modal')?.innerText || ''").lower()))

        # The picker's shape, not just its contents. It once borrowed the metadata editor's styles and
        # was laid out by them: a two-column grid put the tabs top left, the pictures top right, the
        # link box under the tabs and the buttons adrift in the middle. Every element was present and
        # every test passed. So these measure where things actually landed.
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        import json as _json
        art = page.evaluate("""() => [...document.querySelectorAll('.card .frame img')]
            .map(i => i.getAttribute('src').split('?')[0]).filter(Boolean).slice(0, 9)""")
        page.route("**/api/admin/streaming/art/choices*", lambda route: route.fulfill(
            status=200, content_type="application/json", body=_json.dumps({
                "backdrop": [{"ref": f"tmdb:{i}", "url": u, "width": 1920, "height": 1080, "lang": None}
                             for i, u in enumerate(art)],
                "poster": [{"ref": f"tmdb:p{i}", "url": u, "width": 600, "height": 900, "lang": "en"}
                           for i, u in enumerate(art[:5])]})))
        page.hover(".card.streaming")
        page.wait_for_timeout(300)
        page.evaluate("document.querySelector('.card.streaming [data-stream-art]').click()")
        page.wait_for_timeout(2000)
        lay = page.evaluate("""() => {
          const box = document.querySelector('.modal'), grid = document.querySelector('.sa-grid');
          const pick = document.querySelector('.sa-pick'), acts = document.querySelector('.sa-actions');
          const tabs = document.querySelector('.sa-tabs');
          if (!box || !grid || !pick || !acts || !tabs) return { ok: false };
          const R = e => e.getBoundingClientRect();
          return { ok: true, picks: document.querySelectorAll('.sa-pick').length,
                   pickH: Math.round(R(pick).height), pickW: Math.round(R(pick).width),
                   gridFraction: R(grid).width / R(box).width,
                   tabsAboveGrid: R(tabs).bottom <= R(grid).top + 1,
                   actionsBelowGrid: R(acts).top >= R(grid).bottom - 1 };
        }""")
        check(f"the pictures are a usable size, not a strip: {lay.get('pickW')}x{lay.get('pickH')}",
              lay.get("ok") and lay["pickH"] > 60 and lay["pickW"] > 120)
        check(f"the grid uses the width of the dialog, not half of it ({lay.get('gridFraction', 0):.0%})",
              lay.get("ok") and lay["gridFraction"] > 0.8)
        check("the tabs are above the pictures and the buttons below them",
              lay.get("ok") and lay["tabsAboveGrid"] and lay["actionsBelowGrid"])
        check(f"and every choice offered is shown ({lay.get('picks')})", lay.get("picks") == len(art))
        page.unroute("**/api/admin/streaming/art/choices*")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # On a television the row must not appear at all: there is no second tab to open.
        page.goto("http://127.0.0.1:8597/?tv=1", wait_until="domcontentloaded")
        page.wait_for_selector(".card", timeout=30000)
        page.wait_for_timeout(1500)
        on_tv = page.evaluate("document.querySelectorAll('.card.streaming').length")
        check(f"and on a TV there are none of these cards ({on_tv})", on_tv == 0)
        b.close()

print()
if fails:
    print(f"{len(fails)} failed: {', '.join(fails)}")
    raise SystemExit(1)
print("streaming rows ok")
