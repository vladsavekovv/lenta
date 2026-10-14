# Third-party notices

LENTA's own code, artwork and documentation are in the public domain ([the Unlicense](LICENSE)).
The open-source projects and data services listed here are **not** part of that dedication: each keeps its own
license and terms, and every license file they require is included next to them.

## Included in this repository

| Component | Where | License |
|---|---|---|
| [hls.js](https://github.com/video-dev/hls.js) | `web/vendor/hls.min.js` | Apache License 2.0 (`web/vendor/hls.LICENSE`, full text in `web/vendor/APACHE-2.0.txt`) |
| [Golos Text](https://github.com/googlefonts/golos-text) font | `web/fonts/golos-text-*.woff2` | SIL Open Font License 1.1 (`web/fonts/OFL.txt`) |
| [Oswald](https://github.com/googlefonts/OswaldFont) font | `web/fonts/oswald-*.woff2` | SIL Open Font License 1.1 (`web/fonts/OFL.txt`) |
| Outlines of the digits 0–9 from Golos Text | the large *Top 10* numbers in `web/js/ui.js` | SIL Open Font License 1.1 (`web/fonts/OFL.txt`) |
| [core-js](https://github.com/zloirock/core-js) | bundled into `web/legacy/app.js` (the version for older TVs) | MIT (`web/legacy/core-js.LICENSE`) |

## Downloaded during installation

| Component | Where | License |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) static build by [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) | `/opt/lenta/ffmpeg` | GPL (GPL build: includes x264 and other GPL parts). Source: ffmpeg.org and the BtbN repository. Skipped with `FFMPEG_SOURCE=system`. |
| Python packages from `server/requirements.txt` | `/opt/lenta/venv` | see below |

| Python package | License |
|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause |
| [HTTPX](https://github.com/encode/httpx) | BSD-3-Clause |
| [GuessIt](https://github.com/guessit-io/guessit) | LGPL-3.0-or-later |
| [Mutagen](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) |
| [PyJWT](https://github.com/jpadilla/pyjwt) | MIT |
| [NumPy](https://numpy.org) | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) (HTTPS certificates) | Apache-2.0 OR BSD-3-Clause |

These packages are installed from PyPI on your machine; they are not copied into this repository.

## Build tools (not shipped)

[esbuild](https://esbuild.github.io) (MIT), [Babel](https://babeljs.io) (MIT; its small helper functions end up in
`web/legacy/app.js`) and [Lightning CSS](https://lightningcss.dev) (MPL-2.0) build `web/legacy/`
from `tools/`; they are installed only when you rebuild it.

## Used only when you install the Samsung TV app

| Component | Where | License |
|---|---|---|
| [vitalets/tizen-webos-sdk](https://github.com/vitalets/docker-tizen-webos-sdk) Docker image with Samsung's Tizen CLI and `sdb` | Downloaded by `deploy/install-tv.sh` into Docker on your server | MIT (image scripts); Samsung's Tizen tools under Samsung's SDK license |
| [Docker](https://www.docker.com) (`docker.io`) | Installed from your distribution if missing | Apache License 2.0 |

## Data services

LENTA contacts these services only when they are switched on in Settings (Metadata and Media pages), or, for
Google Books and Open Library, when you have an Audiobooks library. Their data stays
under their terms, which bind whoever runs the server, not LENTA. Most need your own free key. Some allow only
personal, non-commercial use (IMDb's datasets; TMDB's free API): if you run LENTA for anything else, switch those
off or get a license from the service.

| Service | Used for | Terms |
|---|---|---|
| [TMDB](https://www.themoviedb.org) | Titles, descriptions, artwork, cast, trailers list | [API terms](https://www.themoviedb.org/api-terms-of-use). *This product uses the TMDB API but is not endorsed or certified by TMDB.* |
| [JustWatch](https://www.justwatch.com) | Which streaming services carry a title, per country (reached through TMDB's watch-provider endpoints, and shown in Settings › Streaming services and the Home rows) | TMDB supplies this on one condition: **"In order to use this data you must attribute the source of the data as JustWatch"**, and says access is revoked where it is not. LENTA shows that credit on the settings page and on every row built from it. If you change how those rows look, keep it. |
| [IMDb Non-Commercial Datasets](https://developer.imdb.com/non-commercial-datasets/) | IMDb ratings and votes | Personal and non-commercial use only. Information courtesy of IMDb (https://www.imdb.com). Used with permission. |
| [Wikipedia](https://www.wikipedia.org) / [Wikidata](https://www.wikidata.org) | Summaries in the About panel | Text under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), shown with a link to the article |
| [OMDb API](https://www.omdbapi.com) | Rotten Tomatoes, Metacritic, awards, posters (own key) | [OMDb terms](https://www.omdbapi.com/legal.htm) |
| [Fanart.tv](https://fanart.tv) | HD artwork and logos (own key) | [Fanart.tv terms](https://fanart.tv/terms-and-conditions/) |
| [AniList](https://anilist.co) | Anime artwork | [AniList API terms](https://docs.anilist.co/guide/terms-of-use) |
| [OpenSubtitles.com](https://www.opensubtitles.com) | Subtitle search and download (own key) | [OpenSubtitles terms](https://www.opensubtitles.com/en/tos) |
| [KinoCheck](https://api.kinocheck.com) | Official trailer picks (videos hosted on YouTube) | KinoCheck API terms of use |
| [Google Books API](https://developers.google.com/books) | Audiobook descriptions, authors, years, genres, covers (no key) | [Google APIs Terms of Service](https://developers.google.com/terms) |
| [Open Library](https://openlibrary.org) | Audiobook covers and details (no key) | [Open Library API usage](https://openlibrary.org/developers/api); book data is public domain (CC0), covers are shown as the site provides them |
| Apple iTunes Search API | Apple TV trailers and soundtrack previews | Apple's terms for the iTunes Search API |
| YouTube | Trailer playback (embedded player, privacy-enhanced mode) | [YouTube Terms of Service](https://www.youtube.com/t/terms) |

**Themes.** LENTA's themes are its own CSS. They imitate the feel of well-known services but carry none of
their artwork, logos, icons or typefaces: the *HBO Max* theme ships no HBO material and stands Golos Text in
for Max Sans, and the *Netflix* theme ships nothing of Netflix's. HBO Max is a trademark of Warner Bros.
Discovery and Netflix is a trademark of Netflix, Inc.

All trademarks belong to their owners. LENTA is not affiliated with or endorsed by any of these projects
or services.
